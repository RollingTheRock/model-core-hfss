# 演示运行手册：AI 编排 HFSS → S 参数 → 精装报告

> 本手册记录 em-copilot 的完整演示流程。AI 助手在对话中按此编排，
> 每一步调用的工具都对用户可见，最终产出：
> - 201 频率点 S 参数文件
> - 3D 模型截图
> - 网格收敛曲线
> - 精装版 HTML 报告（管理摘要 + 指标磁贴 + 图表）
>
> **增强版**（2026-09-14）：在 MVP 8 步基础上加入 4 项"炫酷"功能。
>
> **可视化版**（2026-09-16）：步骤 1 支持图形模式（`non_graphical=false`），
> 从建模到求解全程可在 AEDT 窗口内实时看到，无需改任何核心代码——
> 只是切换启动参数。详见下方"图形模式 vs 非图形模式"对照。

## 前置条件

- AEDT 2025.2 已安装（`C:\Program Files\ANSYS Inc\v252\AnsysEM\`）
- .venv 在 `D:\HCX\HFSS\em-copilot\.venv`（Python 3.12.10）
- em-copilot-core editable 已装（`aedt_ops.py` 可 import）
- pyaedt-em MCP server 已配置（`.mcp.json`），官方工具可用
- 演示工程副本在 `runs/twinaxial-demo-001/Twinaxial_cable_COPY.aedt`
- 当前无 AEDT 进程占用 license（演示前确认）
- 如有残留 `.aedt.lock` 文件且对应 PID 已退出，先删除锁文件

## 完整流程（11 步）

### 步骤 1：启动 AEDT（官方工具）
- 工具：`launch_aedt`
- 参数：
  - `non_graphical=true`（无头模式，默认）— 后台静默建模，适合自动批量 / CI
  - `non_graphical=false`（**图形模式，新增**）— 打开 AEDT 窗口，建模与求解全程可见，适合现场演示
- 产物：AEDT 进程 + license 占用
- AI 角色：启动仿真引擎

> **图形模式（`non_graphical=false`）前置要求**：需要 RDP 桌面会话且该会话有
> 桌面外壳进程（`explorer.exe`）在运行。若无图形桌面，窗口无法渲染。
> 判断方法：`query session` 查看 `RDP-Tcp#N` 会话 State 为 `Active`，
> 且会话内有 `explorer.exe`。满足时 `launch_aedt(non_graphical=false)`
> 返回的 `Non-graphical Mode: False` 即表示窗口成功打开。

### 步骤 1b：建模过程可视化（图形模式，新增 🔥）
- 前置：步骤 1 用 `non_graphical=false` 启动
- 效果：**以下动作在 AEDT 窗口内部实时渲染，观众直接看到**：
  - 每个几何体（圆柱、介质层、剥线等）逐条出现——对应 `templates.py` 的建模代码
  - 端口 / 激励（wave port）在模型上实时高亮
  - 求解时 AEDT 自带的 **"Solve" 进度面板**——自适应 pass 推进条、百分比、
    剩余时间，实时跳动（比后端 log 更直观）
  - 网格剖分 / 场量分布（若后处理）在窗口内刷新
- AI 角色：每执行一步后，口头告知"刚在窗口里建了第 N 个几何体 / 正求解第 M 次 pass"
- 与 no-code 的关系：**这里不需要再写代码**，只是换启动参数 + 同步解说。
  流程图：`launch_aedt(non_graphical=false)` → 建模板 → 端口 → 求解 → 导出

### 步骤 2：打开工程（官方工具）
- 工具：`open_project`
- 参数：`project_path="D:\HCX\HFSS\em-copilot\runs\twinaxial-demo-001\Twinaxial_cable_COPY.aedt"`
- 产物：工程加载；design=Twinaxial_cable_28AWG
- 安全：指向副本，绝不指原始只读工程（§14.1）

### 步骤 3：确认设计（官方工具，只读）
- 工具：`list_designs`
- 产物：确认 design 名 = Twinaxial_cable_28AWG
- AI 角色：检视，不改动

### 步骤 4：列出 setup/sweep（自研工具函数）
- 工具：`run_python_code`
- 代码（固定，AI 只传这一行）：
  ```python
  import importlib.util
  spec = importlib.util.spec_from_file_location("aedt_ops",
      r"D:\HCX\HFSS\em-copilot\src\em_copilot\aedt_ops.py")
  ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)
  result = ops.list_setups_and_sweeps(desktop, project_name="Twinaxial_cable_COPY",
      design_name="Twinaxial_cable_28AWG")
  ```
- 产物：JSON 列出 setup="3GHz"、sweep="DC_10GHz"
- 关键：确认要导出的 setup/sweep 名，避免传错
- 注意：用 `importlib` 直接加载文件，绕过官方 Python 的模块缓存

### 步骤 5：模型截图 ✨（自研工具函数，新增）
- 工具：`run_python_code`
- 代码：
  ```python
  result = ops.get_model_screenshot(desktop,
      r"D:\HCX\HFSS\em-copilot\runs\twinaxial-demo-001\model_preview.jpg",
      project_name="Twinaxial_cable_COPY", design_name="Twinaxial_cable_28AWG")
  ```
- 产物：3D 模型 JPEG 截图（非图形模式离屏渲染）
- 技术：`app.export_design_preview_to_jpg()`，需先 `save_project()`
- 失败处理：返回 `success=False` 但不中断主流程（§8.5）
- **图形模式下**：观众已透过 AEDT 窗口看到 3D 模型本体，此截图降级为
  "报告插图"用途（给报告用），不再是演示中的唯一视觉来源

### 步骤 6：导出 201 频率点 sweep s4p（自研工具函数，核心）
- 工具：`run_python_code`
- 代码：
  ```python
  result = ops.export_touchstone_sweep(desktop,
      setup_name="3GHz", sweep_name="DC_10GHz",
      output_file="D:/HCX/HFSS/em-copilot/runs/twinaxial-demo-001/Twinaxial_cable_28AWG_full.s4p",
      project_name="Twinaxial_cable_COPY", design_name="Twinaxial_cable_28AWG")
  ```
- 产物：201 频率点 s4p（非 LastAdaptive 单点）
- 后校验：函数内 `count_touchstone_freqs` 确认 ≥2 频率点
- 安全：输出在 runs/ 下，不写回工程目录

### 步骤 7：求解（如未求解）+ 进度流 ✨（官方工具 + Monitor，新增）
- 工具：`analyze_design`（如 `is_solved` 已为 True 可跳过）
- 进度流（②）：求解时用 `Monitor` 监控 pyaedt 日志：
  ```
  Monitor(command="tail -f <pyaedt_log> | grep -E --line-buffered
    'Pass|Adaptive|Convergence|Error|Done|elapsed'",
    description="HFSS adaptive pass progress")
  ```
- 产物：每行进度实时显示在对话中（Pass 1/2/3, ΔS 收敛值）
- AI 角色：告诉用户"正在求解第 N 次自适应迭代"

### 步骤 8：取收敛数据 ✨（自研工具函数，新增）
- 工具：`run_python_code`
- 代码：
  ```python
  result = ops.get_convergence_data(desktop, "3GHz",
      project_name="Twinaxial_cable_COPY", design_name="Twinaxial_cable_28AWG")
  ```
- 产物：JSON `[{pass, tetrahedra, delta_s}]`，如：
  - Pass 1: 914 四面体, ΔS=N/A
  - Pass 2: 1029 四面体, ΔS=1.03e-04
  - Pass 3: 1341 四面体, ΔS=8.81e-05
- 技术：三级 fallback——① .EMConvData 文件解析（主） ② pyaedt profile
  API ③ 原生 COM（gRPC 下常失败）
- 关键发现：gRPC 模式下 `GetConvergenceData`/`GetMeshStats` 等原生 COM
  调用均失败，必须用文件解析 fallback

### 步骤 9：SI 分析（em-copilot 离线，无需 AEDT license）
- Python（用 .venv）：
  ```python
  from em_copilot.touchstone import read_touchstone
  from em_copilot.schemas import PortMapping, DifferentialPair, SITask, FrequencyPoint
  from em_copilot.mixed_mode import to_mixed_mode, compute_metrics, worst_case
  from em_copilot.reporting import build_report_data, render_markdown

  ts = read_touchstone("...full.s4p")
  mapping = PortMapping(pairs=[DifferentialPair(positive=1,negative=2,name='TX'),
                               DifferentialPair(positive=3,negative=4,name='RX')])
  mm = to_mixed_mode(ts, mapping)
  metrics = compute_metrics(mm, pair_index=0, far_pair=1)
  task = SITask(task_id='twinaxial-full-demo', touchstone=s4p,
                port_mapping=mapping, compute_tdr=True,
                points_of_interest=[FrequencyPoint(frequency='1GHz'), ...])
  ```
- 数值：由 em_copilot 确定性代码生成，AI 不改数（§13.3）

### 步骤 10：精装报告渲染 ✨（em-copilot 离线，增强）
- 代码：
  ```python
  data = build_report_data(task, ts, mm, metrics, warnings=[],
      model_screenshot='model_preview.jpg',
      convergence_passes=[
          {'pass':'Adaptive Pass 1','tetrahedra':914,'delta_s':None},
          {'pass':'Adaptive Pass 2','tetrahedra':1029,'delta_s':0.000103},
          {'pass':'Adaptive Pass 3','tetrahedra':1341,'delta_s':0.0000881},
      ])
  render_markdown(data, 'runs/twinaxial-demo-001/report_full/')
  ```
- 产物（精装版 HTML 报告）：
  - `report.html` — 管理摘要磁贴 + 指标卡 + 模型截图 + 收敛图 + S 参数图 + TDR 图
  - `report.md` — Markdown 版
  - `model_preview.jpg` — 3D 模型截图
  - `plots/convergence.png` — 双轴收敛图（柱=四面体，线=ΔS）
  - `plots/s_parameters.png` — Sdd21/Sdd11/Scd21
  - `metrics.json` — 结构化数据
- 新增样式：暗色主题自适应、CSS 变量、响应式磁贴网格、图注

### 步骤 11：释放 license（官方工具）
- 工具：`disconnect_from_aedt`（close_desktop=false, close_projects=false）
- 产物：Desktop 句柄释放
- 后续：AEDT 进程退出后 license 归还

## 验证标准

| # | 判据 | 预期 |
|---|------|------|
| 1 | 步骤 6 s4p 频率点数 | 201 |
| 2 | 步骤 5 截图文件存在 | model_preview.jpg > 0 bytes |
| 3 | 步骤 8 收敛 passes 数 | 3 |
| 4 | 步骤 8 Pass 3 四面体数 | 1341 |
| 5 | 步骤 8 Pass 3 delta-S | ~8.8e-05 (< 0.02 收敛目标) |
| 6 | 步骤 10 report.html 含截图 | 有 `<img src="model_preview.jpg">` |
| 7 | 步骤 10 report.html 含收敛图 | 有 `<img src="...convergence.png">` |
| 8 | 步骤 10 metrics.json n_freqs | 201 |
| 9 | 全程现场生成仿真代码 | 0 次（都是固定 import 调用） |
| 10 | 产物路径 | 不在输入工程目录内 |
| 11 | 步骤 11 后 license | 已释放 |

## 图形模式 vs 非图形模式

| 维度 | 非图形 `non_graphical=true`（默认） | 图形 `non_graphical=false`（演示用） |
|------|--------------------------------------|--------------------------------------|
| AEDT 窗口 | 无（后台静默） | 有，实时渲染 |
| 几何建模 | 不可见 | **逐条可见**（观众看着模型长出来） |
| 端口/激励 | 不可见 | 实时高亮 |
| 求解进度 | 仅对话内 log / 进度条 | **AEDT 内置 Solve 面板**（pass 推进条、百分比、剩余时间） |
| 适合场景 | 自动批量、CI、无人值守 | 现场演示、给领导/客户看 |
| license | 一样占用 | 一样占用 |
| 前置条件 | 无 | 需 RDP 桌面会话 + `explorer.exe` |
| 额外风险 | 无 | 需稳定图形会话；断连会导致窗口丢失 |

> **推荐**：日常开发/CI 用非图形；**给领导演示**时切到图形模式，
> 并从 `launch_aedt` 的 `non_graphical=false` 开始。两者共用同一套
> 建模/求解/导出代码，切换成本为一个参数。

## 关键设计点（给领导看的"为什么这样"）

1. **为什么不自己连 AEDT**：独立进程 attach HFSS 的 gRPC 接口会卡死
   （已验证三次），AEDT 的接口只接受一个客户端。改用"官方进程注入
   desktop 句柄"绕开。
2. **为什么不靠 AI 写代码**：仿真代码写错会产出错误数据，工程师不易
   察觉。工具代码固定在 `aedt_ops.py` 里，有版本管理、过单测，AI 只调
   用不修改——零生成风险。
3. **为什么导出是关键**：官方 export_results 默认只导 1 个频率点
   （3GHz），工程师拿到的是不完整数据。自研函数正确导出整段 0–10GHz
   共 201 个点，并后校验频率点数。
4. **为什么收敛数据要 fallback**：gRPC 模式下 AEDT 的原生 COM 调用
   （`GetConvergenceData` 等）大部分失败。直接解析 `.EMConvData`
   文本文件是最可靠的路径，不依赖任何 COM/gRPC 调用。
5. **为什么截图重要**：给领导/客户的报告里有 3D 模型图，一眼就能看
   出分析的是什么结构，比纯数据表格更有说服力。
6. **图形模式为什么零成本**：可视化不是新代码，而是 `launch_aedt`
   一个启动参数（`non_graphical=false`）。templates.py 建几何、求解、
   导出的逻辑完全复用，窗口渲染是 AEDT 自带行为，AI 只负责同步解说。
   演示时"看到的过程 = 真实在跑的过程"，不是模拟的。
