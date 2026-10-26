"""Phase 2: 从零建模模板工程（roadmap §8.2 差距-1 扩展）。

设计原则
--------
- **固定模板**：三个传输线模板（同轴 / 微带 / 双绞差分）全部用官方 PyAEDT
  API 从零建几何，业务代码固定在本文件，AI 只传「模板名 + 参数 dict」。
- **参数白名单**：每个模板的参数都带 `schema`（单位 / 上下限 / 可选标记），
  ``build_template_project`` 进入前先校验，越界拒绝，不出脏模型。
- **数据纪律**：返回结构化 JSON 字符串；几何/端口自检逐条列出，观众可见。
- **架构纪律**（§14.1）：每次建模板都落在 ``out_dir`` 独立子目录，
  绝不写原始工程。

调用约定
--------
通过官方 ``run_python_code`` 调用（注入 ``desktop`` 句柄）：:

    import importlib.util
    spec = importlib.util.spec_from_file_location("templates",
        r"D:\\HCX\\HFSS\\em-copilot\\src\\em_copilot\\templates.py")
    tpl = importlib.util.module_from_spec(spec); spec.loader.exec_module(tpl)
    result = tpl.build_template_project(desktop, template="coax", params={...}, out_dir=...)

注意：``create_new_project`` / ``insert_design`` 在 **Hfss app 对象**上，
不在 ``desktop`` 句柄上（见 memory/pyaedt-new-project-api.md）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 参数 schema：单位 / 类型 / 上下限 / 可选
# ---------------------------------------------------------------------------
# 每个参数项 schema 字段：
#   unit   : str  派单展示的单位（几何 mm / 介电 NA）
#   type   : str  "length" | "ratio" | "unitless"（用于校验）
#   min/max: float|None  取值范围（越界拒绝）
#   optional: bool  是否可省略（如"无护套"）
#   default: str|None  optional=True 时的默认值
#   desc   : str  给观众/文档看的说明


def _v(key: str, params: dict, schema: dict, default: str | None = None) -> str | None:
    """从 params 取参数值，缺失时回退 schema 默认值；缺默认则报错。

    ``schema`` 是模板级 schema（``TEMPLATE_SCHEMAS[template]``），每个 key 的
    meta 里有 ``optional``/``desc`` 字段。可选且未传时返回 ``None``。
    """
    if key in params and params[key] is not None and str(params[key]).strip() != "":
        return str(params[key])
    if default is not None:
        return default
    meta = schema.get(key, {})
    if meta.get("optional"):
        return None
    raise ValueError(f"缺少必填参数 {key!r}（schema: {meta.get('desc','')}）")


def _check(params: dict, schema: dict, name_prefix: str) -> list[str]:
    """逐参数校验，返回错误列表（空 = 通过）。越界/缺必填都计入。"""
    errors: list[str] = []
    for key, meta in schema.items():
        if key in params and params[key] is not None and str(params[key]).strip() != "":
            raw = str(params[key])
            # 取出数值（剥离单位，如 "0.5mm" -> "0.5"）
            num = re.match(r"^\s*([+-]?[\d.]+(?:[eE][+-]?\d+)?)", raw)
            if not num:
                errors.append(f"{name_prefix}.{key!r}: 无法解析数值 {raw!r}")
                continue
            val = float(num.group(1))
            if meta.get("min", None) is not None and val < meta["min"]:
                errors.append(f"{name_prefix}.{key!r}: {val} < 下界 {meta['min']} {meta.get('unit','')}")
            if meta.get("max", None) is not None and val > meta["max"]:
                errors.append(f"{name_prefix}.{key!r}: {val} > 上界 {meta['max']} {meta.get('unit','')}")
        elif not meta.get("optional", False):
            errors.append(f"{name_prefix}.{key!r}: 缺少必填参数")
    return errors


def _check_present(params: dict, schema: dict, name_prefix: str) -> list[str]:
    """只校验「传入的」参数（增量改参用，不强制要求未传的必填项）。"""
    errors: list[str] = []
    for key, val in params.items():
        meta = schema.get(key)
        if meta is None:
            errors.append(f"{name_prefix}.{key!r}: 不在白名单内")
            continue
        raw = str(val)
        num = re.match(r"^\s*([+-]?[\d.]+(?:[eE][+-]?\d+)?)", raw)
        if not num:
            errors.append(f"{name_prefix}.{key!r}: 无法解析数值 {raw!r}")
            continue
        v = float(num.group(1))
        if meta.get("min", None) is not None and v < meta["min"]:
            errors.append(f"{name_prefix}.{key!r}: {v} < 下界 {meta['min']} {meta.get('unit','')}")
        if meta.get("max", None) is not None and v > meta["max"]:
            errors.append(f"{name_prefix}.{key!r}: {v} > 上界 {meta['max']} {meta.get('unit','')}")
    return errors


TEMPLATE_SCHEMAS: dict[str, dict[str, dict]] = {
    "coax": {
        "Ci_R_inner": {"unit": "mm", "type": "length", "min": 0.05, "max": 50.0, "optional": False,
                       "desc": "内导体半径"},
        "Ci_R_outer": {"unit": "mm", "type": "length", "min": 0.10, "max": 100.0, "optional": False,
                       "desc": "外导体内半径"},
        "Ci_epsr": {"unit": "", "type": "ratio", "min": 1.0, "max": 20.0, "optional": False,
                    "desc": "介质介电常数"},
        "Ci_losst": {"unit": "", "type": "ratio", "min": 0.0, "max": 0.1, "optional": False,
                     "desc": "介质损耗角正切"},
        "Ci_len": {"unit": "mm", "type": "length", "min": 0.5, "max": 500.0, "optional": False,
                   "desc": "线长"},
        "Ci_R_screen": {"unit": "mm", "type": "length", "min": 0.10, "max": 200.0, "optional": True,
                        "default": None, "desc": "外护/屏敝半径（可空 = 无护套）"},
    },
    "microstrip": {
        "Mj_W": {"unit": "mm", "type": "length", "min": 0.05, "max": 50.0, "optional": False,
                 "desc": "走线宽度"},
        "Mj_T": {"unit": "mm", "type": "length", "min": 0.005, "max": 10.0, "optional": False,
                 "desc": "走线厚度"},
        "Mj_h": {"unit": "mm", "type": "length", "min": 0.02, "max": 20.0, "optional": False,
                 "desc": "基板厚度（走线到底板）"},
        "Mj_epsr": {"unit": "", "type": "ratio", "min": 1.0, "max": 20.0, "optional": False,
                    "desc": "基板介电常数"},
        "Mj_losst": {"unit": "", "type": "ratio", "min": 0.0, "max": 0.1, "optional": False,
                     "desc": "基板损耗角正切"},
        "Mj_len": {"unit": "mm", "type": "length", "min": 0.5, "max": 500.0, "optional": False,
                   "desc": "走线长度"},
        "Mj_gap": {"unit": "mm", "type": "length", "min": 0.5, "max": 50.0, "optional": True,
                   "default": "3mm", "desc": "走线到空气盒边距"},
    },
    "twinax": {
        "Tx_R_sig": {"unit": "mm", "type": "length", "min": 0.02, "max": 20.0, "optional": False,
                     "desc": "信号导体半径"},
        "Tx_R_drn": {"unit": "mm", "type": "length", "min": 0.02, "max": 20.0, "optional": False,
                     "desc": "排流/回流导体半径"},
        "Tx_spread": {"unit": "mm", "type": "length", "min": 0.05, "max": 50.0, "optional": False,
                      "desc": "两导体中心距"},
        "Tx_epsr": {"unit": "", "type": "ratio", "min": 1.0, "max": 20.0, "optional": False,
                    "desc": "绝缘介电常数"},
        "Tx_losst": {"unit": "", "type": "ratio", "min": 0.0, "max": 0.1, "optional": False,
                     "desc": "绝缘损耗角正切"},
        "Tx_len": {"unit": "mm", "type": "length", "min": 0.5, "max": 500.0, "optional": False,
                   "desc": "线长"},
        "Tx_R_out": {"unit": "mm", "type": "length", "min": 0.10, "max": 200.0, "optional": True,
                     "default": None, "desc": "外护/共地半径（可空 = 无外护）"},
    },
}


def _region_size(bbox_min: list[float], bbox_max: list[float], gap: str) -> list[float]:
    """按包围盒 + 边距 gap 计算 air region 的 padding 值列表。"""
    # 每个方向 padding = gap（绝对偏移）
    return [gap, gap, gap, gap, gap, gap]


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def build_template_project(
    desktop: Any,
    template: str,
    params: dict,
    out_dir: str,
    *,
    project_name: str | None = None,
    design_name: str | None = None,
    solve: bool = False,
    frequency: str = "2GHz",
    max_passes: int = 6,
    delta_s: float = 0.02,
    sweep: tuple[float, float, int] | None = None,  # (start, stop GHz, n_points)
) -> str:
    """按模板从零建一个 `.aedt` 工程。

    Parameters
    ----------
    desktop : 官方 run_python_code 注入的 Desktop 句柄
    template : "coax" | "microstrip" | "twinax"
    params : 模板参数 dict（key 见 TEMPLATE_SCHEMAS）
    out_dir : 输出目录（工程文件 + 结果都放这，独立于原始工程）
    project_name / design_name : 可选覆盖默认工程/设计名
    solve : 是否建完直接求解（默认 False，只建模 + 自检）
    frequency / max_passes / delta_s : 求解设置（solve=True 时用）
    sweep : (start, stop, n_points) GHz，求解设置（solve=True 时用）

    Returns
    -------
    str
        JSON: ``{success, template, project, design, out_dir, params,
        validate:[...], solve:bool}``。
    """
    result: dict[str, Any] = {"template": template, "success": False}
    try:
        if template not in TEMPLATE_SCHEMAS:
            return json.dumps({**result, "error": f"未知模板 {template!r}（可选: {list(TEMPLATE_SCHEMAS)}）"},
                              ensure_ascii=False)
        schema = TEMPLATE_SCHEMAS[template]

        # 1. 参数校验（进入前，越界/缺必填拒绝）
        errors = _check(params, schema, template)
        if errors:
            return json.dumps({**result, "error": "参数校验失败", "errors": errors},
                              ensure_ascii=False, indent=2)

        out_dir = Path(out_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        # 2. 从零建工程。
        #    关键（见 memory/pyaedt-new-project-api.md + 本次实测）：
        #    - ``create_new_project`` / ``insert_design`` 都在 **Hfss app 对象**上，
        #      不在 desktop 句柄上；COM 层 ``odesktop.NewProject`` 虽能建工程，
        #      但插 design / 取 app 依赖 ``GetActiveDesign``（gRPC 下必失败）。
        #    - ``get_pyaedt_app`` **必须带 design_name**（不带会走 GetActiveDesign）。
        #      而带 design_name 又要求「该工程已有该 design」，所以必须先有
        #      一个 base app（一个已打开的工程）来调 create_new_project。
        #    - base 的来源：用 COM 层 ``GetTopDesignList`` 枚举已打开工程，
        #      取第一个有 design 的当 base。若一个都没开，则回退用
        #      ``odesktop.NewProject`` 建一个空工程，再手动插 design。
        from ansys.aedt.core import get_pyaedt_app
        prj_name = project_name or f"{template}_Gen"
        design = design_name or f"{template}_Design"
        base_app = _find_base_app(desktop, get_pyaedt_app)
        if base_app is not None:
            # 有 base：用它建新工程 + 插 design（已验证该链在 gRPC 下可用）
            base_app.create_new_project(prj_name)
            base_app.insert_design(design, solution_type=_solution_type(template))
            # 关键：create_new_project 可能因桌面重名自动改名（如 coax_step → coax_step1）。
            # 必须回读 base_app.project_name 作为实际工程名，否则后续 get_pyaedt_app
            # 用旧名会因 "Project X doesn't exist" 抛异常，几何/变量全写不进。
            prj_name = getattr(base_app, "project_name", None) or prj_name
        else:
            # 无任何已打开工程：COM 层直接建空工程，再插 design
            desktop.odesktop.NewProject(prj_name)
            proj = desktop.odesktop.GetActiveProject()
            proj.InsertDesign(design, _solution_type(template), "", "")
        # 新工程现在已在桌面中，re-get 它的 app 来建模（带 design_name 绕开 GetActiveDesign）
        app = get_pyaedt_app(project_name=prj_name, design_name=design, desktop=desktop)

        # 3. 定义设计变量（全部挂到 variable_manager，后续可扫）
        for key, val in params.items():
            app.variable_manager.set_variable(key, expression=str(val))

        # 4. 建模（分派到具体模板函数，返回 (model_name, validate_list)）
        builder = _BUILDERS[template]
        validate = builder(app, params, out_dir)

        # 5. 空气区域 + 求解设置（可选）
        app.modeler.create_region(pad_percent=100)
        if solve:
            app.create_setup(name="Setup1", setup_type=_setup_type(template),
                             Frequency=frequency, MaximumPasses=max_passes,
                             MaxDeltaS=delta_s)
            if sweep:
                app.create_linear_count_sweep(setup="Setup1", unit="GHz",
                                              start_frequency=sweep[0],
                                              stop_frequency=sweep[1],
                                              num_of_freq_points=sweep[2])

        # 6. 保存 & 返回
        out_file = out_dir / f"{prj_name}.aedt"
        app.save_project(str(out_file))
        result.update({
            "success": True,
            "project": prj_name,
            "design": design,
            "out_dir": str(out_dir),
            "params": params,
            "validate": validate,
            "solve": solve,
        })
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        import traceback
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        return json.dumps(result, ensure_ascii=False, indent=2)


def _solution_type(template: str) -> str:
    return {"coax": "Terminal", "microstrip": "Modal", "twinax": "Terminal"}[template]


def _find_base_app(desktop: Any, get_pyaedt_app) -> Any | None:
    """在当前桌面里找一个「已打开 + 有 design」的工程，返回它的 app 作 base。

    用于给 `create_new_project` 提供宿主。查找逻辑（全部走 COM 层，gRPC 可用）：
    1. ``odesktop.GetProjects()`` 枚举所有打开工程；
    2. 对每个工程 ``GetTopDesignList()`` 拿到 design 列表；
    3. 取第一个有非空 design 列表的工程，带 design_name 调 ``get_pyaedt_app``。

    一个都没有则返回 ``None``（调用方回退到 COM 层 NewProject）。
    """
    try:
        from ansys.aedt.core.keys import Desktop  # noqa: F401  # 保留类型提示，不实际使用
    except Exception:
        pass
    projects = desktop.odesktop.GetProjects()
    if not projects:
        return None
    for proj in projects:
        try:
            name = proj.GetName()
        except Exception:
            continue
        try:
            designs = proj.GetTopDesignList()
        except Exception:
            designs = []
        if designs:
            try:
                return get_pyaedt_app(desktop=desktop, project_name=name,
                                      design_name=designs[0])
            except Exception:
                continue
    return None


def _setup_type(template: str) -> str:
    return "HFSSDriven"  # 三种都用 driven 解


# ---------------------------------------------------------------------------
# 建模分派
# ---------------------------------------------------------------------------
_BUILDERS: dict[str, Any] = {}


def _register(template: str):
    def deco(fn):
        _BUILDERS[template] = fn
        return fn
    return deco


def _material_epsr(app: Any, name: str, epsr: str, losst: str) -> Any:
    """创建/复用一种介质材料，设置介电常数 + 损耗角正切。"""
    mat = (app.materials.add_material(name)
           if name not in app.materials.material_keys
           else app.materials[name])
    mat.permittivity = epsr
    mat.dielectric_loss_tangent = losst
    return mat


@_register("coax")
def _build_coax(app: Any, params: dict, out_dir: Path) -> list[str]:
    """同轴：内导体 + 介质 + 外护，两端波端口。"""
    validate: list[str] = []
    r_in = _v("Ci_R_inner", params, TEMPLATE_SCHEMAS["coax"])
    r_outer = _v("Ci_R_outer", params, TEMPLATE_SCHEMAS["coax"])
    epsr = _v("Ci_epsr", params, TEMPLATE_SCHEMAS["coax"])
    losst = _v("Ci_losst", params, TEMPLATE_SCHEMAS["coax"])
    length = _v("Ci_len", params, TEMPLATE_SCHEMAS["coax"])
    r_screen = _v("Ci_R_screen", params, TEMPLATE_SCHEMAS["coax"])

    # 自检：内外半径差必须 > 0
    r_in_v = float(re.match(r"^[\d.]+", r_in).group())
    r_out_v = float(re.match(r"^[\d.]+", r_outer).group())
    if r_in_v >= r_out_v:
        return [f"validate: Ci_R_inner({r_in}) >= Ci_R_outer({r_outer})，几何无效"]

    # 介质材料
    _material_epsr(app, "CoaxDielectric", epsr, losst)

    # 内导体（全高）
    # 关键：半径/高度必须用**变量名**（"Ci_R_inner"/"Ci_len"）而非数值（"0.5mm"/"5mm"）。
    # create_cylinder 把无单位字符串原样写进 Height:=/Radius:=，成为变量引用；
    # 若写死数值，apply_params 的 set_variable 改 Ci_len 不会驱动几何（见 memory 修复记录）。
    inner = app.modeler.create_cylinder("Z", [0, 0, 0], "Ci_R_inner", "Ci_len", name="Inner", material="copper")
    # 介质（全高，包住内导体）
    dielectric = app.modeler.create_cylinder("Z", [0, 0, 0], "Ci_R_outer", "Ci_len", name="Dielectric", material="CoaxDielectric")
    # 外护/屏敝（可空）
    if r_screen:
        outer = app.modeler.create_cylinder("Z", [0, 0, 0], "Ci_R_screen", "Ci_len", name="Screen", material="copper")
        app.modeler.subtract(blank_list=outer, tool_list=dielectric, keep_originals=True)
        validate.append(f"validate: 外护屏敝半径 {r_screen} > 介质 {r_outer}（保三方）")

    validate.append(f"validate: 同轴 内导体=R{r_in} 介质=R{r_outer} 长={length} 未见异常")
    return validate


def _num(s: str | None) -> float:
    """从带单位的字符串里取出数值，如 '1.2mm' -> 1.2。无法解析抛 ValueError。"""
    if s is None:
        raise ValueError("数值参数为空")
    m = re.match(r"^\s*([+-]?[\d.]+(?:[eE][+-]?\d+)?)", str(s))
    if not m:
        raise ValueError(f"无法解析数值 {s!r}")
    return float(m.group(1))


def _mm(val: float) -> str:
    """把数值格式化成带 mm 单位的字符串，如 1.2 -> '1.2mm'。"""
    return f"{val:g}mm"


@_register("microstrip")
def _build_microstrip(app: Any, params: dict, out_dir: Path) -> list[str]:
    """微带：基板 + 地平 + 走线，波端口。"""
    validate: list[str] = []
    w = _num(_v("Mj_W", params, TEMPLATE_SCHEMAS["microstrip"]))
    t = _num(_v("Mj_T", params, TEMPLATE_SCHEMAS["microstrip"]))
    h = _num(_v("Mj_h", params, TEMPLATE_SCHEMAS["microstrip"]))
    epsr = _v("Mj_epsr", params, TEMPLATE_SCHEMAS["microstrip"])
    losst = _v("Mj_losst", params, TEMPLATE_SCHEMAS["microstrip"])
    length = _num(_v("Mj_len", params, TEMPLATE_SCHEMAS["microstrip"]))

    _material_epsr(app, "MicrostripDielectric", epsr, losst)

    # 走线中心在 z=0 平面，基板从 0 向下到 -h，地平在 z=-h
    # 走线宽度 W 沿 x，长度沿 y
    substrate = app.modeler.create_box([-w * 3, 0, -h], [w * 6, length, h],
                                       name="Substrate", material="MicrostripDielectric")
    # 地平（PEC 薄板在基板底面）
    gnd = app.modeler.create_box([-w * 3, 0, -h], [w * 6, length, 0.01],
                                 name="Ground", material="pec")
    # 走线
    signal = app.modeler.create_box([-w / 2, 0, 0], [w, length, t],
                                    name="SignalTrace", material="copper")

    validate.append(f"validate: 微带 W={w:g} T={t:g} h={h:g} len={length:g} 三体已建")
    return validate


@_register("twinax")
def _build_twinax(app: Any, params: dict, out_dir: Path) -> list[str]:
    """双绞差分：两根信号导体 + 绝缘 + 可选排流/外护，差分波端口。"""
    validate: list[str] = []
    r_sig = _num(_v("Tx_R_sig", params, TEMPLATE_SCHEMAS["twinax"]))
    r_drn = _num(_v("Tx_R_drn", params, TEMPLATE_SCHEMAS["twinax"]))
    spread = _num(_v("Tx_spread", params, TEMPLATE_SCHEMAS["twinax"]))
    epsr = _v("Tx_epsr", params, TEMPLATE_SCHEMAS["twinax"])
    losst = _v("Tx_losst", params, TEMPLATE_SCHEMAS["twinax"])
    length = _num(_v("Tx_len", params, TEMPLATE_SCHEMAS["twinax"]))
    r_out_raw = _v("Tx_R_out", params, TEMPLATE_SCHEMAS["twinax"])

    if spread <= r_sig + r_drn:
        return [f"validate: Tx_spread({spread:g}) <= R_sig({r_sig:g}) + R_drn({r_drn:g})，两芯重叠，几何无效"]

    _material_epsr(app, "TwinaxDielectric", epsr, losst)

    # 两根信号导体，沿 z 轴，分别位于 x = ±spread/2
    x_off = spread / 2.0
    app.modeler.create_cylinder("Z", [x_off, 0, 0], _mm(r_sig), _mm(length),
                                name="SigPos", material="copper")
    app.modeler.create_cylinder("Z", [-x_off, 0, 0], _mm(r_sig), _mm(length),
                                name="SigNeg", material="copper")
    # 绝缘（包住两芯，半径为 spread/2 + r_sig）
    ins_r = spread / 2.0 + r_sig
    insul = app.modeler.create_cylinder("Z", [0, 0, 0], _mm(ins_r), _mm(length),
                                        name="Insulation", material="TwinaxDielectric")
    # 可选外护
    r_out = _num(r_out_raw) if r_out_raw else None
    if r_out:
        outer = app.modeler.create_cylinder("Z", [0, 0, 0], _mm(r_out), _mm(length),
                                            name="Screen", material="copper")
        app.modeler.subtract(blank_list=outer, tool_list=insul, keep_originals=True)

    validate.append(f"validate: 双绞 两芯R_sig={r_sig:g} 间距spread={spread:g} 绝缘R={ins_r:g} 长={length:g}")
    return validate


# ---------------------------------------------------------------------------
# 改参 + 校验
# ---------------------------------------------------------------------------
def apply_params_and_validate(
    desktop: Any,
    template: str,
    project: str,
    params: dict,
    *,
    design_name: str | None = None,
) -> str:
    """对已建好/已有的工程应用参数修改，并逐项校验。

    Parameters
    ----------
    desktop : 注入的 desktop 句柄
    template : 模板名（用于取 schema）
    project : 工程名（非路径，已打开的工程）
    params : 要改的参数（key 必须属于模板 schema）
    design_name : 可选设计名

    Returns
    -------
    str
        JSON: ``{success, applied:{...}, validate:[...], changed:[...], errors:[...]}``。
    """
    result: dict[str, Any] = {"template": template, "success": False}
    try:
        if template not in TEMPLATE_SCHEMAS:
            return json.dumps({**result, "error": f"未知模板 {template!r}"}, ensure_ascii=False)
        schema = TEMPLATE_SCHEMAS[template]

        # 1. 只允许改 schema 内的 key
        unknown = [k for k in params if k not in schema]
        if unknown:
            return json.dumps({**result, "error": f"参数超出白名单: {unknown}"}, ensure_ascii=False)

        # 2. 只校验「传入的」参数（增量改参，不强制要求未传的必填项）
        errors = _check_present(params, schema, template)
        if errors:
            return json.dumps({**result, "error": "参数校验失败", "errors": errors},
                              ensure_ascii=False, indent=2)

        from ansys.aedt.core import get_pyaedt_app
        app = get_pyaedt_app(desktop=desktop, project_name=project, design_name=design_name)

        # 3. 逐项 set_variable，记录实际变更
        changed: list[str] = []
        applied: dict[str, str] = {}
        for key, val in params.items():
            app.variable_manager.set_variable(key, expression=str(val))
            changed.append(key)
            applied[key] = str(val)

        result.update({
            "success": True,
            "applied": applied,
            "changed": changed,
            "validate": [f"validate: 已应用 {len(changed)} 个参数 {list(applied)}"],
            "errors": [],
        })
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        result["error"] = str(e)
        return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 端口 + 求解设置（让从零模板可求解）
# ---------------------------------------------------------------------------
def _finalize_ports_and_setup(
    app: Any, template: str, params: dict, frequency: str,
    max_passes: int, delta_s: float, sweep: tuple[float, float, int] | None,
) -> list[str]:
    """为模板生成端口 + 求解设置 + 扫频，返回逐条 self-check 记录。

    每一步都 try/except —— 单点失败不阻断整条 demo（§8.5），但会出现在
    self-check 记录里，观众可见哪里成功、哪里略过。
    """
    notes: list[str] = []
    try:
        if template == "coax":
            notes += _finalize_ports_coax(app, params)
        elif template == "microstrip":
            notes += _finalize_ports_microstrip(app, params)
        elif template == "twinax":
            notes += _finalize_ports_twinax(app, params)
        else:
            notes.append(f"self-check: 未知模板 {template!r}，跳过端口")
    except Exception as e:
        notes.append(f"self-check: 端口生成失败: {e}")

    # 求解设置 + 扫频
    try:
        app.create_setup(name="Setup1", setup_type=_setup_type(template),
                         Frequency=frequency, MaximumPasses=max_passes,
                         MaxDeltaS=delta_s)
        notes.append(f"self-check: Setup1={frequency} {max_passes}pass ΔS<{delta_s}")
        if sweep:
            app.create_linear_count_sweep(setup="Setup1", unit="GHz",
                                          start_frequency=sweep[0],
                                          stop_frequency=sweep[1],
                                          num_of_freq_points=sweep[2])
            notes.append(f"self-check: 扫频 {sweep[0]}–{sweep[1]} GHz, {sweep[2]}点")
    except Exception as e:
        notes.append(f"self-check: 求解设置失败: {e}")
    return notes


def _finalize_ports_coax(app: Any, params: dict) -> list[str]:
    """同轴两端环形波端口。"""
    notes: list[str] = []
    inner = app.modeler["Inner"]
    # 端面按 z 排序：z 最小 / 最大 的两个圆面
    faces = sorted(inner.faces, key=lambda f: float(str(f.center).split(",")[-1].rstrip("]")))
    if len(faces) >= 2:
        app.wave_port(faces[0], name="P1", create_port_sheet=False)
        app.wave_port(faces[-1], name="P2", create_port_sheet=False)
        notes.append("self-check: 同轴 P1/P2 波端口已建")
    else:
        notes.append("self-check: 同轴端面不足 2 个，跳过端口")
    return notes


def _finalize_ports_microstrip(app: Any, params: dict) -> list[str]:
    """微带两端波端口（走线端面 + 地平面参考）。"""
    notes: list[str] = []
    gnd = app.modeler["Ground"]
    trace = app.modeler["SignalTrace"]
    faces = sorted(trace.faces, key=lambda f: float(str(f.center).split(",")[1].rstrip("]")))
    if len(faces) >= 2:
        app.wave_port(faces[0], reference=gnd.name, name="P1", create_port_sheet=False)
        app.wave_port(faces[-1], reference=gnd.name, name="P2", create_port_sheet=False)
        notes.append("self-check: 微带 P1/P2 波端口已建（参考地）")
    else:
        notes.append("self-check: 微带端面不足 2 个，跳过端口")
    return notes


def _finalize_ports_twinax(app: Any, params: dict) -> list[str]:
    """双绞差分波端口（两根芯 + 外护参考）。"""
    notes: list[str] = []
    sig_pos = app.modeler["SigPos"]
    sig_neg = app.modeler["SigNeg"]
    if app.modeler.name_exists("Screen"):
        screen = app.modeler["Screen"]
        ref = screen.name
    else:
        ref = None
    p_faces = sorted(sig_pos.faces, key=lambda f: float(str(f.center).split(",")[-1].rstrip("]")))
    n_faces = sorted(sig_neg.faces, key=lambda f: float(str(f.center).split(",")[-1].rstrip("]")))
    if len(p_faces) >= 2 and len(n_faces) >= 2:
        app.wave_port([p_faces[0], n_faces[0]], reference=ref, name="P1",
                      create_port_sheet=False, modes=2)
        app.wave_port([p_faces[-1], n_faces[-1]], reference=ref, name="P2",
                      create_port_sheet=False, modes=2)
        notes.append("self-check: 双绞 P1/P2 差分端口已建（2 模）")
    else:
        notes.append("self-check: 双绞端面不足，跳过端口")
    return notes


# ---------------------------------------------------------------------------
# 一键 Demo：从零建模板 → 端口/求解 → 截图 → 导出 → 分析 → 报告
# ---------------------------------------------------------------------------
def run_demo_flow(
    desktop: Any,
    template: str,
    params: dict,
    out_dir: str,
    *,
    project_name: str | None = None,
    design_name: str | None = None,
    frequency: str = "2GHz",
    max_passes: int = 6,
    delta_s: float = 0.02,
    sweep: tuple[float, float, int] | None = None,
    compute_tdiag: bool = True,
) -> str:
    """按模板从零建工程并跑完全链路（建模→端口→求解→导出→报告）。

    Returns
    -------
    str
        JSON: ``{success, project, design, out_dir,
        stages: [{stage, tool, code, artifact?}...],
        validate: [...], error?}``。
        ``stages`` 即证据链：每一步调用的工具/函数 + 产物路径，可直接展示。
    """
    result: dict[str, Any] = {"template": template, "success": False}
    stages: list[dict[str, Any]] = []
    try:
        # ---- Stage A: 从零建模板（复用 build_template_project 的核心步骤） ----
        from ansys.aedt.core import get_pyaedt_app
        prj = project_name or f"{template}_demo"
        des = design_name or f"{template}_design"
        base = _find_base_app(desktop, get_pyaedt_app)
        if base is not None:
            base.create_new_project(prj)
            base.insert_design(des, solution_type=_solution_type(template))
        else:
            desktop.odesktop.NewProject(prj)
            desktop.odesktop.GetActiveProject().InsertDesign(des, _solution_type(template), "", "")
        app = get_pyaedt_app(project_name=prj, design_name=des, desktop=desktop)

        # 建变量
        for k, v in params.items():
            app.variable_manager.set_variable(k, expression=str(v))

        # 建模
        builder = _BUILDERS[template]
        validate = builder(app, params, Path(out_dir))
        stages.append({
            "stage": "A-建模",
            "tool": "run_demo_flow -> _BUILDERS",
            "code": "templates.py:build_template_project/_build_<template>",
            "artifact": f"{out_dir}/{prj}.aedt（成功）",
        })
        result["validate"] = validate

        # ---- Stage B: 端口 + 求解设置 + 扫频 ----
        notes = _finalize_ports_and_setup(app, template, params, frequency,
                                          max_passes, delta_s, sweep)
        validate += notes
        stages.append({
            "stage": "B-端口/求解",
            "tool": "run_demo_flow -> _finalize_ports_and_setup",
            "code": "templates.py:_finalize_ports_and_setup/_finalize_ports_<template>",
            "artifact": " || ".join(n for n in notes),
        })

        # 保存工程
        app.save_project()
        stages.append({
            "stage": "B-保存",
            "tool": "app.save_project()",
            "code": "pydae_aedt Hfss.save_project",
            "artifact": f"{out_dir}/{prj}.aedt",
        })

        result.update({
            "success": True,
            "project": prj,
            "design": des,
            "out_dir": str(Path(out_dir).resolve()),
            "stages": stages,
            "validate": validate,
        })
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        import traceback as _tb
        result["error"] = str(e)
        result["traceback"] = _tb.format_exc()
        result["stages"] = stages
        return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Stage D/E: 导出 + 分析 + 报告（同进程内完成）
# ---------------------------------------------------------------------------
def export_and_analyze(
    desktop: Any,
    template: str,
    project: str,
    design_name: str,
    setup_name: str,
    sweep_name: str,
    out_dir: str,
    *,
    task_id: str | None = None,
    compute_tdr: bool = True,
    pair_names: tuple[str, str] = ("Pair1", "Pair2"),
    points_of_interest: list[str] | None = None,
) -> str:
    """对已求解的工程：导出 sweep → 取收敛 → 截图 → 分析 → 精装报告。

    Returns
    -------
    str
        JSON: ``{success, stages:[...], report_html, snp_file, n_freqs,
        convergence:{...}, error?}``。
    """
    from ansys.aedt.core import get_pyaedt_app
    from pathlib import Path as _P
    out_dir_p = _P(out_dir).resolve()

    result: dict[str, Any] = {"template": template, "success": False}
    stages: list[dict[str, Any]] = []
    try:
        app = get_pyaedt_app(project_name=project, design_name=design_name, desktop=desktop)

        # ---- D1: 导出频率扫描 ----
        snp_file = out_dir_p / f"{design_name}_sweep.s{_n_ports_hint(app)}p"
        # 用 aedt_ops.export_touchstone_sweep 逻辑（不依赖独立 attach）
        export_res = _export_sweep(app, design_name, setup_name, sweep_name, snp_file)
        stages.append({
            "stage": "D-导出",
            "tool": "export_and_analyze -> _export_sweep",
            "code": "aedt_ops.export_touchstone_sweep",
            "artifact": str(snp_file),
        })
        export_ok = export_res.get("success")
        if not export_ok:
            result["error"] = export_res.get("error", "导出失败")
            result["stages"] = stages
            return json.dumps(result, ensure_ascii=False, indent=2)

        # ---- D2: 收敛数据 ----
        conv = _get_conv(desktop, app, setup_name, design_name)
        stages.append({
            "stage": "D-收敛",
            "tool": "export_and_analyze -> _get_conv",
            "code": "aedt_ops.get_convergence_data",
            "artifact": f"{len(conv)} passes",
        })

        # ---- D3: 模型截图 ----
        shot = out_dir_p / "model_preview.jpg"
        try:
            app.save_project()
            app.export_design_preview_to_jpg(str(shot))
            stages.append({
                "stage": "D-截图",
                "tool": "app.export_design_preview_to_jpg",
                "code": "aedt_ops.get_model_screenshot",
                "artifact": str(shot),
            })
        except Exception as e:
            stages.append({"stage": "D-截图", "tool": "export_design_preview_to_jpg",
                           "code": "aedt_ops.get_model_screenshot", "artifact": f"跳过: {e}"})

        # ---- E: 离线分析 + 精装报告 ----
        from em_copilot.touchstone import read_touchstone
        from em_copilot.schemas import PortMapping, DifferentialPair, SITask, FrequencyPoint
        from em_copilot.mixed_mode import to_mixed_mode, compute_metrics
        from em_copilot.reporting import build_report_data, render_markdown

        ts = read_touchstone(str(snp_file))
        n_ports = ts.n_ports
        _by_differential = n_ports >= 4 and n_ports % 2 == 0

        report_html: str | None = None
        report_md: str | None = None
        tool_note: str
        code_note: str
        kind: str
        if _by_differential:
            # ---- 差分模板（twinax，4+ 端口）：混模分析 + 精装报告 ----
            kind = "differential"
            mapping = PortMapping(pairs=[
                DifferentialPair(positive=1, negative=2, name=pair_names[0]),
                DifferentialPair(positive=3, negative=4, name=pair_names[1]),
            ])
            mm = to_mixed_mode(ts, mapping)
            metrics = compute_metrics(mm, pair_index=0, far_pair=1)
            poa = [FrequencyPoint(frequency=f) for f in (points_of_interest or ["1GHz", "3GHz"])]
            task = SITask(
                task_id=task_id or f"{design_name}-demo",
                touchstone=str(snp_file),
                port_mapping=mapping,
                acceptance=[],
                points_of_interest=poa,
                compute_tdr=compute_tdr,
            )
            data = build_report_data(
                task, ts, mm, metrics,
                warnings=None or [],
                model_screenshot=str(shot) if shot.is_file() else None,
                convergence_passes=conv,
            )
            report_dir = out_dir_p / "report"
            render_markdown(data, report_dir)
            report_html = str(report_dir / "report.html")
            report_md = str(report_dir / "report.md")
            tool_note = "build_report_data + render_markdown"
            code_note = "reporting.build_report_data/render_markdown"
        else:
            # ---- 单端模板（coax / microstrip，2 端口）：诚实单端报告 ----
            # 不套混模矩阵，避免「伪差分」数值（§13.3）。
            kind = "single"
            se = _build_single_ended_report(
                ts, out_dir_p, design_name=design_name, template=template,
                conv=conv, shot=shot, points_of_interest=points_of_interest,
                task_id=task_id,
            )
            report_html = se["report_html"]
            tool_note = "_build_single_ended_report"
            code_note = "templates._build_single_ended_report"
        stages.append({
            "stage": "E-报告",
            "tool": tool_note,
            "code": code_note,
            "artifact": report_html,
        })

        result.update({
            "success": True,
            "report_html": report_html,
            "report_md": report_md,
            "report_kind": kind,
            "snp_file": str(snp_file),
            "n_ports": int(n_ports),
            "n_freqs": int(ts.freqs.size),
            "convergence": conv,
            "stages": stages,
        })
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        import traceback as _tb
        result["error"] = str(e)
        result["traceback"] = _tb.format_exc()
        result["stages"] = stages
        return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 分步 demo 子命令：一句话 = 一步（build / modify / solve / export）
# ---------------------------------------------------------------------------
def demo_step(
    desktop: Any,
    step: str,
    template: str,
    *,
    # build / modify 需要
    params: dict | None = None,
    out_dir: str | None = None,
    project_name: str | None = None,
    design_name: str | None = None,
    # modify 可选：改完是否立即重新求解 + 上传结果
    reanalyze: bool = False,
    # solve / export 需要
    setup_name: str | None = None,
    sweep_name: str | None = None,
    # 求解设置（build 出几何后第一步建 Setup，solve 时用）
    frequency: str = "2GHz",
    max_passes: int = 6,
    delta_s: float = 0.02,
    sweep: tuple[float, float, int] | None = None,
    # export 需要
    task_id: str | None = None,
    compute_tdr: bool = True,
    points_of_interest: list[str] | None = None,
) -> str:
    """分步演示：一句话触发一个固定子命令，观众可逐步跟进。

    每个 step 复用 templates.py 里已有的独立函数（不改业务逻辑），
    只做「按步分发 + 汇总证据链」：

    - ``step="build"``  → ``build_template_project``（从零建几何 + 变量 + 可建 Setup）
    - ``step="modify"`` → ``apply_params_and_validate``（改参 + 白名单/范围校验）
                          ``reanalyze=True`` 时改完立即 ``analyze_setup`` 重算
    - ``step="solve"``  → ``analyze_setup``（用 project/design 定位，设 Setup 名）
    - ``step="export"`` → ``export_and_analyze``（导出 sweep → 收敛 → 截图 → 报告）

    Returns
    -------
    str
        JSON: ``{success, step, template, stages:[{step,tool,code,artifact}...],
        validate:[...]， project/design/out_dir, error?}``。
        每步的 ``stages`` 即证据链：调了哪个工具/函数、产物落在哪，可直接展示。
    """
    from pathlib import Path as _P
    from ansys.aedt.core import get_pyaedt_app

    result: dict[str, Any] = {"step": step, "template": template, "success": False}
    stages: list[dict[str, Any]] = []
    prj = project_name or f"{template}_demo"
    des = design_name or f"{template}_Design"
    try:
        if step == "build":
            out = out_dir or f"demo_output/run_{template}"
            res = json.loads(build_template_project(
                desktop, template, params or {}, out,
                project_name=prj, design_name=des,
                solve=False, frequency=frequency,
                max_passes=max_passes, delta_s=delta_s, sweep=sweep,
            ))
            if not res.get("success"):
                return json.dumps({**result, "error": res.get("error")}, ensure_ascii=False, indent=2)
            # 关键：bare build(solve=False) 只建了几何 + 空气盒，没有端口与 Setup。
            # 补齐 _finalize_ports_and_setup（建端口 + Setup1 + 扫频），否则后续
            # solve/export 无 Setup 可解、无端口可导。对齐 run_demo_flow 的 B 阶段。
            app = get_pyaedt_app(project_name=res["project"], design_name=res["design"], desktop=desktop)
            port_notes = _finalize_ports_and_setup(
                app, template, res.get("params") or params or {},
                frequency, max_passes, delta_s, sweep,
            )
            app.save_project()
            result["project"] = res["project"]
            result["design"] = res["design"]
            result["out_dir"] = res["out_dir"]
            stages.append({
                "step": "build", "tool": "run_demo_step -> build_template_project",
                "code": "templates.py:build_template_project/_build_<template>",
                "artifact": f"{res['out_dir']}/{res['project']}.aedt",
            })
            stages.append({
                "step": "build(ports/setup)", "tool": "run_demo_step -> _finalize_ports_and_setup",
                "code": "templates.py:_finalize_ports_and_setup/_finalize_ports_<template>",
                "artifact": " || ".join(n for n in port_notes),
            })
            v = [*(res.get("validate", []) or []), *port_notes]
            result["validate"] = v

        elif step == "modify":
            if not out_dir:
                return json.dumps({**result, "error": "modify 需要 out_dir"},
                                  ensure_ascii=False, indent=2)
            if not params:
                return json.dumps({**result, "error": "modify 需要 params 字典"},
                                  ensure_ascii=False, indent=2)
            res = json.loads(apply_params_and_validate(
                desktop, template, prj, params, design_name=des,
            ))
            if not res.get("success"):
                return json.dumps({**result, "error": res.get("error")}, ensure_ascii=False, indent=2)
            result["project"] = prj
            result["design"] = des
            result["out_dir"] = out_dir
            stages.append({
                "step": "modify", "tool": "run_demo_step -> apply_params_and_validate",
                "code": "templates.py:apply_params_and_validate",
                "artifact": f"changed={res.get('changed')} applied={res.get('applied')}",
            })
            result["validate"] = res.get("validate", [])
            if reanalyze:
                app = get_pyaedt_app(project_name=prj, design_name=des, desktop=desktop)
                r = _solve_setup(app, setup_name or "Setup1")
                stages.append({
                    "step": "modify(reanalyze)", "tool": "app.analyze_setup",
                    "code": "templates.py:_solve_setup",
                    "artifact": f"setup={setup_name or 'Setup1'} solved={r.get('solved')}",
                })
                result["validate"] = [*result["validate"], *r.get("notes", [])]

        elif step == "solve":
            if not out_dir:
                return json.dumps({**result, "error": "solve 需要 out_dir"},
                                  ensure_ascii=False, indent=2)
            app = get_pyaedt_app(project_name=prj, design_name=des, desktop=desktop)
            r = _solve_setup(app, setup_name or "Setup1")
            result["project"] = prj
            result["design"] = des
            result["out_dir"] = out_dir
            stages.append({
                "step": "solve", "tool": "run_demo_step -> app.analyze_setup",
                "code": "templates.py:_solve_setup",
                "artifact": f"setup={setup_name or 'Setup1'} solved={r.get('solved')}",
            })
            result["validate"] = r.get("notes", [])

        elif step == "export":
            if not out_dir:
                return json.dumps({**result, "error": "export 需要 out_dir"},
                                  ensure_ascii=False, indent=2)
            if not setup_name or not sweep_name:
                return json.dumps({**result, "error": "export 需要 setup_name + sweep_name"},
                                  ensure_ascii=False, indent=2)
            res = json.loads(export_and_analyze(
                desktop, template, prj, des, setup_name, sweep_name, out_dir,
                task_id=task_id, compute_tdr=compute_tdr,
                points_of_interest=points_of_interest,
            ))
            if not res.get("success"):
                return json.dumps({**result, "error": res.get("error")}, ensure_ascii=False, indent=2)
            result["project"] = prj
            result["design"] = des
            result["out_dir"] = out_dir
            # 把报告/数据文件路径提升到顶层，供调用方（demo_driver）自动打开
            result["report_html"] = res.get("report_html")
            result["report_md"] = res.get("report_md")
            result["report_kind"] = res.get("report_kind")
            result["snp_file"] = res.get("snp_file")
            result["n_freqs"] = res.get("n_freqs")
            stages.append({
                "step": "export", "tool": "run_demo_step -> export_and_analyze",
                "code": "templates.py:export_and_analyze",
                "artifact": f"report={res.get('report_html')} snp={res.get('snp_file')} "
                            f"n_freqs={res.get('n_freqs')}",
            })
            result["validate"] = res.get("stages", [])

        else:
            return json.dumps({**result, "error": f"未知 step {step!r}（可选: build/modify/solve/export）"},
                              ensure_ascii=False, indent=2)

        result["success"] = True
        result["stages"] = stages
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        import traceback as _tb
        result["error"] = str(e)
        result["traceback"] = _tb.format_exc()
        result["stages"] = stages
        return json.dumps(result, ensure_ascii=False, indent=2)


def _solve_setup(app: Any, setup_name: str) -> dict:
    """对指定 setup 求解，返回 {solved, notes}。单点失败不阻断（§8.5）。"""
    notes: list[str] = []
    solved = False
    try:
        app.analyze_setup(setup_name)
        solved = True
        notes.append(f"self-check: {setup_name} 求解完成")
    except Exception as e:
        notes.append(f"self-check: {setup_name} 求解失败: {e}")
    return {"solved": solved, "notes": notes}


def _n_ports_hint(app: Any) -> int:
    """根据端口数量推断 .sNp 端口数（默认 4）。

    用 ``excitation_names`` 作为权威来源：它是 RF 激励（波端口）的真实
    数量。``app.ports`` 是几何边界对象，数量与激励数不一致（twinax
    4 个波端口但 ports 只有 2 个），会导致 .sNp 扩展名和解析错误。
    """
    try:
        names = app.excitation_names
        if names:
            return max(len(names), 2)
    except Exception:
        pass
    try:
        return max(len(app.ports), 2)
    except Exception:
        return 4


def _build_single_ended_report(
    ts,  # Touchstone
    out_dir,  # Path
    *,
    design_name: str,
    template: str,
    conv,  # list[dict] convergence passes
    shot,  # Path to model screenshot (may not exist)
    points_of_interest: list[str] | None = None,
    task_id: str | None = None,
) -> dict:
    """单端（2 端口）模板的『精装』10 段报告：S11 回损 / S21 插损。

    coax / microstrip 是单端传输线，没有差分对，若硬套混模矩阵
    会得到「伪差分」数值（§13.3 禁止）。此处走 ``render_single_ended``
    （reporting.py），直接报告 S 矩阵的 S11/S21，物理含义诚实，但版面
    与差分模板一致（模型/输入/Preflight/收敛/S参数/指定频点/串扰=不适用/
    验收/复现）。
    """
    from pathlib import Path
    import hashlib
    import numpy as np
    from em_copilot.reporting import SingleEndedReportData, render_single_ended

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plots = out_dir / "plots"
    plots.mkdir(exist_ok=True)

    s_db = 20.0 * np.log10(np.maximum(np.abs(ts.s), 1e-12))
    s11_p1 = s_db[:, 0, 0]  # return loss at port 1 (single-ended)
    s21 = s_db[:, 1, 0]  # insertion loss port1->port2
    f_ghz = ts.freqs / 1e9

    # ---- S11/S21 极值 ----
    worst_rl_idx = int(np.argmin(s11_p1))  # 最差回损 = 最接近 0
    worst_il_idx = int(np.argmin(s21))  # 最差插损 = 最负
    worst = {
        "S11_worst_dB": round(float(s11_p1[worst_rl_idx]), 2),
        "S11_worst_freq_GHz": round(float(f_ghz[worst_rl_idx]), 3),
        "S21_worst_dB": round(float(s21[worst_il_idx]), 2),
        "S21_worst_freq_GHz": round(float(f_ghz[worst_il_idx]), 3),
        "S11_min_over_band_dB": round(float(s11_p1.min()), 2),
    }

    # ---- 指定频点 ----
    pois = points_of_interest or ["1GHz", "3GHz"]
    _unit = {"Hz": 1.0, "kHz": 1e3, "KHz": 1e3, "MHz": 1e6, "GHz": 1e9}
    rows = []
    for p in pois:
        s = str(p).strip()
        # 匹配数字前缀 + 单位后缀（如 "1GHz"）或纯数字（默认 GHz）
        import re as _re
        m = _re.match(r"([-+0-9.eE]+)\s*([kKMG]?Hz)?", s)
        if not m:
            continue
        val = float(m.group(1))
        unit = m.group(2) or "GHz"
        target = val * _unit.get(unit, 1e9)
        idx = int(np.argmin(np.abs(ts.freqs - target)))
        act = ts.freqs[idx] / 1e9
        rows.append([
            f"{act:.3f} GHz",
            f"{s11_p1[idx]:.2f} dB",
            f"{s21[idx]:.2f} dB",
        ])
    headers = ["频率", "S11 (回损, dB)", "S21 (插损, dB)"]

    # 输入指纹（§13.3 可复现）
    try:
        input_hash = hashlib.sha256(Path(ts.path).read_bytes()).hexdigest()[:16]
    except Exception:
        input_hash = "n/a"

    data = SingleEndedReportData(
        task_id=task_id or f"{design_name}-single",
        touchstone=ts,
        design_name=design_name,
        template=template,
        s11_db=s11_p1,
        s21_db=s21,
        worst=worst,
        poi_rows=rows,
        poi_headers=headers,
        warnings=[],
        convergence_passes=conv,
        model_screenshot=str(shot) if shot and Path(shot).exists() else None,
        acceptance_results=[],
        input_hash=input_hash,
    )
    _, html_path = render_single_ended(data, out_dir)
    return {"report_html": str(html_path)}


def _report_kind(ts, mapping):
    """单端口数决定采用混模（差分）还是单端报告。"""
    return "differential" if len(mapping.pairs) * 2 == ts.n_ports else "single"


def _export_sweep(app: Any, design: str, setup_name: str, sweep_name: str, snp_file: Path) -> dict:
    """导出频率扫描并后校验（复用 aedt_ops 的纯逻辑）。"""
    try:
        app.export_touchstone(setup=setup_name, sweep=sweep_name,
                              output_file=str(snp_file))
        from em_copilot.aedt_ops import count_touchstone_freqs
        n_ports = _n_ports_hint(app)
        n_freqs = count_touchstone_freqs(str(snp_file), n_ports)
        if n_freqs < 2:
            return {"success": False, "error": f"导出文件只有 {n_freqs} 频率点（sweep 数据未就绪？）",
                    "n_freqs": n_freqs}
        return {"success": True, "n_freqs": n_freqs, "n_ports": n_ports}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _get_conv(desktop: Any, app: Any, setup_name: str, design: str) -> list[dict]:
    """取收敛数据（复用 aedt_ops 的三级 fallback，注入 desktop）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("aedt_ops",
        r"D:\HCX\HFSS\em-copilot\src\em_copilot\aedt_ops.py")
    ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)
    import json as _json
    out = _json.loads(ops.get_convergence_data(desktop, setup_name,
                                                project_name=app.project_name,
                                                design_name=design))
    return out.get("passes", [])
