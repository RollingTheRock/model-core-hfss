# 云桌面 HFSS 全流程跑通 — 迁移与部署说明

> 目标：在带 HFSS 许可证 + Claude Code 的云桌面上，把 PyAEDT EM Copilot 工具链跑通到"前处理 → 仿真 → 后处理 → 报告"全链路。
> 生成日期 2026-09-08。配套文档：路线图 V1.1（§3.3 / §4.1 / §14 / §16.4）、交接文档（EM-COPILOT-HANDOVER.md）、`docs/smoke-test-record.md`、`docs/mcp-capability-gap.md`、`docs/version-manifest.md`。

---

## 0. 诚实定位：今天"全流程"到底能跑到什么程度

领导要求"全流程跑通"。在动手前必须把**现状**说清楚，否则会在云桌面上花时间找一个不存在的模块：

| 阶段 | 状态 | 由谁实现 | 是否需要 HFSS 许可 |
|---|---|---|---|
| 前处理（工程检查 / 打开工程 / 模型 / 端口 / 材料 / 网格 / Setup） | **官方 MCP 工具可用**，但 em_copilot 无自动封装 | 官方 `ansys-aedt-mcp`（create_design / open_project / get_model_info / get_guidelines_for / run_python_code） | 是 |
| 仿真（求解） | **官方 `analyze_design` 异步可用**；但 Job 模型（submit/status/log/cancel/collect）**尚未编码** | 官方 `analyze_design(blocking=False)` + 手动轮询 | 是 |
| 后处理（Touchstone → 混合模式 → SI 指标 → TDR → 合规判定） | **已实现且已验证** | `em_copilot`（touchstone/mixed_mode/analysis/reporting） | 否 |
| 报告（Markdown + HTML + metrics.json + PNG） | **已实现且已验证** | `em_copilot.reporting` | 否 |

**结论**：今天能跑通的"全流程"= **Skill（automate-pyaedt-em）驱动官方 ansys-aedt-mcp 的 20 个工具完成前处理+仿真，再用 em_copilot 完成后处理+报告**。路线图 §4.1 的企业 Job 模型（`em_copilot.jobs`：submit_analysis / get_analysis_status / get_analysis_log / cancel_analysis / collect_analysis_results）**只在文档与 Pydantic schema（`states.py`）里起草了契约，磁盘上没有实现**——长耗时求解靠手动轮询 `analyze_design` 异步返回 + `get_pyaedt_logs`，没有结构化 Job API。这是路线图阶段1 的工作，可在本次跑通之后开展。

> 一句话：**本次迁移 = 把官方 ansys-aedt-mcp 在云桌面装好、接进 Claude Code、用 Skill 跑通一个真实 HFSS 工程 + em_copilot 出报告。** 不是去部署一个还不存在的 Job 调度平台。

---

## 1. 迁移前准备：带什么、不带什么

源目录：`d:\workspace\HFSS\em-copilot\`（当前工作副本，edits 落在此处）。
配套文档：`d:\workspace\pyaedt-em-copilot-technical-roadmap.md`、`d:\workspace\EM-COPILOT-HANDOVER.md`。

### 1.1 必须带到云桌面

```
em-copilot/
├── src/em_copilot/          # 领域包（11 模块，后处理+报告+envcheck+states schema）
├── skills/automate-pyaedt-em/  # Skill（SKILL.md + references + scripts/check_environment.py）
├── pyproject.toml           # em-copilot-core 构建定义
├── requirements-cloud.txt   # ★ 云桌面 ansys/mcp 全家桶锁版清单（本机已生成）
├── README.md
├── docs/                    # version-manifest / smoke-test-record / mcp-capability-gap / 本文件（参考）
├── scripts/                 # ★ 含 check_environment / verify_error_report / setup-mcp-json / bootstrap-cloud
└── tests/
```

外加两份根目录文档：
- `pyaedt-em-copilot-technical-roadmap.md`（路线图 V1.1，权威规范）
- `EM-COPILOT-HANDOVER.md`（交接文档）

> 打包方式建议：`git archive` 或整目录 zip。若用 U盘/网盘传输，注意 `.git` 可不带（云桌面重新 clone 或 init 均可）。

### 1.2 绝对不要带

| 项 | 原因 |
|---|---|
| `.venv/` | 绝对路径会断，云桌面必须重建（交接文档 §5 明确） |
| `.mcp.json`（当前那份） | 现版本只有 pyfluent 条目，且路径是本机的；云桌面重新建（见 §3） |
| `runs/` 下的 demo 产物 | 可在云桌面重新 `em-copilot demo` 生成，不必带 |
| `__pycache__/`、`*.egg-info` | 构建残留 |
| 任何含凭据/许可证的文件 | 安全约束：凭据与许可证配置不写入仓库（路线图 §14 / §16.3） |

### 1.3 云桌面前置条件（请先确认）

- [ ] **HFSS 许可证可用**（启动 AEDT 能拿到 license，或已有 gRPC 会话可 attach）
- [ ] **AEDT 已安装**（环境变量 `ANSYSEM_ROOTxxx` / `AWP_ROOTxxx` 存在；envcheck 的 `aedt.install` 项会据此转 PASS）
- [ ] **Claude Code 已安装**并能识别 `.mcp.json`（VS Code 扩展 / CLI 均可）
- [ ] **Python 3.12+**（`requires-python >=3.12,<4`；本机验证版本 3.12.10）
- [ ] **pip 走代理可达 PyPI**（企业代理 `http://proxy.xfusion.com:8080`；若云桌面网络策略不同，替换为实际代理）

---

## 2. 在云桌面重建 venv（含 ansys 全家桶锁定安装）

> 命令以 **PowerShell** 为准（云桌面若是 Linux，把 `.\.venv\Scripts\` 换成 `.venv/bin/`、`Activate.ps1` 换成 `activate`）。

### 2.0 快捷路径（推荐）：一键 bootstrap 脚本

本仓库已提供 `scripts/bootstrap-cloud.ps1`，把 §2.1–2.5 + §3 的所有步骤一次跑完（建 venv → 设代理 → 装 em-copilot-core → 装锁版 ansys 全家桶 → 生成 .mcp.json → 跑 envcheck）。在云桌面项目根：

```powershell
# 若执行策略拦
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

# 若云桌面代理不是默认的 proxy.xfusion.com，先覆盖
$env:PIP_PROXY = "http://your-cloud-proxy:8080"

.\scripts\bootstrap-cloud.ps1
```

脚本退出码：`0`=envcheck 健康，`1`=某步失败，`2`=Python 版本不对。成功后直接跳到 §4.2 对比结果，然后 §5 连通 MCP。

> 若你想逐步手验（或脚本在某步失败需定位），按 §2.1–2.5 手工执行。

### 2.1 建虚环境

```powershell
cd <云桌面项目根>   # 例如 C:\workspace\em-copilot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

> 若 `Activate.ps1` 被执行策略拦：`Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`。

### 2.2 设代理 + 信任主机（pip 需要）

```powershell
$env:HTTP_PROXY  = "http://proxy.xfusion.com:8080"
$env:HTTPS_PROXY = "http://proxy.xfusion.com:8080"
# 若云桌面代理不同，替换为实际值
```

### 2.3 安装 em-copilot-core（本包，可编辑模式）

```powershell
pip install -e . --trusted-host pypi.org `
    --trusted-host files.pythonhosted.org --trusted-host pypi.python.org
```

这一步装齐 `numpy / scipy / matplotlib / pydantic`（纯 Python 轮子，无需许可）。

### 2.4 锁定安装 ansys 全家桶（关键：版本对齐 version-manifest）

**必须锁版本**，否则 pip 可能拉到不兼容新版。版本清单已落库为 [requirements-cloud.txt](../requirements-cloud.txt)（本机 2026-09-09 生成，含 typer 0.27.2）。直接用：

```powershell
pip install -r requirements-cloud.txt `
    --trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org
```

`requirements-cloud.txt` 内容（锁版）：

```text
ansys-aedt-mcp==0.1.4
ansys-common-mcp==0.3.3
pyaedt==1.5.0
pyedb==0.83.0
ansys-edb-core==0.3.3
fastmcp==4.0.2
mcp==2.1.1
typer==0.27.2
```

说明：
- `ansys-aedt-mcp==0.1.4`：官方 MCP，20 工具，作为 Agent 工具层原样使用。
- `pyaedt==1.5.0` / `pyedb==0.83.0` / `ansys-edb-core==0.3.3`：PyAEDT 运行时，被 MCP 调用。
- `fastmcp==4.0.2` / `mcp==2.1.1`：MCP server 框架（ansys-aedt-mcp 依赖，锁版防漂移）。
- `typer==0.27.2`：pyaedt CLI 会话发现（`discover_available_aedt_sessions`）需要；本机 envcheck 报 warn 就是缺它，云桌面补上。
- `ansys-common-mcp` 通常是 ansys-aedt-mcp 的依赖，这里显式锁版以确保一致。

### 2.5 验证安装结果（离线，不消耗许可）

```powershell
# em-copilot 自身（无需 AEDT）
em-copilot demo --out runs\demo          # 应退出 0，生成 report.md + metrics.json + PNG

# envcheck（Phase 0，见 §4）
em-copilot-envcheck                       # 人类可读
em-copilot-envcheck --json > docs\_envcheck_cloud.json
```

> 此时 `aedt.install` 项应从本机的 `blocked` **转 PASS**（云桌面装了 AEDT）。这是云桌面相对本机的唯一关键差异，也是全流程能跑通的前提。

---

## 3. 配置 .mcp.json（官方 ansys-aedt-mcp 接入，代理清空）

**不要手写 JSON**——本仓库提供 `scripts/setup-mcp-json.py`，自动定位当前 venv 的 `ansys-aedt-mcp.exe`、写入 `--include-context`、并把 env 代理清空。在项目根（venv 已激活）：

```powershell
python scripts\setup-mcp-json.py            # 写入 .\.mcp.json
python scripts\setup-mcp-json.py --check     # 干跑：只打印不写
python scripts\setup-mcp-json.py --show     # 查看现有 .mcp.json
```

生成的文件形如（`command` 路径由脚本按实际 venv 位置填入）：

```json
{
  "mcpServers": {
    "pyaedt-em": {
      "command": "C:\\workspace\\em-copilot\\.venv\\Scripts\\ansys-aedt-mcp.exe",
      "args": ["--include-context"],
      "env": {
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "NO_PROXY": "*"
      }
    }
  }
}
```

**关键点（路线图 §16.4 / smoke-test-record §5）：**

1. `command` 用云桌面 venv 里 `ansys-aedt-mcp.exe` 的**绝对路径**。请按实际安装位置改。
2. `args: ["--include-context"]`：启用 `get_guidelines_for`（HFSS/Maxwell/Icepak/Circuit 的 geometry/mesh/boundaries/postprocessing/parametric 指南），Skill 按需检索。**不要**加 `--connect`（启动即连接会锁定 launch/connect/disconnect 工具，丧失会话弹性）。**不要**加 `--dynamic-tool-discovery`（静态暴露全部 20 工具，便于 Agent 规划；连接与否由 Skill 把控）。
3. `env` **必须清空代理**：`HTTP_PROXY=""` / `HTTPS_PROXY=""` / `NO_PROXY="*"`。否则企业代理会切断 stdio，MCP server 连不上。这是本机调试时踩过的坑（commit 6256874 修过同名问题）。
4. 默认 `--transport stdio`，无需显式写。

> 在 Claude Code 里用 `/mcp` 命令查看 server 连接状态；首次连接会弹权限确认。

---

## 4. Phase 0 环境验证（envcheck）

`em_copilot.envcheck` 是**离线验证器**，绝不启动 AEDT、不消耗许可（路线图 §17 Phase 0）。

### 4.1 运行

```powershell
em-copilot-envcheck              # 人类可读，退出码 0=健康 1=失败 2=参数错
em-copilot-envcheck --json       # CI/机器可读
```

或经 Skill 脚本：

```powershell
python skills\automate-pyaedt-em\scripts\check_environment.py --json
```

### 4.2 云桌面预期结果（对照本机 version-manifest）

| 检查 | 本机（无许可） | 云桌面（有许可）预期 |
|---|---|---|
| Python 3.12 | pass | pass |
| 14 核心包（em-copilot-core / ansys-aedt-mcp / ansys-common-mcp / pyaedt / pyedb / ansys-edb-core / fastmcp / mcp / numpy / scipy / matplotlib / pydantic / typer / click） | 全 pass（typer 已补装 0.27.2） | 全 pass |
| em_copilot 回归导入 | pass | pass |
| MCP CLI `ansys-aedt-mcp --help` | pass（超时已放宽到 120s 防冷启动抖动） | pass |
| MCP 工具枚举（20 工具） | pass | pass |
| AEDT 安装探测 | **blocked**（本机无 AEDT，预期） | **pass** ✅ |

> 全绿（0 fail / 0 warn / 0 blocked）= Phase 0 通过，可进入 §5/§6。若 `aedt.install` 仍 blocked，检查 AEDT 环境变量是否对当前 shell 可见（可能需重启 shell 或 source AEDT 的环境脚本）。

---

## 5. 验证 MCP 工具面（20 工具枚举 + /mcp 连通）

### 5.1 工具枚举（离线，envcheck 已覆盖，也可单独验）

```powershell
ansys-aedt-mcp --help          # exit 0，stdout 含 --transport/--connect/--include-context
```

应看到 20 个工具（字母序）：

```
analyze_design, check_aedt_installed, check_aedt_status, clear_aedt,
connect_to_aedt, create_design, disconnect_from_aedt, export_config,
export_results, get_guidelines_for, get_model_info, get_pyaedt_logs,
launch_aedt, list_designs, list_projects, open_project, run_python_code,
run_python_script, save_project, screenshot
```

标签分布（`docs/mcp-capability-gap.md` §1）：
- **离线可用** 3 个：`check_aedt_installed` / `check_aedt_status` / `get_pyaedt_logs`
- `locked_connection` 2 个：`launch_aedt` / `connect_to_aedt`
- `requires_aedt` 14 个：其余（需连上 AEDT 才能用）
- `--include-context` 启用 1 个：`get_guidelines_for`

### 5.2 真实 MCP 会话连通（Claude Code 内）

1. 打开 Claude Code（VS Code 扩展或 CLI），工作目录指向项目根。
2. `/mcp` 查看 `pyaedt-em` server 状态应为 connected。
3. 让 Claude 调 `check_aedt_installed`：应返回检测到的 AEDT 版本（不再是 "No AEDT versions found"）。
4. 调 `check_aedt_status`：经真实 MCP lifespan 后，应返回 desktop/会话状态（本机离线裸调会抛 `NoneType.lifespan_context`，这是预期边界；云桌面经 stdio 会话后可正常返回）。

> 这两步是 §6 全流程前的最后闸门。`check_aedt_status` 在云桌面 PASS = stdio + 代理清空配置正确。

---

## 6. 全流程跑通：Skill 驱动的安全调用顺序

> 本节是核心。调用顺序严格遵循 `skills/automate-pyaedt-em/references/pyaedt-mcp.md` §3 与 `SKILL.md` 的 MCP Tool Discipline。**不要跳步**——跳过端口/参考/材料检查、或把单次残差当收敛，是路线图明令禁止的（SKILL.md Operating Rules）。

让 Claude 加载 Skill（`automate-pyaedt-em`），按以下步骤驱动。每一步的工具名即官方 MCP 工具。

### 步骤 0：框定案例 + 选设计类型（Skill 工作流第 1-2 步）

先用自然语言向 Claude 说清：物理（HFSS 电磁 / SI）、目标指标（如 Sdd21≥-12dB@16GHz）、输入工程。Skill 会先**定义物理、生成 SimulationTask 草稿但不求解**（SKILL.md: "先生成 SimulationTask 草稿，不要直接求解"）。

### 步骤 1：确认 AEDT 在线 + 许可可拿

```
check_aedt_installed   → 确认检测到 AEDT 版本
check_aedt_status      → 确认当前有无活动会话 / 许可状态
```

### 步骤 2：选择连接方式（Skill 会主动问）

- **attach 已有会话**（gRPC 远程机器:端口，或本机已有 desktop）：`connect_to_aedt`（`--machine`/`--port`）
- **launch 新会话**：`launch_aedt`

> **许可风险声明（SKILL.md / L3）**：launch/connect 会占一个 HFSS license，Skill 必须在调用前**显式声明许可风险并请用户确认**。长耗时求解属于 L3，需显式确认（路线图 §14.2）。

### 步骤 3：检视工程（只读，L0）

```
list_projects          → 列出已打开工程
list_designs           → 列出 design
get_model_info         → 模型/端口/材料概览
```

### 步骤 4：打开工程副本（copy-before-run，L2）

```
open_project  →  打开的是**副本**，不是原始输入工程
```

**文件安全铁律（路线图 §14 / SKILL.md）**：
- 原始工程目录**只读**；
- 每个任务创建独立运行目录；
- 所有修改针对**副本**；
- 记录输入文件 SHA-256（em_copilot 报告里已有 `input_hash`）；
- 输出文件**不得写回**输入目录。

> 实操：把原始 `.aedtproj`/`.aedt` 复制到一个 `runs/<task-id>/` 目录，`open_project` 指向副本路径。em_copilot 的 `em-copilot analyze` 有 `_check_output_not_inside_input` 守卫，但官方 MCP 没有——靠 Skill 与人工把关。

### 步骤 5：取设计指南（需 `--include-context`）

```
get_guidelines_for  →  按 design type（HFSS/SIwave/Circuit…）取
                       geometry / mesh / boundaries / postprocessing / parametric 指南
```

Skill 据此核对端口/参考/材料/网格是否就位（SKILL.md: "不要跳过端口/参考/材料检查"）。

### 步骤 6：求解（异步，先 smoke 后 full）

```
analyze_design  (blocking=False)   →  异步立即返回，求解在后台跑
```

**两阶段**：
1. **Smoke 求解**：先用极小扫频/粗网格跑通，确认端口/参考/材料无硬伤（SKILL.md 工作流第 8 步 "smoke solve"）。
2. **Full 求解**：smoke 通过后再上完整扫频/精细网格。

> **Job 模型尚未编码的诚实后果**：官方 `analyze_design(blocking=False)` 立即返回，**没有**配套的 `get_analysis_status` / `get_analysis_log` / `cancel_analysis`（这些是 `em_copilot.jobs` 待建工具，见 §10）。**本轮询方式**：调用 `get_pyaedt_logs` 拉增量日志 + `check_aedt_status` 看 solver 状态，人工判断是否收敛。Skill 会提醒"不要把单次残差当收敛"（SKILL.md）。

### 步骤 7：收集与导出结果

```
export_results   →  导出 Touchstone .sNp（S 参数）
export_config    →  可选，导出 setup/配置快照
get_pyaedt_logs  →  取求解日志
```

导出的 `.sNp` 落到 `runs/<task-id>/` 运行目录（不写回输入目录）。

### 步骤 8：后处理 + 报告（em_copilot 切片，无需许可）

见 §7。

### 步骤 9：收尾（必须做）

```
save_project        →  保存工程副本（注意是副本路径）
disconnect_from_aedt →  断开，**确认 PID 释放 + license 归还**
```

> SKILL.md: "以 disconnect_from_aedt 结束，确认 PID + license 已释放"。漏掉这步会留 orphan AEDT 进程占许可（error-recovery.md 有清理路径）。

---

## 7. 后处理与报告（em_copilot 切片）

这一步完全离线、无需许可，用 `em-copilot` CLI：

### 7.1 单文件分析（把导出的 .s4p 一键出报告）

```powershell
em-copilot analyze runs\<task-id>\exported.s4p `
    --pair TX0 1 2 --pair RX0 3 4 `
    --accept "Sdd21 >= -12dB 16GHz" `
    --accept "Sdd11 <= -10dB 4GHz" `
    --poi 4GHz --poi 16GHz `
    --tdr --near-pair TX0 --far-pair RX0 `
    --task-id <task-id> --out runs\<task-id>\report
```

退出码三态：`0`=PASS/NOT_EVALUATED，`1`=FAIL，`2`=ERROR（ERROR 优先）。可直接做 CI 门禁。

### 7.2 批量（多次扫频/多个变体一起出汇总）

```powershell
em-copilot batch runs\<task-id>\*.s4p `
    --pair TX0 1 2 --pair RX0 3 4 `
    --accept "Sdd21 >= -12dB 16GHz" --tdr `
    --out runs\<task-id>\batch
```

产出 `summary.json` + `summary.md`（4 节：逐文件结果、统计、失败清单、复现信息），逐文件隔离失败。

### 7.3 报告数值纪律（路线图 §13.3）

- 报告所有数值由 `em_copilot` **确定性代码**生成，**未经 AI 修改**；
- AI 只写解释，不改数；`metrics.json` 是同一份数据的机器可读形态；
- 计算结果与 AI 建议明确分离（报告里有 banner 明示）。

---

## 8. 安全与审计约束（必须遵守）

以下来自路线图 §14 / §16.3 与交接文档 §2，**云桌面同样适用**：

1. **命名空间纪律**：官方 `ansys.aedt.mcp` **原样使用、禁止占用**；企业扩展一律落 `em_copilot` / `em-copilot-mcp`。禁止结构：`src/ansys/aedt/mcp/`、`ansys-pyaedt-mcp`、`ansys-aedt-mcp`（作为我们的包名）。
2. **文件安全**：原始工程只读；每任务独立运行目录；修改针对副本；文件哈希记录输入；输出不写回输入目录。
3. **L0–L4 工具权限分级**：默认只开 L0-L2（查看/导出/改副本）；**L3 启动长耗时求解需显式确认**；**L4 `run_python_code` 仅受信任开发人员**。云桌面若多人用，务必确认默认不开放 L4。
4. **代理**：pip 走代理 `http://proxy.xfusion.com:8080`；**MCP server 子进程 env 必须清空代理**（`HTTP_PROXY=""` / `HTTPS_PROXY=""` / `NO_PROXY="*"`），否则 stdio 被切断。
5. **数据不出内网**：输入工程/几何/叠层/材料等敏感数据**不送外部模型服务**；NL 模型只收必要的结构化元数据；报告发布前执行脱敏检查。
6. **凭据/许可证不写入仓库**：`.mcp.json` 里不塞 license key；许可证配置不入 git。
7. **`.venv` 不迁移**：绝对路径会断，云桌面重建（§2）。
8. **遵循官方 PyAEDT API 文档**：https://aedt.docs.pyansys.com/version/stable/ —— 不绕开官方 API 自己造轮子。

---

## 9. 一次端到端 demo 建议（证明跑通）

为向领导证明"全流程跑通"，建议在云桌面跑一个最小可复现案例：

1. **准备一个已有 HFSS 工程**（云桌面应有测试工程；或用 `create_design` + `run_python_code` 建一个最简 4 端口差分对结构——但这属于前处理自动化，本次可先用现成工程）。
2. 按 §6 步骤 1-5 连接、打开副本、检视、取指南。
3. §6 步骤 6：先 smoke（1-2 个频点）再 full（如 0.1-40GHz, 401 点）。
4. §6 步骤 7：`export_results` 导出 `.s4p`。
5. §7.1：`em-copilot analyze` 出报告。
6. 打开 `runs/<task-id>/report/report.html` —— 这就是全流程交付物。
7. §6 步骤 9：`disconnect_from_aedt`，确认许可释放。

> 成功标志：`report.html` 显示 PASS、含 S 参数图 + TDR 图 + 验收表 + `metrics.json`，且 AEDT 进程已退出、license 已归还。

---

## 10. 已知差距与后续（阶段1 待建）

本次跑通后，路线图阶段1 的工作（**尚未编码**，见 `docs/mcp-capability-gap.md` 差距-1）：

| 待建（`em_copilot.jobs`） | 语义 | 包装 |
|---|---|---|
| `submit_analysis` | 校验任务、copy-before-run、启动异步求解、返回 job_id | `analyze_design` |
| `get_analysis_status` | 查询 Job 子状态机（QUEUED→STARTING→SOLVING→EXPORTING→SUCCEEDED） | 轮询 solver |
| `get_analysis_log` | 拉增量日志 | `get_pyaedt_logs` |
| `cancel_analysis` | 请求取消 | Desktop 停止求解 |
| `collect_analysis_results` | 求解完成后导出 Touchstone/profile/convergence/mesh | `export_results` |

这些工具**必须落 `em_copilot.jobs` 命名空间**，绝不回写 `ansys.aedt.mcp`。建好后，§6 步骤 6-7 的手动轮询就能换成结构化 Job API，长耗时求解可被 Agent 安全跟踪与取消——那是下一阶段"平台化"的基础，但**不是本次全流程跑通的前置条件**。

---

## 附：速查命令清单

> 推荐用 §2.0 的一键脚本；下面给出等价的手工步骤供排错。

```powershell
# === 一次性环境（云桌面）——手工等价步骤 ===
cd C:\workspace\em-copilot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
$env:HTTP_PROXY="http://proxy.xfusion.com:8080"; $env:HTTPS_PROXY=$env:HTTP_PROXY
pip install -e . --trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org
pip install -r requirements-cloud.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org
python scripts\setup-mcp-json.py

# 或直接一键（推荐）：
#   .\scripts\bootstrap-cloud.ps1

# === Phase 0 验证 ===
em-copilot-envcheck
em-copilot demo --out runs\demo

# === MCP 连通（Claude Code 内） ===
# /mcp  →  pyaedt-em 应为 connected
# 让 Claude 调 check_aedt_installed / check_aedt_status

# === 全流程（Claude Code + Skill 驱动） ===
# 加载 automate-pyaedt-em skill，按 §6 步骤 1-9 执行
# check_aedt_installed → check_aedt_status → launch_aedt/connect_to_aedt
# → list_projects/list_designs/get_model_info → open_project(副本)
# → get_guidelines_for → analyze_design(smoke→full) → export_results
# → em-copilot analyze <导出.s4p> ... → save_project(副本) → disconnect_from_aedt
```
