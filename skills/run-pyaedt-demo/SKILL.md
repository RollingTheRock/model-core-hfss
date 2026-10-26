---
name: run-pyaedt-demo
description: Run the verified em-copilot HFSS demo pipeline (build → modify → solve → export) deterministically. Use when the user wants to show the one-shot AI-orchestrated HFSS simulation demo, especially a live/visualizable run in the AEDT window. Wraps the validated functions in templates.py via the official run_python_code tool and injects the desktop handle. Also use to demo parameter-modification (re-analyze) mid-demo.
---

# Run PyAEDT Demo (执行型)

This skill runs the **verified, deterministic** em-copilot demo pipeline. It is the
*execution* counterpart to `automate-pyaedt-em` (which is the *judgment* layer). Where
`automate-pyaedt-em` teaches Claude how to decide, this skill teaches Claude how to
**run the exact sequence that is proven to work**, so a live demo never hits an
avoidable pitfall.

## When to use

- The user says "跑一遍演示" / "一键演示" / "演示模板建模求解" / "show the demo".
- A live, visualizable run in the AEDT window (`non_graphical=false`) is wanted.
- The user wants to demo **parameter modification + re-solve** (参数化仿真对比).

## How to invoke (single canon call)

Do **not** hand-write low-level AEDT calls. Load the driver and call `run()` inside a
`run_python_code` block:

```python
import importlib.util
spec = importlib.util.spec_from_file_location("demo_driver",
    r"D:\HCX\HFSS\em-copilot\skills\run-pyaedt-demo\demo_driver.py")
drv = importlib.util.module_from_spec(spec); spec.loader.exec_module(drv)
result = drv.run(desktop, step="all", template="coax",
                 params={"Ci_R_outer": "1.7mm"},
                 out_dir="D:/HCX/HFSS/em-copilot/demo_output/run_coax_demo",
                 project_name="coax_demo", design_name="coax_demo_Design",
                 setup_name="Setup1")
```

`run()` returns a JSON string. `stages`/`validate` is the evidence chain (tool called,
function, output path) — display it directly to the audience.

## Steps

`step` is one of:

| step | 作用 | 说明 |
|------|------|------|
| `build` | 从零建几何 + 变量 + 波端口 + Setup1 + 扫频 | 停在窗口，供观众检查模型 |
| `modify` | 改参（白名单/范围校验）+ 落库 | `reanalyze=True` 改完立即重算 |
| `solve` | 对 Setup1 求解 | 窗口里看 AEDT Solve 面板 pass 进度 |
| `export` | 导出 sweep → 收敛 → 截图 → 单端/差分报告 | 产出 `.sNp` + `report.html`，并**自动打开报告** |
| `all` | build → solve → export 一键闭环 | 最常用；`modify` 可由调用方在中间插入 |
| `rerun` | **同一工程**下 修改→solve→导出 的版本化闭环 | 不改工程名，只改参数；报告写入 `out/run_v2/report`，避免覆盖 v1 |

`template` is `coax` | `microstrip` | `twinax`. Each has canonical default params in
`demo_driver.py` (`DEFAULT_PARAMS`); pass `params={...}` to override. Keys must belong
to `TEMPLATE_SCHEMAS` in `templates.py` (whitelist + range enforced).

## The demo script (分阶段 v1/v2 全流程)

这是本次演示的**完整过程**，把「改参数 → 重新仿真 → 出新报告」作为核心一步，
一步步走，每个产物都当场可见：

| # | 动作 | 调用 | 现场可见产物 |
|---|------|------|--------------|
| 1 | **建模** | `run(step="build")` 建工程+几何+波端口+Setup1+扫频 | 窗口里 model & 要素树 |
| 2 | **运行** | `run(step="solve")` | Solve 面板 Pass 进度 |
| 3 | **输出 v1 报告** | `run(step="export")` → `.s2p` + `report.html` | 报告**自动打开** |
| 4 | **改参数** | `run(step="modify", params={...})` | 几何**当场变化** |
| 5 | **重新仿真** | `run(step="solve")` 同一工程再次求解 | 新一轮 Solve 进度 |
| 6 | **输出 v2 报告** | `run(step="rerun")` → `run_v2/report.html` | 新报告**自动打开**，与 v1 对比 |
| 7 | **结束** | 结束演示 | — |
| 8 | **释放** | `disconnect_from_aedt(close_desktop=...)` | license 释放 |

> **关键**：第 6 步「重新仿真出 v2」**不新建工程**——复用第 1 步的同一个
> project，只改参数后重算，报告落到 `out/run_v2/report`（与 v1 同存不覆盖），
> 观众可以来回翻 v1/v2 两根 S 参数曲线的差异。这一步就是 `step="rerun"`。

## Two demo modes

1. **Step-by-step** (best for "打破黑箱", 推荐的演示方式): 按上面 1→6 逐条调用
   `run(step=...)`，每步一个 `run_python_code`，讲完一步出一个产物。观众跟随
   工具往返、亲眼看到 model / Solve 进度 / 两份报告先后出现。
2. **One-shot** (`step="all"`): 一次调用 build→solve→export（只有 v1，不含改参）。
   仅用于快速热身；要展示改参重算是全场亮点，仍按 step-by-step 走 1→6。

**Mid-demo parameterization (全场亮点)**: 上面第 4 步，改一个观众能一眼看出的
几何参数（如 coax 内导体 `Ci_R_inner` 0.5mm→0.8mm、microstrip 线宽 `Mj_W`、
twinax 芯间距 `Tx_spread`）。改完现场几何立刻变粗/变宽，再第 5 步重算、第 6 步
出 v2 —— 观众在同一份报告里对比 S 参数的前后差异。

## Mandatory preconditions (CRITICAL — these are the pitfalls)

Read this before any live run. Each item is a failure this sequence has actually hit.

1. **A base project must already be open and attachable.** `_find_base_app` relies on
   `GetTopDesignList` over gRPC, which fails on an empty desktop → the code falls back
   to the COM `NewProject` branch, which does **not** survive gRPC. So: launch AEDT,
   then `open_project` an existing `.aedt` (e.g. `Twinaxial_cable_COPY.aedt`) as the
   base host, and confirm `_find_base_app` finds it before `build`.
2. **Use `open_project` (official tool) to load the base** — never `Hfss(...)` or
   `create_design` inside `run_python_code`; those fail over gRPC.
3. **Project-name readback**: `create_new_project` auto-renames on name collision
   (`coax_step` → `coax_step1`). The driver/templates read back
   `base_app.project_name`, so always use the **returned** `project` name in subsequent
   steps, not the name you passed in.
4. **Sweep name is auto-generated** (`Sweep_2HCO43`). Never hardcode it. The driver's
   `export` step resolves it dynamically via `_first_sweep()`.
5. **`_first_sweep` 在重新仿真后可能枚举为空**：`setup.sweeps` / `setup._sweeps`
   在 `analyze_setup` 重算之后会返回空列表（虽然 `.aedt` 里 `$begin 'Sweeps'`
   段仍在）。`_first_sweep` 已加**兜底**：枚举为空时解析 `.aedt` 文件的
   `$begin 'Sweeps' ... $end 'Sweeps'` 段，正则提取 `Sweep_<X>` 名（如
   `Sweep_45SCN2`）。若仍拿不到，r步骤 export/rerun 会报「未找到 <setup> 的
   sweep」。
6. **版本化子目录需预创建**：导出到 `out/run_v2/report` 这类子路径时，若目录
   不存在，`export_touchstone` 会静默写空文件 → 报「0 频率点」。`_version_out`
   已加 `Path.mkdir(parents=True, exist_ok=True)` 确保目录先建好。
7. **`run_python_code` path form**: use Windows backslash absolute paths in the
   `spec_from_file_location` string. Forward-slash absolute paths intermittently fail
   with `No such file or directory`.
6. **Start AEDT graphical for a visualizable demo**: `launch_aedt(non_graphical=false)`
   when the audience should see the window; `non_graphical=true` for headless/CI.
   Graphical needs an RDP desktop session with `explorer.exe`.

## Preflight checklist

- [ ] `check_aedt_status` returns a clean session (`connected:false` is fine = ready).
- [ ] If a session is stale (objectID/GetName errors or ≥30s no response), recover it:
      see `automate-pyaedt-em`/memory — kill stale `ansys-aedt-mcp.exe`/`ansysedt.exe`,
      delete orphan `.aedt.lock` (only after confirming its PID is dead), relaunch.
- [ ] A base `.aedt` copy is opened via `open_project`.
- [ ] `demo_output/` is writable; output dir uses a **new** project name each demo
      (or clean up old ones) to avoid auto-rename surprise.
- [ ] (graphical demo) `non_graphical=false` launched and window visible.

## Expected artifacts (per `run_<template>/`)

- `<Project>.aedt` — the built geometry + variables + ports + Setup1 + sweep
- `<Design>_sweep.s<N>p` — exported frequency sweep (e.g. 11 points for coax)
- `model_preview.jpg` — 3D model screenshot (report illustration)
- `report.html` — **10 段精装**报告 (single-ended via `render_single_ended`, 诚实 S11/S21
  无伪差分; differential via `render_markdown`, 混模 Sdd21/Scd21)
- `plots/*.png` — S-parameter / convergence plots

## Safety discipline

- `run_python_code` is L4 admin-diagnostic (roadmap §8.4) — the business path is the
  fixed function library; this driver only *calls* it, never edits business logic.
- Original projects are read-only (§14.1); outputs never write back to input dir.
- Numbers come from deterministic `em_copilot` code (§13.3); the LLM only narrates.
- End with `disconnect_from_aedt` (close_desktop per user's choice) and confirm the
  PID/license are released and no orphan remains.

## If a call fails

Stop the sequence. Read the `error`/`traceback` in the JSON. The most common failures
and fixes are listed above in "Mandatory preconditions". Do **not** blind-retry the
same call; diagnose against that list first.
