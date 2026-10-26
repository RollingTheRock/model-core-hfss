"""run-pyaedt-demo skill — 已验证的分步闭环驱动脚本。

本脚本封装了 2026-09-17 实测跑通的**完整演示流程**：

    build(建模) → solve(仿真) → export(v1 报告, 自动打开)
    → modify(改参数) → solve(重算) → rerun(v2 报告, 自动打开) → disconnect

它通过官方 ``run_python_code`` 注入 ``desktop`` 句柄来调用，**不修改业务
代码**，只做按步分发 + 确定性编排。

为什么做成 skill（而不是演示时现场写脚本）：
  演示流程里很多坑是踩过才知的（见 SKILL.md 的"已知坑"）：
  1. 从空白桌面建新工程会走 COM 回退分支而失败 → 必须先有 base 工程。
  2. ``create_new_project`` 遇到桌面同名会自动改名（coax_step → coax_step1），
     必须回读 ``base_app.project_name``，否则后续 get_pyaedt_app 用旧名失败。
  3. sweep 名是 PyAEDT 自动生成的（如 Sweep_2HCO43），不能硬编码，要动态取。
  4. 重新仿真（analyze_setup）后 ``setup.sweeps`` 可能枚举为空 → ``_first_sweep``
     有解析 ``.aedt`` 文件的兜底。
  5. 版本化导出目录（run_v2/report）要预创建，否则导出写空文件报 0 频率点。
  6. ``run_python_code`` 的路径要 Windows 反斜杠绝对路径，前斜杠偶发丢文件。

调用方式（AI 在 run_python_code 里注入 desktop 后，用本模块的 ``run``）：

    import importlib.util
    spec = importlib.util.spec_from_file_location("demo_driver",
        r"D:\\HCX\\HFSS\\em-copilot\\skills\\run-pyaedt-demo\\demo_driver.py")
    drv = importlib.util.module_from_spec(spec); spec.loader.exec_module(drv)
    result = drv.run(desktop, step="build", template="coax", params={...}, ...)

返回统一 JSON 字符串（每步的 stages 即证据链，可直接投屏）。
可 step 单独触发（分步演示，含改参数重算），也可 step="all" 一键跑 v1 闭环。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

TEMPLATES = ("coax", "microstrip", "twinax")

# 每个模板的默认参数（演示时 AI 可覆写；key 必须属于 TEMPLATE_SCHEMAS）
DEFAULT_PARAMS: dict[str, dict[str, str]] = {
    "coax": {
        "Ci_R_inner": "0.5mm", "Ci_R_outer": "1.5mm", "Ci_epsr": "2.2",
        "Ci_losst": "0.001", "Ci_len": "50mm",
    },
    "microstrip": {
        "Mj_W": "1.2mm", "Mj_T": "0.035mm", "Mj_h": "0.8mm", "Mj_epsr": "4.4",
        "Mj_losst": "0.02", "Mj_len": "30mm",
    },
    "twinax": {
        "Tx_R_sig": "0.16mm", "Tx_R_drn": "0.1mm", "Tx_spread": "0.6mm",
        "Tx_epsr": "1.4", "Tx_losst": "0.0002", "Tx_len": "60mm",
    },
}

# 默认求解设置（可覆写）
DEFAULT_SOLVE = {
    "frequency": "2GHz",
    "max_passes": 4,
    "delta_s": 0.02,
    "sweep": (1.0, 3.0, 11),  # (start, stop GHz, n_points)
}


def _load_templates() -> Any:
    """原子地加载 templates.py（绕开官方 Python 模块缓存）。

    用 importlib 直接载文件，不依赖已装包，确保每次拿到当前磁盘版本。
    路径必须用 Windows 反斜杠绝对路径（run_python_code 环境下前斜杠偶发丢文件）。
    """
    import importlib.util
    wpath = r"D:\HCX\HFSS\em-copilot\src\em_copilot\templates.py"
    spec = importlib.util.spec_from_file_location("templates", wpath)
    tpl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tpl)
    return tpl


def _first_sweep(desktop: Any, tpl: Any, project: str, design: str,
                 setup_name: str) -> str | None:
    """动态取第一个 sweep 名（PyAEDT 自动生成，不能硬编码）。

    主路径用 pyaedt 的 ``setup.sweeps`` 枚举；若为空（重新求解后常见，
    见 [[pyaedt-sweeps-empty-after-analyze]]），回退到解析 ``.aedt`` 文件
    里的 ``$begin 'Sweeps'`` 段，正则提取 ``Sweep_<X>`` 名。
    """
    from ansys.aedt.core import get_pyaedt_app
    app = get_pyaedt_app(project_name=project, design_name=design, desktop=desktop)
    # 主路径：pyaedt 枚举
    try:
        setup = app.get_setup(setup_name)
        for s in getattr(setup, "sweeps", []):
            try:
                return s.name
            except Exception:
                continue
    except Exception:
        pass
    # 回退路径：解析 .aedt 文件
    try:
        import glob as _glob
        import re as _re
        proj_dir = app.project_path
        for p in _glob.glob(str(proj_dir) + "/**/*.aedt", recursive=True):
            data = open(p, encoding="utf-8", errors="replace").read()
            m = _re.search(r"\$begin 'Sweeps'.*?\$end 'Sweeps'", data, _re.S)
            if m:
                names = _re.findall(r"\$begin '(Sweep_[A-Za-z0-9_]+)'", m.group(0))
                if names:
                    return names[0]
    except Exception:
        pass
    return None


def _open_report(path: str | None) -> list[str]:
    """打开生成的 HTML 报告（仅本机、尽力而为，不阻断主流程）。

    优先浏览器默认打开；失败则回退到资源管理器定位。返回用于证据链的说明。
    """
    if not path:
        return ["skip: 无报告路径"]
    p = Path(path)
    if not p.is_file():
        return [f"skip: 报告不存在 {path}"]
    notes: list[str] = []
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f"open {str(p)!r}")  # noqa: S605
        else:
            os.system(f"xdg-open {str(p)!r} >/dev/null 2>&1 &")  # noqa: S605
        notes.append(f"report opened: {path}")
    except Exception as e:  # noqa: BLE001
        notes.append(f"report open failed: {e}")
    return notes


def _version_out(tag: str, out_dir: str | None) -> str:
    """给报告/导出文件加版本后缀，避免同一工程二次导出互相覆盖。

    ``out_dir`` 缺省时原样返回。返回类似 ``demo_output/run_coax/run1/report``，
    其中 ``run_{tag}`` 为版本目录；**返回前确保目录已存在**（导出方不建目录会
    静默写空文件，导致 0 频率点）。
    """
    if not out_dir:
        return out_dir or ""
    base = str(out_dir).rstrip("/\\")
    vdir = f"{base}/run_{tag}/report"
    # 确保版本目录存在（Windows 正反斜杠均接受）
    try:
        Path(vdir).mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return vdir


def run(
    desktop: Any,
    step: str,
    template: str,
    *,
    params: dict | None = None,
    out_dir: str | None = None,
    project_name: str | None = None,
    design_name: str | None = None,
    reanalyze: bool = False,
    setup_name: str | None = None,
    frequency: str = DEFAULT_SOLVE["frequency"],
    max_passes: int = DEFAULT_SOLVE["max_passes"],
    delta_s: float = DEFAULT_SOLVE["delta_s"],
    sweep: tuple[float, float, int] | None = DEFAULT_SOLVE["sweep"],
    points_of_interest: list[str] | None = None,
) -> str:
    """执行一个 demo step 或完整闭环。

    step 取值：
      "build"   仅建模（+端口+Setup+扫频）
      "modify"  改参（白名单/范围校验）+ 落库
      "solve"   仅求解（同一工程再次调 = 重新仿真，用于改参后重算）
      "export"  导出 sweep → 收敛 → 截图 → 报告，并自动打开
      "rerun"   同一工程下 改参数→solve→导出 v2 的版本化闭环（演示第 4-6 步）
      "all"     build → solve → export（一键 v1 闭环，modify 可由调用方插入）

    Returns
    -------
    str
        JSON：``{success, step, template, project, design, out_dir,
        stages:[{step,tool,code,artifact}...], validate:[...], error?}``。
    """
    import json as _json
    tpl = _load_templates()
    if template not in TEMPLATES:
        return _json.dumps({"success": False, "error": f"未知模板 {template!r}"},
                           ensure_ascii=False, indent=2)
    # 模板默认参数 + 调用方覆写
    prm = {**DEFAULT_PARAMS.get(template, {}), **(params or {})}
    out = out_dir or f"demo_output/run_{template}"

    # 解析 setup 名（solve/export 需要；build 后 setup 名通常是 Setup1）
    setup = setup_name or "Setup1"

    if step == "all":
        notes: list[dict[str, Any]] = []
        r = _json.loads(run(desktop, "build", template, params=prm, out_dir=out,
                            project_name=project_name, design_name=design_name,
                            frequency=frequency, max_passes=max_passes,
                            delta_s=delta_s, sweep=sweep))
        if not r.get("success"):
            return _json.dumps({"success": False, "error": r.get("error")},
                               ensure_ascii=False, indent=2)
        notes.append({"step": "build", "result": {k: r.get(k) for k in
                      ("project", "design", "out_dir")}})
        # 后续步骤用 build 回读的实际工程名
        prj = r["project"]
        des = r["design"]
        r2 = _json.loads(run(desktop, "solve", template, out_dir=out,
                             project_name=prj, design_name=des, setup_name=setup))
        if not r2.get("success"):
            return _json.dumps({"success": False, "error": r2.get("error")},
                               ensure_ascii=False, indent=2)
        notes.append({"step": "solve", "notes": r2.get("validate", [])})
        sw = _first_sweep(desktop, tpl, prj, des, setup)
        r3 = _json.loads(run(desktop, "export", template, out_dir=out,
                             project_name=prj, design_name=des,
                             setup_name=setup, sweep_name=sw,
                             points_of_interest=points_of_interest))
        if not r3.get("success"):
            return _json.dumps({"success": False, "error": r3.get("error")},
                               ensure_ascii=False, indent=2)
        return _json.dumps({"success": True, "step": "all", "template": template,
                            "project": prj, "design": des, "out_dir": out,
                            "stages": notes, "export": r3.get("validate", [])},
                           ensure_ascii=False, indent=2)

    # ---- 单步 ----
    try:
        if step == "build":
            # 关键：先确保有 base 工程可 attach（空白桌面会走 COM 回退而失败）。
            # demo_step(build) 内部已回读工程名 + 补 _finalize_ports_and_setup。
            return tpl.demo_step(desktop, "build", template, params=prm, out_dir=out,
                                 project_name=project_name, design_name=design_name,
                                 frequency=frequency, max_passes=max_passes,
                                 delta_s=delta_s, sweep=sweep)
        if step == "modify":
            if not params:
                return _json.dumps({"success": False, "error": "modify 需要 params"},
                                   ensure_ascii=False, indent=2)
            return tpl.demo_step(desktop, "modify", template, params=params,
                                 out_dir=out, project_name=project_name,
                                 design_name=design_name, reanalyze=reanalyze,
                                 setup_name=setup)
        if step == "solve":
            return tpl.demo_step(desktop, "solve", template, out_dir=out,
                                 project_name=project_name, design_name=design_name,
                                 setup_name=setup)
        if step == "export":
            sweep_name = _first_sweep(desktop, tpl, project_name, design_name, setup)
            if not sweep_name:
                return _json.dumps({"success": False,
                                    "error": f"未找到 {setup} 的 sweep"},
                                   ensure_ascii=False, indent=2)
            res = _json.loads(tpl.demo_step(desktop, "export", template, out_dir=out,
                                            project_name=project_name, design_name=design_name,
                                            setup_name=setup, sweep_name=sweep_name,
                                            points_of_interest=points_of_interest))
            if res.get("success"):
                # 缺陷1：导出后自动打开报告
                res["open"] = _open_report(res.get("report_html"))
            return _json.dumps(res, ensure_ascii=False, indent=2)
        if step == "rerun":
            # 同一工程下 改参数→solve→导出 v2 的版本化闭环（演示第 4-6 步）。
            # 不改工程名，只改参数，报告输出到 run_v2 目录避免覆盖 v1。
            if not params:
                return _json.dumps({"success": False, "error": "rerun 需要 params"},
                                   ensure_ascii=False, indent=2)
            if not project_name or not design_name:
                return _json.dumps({"success": False,
                                    "error": "rerun 需要 project_name + design_name"},
                                   ensure_ascii=False, indent=2)
            out_v2 = _version_out("v2", out_dir)
            rr: list[dict[str, Any]] = []
            r = _json.loads(tpl.demo_step(desktop, "modify", template, params=params,
                                          out_dir=out_v2, project_name=project_name,
                                          design_name=design_name, reanalyze=reanalyze,
                                          setup_name=setup))
            if not r.get("success"):
                return _json.dumps({"success": False, "error": r.get("error")},
                                   ensure_ascii=False, indent=2)
            rr.append({"step": "modify", "notes": r.get("validate", []),
                       "artifact": f"changed={r.get('changed')} applied={r.get('applied')}"})
            if not reanalyze:
                r2 = _json.loads(tpl.demo_step(desktop, "solve", template, out_dir=out_v2,
                                               project_name=project_name, design_name=design_name,
                                               setup_name=setup))
                if not r2.get("success"):
                    return _json.dumps({"success": False, "error": r2.get("error")},
                                       ensure_ascii=False, indent=2)
                rr.append({"step": "solve", "notes": r2.get("validate", [])})
            sw = _first_sweep(desktop, tpl, project_name, design_name, setup)
            if not sw:
                return _json.dumps({"success": False, "error": f"未找到 {setup} 的 sweep"},
                                   ensure_ascii=False, indent=2)
            r3 = _json.loads(tpl.demo_step(desktop, "export", template, out_dir=out_v2,
                                           project_name=project_name, design_name=design_name,
                                           setup_name=setup, sweep_name=sw,
                                           points_of_interest=points_of_interest))
            if not r3.get("success"):
                return _json.dumps({"success": False, "error": r3.get("error")},
                                   ensure_ascii=False, indent=2)
            rr.append({"step": "export", "notes": r3.get("validate", []),
                       "report_html": r3.get("report_html")})
            # 版本化报告也自动打开
            rr_open = _open_report(r3.get("report_html"))
            return _json.dumps({"success": True, "step": "rerun", "template": template,
                                "project": project_name, "design": design_name,
                                "out_dir": out_v2, "report_html": r3.get("report_html"),
                                "stages": rr, "open": rr_open},
                               ensure_ascii=False, indent=2)
        return _json.dumps({"success": False,
                            "error": f"未知 step {step!r}（可选: build/modify/solve/export/all/rerun）"},
                           ensure_ascii=False, indent=2)
    except Exception as e:
        return _json.dumps({"success": False, "error": str(e)},
                           ensure_ascii=False, indent=2)
