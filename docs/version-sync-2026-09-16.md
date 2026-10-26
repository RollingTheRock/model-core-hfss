# 版本同步记录 — em-copilot 增强版（09-16 增量）

> **前序基线**：[version-sync-2026-09-14.md](version-sync-2026-09-14.md)（archite 落定：官方 MCP + 固定函数库）
> **本增量**：2026-09-16，模板工程「从零建模 → 导出 → 分析 → 报告」全链路闭环 + 单端/差分双分支 + **分步执行 demo_step（含演示中途改参）**
> **环境**：Python 3.12.10 · AEDT 2025.2 · Windows Server 2025 · 非图形模式（未变）

---

## 一、新增了什么（相对 09-14）

09-14 基线已具备：官方 MCP（launch/open/analyze/disconnect）+ 固定函数库 aedt_ops.py（setup/sweep 查询、导出 s4p、截图、收敛数据）+ 离线分析（touchstone/mixed_mode/reporting）。

**09-16 在 `src/em_copilot/templates.py` 上补上了「从零建工程」到「出报告」的闭环，并修复了端口数判定的关键 bug。**

### 1.1 新函数（`templates.py`，共 5 个，1095 行）

| 函数 | 行号 | 职责 | 验证 |
|------|------|------|------|
| `build_template_project` | 163 | 从零建模板工程 + schema 校验（09-14 已部分有，本增量补全参数体系） | ✅ |
| `_finalize_ports_and_setup` | 530 | 一次性补全：波端口 + Setup + 扫频（coax/microstrip/twinax 各分流） | ✅ |
| `run_demo_flow` | 625 | **一键编排**：建工程→建模→端口/求解/扫频→保存，返回证据链 stages | ✅ |
| `export_and_analyze` | 720 | **导出→收敛→截图→离线分析→精装报告** 一体化 | ✅ |
| `_build_single_ended_report` | 893 | **单端（2端口）诚实报告** S11/S21 | ✅ |

### 1.2 关键 Bug 修复：端口数判定（重要）

**问题**：`_n_ports_hint` 原来用 `len(app.ports)` 判定端口数。但 `app.ports` 返回的是**几何边界对象**，不是 RF 激励数。实测 twinax 是 **4 个波端口**，`app.ports` 却只返回 **2**。

**后果链**：
1. 导出文件名被错生成 `.s2p`（应为 `.s4p`）
2. `read_touchstone` 按**扩展名**推断端口数 → 把 4 端口数据当 2 端口解析
3. 频率点错乱（实测 737 个假频点，正确应为 201）

**修复**：改为权威来源 **`app.excitation_names`**（返回真实 RF 波端口数），fallback 到 `app.ports`。修复后 twinax 正确识别为 `n_ports=4, n_freqs=201`。

### 1.3 单端/差分双分支（新逻辑）

`export_and_analyze` 按端口数分流，避免"伪差分"（违反 §13.3 数据纪律）：

```
n_ports >= 4 且偶数  →  差分分支（twinax）：
                         mixed_mode.to_mixed_mode → compute_metrics
                         → build_report_data + render_markdown
                         产出 report.html/md + JSON + 截图 + S参数/TDR/收敛图
其他（2 端口）        →  单端分支（coax/microstrip）：
                         _build_single_end_report 直接报告 S11（回损）/ S21（插损）
                         产出 report.html + plots/s_parameters.png
```

### 1.4 其他修复
- **频率解析 bug**：`"1GHz"` 被倒序切分成 `"1GH"` 导致 `float()` 失败 → 改用正则 `([-+0-9.eE]+)\s*([kKMG]?Hz)?`
- **中文字体缺失**：图表中文显示方框 → 设置 `matplotlib` 使用 Microsoft YaHei
- **截图被占用崩溃**：`WinError 32`（文件被占用）→ try/except 保护，不阻断报告生成

---

## 二、全链路流程（闭环）

```
┌─ Stage A  从零建模板（build_template_project）
│     几何对象 + schema 校验 → {out_dir}/{template}_demo.aedt
├─ Stage B  端口 + 求解 + 扫频（_finalize_ports_and_setup）
│     波端口(每模板) → Setup1 → 离散/插值 scan
├─ Stage C  求解（MCP analyze_design + Monitor 进度）
│
├─ Stage D  导出 sweep → 收敛 → 截图（export_and_analyze 前半）
│     .sNp / 收敛表 / model_preview.jpg
└─ Stage E  离线分析 → 精装报告（export_and_analyze 后半）
       差分: mixed_mode + build_report_data + render_markdown
       单端: _build_single_end_report
```

---

## 三、验收结果（真实 AEDT 数据）

| # | 判据 | 预期 | 实测 |
|---|------|------|------|
| 1 | 单端（coax）端口数 | 2 | ✅ 2 |
| 2 | 单端（coax）频点数 | 11 | ✅ 11 |
| 3 | 单端 report_kind | single | ✅ single |
| 4 | 单端报告含 S11/S21 | 是 | ✅ 指定频点表 + 极值表 + S参数图 |
| 5 | 差分（twinax）端口数 | 4 | ✅ 4 |
| 6 | 差分（twinax）频点数 | 201 | ✅ 201 |
| 7 | 差分 report_kind | differential | ✅ differential |
| 8 | 差分报告含 Sdd21/Sdd11 | 是 | ✅ 混模 Sdd21×9, Sdd11×7 |
| 9 | 差分截图 | >0 bytes | ✅ 14,748 bytes |
| 10 | 全程现场生成仿真代码 | 0 次 | ✅ 全是固定 import 调用 |

**产物**：
- `demo_output/run_coax2/` → report.html + plots/s_parameters.png + .s2p
- `demo_output/run_twinax/report/` → report.html/md + metrics.json + model_preview.jpg + plots/

---

## 四、遗留问题

1. **单端截图偶发 0 字节**：coax（PortProbe）的 `model_preview.jpg` 在无交互桌面上偶发为空。不影响报告核心（数据/图/报告正确），twinax 截图正常。下次在有交互桌面验证。
2. **`_export_sweep` 依赖 `.sNp` 扩展名**：虽已用 `excitation_names` 修复，但健壮性仍依赖文件名正确。更稳做法是先用临时固定名导出再由文件头解析端口数（后续可做）。

---

## 五、关联

- 前序基线：[version-sync-2026-09-14.md](version-sync-2026-09-14.md)
- 演示流程：[demo-phase2-flow.md](demo-phase2-flow.md)
- 设计文档：[design-phase2-templates-2026-09-14.md](design-phase2-templates-2026-09-14.md)

---

## 六、分步执行 + 参数修改（同日增量，demo_step）

> 在 1.1 的 5 个函数之上，为「分步演示 + 演示中途改参」新增 2 个函数
> （仍只用官方 run_python_code 注入 desktop 调用，遵守固定函数库纪律）。

### 6.1 新函数

| 函数 | 行号 | 职责 | 验证 |
|------|------|------|------|
| `demo_step` | 877 | **分步分发器**：`step=build/modify/solve/export`，一句话=一步，复用既有函数 | ✅ dry-run 4 个 guard 全通过 |
| `_solve_setup` | 1050 | 对指定 setup 求解，单点失败不阻断（§8.5），返回 `{solved,notes}` | ✅ |

### 6.2 为什么加它

`run_demo_flow` 是"一键全流程"（建模→端口→求解→导出→报告），**无法拆开**。
领导演示时要么全自动（黑箱），要么停不下来。`demo_step` 把同一套既有函数拆成
4 个可单独触发的固定子命令，观众可逐步跟进，且支持"建模后、求解前改参再重算"
的参数化对比。

### 6.3 4 个子命令

| step | 内部调用 | 产物 |
|------|----------|------|
| `build` | `build_template_project` + `_finalize_ports_and_setup` | `.aedt`（几何+端口+Setup1+扫频） |
| `modify` | `apply_params_and_validate`（`reanalyze=True` 重算） | 变更清单 / 重算结果 |
| `solve` | `analyze_setup` | 收敛结果 |
| `export` | `export_and_analyze` | `.sNp` + `report.html` |

> **关键点**：bare `build_template_project(solve=False)` 只建几何+空气盒，
> 没有端口和 Setup。`demo_step(build)` 会额外补调用
> `_finalize_ports_and_setup`（建端口 + Setup1 + 扫频），否则后续
> `solve`/`export` 无 Setup 可解、无端口可导。

### 6.4 返回统一 JSON

```json
{success, step, template, stages:[{step,tool,code,artifact}...],
 validate:[...], project, design, out_dir, error?}
```

`stages` 即证据链——每步调了哪个工具/函数、产物落在哪，可直接投屏。

### 6.5 演示中途改参（参数化对比）

```
demo_step(step="build",   template="coax", params={...})          # 建模
demo_step(step="modify",  template="coax", params={"Ci_R_outer":"1.8mm"}, reanalyze=True)  # 改参+重算
demo_step(step="export",  template="coax", setup_name="Setup1", sweep_name="...")           # 导出报告
```

`modify` 走 `apply_params_and_validate`：白名单校验（`Ci_R_outer∈[0.1,100mm]`）+
增量校验（只查传入的）+ 逐项 `set_variable` + 记录变更清单。改不坏、看得见。

### 6.6 验证

- `py_compile` 通过。
- dry-run（不连 AEDT）：4 个 guard（未知 step / modify 缺 params / export 缺
  setup+sweep / 假 desktop 进入 build）全部正确返回 `success=False` + 中文错误。
  - 未知 step → `未知 step 'bogus'（可选: build/modify/solve/export）`
  - modify 缺 params → `modify 需要 params 字典`
  - export 缺 setup → `export 需要 setup_name + sweep_name`
