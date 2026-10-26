# 演示报告：AI 编排 HFSS 三维电磁仿真 — 全流程可视化版

> **日期**：2026-09-16
> **演示对象**：领导 / 客户现场
> **核心卖点**：说一句话 → AI 自动建模 → 仿真求解 → S 参数 → 精装报告，
> **全程在 AEDT 窗口内实时可见**。
>
> 本报告回答三个问题：
> 1. **改动是否要同步到原代码？** → 可视化部分**无需改任何核心代码**（只换启动参数）。
> 2. **现场怎么一步步演示？** → 见第 4 节的逐句话术 + 操作。
> 3. **观众到底能看到什么？** → 见第 5 节的可视化效果清单。
>
> **分步执行**（2026-09-16 更新）：新增 `demo_step` 子命令
> （`build` / `modify` / `solve` / `export`），一句话 = 一步，
> 并支持「演示中途改参 → 重新求解」的参数化对比演示。详见第 6 节。

---

## 1. 一句话卖点

> 你只说一句中文："跑一遍同轴模板的一键演示。"
>
> Claude Code（AI 编排者）随即像一个熟练的仿真工程师：
> 打开 AEDT → 建模 → 设端口 → 求解 → 导 S 参数 → 出一份**精装 HTML 报告**。
> 每一步调用哪个工具、产出在哪，全部实时亮在屏幕上；
> 而 **AEDT 窗口里，模型正在真的"长出来"**。

---

## 2. 改动同步结论（关键先讲）

**问题**：可视化改动能同步到原代码吗？

**结论**：可视化**不是代码改动**，而是 `launch_aedt` 的一个**启动参数**：

| | 无头模式（默认） | 图形模式（演示用） |
|---|---|---|
| 调用 | `launch_aedt(non_graphical=true)` | `launch_aedt(non_graphical=false)` |
| 建模/求解/导出代码 | 同一套 | 同一套（零改动） |
| 窗口 | 无 | 有，实时渲染 |

**无需同步任何 Python 代码**。演示时只改一个参数即可。
（之前的端口计数 bug 修复、单端/差分双分支报告，是更早的版本演进，
已在 `version-sync-2026-09-16.md` 记录，属既有功能，非本次可视化改动。）

**唯一前置条件**：要有桌面会话（RDP 且会话内有 `explorer.exe`）。
已验证：RDP-Tcp#10 会话存在，图形模式启动成功，三维几何正常渲染
（截图 11709 字节、389 级灰度 = 真实像素，非空白）。

---

## 3. 技术栈一句话

- **编排**：Claude Code（你）——AI 决定"下一步调谁"。
- **控制**：官方 `pyaedt-em` MCP server——建工程、打开、求解、注入 desktop 句柄。
- **业务**：固定函数库 `aedt_ops.py` / `templates.py`——建模模板、导出、收敛、截图。
  AI 只调用、不写仿真代码（安全纪律 §13.3 + 零生成风险）。
- **分析**：离线 `em-copilot` 包——touchstone 解析、混合模 S 参数、报告渲染。

---

## 4. 现场演示流程（逐句脚本）

> 前提：演示机已登录桌面会话，AEDT 未启动（避免之前占用 license 冲突）。

### 第 0 步：开场（30 秒）
**你**：向观众说明今天的看点——"AI 当仿真工程师，全程透明可回溯"。

### 第 1 步：以图形模式启动 AEDT
**你（对 Claude Code）说**：
> "以图形模式启动 AEDT，打开一个空白工程空间。"

**Claude Code 动作**：`launch_aedt(non_graphical=false)`。

**观众看到**：
- 命令区显示 `launch_aedt` 工具名 + 参数 `non_graphical=false`（透明）。
- **AEDT 主窗口弹出**，一个 3D 坐标系视图打开。

### 第 2 步：AI 建模（看点是"模型长出来"）
**你（对 Claude Code）说**：
> "跑一遍同轴模板的一键演示，内导体 0.5mm，外护 1.5mm。"

**Claude Code 动作**：
- 调 `run_python_code` → `templates.build_template_project(desktop, "coax", params)`。
- 内部逐步：建工程 → 画内导体圆柱 → 画介质层 → 画外护套 → 赋材料 → 加波端口 → 设求解频率。

**观众看到**（**这是全场最佳镜头**）：
- 窗口里 **内导体圆柱先出现** → 介质筒套上去 → 外护套包住 → 两端端口高亮。
- 每一步 AI 配一句解说："现在建的是 0.5mm 内导体"、"赋铜材质，变橙色了"…
- 窗口与话术**同步**，观众意识到"这不是动画，是在跑真仿真"。

### 第 3 步：求解 + 进度可视化
**你（对 Claude Code）说**：
> "开始求解。"

**Claude Code 动作**：`analyze_design` + `Monitor` 监控 pyaedt 日志。

**观众看到**：
- **AEDT 自带 "Solve" 进度面板**：pass 推进条、当前四面体数、ΔS 收敛值、百分比、
  剩余时间，实时跳动。
- 对话区并行滚动：`Pass 1/2/3 … ΔS=1.03e-04 → 8.8e-05`（透明 + 可读）。

### 第 4 步：导 S 参数 + 取收敛
**Claude Code 动作**：
- `aedt_ops.export_touchstone_sweep(...)` → 整段 0–10GHz 频率点导出。
- `aedt_ops.get_convergence_data(...)` → 四面体 + ΔS 序列。
- `aedt_ops.get_model_preview(...)` → 报告用 3D 截图。

**观众看到**：命令区显示"导出文件含 **201 个频率点**、**2 端口**、收敛 3 passes"。

### 第 5 步：离线分析 + 精装报告
**Claude Code 动作**：`em_copilot` 包解析 s2p → 单端口 S11/S21 → 生成精装 HTML。

**观众看到**：
- 弹出 `report.html`：管理摘要磁贴 + 指标卡 + 3D 模型图 + 收敛曲线 + S 参数曲线。

### 第 6 步：释放 license（收尾）
**Claude Code 动作**：`disconnect_from_aedt`（不关工程、不关桌面）。

**你说**："收工，license 已归还。"

---

## 5. 观众可视化效果清单（对照检查）

| # | 时间段 | 观众在窗口/屏幕看到 | 透明可回溯点 |
|---|--------|---------------------|--------------|
| 1 | 启动 | AEDT 窗口弹出，3D 坐标空间 | 工具名 `launch_aedt` + 参数 |
| 2 | 建模 | 圆柱 → 介质 → 护套逐条长出 | 每个几何体是哪行模板代码 |
| 3 | 赋材 | 材料颜色切换（金属/介质） | 材料表 |
| 4 | 设端口 | 端面端口高亮 | 波端口激励面 |
| 5 | 求解 | Solve 面板 pass 推进 / 四面体 / ΔS / 百分比 / 剩余时间 | 对话并行滚动日志 |
| 6 | 导出 | 命令区显示频率点数 / 端口数 | 产物文件路径 |
| 7 | 报告 | 精装 HTML（磁贴/图表/截图） | `report.html` 路径 |

> **真实性承诺**：以上每一帧都是 `templates.py` / `aedt_ops.py` 的真实调用在跑，
> 不是预录。观众看到的＝实际发生的。

---

## 6. 分步执行 + 参数修改（demo_step 🔥）

> 新增能力：**一句话 = 一步**。不再只能"一键跑完"，而是拆成 4 个固定子命令，
> 每步观众都能看到调了哪个工具、产物在哪。核心是新增的
> [templates.py:`demo_step`](src/em_copilot/templates.py) 分发器——它**复用**现有
> 的 `build_template_project` / `apply_params_and_validate` / `export_and_analyze`，
> 不改业务逻辑，只是按步分发 + 汇总证据链。

### 一键 vs 分步

| 模式 | 触发语 | 对应函数 | 特点 |
|------|--------|----------|------|
| 一键 | "跑一遍同轴模板演示" | `run_demo_flow` | 建模→端口→求解→导出→报告，全自动 |
| 分步 | "第一步：建同轴模型" | `demo_step(step="build")` | 只建模，停在窗口，供观众检查 |
| 分步 | "把外导体改成 1.8mm" | `demo_step(step="modify")` | 改参 + 校验，`reanalyze=True` 可重算 |
| 分步 | "开始求解" | `demo_step(step="solve")` | 只求解，窗口里看 pass 进度 |
| 分步 | "导出结果出报告" | `demo_step(step="export")` | 导 sNp + 出精装 HTML |

### demo_step 的 4 个子命令

| step | 内部调用 | 做什么 | 产物 |
|------|----------|--------|------|
| `build` | `build_template_project` + `_finalize_ports_and_setup` | 从零建几何 + 变量 + 端口 + Setup1 + 扫频 | `.aedt` |
| `modify` | `apply_params_and_validate` | 白名单/范围校验 → 逐项 set_variable；`reanalyze=True` 重新求解 | 变更清单 / 重算结果 |
| `solve` | `analyze_setup` | 对 Setup1 求解，实时 pass 进度 | 收敛结果 |
| `export` | `export_and_analyze` | 导 sweep → 收敛 → 截图 → 报告 | `.sNp` + `report.html` |

> 每个 step 返回统一 JSON：`{success, step, template, stages:[{step,tool,code,artifact}],
> validate:[...], project, design, out_dir, error?}`。`stages` 即证据链——
> 每步调了哪个工具/函数、产物落在哪，可直接投屏。

### 演示中途改参（参数化对比，全场亮点 🔥）

观众先看第一版模型 → 改一个参数 → 重新求解 → 对比前后。

```
说："跑分步演示，建同轴模型"      → 窗口里模型长出来（第一版）
说："把外导体从 1.5mm 改成 1.8mm" → demo_step(step="modify", params={"Ci_R_outer":"1.8mm"}, reanalyze=True)
     ↓
     ✓ 白名单校验（Ci_R_outer ∈ [0.1,100]mm）
     ✓ set_variable → 窗口里模型尺寸实时变化
     ✓ 重新求解 → 窗口里 pass 进度再走一遍
说："导出结果"                     → demo_step(step="export") → 前后 sNp + 报告对比
```

**观众看到**：模型尺寸**当场变**（外导体变粗/变细），求解重跑，报告里两个版本
的 S21 / 阻抗并列对比——"参数化仿真"的能力肉眼可见，不止口播。

### 为什么这一步有价值

- **打破"黑箱"**：领导最怕 AI 是黑箱——分步让她看到每一步、每个参数都透明可查。
- **参数白名单**：能改什么、范围多少内，写死在 schema，改不坏。
- **零新风险**：`demo_step` 只做分发，核心几何/求解/导出逻辑全复用既有函数。

---

## 7. 前置检查清单（演示当天）

| # | 检查项 | 命令 | 通过标准 |
|---|--------|------|----------|
| 1 | 有桌面会话 | `query session` | 有 `RDP-Tcp#N` State=`Active` |
| 2 | 桌面会话有 shell | `query session` 或任务管理器 | 会话内有 `explorer.exe` |
| 3 | AEDT 未占用 | `tasklist \| findstr ANSYS` | 演示前无 ANSYS 进程 |
| 4 | venv 就绪 | `.venv/Scripts/python -c "import em_copilot"` | 无报错 |
| 5 | MCP server 配置 | `.mcp.json` | `launch_aedt` 等工具可用 |
| 6 | 输出目录可写 | `docs/../` 下 | `demo_output/` 存在 |

---

## 8. 备选方案（无桌面时的降级）

若演示会场**没有**可共享的桌面（比如纯远程终端），用：

- **非图形模式** `launch_aedt(non_graphical=true)` —— 后台静默建模。
- 观众看**对话区**：工具调用列表 + 仿真进度条 / log 流（`Monitor`）+ 最终报告。
  （进度条在前一轮已实现，流程相同，只是画面感弱一些。）

---

## 9. 交付物清单

本次演示落地为 4 份产出：

| 文件 | 说明 |
|------|------|
| [mvp-runbook.md](mvp-runbook.md) | 更新：步骤 1 支持图形模式 + 步骤 1b 可视化 + 模式对照表 |
| [demo-phase2-flow.md](demo-phase2-flow.md) | 更新：A0 图形模式启动步 + 4.5 可视化效果清单 |
| [version-sync-2026-09-16.md](version-sync-2026-09-16.md) | 9-16 版本演进（端口修复 / 单端-差分双分支） |
| [templates.py](src/em_copilot/templates.py) | 新增 `demo_step` 分发器 + `_solve_setup`（分步 + 改参） |

> **可视化零代码成本**：建模过程可视化只是 `launch_aedt(non_graphical=false)`，
> 核心 `templates.py` / `aedt_ops.py` 不需要为此改动。
> **本次唯一代码改动**：为"分步执行 + 演示中途改参"新增 `demo_step` /
> `_solve_setup`（约 180 行），仅做分发，复用既有函数。
