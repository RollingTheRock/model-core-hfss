# 阶段0 — 版本清单 (Version Manifest)

> 路线图 §17 Phase 0 / §3.3 环境基线。本文件记录 PyAEDT EM Copilot 工具链在本机的已安装版本。
> 由 `python -m em_copilot.envcheck --json` 在 `2026-09-03` 生成，机器：Windows 11 Pro (win32)。
> 离线子集全部 PASS；AEDT 安装项 BLOCKED（本机未安装 AEDT/HFSS，需带许可的工作站完成该项）。

## 摘要

| 状态 | 数量 |
|---|---|
| pass | 17 |
| warn | 1 |
| blocked | 1 |
| fail | 0 |

- `warn`：`typer` 未安装（pyaedt CLI 会话发现需要该可选依赖；见能力差距 §能力差距-2）。
- `blocked`：`aedt.install` — `ANSYSEM_ROOTxxx` / `AWP_ROOTxxx` 环境变量中无 AEDT 版本。**非失败**，完整验证需 HFSS 许可。

## Python / 平台

| 组件 | 版本 |
|---|---|
| Python | 3.12.10 |
| 平台 | win32 (Windows 11 Pro 10.0.26200) |

## 核心依赖（required）

| 包 (dist) | 导入模块 | 版本 |
|---|---|---|
| em-copilot-core | em_copilot | 0.1.dev0 |
| ansys-aedt-mcp | ansys.aedt.mcp | 0.1.4 |
| ansys-common-mcp | ansys.common.mcp | 0.3.3 |
| pyaedt | ansys.aedt.core | 1.5.0 |
| pyedb | pyedb | 0.83.0 |
| ansys-edb-core | ansys.edb.core | 0.3.3 |
| fastmcp | fastmcp | 4.0.2 |
| mcp | mcp | 2.1.1 |
| numpy | numpy | 2.5.2 |
| scipy | scipy | 1.18.1 |
| matplotlib | matplotlib | 3.11.1 |
| pydantic | pydantic | 2.13.5 |

## 可选依赖

| 包 (dist) | 版本 | 状态 | 说明 |
|---|---|---|---|
| typer | — | warn (未安装) | pyaedt CLI 会话发现 (`discover_available_aedt_sessions`) 需要；见能力差距 |
| click | 8.5.0 | pass | typer 的底层依赖 |

## em-copilot-core 回归

| 检查 | 状态 | 详情 |
|---|---|---|
| em_copilot.import | pass | em_copilot 0.1.dev0 imports cleanly（schemas/mixed_mode/analysis/reporting/touchstone/cli/demo 全部可导入） |

## MCP CLI / 工具面

| 检查 | 状态 | 详情 |
|---|---|---|
| mcp.cli | pass | `ansys-aedt-mcp --help` exit 0（含 `--transport`） |
| mcp.tools | pass | 20 个工具已注册（离线可枚举） |

20 个工具（按字母序）：

```
analyze_design, check_aedt_installed, check_aedt_status, clear_aedt,
connect_to_aedt, create_design, disconnect_from_aedt, export_config,
export_results, get_guidelines_for, get_model_info, get_pyaedt_logs,
launch_aedt, list_designs, list_projects, open_project, run_python_code,
run_python_script, save_project, screenshot
```

## AEDT 安装

| 检查 | 状态 | 详情 |
|---|---|---|
| aedt.install | blocked | No AEDT versions found in ANSYSEM_ROOTxxx / AWP_ROOTxxx env |

> 该项为阶段0已知的、被阻塞项。完整 MCP 工具验证（`requires_aedt` 标签的 16 个工具、真实求解、Touchstone 导出）需在带 HFSS 许可的工作站完成。当前离线子集已覆盖 `check_aedt_installed` / `check_aedt_status` / `get_pyaedt_logs`。

## 复现

```bash
# 在 em-copilot venv 中
python -m em_copilot.envcheck              # 人类可读
python -m em_copilot.envcheck --json       # CI/机器可读
```

原始 JSON 输出存档于 [`_envcheck_live.json`](_envcheck_live.json)。
