# Phase 1 实施计划：em-copilot-mcp 独立 server + sweep 导出工具

> 目标：建起 em_copilot 自己的 MCP server，落地第一个工具 `export_touchstone_sweep`，
> 让 sweep Touchstone 导出不再依赖 L4 `run_python_code`。
> 路线图依据：§3.2（不碰官方命名空间）、§8.2/差距-1（企业扩展工具）、§8.4（业务路径不依赖 L4）。

## 架构决策（已与用户确认）

- **独立 stdio server** `em-copilot-mcp`，与官方 `pyaedt-em` 并列在 `.mcp.json`。
- **子类化 `PyAnsysBaseMCP`**（`ansys.common.mcp.server`），复用官方 lifespan/transport/CLI 框架。
  - 不需要 `PersistentPythonSession`（我们不跑即时代码）：`need_python=False`。
  - context 自定义：只存 `aedt_port`（可选，工具也接受 port 参数覆盖）。
- **不重写官方工具**：em-copilot-mcp 只暴露官方没有的扩展工具（本阶段 1 个，后续 Phase 2 加 Job 工具）。
- **共享 AEDT 进程**：em-copilot 工具用 `Desktop(new_desktop=False, machine, port)` 连到官方 server 启动的同一个 AEDT gRPC 进程，不额外占 license。
- **port 传递**：工具签名带 `port: int` 参数。Agent 从官方 `check_aedt_status` 返回的 `connectable_sessions[].port` 拿到 port，调 em-copilot 工具时传入。首次调用后 context 缓存 port，后续调用可省略（但参数优先级高于缓存）。

## 新增文件

### 1. `src/em_copilot/mcp/__init__.py`（新包）
空 init 或导出 `app`、`launcher`。

### 2. `src/em_copilot/mcp/server.py`（新）
- `class EmCopilotMCP(PyAnsysBaseMCP)`：
  - `create_context()` → 返回 `EmCopilotContext`（只含 `aedt_port: int|None`），不建 PersistentPythonSession。
  - `product_startup()` / `product_cleanup()`：空实现（AEDT 连接由工具自管，不由 server lifespan 管）。
- `app = EmCopilotMCP(name="em-copilot-mcp")`。
- `launcher()`：解析 `--transport`（stdio/http），调 `app.run_cli()`。可加 `--aedt-port` 可选参数预填 context。

### 3. `src/em_copilot/mcp/tools.py`（新）
第一个工具：

```python
@app.tool(tags={"em_copilot"})
def export_touchstone_sweep(
    port: int,
    setup_name: str,
    sweep_name: str,
    output_file: str,
    project_name: str | None = None,
    design_name: str | None = None,
    machine: str = "localhost",
) -> str:
    """Export a frequency-sweep Touchstone (.sNp) from a solved HFSS design.

    Connects to the SAME AEDT gRPC process the official pyaedt-em server launched
    (pass the port from check_aedt_status). Exports the named sweep, NOT the
    LastAdaptive single point. Validates the output has >1 freq point before
    returning.
    """
```

内部逻辑：
1. `from ansys.aedt.core import Desktop, get_pyaedt_app`
2. `desktop = Desktop(new_desktop=False, non_graphical=True, machine=machine, port=port)` 连已有进程
3. `app = get_pyaedt_app(project_name=..., design_name=..., desktop=desktop)`
4. 校验 `setup_name` 在 `app.setups` 里，`sweep_name` 在 `setup.get_sweep_names()` 里
5. `app.export_touchstone(setup=setup_name, sweep=sweep_name, output_file=output_file)`
6. **后置校验**：读导出文件，数频点（数据行数 / 端口数），若 <2 频点报错（防止又出 LastAdaptive 单点）
7. 返回 JSON：`{output_file, n_freqs, freq_range, n_ports, setup, sweep}`

### 4. `src/em_copilot/mcp/__main__.py`（新，入口）
`from em_copilot.mcp.server import launcher; launcher()`

## 修改文件

### 5. `pyproject.toml`
- `[project.scripts]` 加 `em-copilot-mcp = "em_copilot.mcp.server:launcher"`
- `dependencies` 加 `fastmcp`、`ansys-aedt-mcp`（只需其 `ansys.common.mcp` 基类 + `ansys.aedt.core`）——或归到新的 optional-dependencies `mcp`。

### 6. `.mcp.json`（em-copilot/ 和 D:/HCX/HFSS/ 两份）
加第二个 server：
```json
"em-copilot-mcp": {
  "command": "D:\\HCX\\HFSS\\em-copilot\\.venv\\Scripts\\em-copilot-mcp.exe",
  "args": ["--transport", "stdio"],
  "env": { "HTTP_PROXY": "", "HTTPS_PROXY": "", "NO_PROXY": "*" }
}
```

## 不做（Phase 1 边界）
- Job 模型工具（submit_analysis/get_analysis_status/...）→ Phase 2
- 一键编排 → Phase 3
- 完整报告合并 → Phase 4
- 不碰官方 `ansys.aedt.mcp` 命名空间任何代码

## 验证步骤
1. `pip install -e .` 重装，确认 `em-copilot-mcp` 脚本生成
2. `em-copilot-mcp --help` exit 0
3. 重启 MCP 连接（让两个 server 都起来）
4. Twinaxial 回归：官方 launch→open→analyze，然后调 `export_touchstone_sweep(port=..., setup="3GHz", sweep="DC_10GHz", output_file=...)`，确认输出 201 点 s4p，**全程零 run_python_code**
5. 单元测试：`tests/test_mcp_tools.py`——mock Desktop/get_pyaedt_app，验证工具逻辑（频点校验、setup/sweep 名校验的错误路径）
