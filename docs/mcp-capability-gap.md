# 阶段0 — MCP 能力差距清单 (Capability Gap)

> 路线图 §4.1 / §8.3 / §17 Phase 0。
> 本文件枚举官方 `ansys-aedt-mcp` 0.1.4 的完整工具面、标签、离线可用性，
> 并指出路线图要求、但官方 MCP **未原生提供**、必须在 `em_copilot` 命名空间内补齐的能力。
>
> 命名空间纪律（路线图 §2.2 / §8.2）：官方 `ansys.aedt.mcp` 原样作为 Agent 工具层使用，**禁止占用**其命名空间；企业扩展一律落在 `em_copilot` / `em-copilot-mcp`。禁止结构：`src/ansys/aedt/mcp/`、`ansys-pyaedt-mcp`、`ansys-aedt-mcp`（作为我们的包名）。

## 1. 官方工具完整清单（20 个）

来源：`ansys.aedt.mcp.tools`（19 个 `@app.tool`）+ `ansys.aedt.mcp.contexts`（1 个 `get_guidelines_for`）。
标签常量：`REQUIRES_AEDT_TAG = "requires_aedt"`；超时常量 `_TIMEOUT_QUICK=30` / `_TIMEOUT_MEDIUM=120` / `_TIMEOUT_LONG=600`。

| # | 工具 | 标签 | 超时(s) | 工具集 | 离线可用 |
|---|---|---|---|---|---|
| 1 | check_aedt_installed | aedt_tools | 30 | lifecycle | ✅ 是 |
| 2 | check_aedt_status | aedt_tools | 30 | lifecycle | ⚠️ 需 MCP lifespan（见 §3） |
| 3 | get_pyaedt_logs | aedt_tools | 30 | inspection | ✅ 是 |
| 4 | launch_aedt | aedt_tools, locked_connection | 120 | lifecycle | ❌ 需 AEDT |
| 5 | connect_to_aedt | aedt_tools, locked_connection | 120 | lifecycle | ❌ 需 AEDT |
| 6 | disconnect_from_aedt | aedt_tools, locked_connection, **requires_aedt** | 120 | lifecycle | ❌ 需 AEDT |
| 7 | clear_aedt | aedt_tools, **requires_aedt** | 120 | lifecycle | ❌ 需 AEDT |
| 8 | list_projects | aedt_tools, **requires_aedt** | 30 | project-management | ❌ 需 AEDT |
| 9 | list_designs | aedt_tools, **requires_aedt** | 30 | project-management | ❌ 需 AEDT |
| 10 | open_project | aedt_tools, **requires_aedt** | 120 | project-management | ❌ 需 AEDT |
| 11 | save_project | aedt_tools, **requires_aedt** | 120 | project-management | ❌ 需 AEDT |
| 12 | create_design | aedt_tools, **requires_aedt** | 120 | project-management | ❌ 需 AEDT |
| 13 | analyze_design | aedt_tools, **requires_aedt** | 600 | simulation | ❌ 需 AEDT |
| 14 | export_config | aedt_tools, **requires_aedt** | 120 | simulation | ❌ 需 AEDT |
| 15 | export_results | aedt_tools, **requires_aedt** | 600 | results | ❌ 需 AEDT |
| 16 | run_python_script | aedt_tools, **requires_aedt** | 600 | scripting | ❌ 需 AEDT |
| 17 | run_python_code | aedt_tools, **requires_aedt** | 600 | scripting | ❌ 需 AEDT |
| 18 | get_model_info | aedt_tools, **requires_aedt** | 30 | inspection | ❌ 需 AEDT |
| 19 | screenshot | **requires_aedt**（无 aedt_tools） | 120 | inspection | ❌ 需 AEDT |
| 20 | get_guidelines_for | pyaedt_context | — | guidelines | ⚠️ 需 `--include-context` 启用 |

### 标签互斥分组

| 类别 | 数量 | 工具 |
|---|---|---|
| 离线可用（无 requires_aedt、非 locked） | 3 | check_aedt_installed, check_aedt_status, get_pyaedt_logs |
| locked_connection 且非 requires_aedt | 2 | launch_aedt, connect_to_aedt |
| locked_connection 且 requires_aedt | 1 | disconnect_from_aedt |
| requires_aedt（非 locked） | 13 | clear_aedt, list_projects, list_designs, open_project, save_project, create_design, analyze_design, export_config, export_results, run_python_script, run_python_code, get_model_info, screenshot |
| 可选上下文（pyaedt_context） | 1 | get_guidelines_for |

合计 3 + 2 + 1 + 13 + 1 = 20。`requires_aedt` 标签工具共 **14 个**。

### 标签的运行时效应

- `--connect`（启动即连接）：`launch_aedt` / `connect_to_aedt` / `disconnect_from_aedt` 被禁用（locked_connection）。
- `--include-context`：`get_guidelines_for` 启用（`pyaedt_context` 标签）。
- `--dynamic-tool-discovery` 且未 `--connect`：所有 `requires_aedt` 工具在连接建立前隐藏。

## 2. 能力差距

### 差距-1：缺失 Job 模型工具（路线图 §4.1 — 必须在 em_copilot 补齐）

官方 MCP 的 `analyze_design` 调用 `app_instance.analyze(..., blocking=False)`，**异步立即返回**，但没有配套的提交/状态/日志/取消/收集工具。Agent 无法以结构化方式跟踪长耗时求解。路线图 §4.1 的 Job 模型要求以下 5 个工具，必须在 `em_copilot` 命名空间实现，包装 `analyze_design` + `export_results` + `get_pyaedt_logs`：

| 待建工具 | 语义 | 包装的官方工具 |
|---|---|---|
| submit_analysis | 校验任务、copy-before-run、启动异步求解、返回 job_id | analyze_design |
| get_analysis_status | 查询 Job 子状态机 | （轮询 PyAEDT solver 状态） |
| get_analysis_log | 拉取增量日志 | get_pyaedt_logs |
| cancel_analysis | 请求取消 | （调用 Desktop 停止求解） |
| collect_analysis_results | 求解完成后导出 Touchstone/profile/convergence/mesh | export_results |

Job 子状态机（§4.1）：`QUEUED → STARTING → SOLVING → EXPORTING → SUCCEEDED`；异常 `TIMED_OUT / FAILED / CANCEL_REQUESTED / CANCELLED / ORPHANED`。
任务状态机（§6.3）：`DRAFT → VALIDATING → NEEDS_INPUT → READY_FOR_REVIEW → APPROVED → RUNNING → POST_PROCESSING → COMPLETED`；异常 `REJECTED / FAILED / CANCELLED / PARTIAL_SUCCESS`。

> 这些工具必须落在 `em_copilot.jobs`（或 `em_copilot.mcp.jobs`），**绝不**回写到 `ansys.aedt.mcp` 命名空间。

### 差距-2：会话发现依赖 typer（阶段0 warn）

pyaedt CLI 的 `discover_available_aedt_sessions` 需要可选依赖 `typer`，当前 venv 未安装（envcheck 报 warn）。`check_aedt_status` 官方实现也尝试通过 pyaedt 发现本地会话。**修复**：在 em-copilot-core 的依赖声明中显式加 `typer`（或仅在工作站镜像安装），使会话发现可用。这是阶段0的 warn，不是 fail。

### 差距-3：离线 smoke-test 边界（阶段0 已确认）

- `check_aedt_installed`：可离线 `call_tool`，返回 "No AEDT versions found installed on this system." ✅
- `check_aedt_status`：工具体读取 `ctx.request_context.lifespan_context.desktop`，**无真实 MCP lifespan 时为 None**，`call_tool` 直接调用会抛 `'NoneType' object has no attribute 'lifespan_context'`。**不能**在无 MCP 会话的进程内 smoke-test；必须经真实 stdio/http MCP 会话验证。已记录为离线验证边界。
- `get_pyaedt_logs`：FastMCP `call_tool` 返回结构化 ToolResult，离线路径可调用但无实际日志（无 AEDT）。✅（空日志）
- 其余 14 个 `requires_aedt` 工具：**全部阻塞**，需带 HFSS 许可的工作站。

### 差距-4：数值与文件安全护栏未在官方 MCP 体现（路线图 §13.3 / §14 — em_copilot 负责）

官方 MCP 不提供：
- copy-before-run（原始项目只读，输出不回写输入目录，文件哈希记录）；
- L0–L4 工具权限分级与审计；
- 数值纪律（报告数值由确定性代码生成，LLM 不得改数，`metrics.json` 为机器可读形态）；
- 敏感数据不出内网（输入工程/几何/叠层/材料不送外部模型服务，NL 模型仅收必要结构化元数据）。

这些全部由 `em_copilot` 层实现，官方 MCP 仅作"工具手"。

## 3. 离线验证记录要点

- `ansys-aedt-mcp --help` exit 0，含 `--transport`。✅
- `list_tools()` 枚举 20 个工具。✅
- `check_aedt_installed` 经 `call_tool` 成功返回。✅
- `check_aedt_status` 经 `call_tool` 抛 lifespan None（预期，非缺陷）。⚠️
- AEDT 安装探测：`aedt_versions.installed_versions` 为空 + UserWarning。❌（阻塞，需工作站）

详见 [`smoke-test-record.md`](smoke-test-record.md)。

## 4. 与路线图的映射

| 路线图节 | 本文档对应 |
|---|---|
| §4.1 Session/Job 架构 | 差距-1（Job 模型 5 工具） |
| §8.3 结构化任务面 | 差距-1 + SimulationTask schema（§6.2） |
| §13.3 数值纪律 | 差距-4 |
| §14 文件安全 | 差距-4 |
| §2.2/§8.2 命名空间 | 全文约束 |
| §17 Phase 0 | 阶段0 交付物之一 |
