# Phase 2 一键演示流程：空白画布 → 精装报告

> 写给：需要向领导 / 客户现场演示的人。
> 目标：说一句话，Claude Code（你）作为触发点，**逐步展示**每一步调用了哪个
> MCP 工具、用了固有代码库里的哪个函数、产物落在哪里，最后是一份可点开的
> 精装 HTML 报告。
>
> 数据纪律（§13.3）：数值由确定性代码生成，AI 只做解释，不改数。
>
> **可视化**（2026-09-16）：如果想让**建模到求解全程在 AEDT 窗口里实时可见**，
> 只需在最后加一个图形模式启动（`launch_aedt(non_graphical=false)`），
> 其余步骤与无头模式完全一样。详见第 2 节 A0 步与"可视化效果"。
>
> **分步执行**（2026-09-16）：不想"一键跑完"，想**一句话=一步**、并支持
> **演示中途改参再重算**？用新增的 `demo_step`（`build`/`modify`/`solve`/`export`
> 四个子命令，复用既有函数）。详见 [demo-report-visualization-2026-09-16.md](demo-report-visualization-2026-09-16.md) 第 6 节。

---

## 0. 一句话触发

你对 Claude Code 说：

> **"跑一遍同轴模板的一键演示。"**

（模板可选：`coax` 同轴 / `microstrip` 微带 / `twinax` 双绞差分。
也可指定参数，例如 "跑同轴，内导体 0.6mm，外护 2.8mm"。）

Claude Code 随即按下面 B→H 的链路依次调用，并把每一步的工具、函数、产物
**实时亮给观众看**。

---

## 1. 全链路总览（B → H）

```
说一句话
   ↓
A0 以图形模式启动 AEDT（可选，可视化）(官方 launch_aedt non_graphical=false)
   ↓
B  检查 AEDT 状态          (官方 check_aedt_status)
   ↓
G  从零建 .aedt 工程        (templates.py: run_demo_fb)
   ↓
G  建端口 + 求解设置 + 扫频   (templates.py: _finalize_ports_and_setup)
   ↓
C  求解 + 进度流            (官方 analyze_design + Monitor 进度 / AEDT 内置 Solve 面板)
   ↓
D  导出 .sNp / 收敛 / 截图   (aedt_ops.py: export_touchstone_sweep / get_convergence_data / get_model_screenshot)
   ↓
E  离线 SI 分析 + 精装报告   (reporting.py: build_report_data / render_markdown)
   ↓
H  展示报告 + 释放 license   (report.html 打开 / disconnect_from_aedt)
```

> 右上角若是**图形模式**：观众盯着 AEDT 窗口，能看到每个几何体逐条长出、
> 端口实时高亮、求解 pass 进度条跳动——这些是真实在跑的过程，不是动画。

**产物一根线到底：**

- `demo_output/run_<template>/` 工程目录
  - `<Project>.aedt` — 从零建的工程文件
  - `<Project>.aedtresults/` — 求解结果（收敛数据在 `*.results/*.EMConvData`）
  - `<Design>_sweep.s<N>p` — 导出的频率扫描 S 参数
  - `model_preview.jpg` — 3D 模型截图
  - `report/report.html` — ✨ 精装 HTML 报告
  - `report/report.md` — Markdown 版
  - `report/plots/*.png` — S 参数图 / TDR 图 / 收敛图
  - `report/metrics.json` — 结构化数值

---

## 2. 每一步：工具 / 代码 / 产物

### A0 — 以图形模式启动（可选，可视化 🔥）
| 项 | 值 |
|----|----|
| **MCP 工具** | `launch_aedt` |
| **参数** | `non_graphical=false` |
| **库函数** | 无（官方内置） |
| **前置** | 需 RDP 桌面会话 + 会话内有 `explorer.exe`（`query session` 确认） |
| **产物** | AEDT 窗口打开，返回 `Non-graphical Mode: False` |
| **观众看到** | **AEDT 窗口真正打开**——后续每个几何体、每个端口都在里面实时渲染 |

> **什么时候用**：给领导/客户现场演示时用。日常开发 / CI / 无人值守
> 用回 `non_graphical=true`（无头）。两种模式共用同一套建模/求解/导出代码，
> **切换成本只是这一个参数**，核心代码不需要改。
>
> **验证过**：在 RDP-Tcp#10 会话（ID 110，有 `explorer.exe`）下
> `launch_aedt(non_graphical=false)` 成功，窗口内 Cyl_Ant + Dielectric 两个
> 几何体渲染正常，截图 11709 bytes 非 0、且渲染出 389 级灰度（非空白）。

### B — 检查 AEDT 是否就绪（官方工具）
| 项 | 值 |
|----|----|
| **MCP 工具** | `check_aedt_status` |
| **库函数** | 无（官方内置） |
| **产物** | 返回 `port`（gRPC 端口）+ 已打开工程列表 |
| **观众看到** | "AEDT 2025.2 已连接，端口 55864，当前工程 Twinaxial_cable_COPY" |

> 前置：AEDT 已经在跑（`launch_aedt` 或已连接）。`port` 值是从这步取到的，
> 后续工具调用都要带上它。

### G — 从零建工程 + 端口（固定函数，`templates.py`）
| 项 | 值 |
|----|----|
| **MCP 工具** | `run_python_code` |
| **库函数** | `em_copilot.templates.run_demo_flow(desktop, template, params, out_dir)` |
| **代码引用** | [templates.py:...`run_demo_flow`] |
| **内部调用** | `build_template_project` → `_find_base_app` → `_BUILDERS[template]`；`_finalize_ports_and_setup` → `_finalize_ports_<template>` |
| **产物** | `<Project>.aedt`（几何 + 变量 + 端口 + Setup1 + 扫频） |
| **观众看到** | 逐条 `validate:` 自检：内导体 / 介质 / 外护 / 端口 / 求解设置 |
| **图形模式** | **AEDT 窗口里几何体逐条长出**（圆柱→介质→剥线→端口），看得见"模型在长出来" |

**关键点**：`create_new_project` / `insert_design` 在 **Hfss app 对象**上，
不在 `desktop` 句柄上；`get_pyaedt_app` **必须带 `design_name`**（否则内部走
`GetActiveDesign` 在 gRPC 下必失败）。base app 用 `_find_base_app` 自动枚举
已打开工程取一个当宿主。

### C — 求解 + 进度流（官方工具 + Monitor）
| 项 | 值 |
|----|----|
| **MCP 工具** | `analyze_design`（异步）+ `Monitor` |
| **库函数** | 无（官方内置） |
| **进度** | `Monitor(command="tail -f <pyaedt_log> \| grep -E 'Pass\|Adaptive\|Convergence\|Error\|Done'")` |
| **观众看到** | 实时滚动：`Pass 1/2/3 … 基准：ΔS 收敛值` |
| **图形模式** | **AEDT 内置 Solve 面板**——自适应 pass 推进条 / 百分比 / 剩余时间，窗口内实时跳动 |
| **产物** | `<Project>.aedtresults/`（`*.EMConvData` 记录四面体 + ΔS） |

### D — 导出 / 收敛 / 截图（固定函数，`aedt_ops.py`）
| 项 | 值 |
|----|----|
| **MCP 工具** | `run_python_code` |
| **库函数** | `aedt_ops.export_touchstone_sweep` / `aedt_ops.get_convergence_data` / `aedt_ops.get_model_screenshot` |
| **代码引用** | [aedt_ops.py:...] |
| **产物** | `<Design>_sweep.s<N>p`、`*.EMConvData`（收敛）、`model_preview.jpg` |
| **观众看到** | 导出文件 `n_freqs`（如 201 点）、`n_ports`、收敛 `passes` 列表 |
| **图形模式** | 3D 模型已在窗口里看过，`model_preview.jpg` 降级为"报告插图"用 |

> **关键点**：官方 `export_results` 默认只导 **1 个频率点**（LastAdaptive）。
> 自研 `export_touchstone_sweep` 正确传 `setup=` + `sweep=`，并后校验
> `count_touchstone_freqs ≥ 2`，防止拿到单点文件。收敛数据在 gRPC 下原生 COM
> 会失败，用 `.EMConvData` 文件解析（最可靠）。

### E — 离线分析 + 精装报告（`reporting.py`）
| 项 | 值 |
|----|----|
| **MCP 工具** | `run_python_code`（或本地 .venv 直接跑） |
| **库函数** | `reporting.build_report_data` / `reporting.render_markdown`；`touchstone.read_touchstone`、`mixed_mode.to_mixed_mode/compute_metrics` |
| **代码引用** | [reporting.py:...] |
| **产物** | `report/report.html`（管理摘要磁贴 + 指标卡 + 模型截图 + 收敛图 + S 参数图 + TDR 图）、`report.md`、`plots/*.png`、`metrics.json` |
| **观众看到** | 卡片化的指标 / 通过-失败 / 最差情形 / 收敛曲线 |

### H — 展示 + 释放
- 打开 `report.html`（给观众看精装报告）。
- 官方 `disconnect_from_aedt`（`close_desktop=false, close_projects=false`）归还 license。

---

## 3. 三种模板速查

| 模板 | 触发语（模板名） | 端口结构 | 求解类型 |
|------|------------------|----------|----------|
| 同轴 | `coax` | 2 × 波端口（内外导体环形面） | Terminal |
| 微带 | `microstrip` | 2 × 波端口（走线端面 + 地参考） | Modal |
| 双绞差分 | `twinax` | 2 × 差分波端口（两芯 + 外护 2 模） | Terminal |

默认参数示例（可在触发语里覆盖）：

- 同轴：`Ci_R_inner=0.5mm, Ci_R_outer=1.5mm, Ci_epsr=2.2, Ci_losst=0.001, Ci_len=50mm`
- 微带：`Mj_W=1.2mm, Mj_T=0.035mm, Mj_h=0.8mm, Mj_epsr=4.4, Mj_losst=0.02, Mj_len=30mm`
- 双绞：`Tx_R_sig=0.16mm, Tx_R_drn=0.1mm, Tx_spread=0.6mm, Tx_epsr=1.4, Tx_losst=0.0002, Tx_len=60mm`

---

## 4. 演示前检查清单

- [ ] `.venv` 就绪，`em_copilot` 可 import（`.venv/Scripts/python.exe -c "import em_copilot"`）
- [ ] AEDT 2025.2 已启动，`check_aedt_status` 返回 `port`
- [ ] 无残留 `.aedt.lock`（若 PID 已退出，删除锁文件）
- [ ] 输出目录 `demo_output/` 可写
- [ ] 演示工程副本存在（如 `runs/twinaxial-demo-001/Twinaxial_cable_COPY.aedt` 当 base）
- [ ] **（图形模式）** `query session` 有 `RDP-Tcp#N` 会话 State=`Active` 且会话内有 `explorer.exe`
- [ ] **（图形模式）** 显示屏 / 远程桌面已连接，能看见 AEDT 窗口

---

## 4.5 可视化：观众在窗口里看到什么（图形模式）

按时间顺序，观众盯着 AEDT 主窗口，能看到以下"真实在跑"的过程：

| 阶段 | 窗口里的可见效果 |
|------|------------------|
| **启动** | 空白工程 → 一个 3D 坐标系视图打开 |
| **建几何** | 内导体圆柱 → 介质筒 → 外护套（/ 剥线）**逐条出现**，带坐标轴、尺寸标注 |
| **赋材料** | 每个体块随材料赋色变化（金属/介质颜色切换） |
| **设端口** | 波端口在端面 **实时高亮**（紫色/蓝色激励面闪现边界） |
| **网格&求解** | AEDT **Solve 进度面板**：pass 推进条、当前四面体数、ΔS、百分比、剩余时间 |
| **收敛完成** | 面板显示收敛 / 停止；菜单里可点开 S 参数曲线 |
| **导出** | 命令行/对话里同步输出 `sNp 频率数、端口数、收敛 passes` |

> 观众看到的"每一次几何出现 / 每一条进度推进"，背后都是 `templates.py`
> 里那一行行真实的调试调用在跑——**不是预录动画**。AI 的角色是把窗口里
> 正在发生的事同步翻译成话术（"刚到第三步，正在建介质层"）。
>
> 若会场没有实时桌面（远程会议共享了终端），可退而用非图形模式 +
> 对话里的仿真进度条 / log 流：多一个低成本备选，画面感弱一些但流程相同。

---

## 5. 验证标准（演示完对照）

| # | 判据 | 预期 |
|---|------|------|
| 1 | `<Project>.aedt` 存在 | 有几何 + 变量 + 端口 |
| 2 | 求解收敛 | `*.EMConvData` 里 `IsConverged=true` |
| 3 | `*.sNp` 频率点数 | > 1（如 201） |
| 4 | `model_preview.jpg` | > 0 bytes |
| 5 | `report.html` 含截图 | `<img src="model_preview.jpg">` |
| 6 | `report.html` 含收敛图 | `<img src="...convergence.png">` |
| 7 | `metrics.json` `n_freqs` | 与 `.sNp` 一致 |
| 8 | 现场生成仿真代码 | 0 次（都是固定函数调用） |
| 9 | 产物路径 | 不在输入工程目录内（§14.1） |

---

## 6. 想横向对比？

一次跑三个模板，各生成一份报告，可同屏对比：
`coax`、`microstrip`、`twinax`。每份都在独立目录 `demo_output/run_<template>/`，
互不干扰。
