# 阶段0 — 离线冒烟测试记录 (Smoke-Test Record)

> 路线图 §17 Phase 0 / 交接文档 §7「下一步-1」。
> 目标：在不消耗 HFSS 许可的前提下，验证官方 `ansys-aedt-mcp` 0.1.4 本地可用（CLI、工具枚举、离线子集）。
> 完整验证（`requires_aedt` 的 14 个工具、真实求解、Touchstone 导出）需带 HFSS 许可的工作站，记为阻塞项。
>
> 环境：`d:\workspace\HFSS\em-copilot\.venv`（Python 3.12.10, win32）。代理已对 pip 生效；MCP server 子进程 env 必须清空代理（见 §5）。

## 1. 验证矩阵

| # | 检查 | 方法 | 结果 | 状态 |
|---|---|---|---|---|
| 1 | CLI 入口可用 | `ansys-aedt-mcp --help` | exit 0，stdout 含 `--transport`、`--connect`、`--include-context`、`--dynamic-tool-discovery` 等全部选项 | ✅ PASS |
| 2 | 工具注册数 | `await srv.list_tools()`（FastMCP API） | 20 个工具，名称与 `tools.py` 的 `@app.tool` 一一对应 | ✅ PASS |
| 3 | 工具集资源 | `toolsets://definition`（`toolsets.py`） | 7 个工具集：lifecycle / project-management / simulation / scripting / inspection / results / guidelines | ✅ PASS |
| 4 | 离线工具调用：check_aedt_installed | `await srv.call_tool('check_aedt_installed', {})` | 返回 "No AEDT versions found installed on this system." | ✅ PASS |
| 5 | 离线工具调用：check_aedt_status | `await srv.call_tool('check_aedt_status', {})` | ToolError: `'NoneType' object has no attribute 'lifespan_context'` — 工具读 `ctx.request_context.lifespan_context.desktop`，无真实 MCP lifespan 时为 None | ⚠️ 预期边界 |
| 6 | 离线工具调用：get_pyaedt_logs | `await srv.call_tool('get_pyaedt_logs', {})` | 返回结构化 ToolResult（无 AEDT 日志为空） | ✅ PASS（空日志） |
| 7 | AEDT 安装探测 | `aedt_versions.installed_versions` | 空 + `UserWarning: No installed versions of AEDT are found` | ❌ BLOCKED |
| 8 | em-copilot-core 回归 | `em-copilot demo` / `analyze` | （上一阶段已 PASS） | ✅ PASS |

## 2. 关键事实

### 2.1 FastMCP API 正确调用方式

- `srv.get_tools()` ❌ — `PyAEDTMCP` 无此属性。
- `await srv.list_tools()` ✅ — 正确方法，返回 Tool 对象列表。
- `await srv.call_tool(name, args)` ✅ — **不要**传 `context=` kwarg（FastMCP `call_tool` 不接受该参数，会抛 `TypeError`）。Context 由 server lifespan 注入。

### 2.2 离线可 smoke-test 的工具边界

仅 3 个工具不携带 `requires_aedt` 标签：`check_aedt_installed`、`check_aedt_status`、`get_pyaedt_logs`。
其中 `check_aedt_status` 虽无 `requires_aedt` 标签，但实现读取 lifespan desktop，**必须**经真实 MCP 会话（stdio/http）验证，不能在进程内裸调。`check_aedt_installed` 与 `get_pyaedt_logs` 可进程内裸调。

其余 14 个 `requires_aedt` 工具 + 2 个 `locked_connection` 工具（launch/connect）需真实 AEDT 连接，本机阻塞。

### 2.3 typer 缺失

pyaedt CLI 会话发现（`discover_available_aedt_sessions`）`ImportError: No module named 'typer'`。非 MCP 工具缺陷，是 venv 缺可选依赖。记为能力差距-2。

## 3. CLI 选项清单（`ansys-aedt-mcp --help`）

```
--transport {stdio,http}   传输类型（默认 stdio）
--machine MACHINE          AEDT 机器名（默认 localhost）
--port PORT                AEDT gRPC 端口（默认 50051）
--version VERSION          AEDT 版本，如 '2026.1' 或 '261'
--non-graphical            非图形模式（默认）
--graphical                图形模式
--connect                  启动即连接 AEDT（会锁定 launch/connect/disconnect 工具）
--include-context          注册 get_guidelines_for 等上下文工具
--dynamic-tool-discovery   连接建立前隐藏 requires_aedt 工具
--http-host HOST           HTTP 传输主机（默认 127.0.0.1）
--http-port PORT           HTTP 传输端口（默认 8080）
--cors-origins ORIGINS     HTTP 传输允许的 CORS 源
```

## 4. 推荐的 MCP 连接配置（Claude /mcp）

```json
{
  "mcpServers": {
    "pyaedt-em": {
      "command": "D:\\workspace\\HFSS\\em-copilot\\.venv\\Scripts\\ansys-aedt-mcp.exe",
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

- `--include-context`：启用 `get_guidelines_for`（HFSS/Maxwell/Icepak/Circuit/geometry/mesh/boundaries/postprocessing/parametric 指南），供 Skill 按需检索。
- 不加 `--connect`：由 Skill 工作流显式 `connect_to_aedt`，保留会话弹性。
- 不加 `--dynamic-tool-discovery`：静态暴露全部工具，便于 Agent 规划；连接与否由 Skill 把控。

## 5. 代理环境约束（关键）

本机 pip 必须走代理 `http://proxy.xfusion.com:8080`；但 **MCP server 子进程 env 必须清空代理**，否则 stdio 连接被代理切断：

```bash
# pip 安装（需要代理）
HTTP_PROXY=http://proxy.xfusion.com:8080 HTTPS_PROXY=http://proxy.xfusion.com:8080 \
  pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org ansys-aedt-mcp

# MCP server 启动（必须清空代理）
HTTP_PROXY="" HTTPS_PROXY="" NO_PROXY="*" ansys-aedt-mcp --include-context
```

## 6. 阻塞项与后续

| 阻塞项 | 需要什么 | 后续 |
|---|---|---|
| 14 个 `requires_aedt` 工具验证 | 带 HFSS 许可的 AEDT 工作站 | 在该机器跑 `envcheck`（aedt.install 转 PASS），并经真实 MCP 会话 smoke-test `check_aedt_status` / `list_projects` / `analyze_design` / `export_results` |
| typer 安装 | venv 补 `typer` | 补依赖后重跑 envcheck，warn 应消除 |

## 7. 复现命令

```bash
cd d:/workspace/HFSS/em-copilot
.venv/Scripts/ansys-aedt-mcp.exe --help                                # §1 检查 1
.venv/Scripts/python.exe -m em_copilot.envcheck --json                  # §1 检查 2-7 汇总
.venv/Scripts/python.exe -m em_copilot.envcheck                        # 人类可读
```

原始 envcheck JSON：[`_envcheck_live.json`](_envcheck_live.json)。
