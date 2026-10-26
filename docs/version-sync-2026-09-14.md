# 版本同步记录 — em-copilot 增强版

> **日期**：2026-09-14
> **基线**：em-copilot-core 0.1.dev0 + pyaedt 1.5.0 + ansys-aedt-mcp 0.1.4
> **环境**：Python 3.12.10 · AEDT 2025.2 · Windows Server 2025 · 非图形模式

---

## 一、当前架构（已落定）

```
┌─────────────────────────────────────────────────────────────┐
│  Claude Code（AI 编排）                                      │
│  ┌─ pyaedt-em MCP（官方）    ── launch/open/analyze/disconnect │
│  └─ run_python_code（官方）  ── 注入 desktop 句柄              │
│       │                                                      │
│       ▼ 一行 import + 函数调用（AI 不传业务逻辑 = 零生成风险） │
│  ┌─ aedt_ops.py（固定工具函数库）                             │
│  │   list_setups_and_sweeps     export_touchstone_sweep       │
│  │   get_model_screenshot       get_convergence_data          │
│  └────────────────────────────────────────────────────────────│
│  ┌─ em-copilot 离线分析（无需 AEDT license）                   │
│  │   touchstone → mixed_mode → compute_metrics → TDR          │
│  │   → build_report_data → render_markdown（精装 HTML）        │
│  └────────────────────────────────────────────────────────────│
│  产物：s4p + 截图 + 收敛图 + S参数图 + TDR图 + HTML报告        │
└─────────────────────────────────────────────────────────────┘
```

**核心决策**：放弃独立 MCP server 自己 attach AEDT（gRPC 单客户端限制，已证伪），
改用"固定函数库 + 官方 run_python_code 注入 desktop 句柄"方式。

---

## 二、已实现功能清单

### 2.1 AEDT 工具函数（`src/em_copilot/aedt_ops.py`，412 行）

| 函数 | 用途 | 验证状态 |
|------|------|----------|
| `list_setups_and_sweeps(desktop, ...)` | 列出工程内 setup/sweep 名 | ✅ 产出 setup=3GHz, sweep=DC_10GHz |
| `export_touchstone_sweep(desktop, setup, sweep, output_file, ...)` | 导出完整 sweep s4p（非 LastAdaptive 单点） | ✅ 201 频率点，4 端口 |
| `get_model_screenshot(desktop, output_file, ...)` | 3D 模型 JPEG 截图（非图形模式离屏渲染） | ✅ 14.7KB JPEG |
| `get_convergence_data(desktop, setup_name, ...)` | adaptive pass 收敛数据（四面体 + ΔS） | ✅ 3 passes: 914→1029→1341 |
| `guess_ports_from_path(path)` | 从 .sNp 文件名推断端口数 | ✅ 纯函数可单测 |
| `count_touchstone_freqs(path, n_ports)` | 数 s4p 频率点数（后校验） | ✅ 纯函数可单测 |

**收敛数据三级 fallback 策略**（gRPC 模式下原生 COM 全失败）：

| 级别 | 方法 | 可靠性 | 说明 |
|------|------|--------|------|
| 策略 1（主） | 解析 `.EMConvData` 文本文件 | ★★★ | 纯文本解析，不依赖 COM/gRPC |
| 策略 2 | pyaedt profile API | ★★ | ΔS 可靠，四面体需 pandas |
| 策略 3 | 原生 oDesign.GetConvergenceData | ★ | gRPC 下常失败 |

**内部函数**（`_parse_emconv_file`, `_conv_from_profile`, `_conv_from_native`）支持上述策略。

### 2.2 报告渲染（`src/em_copilot/reporting.py`，701 行）

| 组件 | 功能 | 验证状态 |
|------|------|----------|
| `ReportData` 数据类 | 新增 `model_screenshot` + `convergence_passes` 可选字段 | ✅ |
| `build_report_data(..., model_screenshot=, convergence_passes=)` | 关键字参数传截图/收敛数据 | ✅ |
| `_html_for_report` 精装版 | 管理摘要磁贴 + 指标卡 + 暗色主题 + 响应式 | ✅ |
| `_plot_convergence` | 双轴收敛图（柱=四面体，线=ΔS） | ✅ 46.7KB PNG |
| `_plot_s_params` | Sdd21/Sdd11/Scd21 频率域图 | ✅ 39KB PNG |
| `_plot_tdr` | TDR 阻抗时域图 | ✅ 34.3KB PNG |
| `render_markdown` | HTML + MD + JSON + plots 一体渲染 | ✅ |

**HTML 精装版特性**：
- CSS 变量主题系统（`:root` + `@media prefers-color-scheme:dark`）
- 管理摘要磁贴网格（4 列桌面 / 2 列手机，`@media max-width:640px`）
- 图路径自动归一化（`plot_links` 转为相对于 `out_dir`）
- 模型截图自动复制到报告目录

### 2.3 离线 SI 分析（无 AEDT license 依赖）

| 模块 | 文件 | 功能 |
|------|------|------|
| Touchstone 解析 | `touchstone.py` (554 行) | .sNp 读取、端口推断、Preflight 检查 |
| 混合模式 | `mixed_mode.py` (563 行) | 单端→差分变换、Sdd/Scc/Scd 四象限 |
| SI 指标 | `mixed_mode.py` `SIMetrics` | IL/RL/mode-conv/worst-case/POI 快照 |
| TDR | `analysis.py` (390 行) `compute_tdr` | 逆 FFT 阻抗估计 |
| 验收规则 | `analysis.py` `evaluate_acceptance` | 三态合规判定（PASS/FAIL/ERROR） |
| 批处理 | `batch.py` (418 行) | 多文件并行分析 |
| CLI | `cli.py` (444 行) | `em-copilot analyze/batch` 命令 |
| 合成数据 | `demo.py` | 无需 HFSS 的演示 .s4p 生成 |

---

### 2.4 模板工程 + 导出分析一体化（`src/em_copilot/templates.py`，2026-09-16 新增）

| 函数 | 用途 | 验证状态 |
|------|------|----------|
| `build_template_project(desktop, template, params, out_dir, ...)` | 从零建模板工程（coax/microstrip/twinax）+ schema 校验 | ✅ 三模板建模 |
| `run_demo_flow(desktop, template, params, out_dir, ...)` | 一键编排：建工程 → ports/setup → 求解 | ✅ |
| `export_and_analyze(desktop, template, project, design_name, setup, sweep, out_dir, ...)` | 导出 sweep → 收敛 → 截图 → 离线分析 → 精装报告 | ✅ 单端+差分均通过 |
| `_build_single_ended_report(...)` | 单端（2 端口）诚实报告 S11/S21，不套混模矩阵 | ✅ 避免"伪差分" |

**端口数探测**：`app.ports` 是几何边界对象（twinax 4 波端口但 ports 只有 2），**权威来源是 `app.excitation_names`**。

**分支分流**（`export_and_analyze` 内，`read_touchstone` 按文件扩展名推断端口数）：
- `n_ports >= 4 且偶数` → 差分模板（twinax）：混模矩阵 → `build_report_data + render_markdown`
- 其它（coax/microstrip，2 端口单端）→ `_build_single_ended_report`（直接报告 S11/S21）

---

## 三、完整演示流程（11 步）

```
步骤 1   launch_aedt(非图形)              → AEDT 进程 + license
步骤 2   open_project(副本.aedt)           → 工程加载（不碰原始只读工程）
步骤 3   list_designs                      → 确认 design=Twinaxial_cable_28AWG
步骤 4   run_python_code(list_setups_and_sweeps) → setup=3GHz, sweep=DC_10GHz
步骤 5   run_python_code(get_model_screenshot)  → 模型截图 JPEG ✨新增
步骤 6   run_python_code(export_touchstone_sweep) → 201 频率点 s4p
步骤 7   analyze_design + Monitor 进度流   → 实时显示 Pass/ΔS/收敛 ✨新增
步骤 8   run_python_code(get_convergence_data)  → 收敛数据 ✨新增
步骤 9   em-copilot 离线 SI 分析            → metrics + TDR
步骤 10  render_markdown(精装报告)          → HTML + 截图 + 4 图 ✨增强
步骤 11  disconnect_from_aedt              → license 释放
```

**run_python_code 调用约定**：
```python
import importlib.util
spec = importlib.util.spec_from_file_location("aedt_ops",
    r"D:\HCX\HFSS\em-copilot\src\em_copilot\aedt_ops.py")
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)
result = ops.<function_name>(desktop, ...)
```
> 用 `importlib` 直接加载文件，绕过官方 Python 的模块缓存。

---

## 四、验证结果

| # | 判据 | 预期 | 实测 |
|---|------|------|------|
| 1 | s4p 频率点数 | 201 | ✅ 201 |
| 2 | s4p 端口数 | 4 | ✅ 4 |
| 3 | 截图文件 | >0 bytes | ✅ 14,748 bytes |
| 4 | 收敛 passes 数 | 3 | ✅ 3 |
| 5 | Pass 3 四面体数 | ~1341 | ✅ 1341 |
| 6 | Pass 3 delta-S | <0.02 (收敛) | ✅ 8.81e-05 |
| 7 | HTML 含截图引用 | model_preview.jpg | ✅ |
| 8 | HTML 含收敛图引用 | convergence.png | ✅ |
| 9 | HTML 含 S参数图引用 | s_parameters.png | ✅ |
| 10 | HTML 含 TDR 图引用 | tdr.png | ✅ |
| 11 | metrics.json n_freqs | 201 | ✅ 201 |
| 12 | freq_range | [0, 10GHz] | ✅ [0.0, 1e10] |
| 13 | 全程现场生成仿真代码 | 0 次 | ✅ 全是固定 import |
| 14 | 产物路径不在工程目录内 | 是 | ✅ runs/ 下 |
| 15 | license 释放 | 已释放 | ✅ disconnect 成功 |

---

## 五、文件清单与行数

### 源码（`src/em_copilot/`）

| 文件 | 行数 | 职责 |
|------|------|------|
| `aedt_ops.py` | 412 | **AEDT 固定工具函数库**（本版新增截图+收敛） |
| `reporting.py` | 701 | **报告渲染**（本版精装 HTML + 收敛图） |
| `mixed_mode.py` | 563 | 混合模式变换 + SI 指标 |
| `touchstone.py` | 554 | Touchstone 解析 + Preflight |
| `cli.py` | 444 | CLI 入口 |
| `batch.py` | 418 | 批处理 |
| `analysis.py` | 390 | TDR + 验收规则评估 |
| `schemas.py` | 217 | Pydantic 数据模型 |
| `demo.py` | — | 合成 .s4p 生成 |
| `envcheck.py` | — | 环境检查 |
| `states.py` | — | 任务状态模型 |

### 配置

| 文件 | 用途 |
|------|------|
| `.mcp.json` | 两个 MCP server：pyaedt-em（官方）+ em-copilot-mcp（搁置） |
| `pyproject.toml` | em-copilot-core 0.1.dev0，依赖 numpy/scipy/matplotlib/pydantic |

### 文档（`docs/`）

| 文件 | 内容 |
|------|------|
| `mvp-runbook.md` | **11 步演示流程手册**（本版更新为增强版） |
| `version-sync-2026-09-14.md` | **本文件** |
| `version-manifest.md` | 环境版本清单 |
| `mcp-capability-gap.md` | MCP 能力差距分析 |
| `cloud-desktop-migration.md` | 云桌面迁移说明 |
| `smoke-test-record.md` | 离线冒烟测试记录 |
| `progress-report-2026-09-11.html` | 领导汇报版 HTML |

### 演示产物（`runs/twinaxial-demo-001/report_full/`）

| 文件 | 大小 | 内容 |
|------|------|------|
| `report.html` | 8.5KB | 精装版 HTML（磁贴+截图+4图） |
| `report.md` | 7.1KB | Markdown 版 |
| `model_preview.jpg` | 14.7KB | 3D 模型截图 |
| `plots/convergence.png` | 46.7KB | 收敛曲线（双轴） |
| `plots/s_parameters.png` | 39KB | S 参数图 |
| `plots/tdr.png` | 34.3KB | TDR 阻抗图 |
| `metrics.json` | 3.6KB | 结构化数据 |

### AEDT 工程

| 文件 | 说明 |
|------|------|
| `Twinaxial_cable.aedt` | 原始只读工程（不直接操作） |
| `Twinaxial_cable_COPY.aedt` | **工作副本**（所有操作指向这个） |
| `Twinaxial_cable_28AWG_full.s4p` | 最新导出（201 频率点） |

---

## 六、关键技术发现（踩坑记录）

### 6.1 gRPC 单客户端限制（致命）

```
独立进程 Desktop(new_desktop=False, port=...) → CreateAedtApplication 卡死
```

- AEDT gRPC DesktopPlugin 只接受一个客户端
- `_desktop_sessions` 是进程内 dict，无 IPC 共享
- WNUA / INSECURE 两种 transport 都一样卡
- **解法**：放弃独立 attach，改用官方 `run_python_code` 注入 `desktop` 句柄

### 6.2 gRPC 下原生 COM 调用大面积失败

| 调用 | 结果 |
|------|------|
| `oDesign.GetConvergenceData(setup)` | ❌ Failed to execute gRPC |
| `oDesign.GetMeshStats()` | ❌ Failed to execute gRPC |
| `oDesign.GetSolutionInfo()` | ❌ Failed to execute gRPC |
| `setup.get_profile()` | ✅ 正常 |
| `profile.adaptive_pass.steps` | ✅ 正常 |
| `step.delta_s_max` | ✅ 正常 |
| `step.table()` | ❌ 需要 pandas（未装） |

**解法**：收敛数据用 `.EMConvData` 文本文件解析（策略 1），完全不依赖 COM/gRPC。

### 6.3 路径 API 陷阱

```
app.project_path   → "D:\...\twinaxial-demo-001"    （工程目录，不是文件路径）
app.project_name   → "Twinaxial_cable_COPY"         （工程名，不含.aedt）
app.project_file   → "D:\...\Twinaxial_cable_COPY.aedt"  （完整文件路径）
app.design_name    → "Twinaxial_cable_28AWG"        （design 名）
```

results 目录 = `project_path / f"{project_name}.aedtresults" / f"{design_name}.results"`

### 6.4 export_results 的 LastAdaptive 坑

官方 `export_results` 默认导出 LastAdaptive（单频率点，如 3GHz），不是整段 sweep。
正确方式：`app.export_touchstone(setup=setup_name, sweep=sweep_name, output_file=...)`

### 6.5 importlib 绕过模块缓存

官方 `run_python_code` 的 Python 可能缓存旧版 `em_copilot` 模块。
用 `importlib.util.spec_from_file_location` 直接加载文件路径绕过缓存。

---

## 七、搁置项

| 项目 | 状态 | 原因 |
|------|------|------|
| 独立 em-copilot-mcp server | 搁置 | 连不上 AEDT gRPC（单客户端限制） |
| 参数化扫描（功能③） | 用户自行做 | 用户表示"这个部分我更感兴趣，后面单独搞" |
| `em_copilot/mcp/` 骨架代码 | 保留不启用 | 架构已转向函数库方向 |

---

## 八、同步新环境时的检查清单

```bash
# 1. 确认 Python 环境
D:\HCX\HFSS\em-copilot\.venv\Scripts\python.exe --version
# → Python 3.12.10

# 2. 确认 em-copilot 可导入
.venv\Scripts\python.exe -c "from em_copilot.aedt_ops import get_model_screenshot, get_convergence_data; print('OK')"

# 3. 确认 AEDT 安装
# （通过 MCP check_aedt_installed 或查 ANSYS Inc/v252）

# 4. 清理残留锁文件（如有）
# 检查 runs/twinaxial-demo-001/*.aedt.lock，若 PID 不存在则删除

# 5. 按步骤执行演示
# 参考 docs/mvp-runbook.md 的 11 步流程

# 6. 验证产物
# 报告目录下应有 7 个文件（report.html/md, model_preview.jpg, plots/×3, metrics.json）
```

---

## 九、下一步方向

1. **参数化扫描**（功能③，用户自行实现）—— `app.variable_manager.set_variable("SigRadius", expression="8mm")` + parametric sweep
2. **验收规则接入**——定义 Sdd21/RL/mode-conv 的限值，让报告从 NOT_EVALUATED 变为 PASS/FAIL
3. **多设计/多工程批量**——批处理多个 .s4p 文件
4. **报告模板定制**——不同受众（领导 vs 工程师）不同模板
5. **License 管理优化**——求解完后自动释放，避免长时间占用
