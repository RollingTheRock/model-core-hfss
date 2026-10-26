"""em-copilot 固定 AEDT 工具函数库（roadmap §8.2 差距-1）。

这些函数由 AI 通过官方 ``run_python_code`` 调一行 import 来执行——
官方进程注入已持有的 ``desktop`` 句柄，不自己 attach AEDT
（独立进程 attach 已证伪，见 memory/desktop-grpc-attach-hang.md）。

设计原则
--------
- **纯函数**：接收 ``desktop`` + 参数，返回结构化 JSON 字符串，无隐藏状态。
- **可单测**：脱离 AEDT 的纯逻辑（频率点计数、端口数推断）单独成函数，可 mock。
- **AI 只调用，不改逻辑**：业务代码固定在本文件里，有版本管理；AI 传给
  ``run_python_code`` 的只有 ``import; 函数调用`` 这一行，不是业务逻辑。
- **数值纪律**（§13.3）：返回原始数值，不做 AI 可篡改的解释。

调用约定
--------
``run_python_code`` 注入 ``desktop``（PyAEDT Desktop 实例）、``odesktop``、
``aedt_port``。本模块函数的第一个参数恒为 ``desktop``，调用语句把它传进去::

    from em_copilot.aedt_ops import list_setups_and_sweeps
    result = list_setups_and_sweeps(desktop)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def list_setups_and_sweeps(
    desktop: Any,
    project_name: str | None = None,
    design_name: str | None = None,
) -> str:
    """列出当前 design 的所有 setup 及每个 setup 的 sweep 名。

    AI 用这个确认 setup_name / sweep_name，避免后续导出传错。

    Parameters
    ----------
    desktop : PyAEDT Desktop
        由 ``run_python_code`` 注入的官方 server Desktop 句柄。
    project_name : str, optional
        工程名；None = 活动工程。
    design_name : str, optional
        设计名；None = 活动设计。

    Returns
    -------
    str
        JSON: ``{"design": ..., "setups": [{"name": ..., "sweeps": [...]}]}``
    """
    from ansys.aedt.core import get_pyaedt_app

    app = get_pyaedt_app(
        project_name=project_name, design_name=design_name, desktop=desktop
    )
    setups = []
    for s in app.setups:
        sweeps = s.get_sweep_names()
        setups.append({"name": s.name, "sweeps": sweeps})
    return json.dumps(
        {"design": app.design_name, "setups": setups}, ensure_ascii=False, indent=2
    )


def export_touchstone_sweep(
    desktop: Any,
    setup_name: str,
    sweep_name: str,
    output_file: str,
    project_name: str | None = None,
    design_name: str | None = None,
) -> str:
    """导出频率扫描 Touchstone（非 LastAdaptive 单点）。

    官方 ``export_results`` 默认导 LastAdaptive 单频率点（3GHz 一个点），
    而工程需要的整段 0–10 GHz 扫描要 ``setup=, sweep=`` 才能拿到。这个函数
    正确传 ``setup`` 和 ``sweep``，并后校验输出文件含 >1 频率点。

    Parameters
    ----------
    desktop : PyAEDT Desktop
        由 ``run_python_code`` 注入的官方 server Desktop 句柄。
    setup_name : str
        Setup 名，如 ``"3GHz"``（先调 :func:`list_setups_and_sweeps` 确认）。
    sweep_name : str
        Sweep 名，如 ``"DC_10GHz"``。
    output_file : str
        目标 ``.sNp`` 路径。**不能**在输入工程目录内（§14 安全）。
    project_name, design_name : str, optional
        None = 活动工程/设计。

    Returns
    -------
    str
        JSON: ``{success, output_file, n_freqs, n_ports, setup, sweep}``。
        失败时 ``success=False`` 并带 ``error``。
    """
    from ansys.aedt.core import get_pyaedt_app

    app = get_pyaedt_app(
        project_name=project_name, design_name=design_name, desktop=desktop
    )

    # 校验 setup
    setup_names = [s.name for s in app.setups]
    if setup_name not in setup_names:
        return json.dumps(
            {"success": False, "error": f"setup {setup_name!r} not in {setup_names}"},
            ensure_ascii=False,
        )
    setup = next(s for s in app.setups if s.name == setup_name)

    # 校验 sweep（get_sweep_names 是权威源；setup.sweeps 列表可能返回空）
    sweep_names = setup.get_sweep_names()
    if sweep_name not in sweep_names:
        return json.dumps(
            {
                "success": False,
                "error": f"sweep {sweep_name!r} not in {sweep_names} (setup {setup_name!r})",
            },
            ensure_ascii=False,
        )

    # 导出真正的频率扫描（非 LastAdaptive）
    app.export_touchstone(
        setup=setup_name, sweep=sweep_name, output_file=output_file
    )

    # 后校验：确保 >1 频率点（防止悄悄拿到单点 LastAdaptive 文件）
    n_ports = len(getattr(app, "port_list", []) or []) or guess_ports_from_path(output_file)
    n_freqs = count_touchstone_freqs(output_file, n_ports)
    if n_freqs < 2:
        return json.dumps(
            {
                "success": False,
                "error": f"导出文件只有 {n_freqs} 频率点；期望扫描。"
                f" setup={setup_name!r} sweep={sweep_name!r} 是否正确？",
                "output_file": output_file,
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "success": True,
            "output_file": str(Path(output_file).resolve()).replace("\\", "/"),
            "n_freqs": n_freqs,
            "n_ports": n_ports,
            "setup": setup_name,
            "sweep": sweep_name,
        },
        ensure_ascii=False,
        indent=2,
    )


def get_model_screenshot(
    desktop: Any,
    output_file: str,
    project_name: str | None = None,
    design_name: str | None = None,
) -> str:
    """导出当前设计的 3D 模型截图（JPEG，非图形模式离屏渲染）。

    用官方 ``export_design_preview_to_jpg``，在无图形模式下也能渲染。
    截图失败不影响主流程——返回 success=False 但不抛异常（§8.5）。

    Returns
    -------
    str
        JSON: ``{success, output_file, error?}``。
    """
    from ansys.aedt.core import get_pyaedt_app

    try:
        app = get_pyaedt_app(
            project_name=project_name, design_name=design_name, desktop=desktop
        )
        # export_design_preview_to_jpg 要求先 save
        app.save_project()
        app.export_design_preview_to_jpg(output_file)
        return json.dumps(
            {
                "success": True,
                "output_file": str(Path(output_file).resolve()).replace("\\", "/"),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps(
            {"success": False, "error": str(e)}, ensure_ascii=False
        )


def get_convergence_data(
    desktop: Any,
    setup_name: str,
    project_name: str | None = None,
    design_name: str | None = None,
) -> str:
    """取 adaptive pass 收敛数据（四面体数 + delta-S）。

    三级 fallback 策略（gRPC 模式下原生 COM API 多不可用）：

    1. **.EMConvData 文件解析**（最可靠）：直接读 AEDT results 目录下的
       ``*.EMConvData`` 文本文件，解析 ``SolvedElements`` 和
       ``MaxMagDeltaS`` 字段。纯文本解析，不依赖 gRPC COM 调用。
    2. **pyaedt profile API**：``setup.get_profile()`` →
       ``adaptive_pass.steps`` → ``delta_s_max`` 属性。能拿到 delta-S
       但四面体数需要 pandas（非图形环境通常没装）。
    3. **原生 oDesign.GetConvergenceData**：COM 调用，gRPC 下常失败。

    返回每个 pass 的 ``{pass, tetrahedra, delta_s}``。
    若工程未求解或无收敛数据，返回空列表（不报错）。

    Returns
    -------
    str
        JSON: ``{setup, passes: [{pass, tetrahedra, delta_s}], n_passes}``。
    """
    from ansys.aedt.core import get_pyaedt_app

    app = get_pyaedt_app(
        project_name=project_name, design_name=design_name, desktop=desktop
    )

    setup_names = [s.name for s in app.setups]
    if setup_name not in setup_names:
        return json.dumps(
            {"success": False, "error": f"setup {setup_name!r} not in {setup_names}"},
            ensure_ascii=False,
        )

    passes: list[dict] = []

    # ---- 策略 1：直接解析 .EMConvData 文本文件 ----
    try:
        passes = _parse_emconv_file(app, setup_name, design_name)
    except Exception:
        pass  # 文件解析失败不致命，继续 fallback

    # ---- 策略 2：pyaedt profile API（补 delta-S） ----
    if not passes:
        try:
            passes = _conv_from_profile(app, setup_name)
        except Exception:
            pass

    # ---- 策略 3：原生 COM（gRPC 下大概率失败，最后尝试） ----
    if not passes:
        try:
            passes = _conv_from_native(app, setup_name)
        except Exception:
            pass

    return json.dumps(
        {"setup": setup_name, "passes": passes, "n_passes": len(passes)},
        ensure_ascii=False,
        indent=2,
    )


def _parse_emconv_file(app: Any, setup_name: str, design_name: str | None) -> list[dict]:
    """解析 ``.EMConvData`` 文本文件取四面体数 + delta-S。

    文件格式（AEDT 收敛数据文本）::

        $begin 'EMConvergenceData'
            $begin 'ConvInfo'
                IsConverged=true
                MaxPasses='3'
                ConvTarget='0.02'
            $end 'ConvInfo'
            $begin 'ConvValues'
                $begin 'QuantityValues'
                    PassNo=1
                    SolvedElements(9.14e+02, 0.0)
                $end 'QuantityValues'
                $begin 'QuantityValues'
                    PassNo=2
                    SolvedElements(1.029e+03, 0.0)
                    MaxMagDeltaS(1.03e-04, 0.0)
                $end 'QuantityValues'
            $end 'ConvValues'
        $end 'EMConvergenceData'

    每个 pass 可能有两条 QuantityValues（一条无 Intrinsics，一条带
    ``Intrinsics='Freq=...'``），数值相同——只取无 Intrinsics 的那条去重。
    """
    import glob

    # 定位 .EMConvData 文件
    # app.project_path 返回工程目录，app.project_name 返回工程名（不含.aedt）
    proj_dir = Path(app.project_path)
    proj_name = app.project_name  # e.g. "Twinaxial_cable_COPY"
    design = design_name or app.design_name
    results_dir = proj_dir / f"{proj_name}.aedtresults" / f"{design}.results"
    conv_files = glob.glob(str(results_dir / "*.EMConvData"))
    if not conv_files:
        return []

    content = Path(conv_files[0]).read_text(errors="replace")
    passes: dict[int, dict] = {}
    current_pass: int | None = None
    has_intrinsics = False

    for line in content.splitlines():
        line = line.strip()
        m_pass = re.match(r"PassNo=(\d+)", line)
        if m_pass:
            # 如果上一个 pass 有 Intrinsics，不覆盖已存的
            if current_pass is not None and has_intrinsics:
                pass
            current_pass = int(m_pass.group(1))
            has_intrinsics = False
            if current_pass not in passes:
                passes[current_pass] = {
                    "pass": f"Adaptive Pass {current_pass}",
                    "tetrahedra": 0,
                    "delta_s": None,
                }
            continue
        if line.startswith("Intrinsics="):
            has_intrinsics = True
            continue
        m_tets = re.match(r"SolvedElements\(([\d.eE+-]+)", line)
        if m_tets and current_pass is not None:
            passes[current_pass]["tetrahedra"] = int(float(m_tets.group(1)))
            continue
        m_delta = re.match(r"MaxMagDeltaS\(([\d.eE+-]+)", line)
        if m_delta and current_pass is not None:
            passes[current_pass]["delta_s"] = float(m_delta.group(1))
            continue

    # 按_pass_号排序
    return [passes[k] for k in sorted(passes.keys())]


def _conv_from_profile(app: Any, setup_name: str) -> list[dict]:
    """用 pyaedt profile API 取收敛数据（delta-S 可靠，四面体需 pandas）。"""
    setup = next(s for s in app.setups if s.name == setup_name)
    profile = setup.get_profile()
    if not profile:
        return []
    # Profiles 是 dict-like，取第一个 SimulationProfile
    first_key = next(iter(profile.keys()))
    sim_profile = profile.get(first_key)
    if not sim_profile or not hasattr(sim_profile, "adaptive_pass"):
        return []
    ap = sim_profile.adaptive_pass
    pass_names = [s for s in ap.process_steps if "Pass" in s]
    passes = []
    for pn in pass_names:
        step = ap.steps.get(pn)
        entry = {"pass": pn, "tetrahedra": 0, "delta_s": None}
        if step:
            if hasattr(step, "delta_s_max"):
                entry["delta_s"] = step.delta_s_max
        passes.append(entry)
    return passes


def _conv_from_native(app: Any, setup_name: str) -> list[dict]:
    """用原生 oDesign.GetConvergenceData（gRPC 下常失败）。"""
    o_design = app.odesign
    conv = o_design.GetConvergenceData(setup_name)
    if not conv:
        return []
    n = conv.GetNumPasses()
    passes = []
    for i in range(n):
        tets = int(conv.GetNumElements(i))
        delta = float(conv.GetPassConvergenceValue(i))
        passes.append(
            {"pass": f"Adaptive Pass {i+1}", "tetrahedra": tets, "delta_s": delta}
        )
    return passes


# ---- 纯逻辑（不依赖 AEDT，可单测） ---------------------------------


def guess_ports_from_path(path: str) -> int:
    """从 ``.sNp`` 文件名推断端口数（``foo.s4p`` → 4）。"""
    m = re.search(r"\.s(\d+)p", Path(path).name, re.IGNORECASE)
    return int(m.group(1)) if m else 2


def count_touchstone_freqs(path: str, n_ports: int) -> int:
    """数 Touchstone 文件的频率点数（按数据行 / 每频行数）。

    ``.sNp`` 文件每个频率点占 n_ports 行（每行一个端口的 S 参数）。
    返回频率点数。文件不存在返回 0。
    """
    p = Path(path)
    if not p.is_file():
        return 0
    rows_per_freq = max(n_ports, 1)
    data_rows = 0
    with p.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("!") or s.startswith("#"):
                continue
            # 数据行以数字或正负号开头
            if re.match(r"^[+-]?[0-9.]", s):
                data_rows += 1
    return max(data_rows // rows_per_freq, 0)
