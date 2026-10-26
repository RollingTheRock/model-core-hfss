# 已有完整 .aedt 工程 → 一句话 仿真/导出/报告

> 写给：读了 `automate-pyaedt-em` skill 的 Claude。当用户给一个**已完成建模的完整 `.aedt`
> 工程**（任意来源，不一定是本项目三模板建的），说「帮我把这个工程跑一遍：仿真→导出→
> 生成报告」这类一句话时，按本说明执行。
>
> **覆盖：任意第三方工程。** 关键难点是 setup/sweep 名、端口数、单端/差分报告都要**动态发现**，
> 绝不硬编码。所有数值由确定性 `em_copilot` 代码生成（§13.3），Claude 只叙述。

---

## 0. 你要交付什么

一个**结构化的 JSON 证据链**和一份**精装 HTML 报告**。整条链路：

```
打开工程(副本) → 发现 setup/sweep → (未解则)求解 → 导出多点sweep
→ 取收敛 → 截图 → 端口数判定单端/差分 → em_copilot 分析 → 10段精装报告
```

每步产物落在 `runs/<case>/` 独立目录，**绝不写回原始工程**（§14.1）。

---

## 1. 调用顺序（安全纪律，一条不能少）

### 1.1 启动/连接 AEDT
1. `check_aedt_installed` —— 确认 AEDT 在盘上（离线，不占 license）。
2. `check_aedt_status` —— 看 MCP 是否有活连接 / 可 attach 的本地会话。
3. 多个会话 → 问用户 attach 还是开新实例；单个会话 → 也问一句。**只有用户明确要新实例才 `launch_aedt`**。
4. `connect_to_aedt`（attach）或 `launch_aedt`（新实例）。**调用前告知**「可能启动 AEDT 并占用 license」。

### 1.2 打开工程（副本）
5. `list_projects` / `list_designs` / `get_model_info` 检查已加载状态。
6. `open_project` 加载一个**副本**——绝不用只读原始工程（§14.1）。若用户给的是只读输入路径，
   先**复制一份**到 `runs/<case>/`（copy-before-run），打开副本。
   > 用官方 `open_project`（`mcp__pyaedt-em__open_project`），传副本路径。
7. `export_config` 持久化 setup/sweep 配置，用于复现。

### 1.3 发现 setup / sweep（动态）
- 用 `run_python_code` 调 `aedt_ops.list_setups_and_sweeps`（固定函数，注入 `desktop`）：
  ```python
  import importlib.util
  spec = importlib.util.spec_from_file_location("aedt_ops",
      r"D:\HCX\HFSS\em-copilot\src\em_copilot\aedt_ops.py")
  ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)
  print(ops.list_setups_and_sweeps(desktop))
  ```
- 返回 JSON 列出 `setup` 名（如 `"3GHz"`）和它的 `sweep`（如 `"DC_10GHz"`）。
- **若不只一个 setup/sweep**，问用户要哪个，或用配置里记录的默认。
- 兜底：若列表为空，用 `app.setups` / `setup.get_sweep_names()` 再查。

### 1.4 求解（若未解）
- 判定是否已解：`get_model_info` 或查 `.aedtresults` 目录是否存在。
- **用户想跑全流程**（本次），所以不要跳过：用官方 `analyze_design` 求解。
  - 参数：`design_name`（发现到）+ `setup_name`（发现到）+ `machine`。
  - `analyze_design` 默认异步返回，**不是** blocking 全等。要用进度监控：`Monitor`
    流式看 pyaedt 日志（`tail -f <pyaedt_log> | grep -E --line-buffered 'Pass|Adaptive|Convergence|Error|Done|elapsed'`），
    或轮询 `get_analysis_status`（Job 模型）。
  - 求解较久，用 Job 模型：`submit_analysis` → `get_analysis_status` → `get_analysis_log`。
  - 求解前告知用户「这一步会开解，可能耗时」。

### 1.5 导出多点 sweep（核心，别踩单点坑）
- **官方 `export_results` 默认只导 `LastAdaptive` 单频率点**——这是最致命的坑。
- 用 **em-copilot 的 `export_touchstone_sweep`**（在 `run_python_code` 里调 `aedt_ops.export_touchstone_sweep`）：
  ```python
  result = ops.export_touchstone_sweep(desktop,
      setup_name="3GHz", sweep_name="DC_10GHz",
      output_file="D:/HCX/HFSS/em-copilot/runs/<case>/<design>_full.s4p",
      project_name="<project>", design_name="<design>")
  ```
- 函数内部用 `count_touchstone_freqs` 校验 ≥2 频率点，防止拿到单点文件。
- 输出目录必须**预先存在**（`export_touchstone` 对不存在子目录会静默写空 → "0 频率点"）。

### 1.6 截图 + 收敛
- 截图：`get_model_screenshot`（固定函数）→ 报告插图。
- 收敛：`get_convergence_data`（三级 fallback，gRPC 下原生 COM 失败，用文件解析）。

### 1.7 判定单端 / 差分 + 分析 + 报告
- **端口数**决定走哪条报告（`_report_kind` 逻辑）：
  - `n_ports >= 4 且偶数` → **差分报告**（twinax 类）：混模 Sdd21/Scd21。
  - 否则（2 端口）→ **单端报告**（coax/microstrip 类）：诚实 S11/S21。
- 就在 `run_python_code` 里调 `templates.export_and_analyze`（固定函数，串起 导出→收敛→截图→
  分析→报告），它内部已按 `_n_ports_hint` 判定并产出 `report.html`：
  ```python
  import importlib.util
  spec = importlib.util.spec_from_file_location("templates",
      r"D:\HCX\HFSS\em-copilot\src\em_copilot\templates.py")
  tpl = importlib.util.module_from_spec(spec); spec.loader.exec_module(tpl)
  result = tpl.export_and_analyze(
      desktop, template="twinax",           # 仅供报告模板名回显；端口数决定报告类型
      project="<project>", design_name="<design>",
      setup_name="3GHz", sweep_name="DC_10GHz",
      out_dir="D:/HCX/HFSS/em-copilot/runs/<case>",
      task_id="<case>", compute_tdr=True,
      points_of_interest=["1GHz","3GHz"],
  )
  ```
- 返回 JSON 含 `report_html` / `report_md` / `report_kind` / `snp_file` / `n_freqs`。

---

## 2. 一句话触发的完整编排（用户只说一句，你串全流程）

当用户说「把这个工程跑一遍仿真导出报告」「帮我把 xxx.aedt 跑完出报告」时，用以下**固定顺序**（每步对应上面某节）：

| # | 动作 | 工具 |
|---|------|------|
| 1 | 检查 AEDT 安装/会话 | `check_aedt_installed` → `check_aedt_status` |
| 2 | 连接/启动 | `connect_to_aedt` 或 `launch_aedt`（先确认） |
| 3 | 打开副本 | `open_project`（副本路径） |
| 4 | 发现 setup/sweep | `run_python_code` → `aedt_ops.list_setups_and_sweeps` |
| 5 | 求解 | `analyze_design` + `Monitor`/Job 轮询进度 |
| 6 | 导出多点 sweep | `run_python_code` → `aedt_ops.export_touchstone_sweep` |
| 7 | 截图+收敛 | `get_model_screenshot` / `get_convergence_data` |
| 8 | 分析+报告 | `run_python_code` → `templates.export_and_analyze` |
| 9 | 打开报告 | 自动打开 `report.html` |
| 10 | 释放 | `disconnect_from_aedt(close_desktop=...)` |

> **别漏**：每一步返回的 `stages` 即证据链，直接展示给用户。
> **别硬编码**：所有 setup/sweep/端口数/单端差分 都动态发现。

---

## 3. 你必须先问/确认的（这些不能瞎猜）

- 用户给的工程路径是**原始只读**还是**副本**？只读必须 copy-before-run。
- 哪个 setup / sweep？（若不唯一）
- 是否已求解？（本次用户要全流程 → 默认求解；若 `.aedtresults` 已存在且用户说"别再算"则跳过）
- 关注频点（points_of_interest），默认 `["1GHz","3GHz"]`。
- 报告类型（单端 vs 差分）由端口数自动定，**不要问用户**，但要告知。

---

## 4. 已知坑（这些流程踩过，必须遵守）

| 坑 | 规避 |
|----|------|
| 官方 `export_results` 默认只导 1 个频率点 | 用 `aedt_ops.export_touchstone_sweep`，函数内校验 ≥2 点 |
| gRPC 下原生 COM（`GetConvergenceData`）失败 | 用 `get_convergence_data` 的文件解析 fallback |
| `open_project` 对空桌面/只读路径失败 | 先复制副本，再用副本路径；副本目录先建好 |
| 导出到不存在子目录 → 静默写空 → "0 频率点" | `runs/<case>/` 目录先 mkdir |
| 工程名/设计名自动改名 | 一律用**回读**的实际名，step 间传递 |
| setup.sweeps 重算后枚举为空 | `list_setups_and_sweeps`/`get_sweep_names` 兜底解析 `.aedt` |
| `run_python_code` 路径正斜杠偶发丢文件 | 用 **Windows 反斜杠**绝对路径 |
| 会话僵死（≥30s 无响应/objectID 错） | kill 僵死进程 + 删孤立 `.aedt.lock`，别反复 launch |

---

## 5. 安全纪律

- `run_python_code` 是 L4 管理诊断通道——业务路径是固定函数库，你只调用、不改逻辑。
- 原始工程只读（§14.1）；输出从不写回输入目录；记录文件哈希。
- 数值全部由确定性 `em_copilot` 代码生成（§13.3）；你只叙述，不编造数字。
- 单端（2 端口）**绝不**套混模矩阵算 Sdd21（§13.3 禁伪差分）——只报诚实 S11/S21。
- 结束务必 `disconnect_from_aedt` 并确认 license/PID 已释放、无孤立进程。
