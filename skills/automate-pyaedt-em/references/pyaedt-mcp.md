# PyAEDT-MCP Tool Reference

> Source of truth for the tool surface used by the `automate-pyaedt-em` skill.
> Two layers: the **official `ansys-aedt-mcp` 0.1.4** server (used as-is, unmodified) and the **`em_copilot` domain extension** (Job/session/file-safety, Touchstone/SI).
> Namespace rule (roadmap §2.2/§8.2): the official `ansys.aedt.mcp` namespace is never occupied or modified; enterprise extensions live only in `em_copilot` / `em-copilot-mcp`.

## 1. Official `ansys-aedt-mcp` tools (20)

Tag constants: `REQUIRES_AEDT_TAG = "requires_aedt"`. Timeouts: `_TIMEOUT_QUICK=30`, `_TIMEOUT_MEDIUM=120`, `_TIMEOUT_LONG=600`.

| # | Tool | Tags | Timeout(s) | Toolset | Offline |
|---|---|---|---|---|---|
| 1 | `check_aedt_installed` | aedt_tools | 30 | lifecycle | yes |
| 2 | `check_aedt_status` | aedt_tools | 30 | lifecycle | needs MCP lifespan |
| 3 | `get_pyaedt_logs` | aedt_tools | 30 | inspection | yes (empty) |
| 4 | `launch_aedt` | aedt_tools, locked_connection | 120 | lifecycle | needs AEDT |
| 5 | `connect_to_aedt` | aedt_tools, locked_connection | 120 | lifecycle | needs AEDT |
| 6 | `disconnect_from_aedt` | aedt_tools, locked_connection, requires_aedt | 120 | lifecycle | needs AEDT |
| 7 | `clear_aedt` | aedt_tools, requires_aedt | 120 | lifecycle | needs AEDT |
| 8 | `list_projects` | aedt_tools, requires_aedt | 30 | project-management | needs AEDT |
| 9 | `list_designs` | aedt_tools, requires_aedt | 30 | project-management | needs AEDT |
| 10 | `open_project` | aedt_tools, requires_aedt | 120 | project-management | needs AEDT |
| 11 | `save_project` | aedt_tools, requires_aedt | 120 | project-management | needs AEDT |
| 12 | `create_design` | aedt_tools, requires_aedt | 120 | project-management | needs AEDT |
| 13 | `analyze_design` | aedt_tools, requires_aedt | 600 | simulation | needs AEDT |
| 14 | `export_config` | aedt_tools, requires_aedt | 120 | simulation | needs AEDT |
| 15 | `export_results` | aedt_tools, requires_aedt | 600 | results | needs AEDT |
| 16 | `run_python_script` | aedt_tools, requires_aedt | 600 | scripting | needs AEDT |
| 17 | `run_python_code` | aedt_tools, requires_aedt | 600 | scripting | needs AEDT |
| 18 | `get_model_info` | aedt_tools, requires_aedt | 30 | inspection | needs AEDT |
| 19 | `screenshot` | requires_aedt (no aedt_tools) | 120 | inspection | needs AEDT |
| 20 | `get_guidelines_for` | pyaedt_context | — | guidelines | needs `--include-context` |

### Toolset resource

`toolsets://definition` (a pure discovery aid) lists 7 toolsets: lifecycle, project-management, simulation, scripting, inspection, results, guidelines.

### Tag runtime effects

- `--connect` (connect on startup): `launch_aedt` / `connect_to_aedt` / `disconnect_from_aedt` are disabled (locked_connection).
- `--include-context`: enables `get_guidelines_for` (pyaedt_context tag).
- `--dynamic-tool-discovery` without `--connect`: all `requires_aedt` tools hidden until a connection is established.

### Offline-capable subset (no HFSS license)

Only 3 tools carry no `requires_aedt` tag:

- `check_aedt_installed` — confirm the AEDT binary is on disk. Safe to call in-process.
- `check_aedt_status` — reads `ctx.request_context.lifespan_context.desktop`; **must** run over a real stdio/http MCP session, cannot be smoke-tested by calling the function in-process (returns `'NoneType' object has no attribute 'lifespan_context'`).
- `get_pyaedt_logs` — returns an empty structured result without AEDT.

The remaining 14 `requires_aedt` tools + 2 `locked_connection` launch/connect tools need a live AEDT connection and are blocked pending an HFSS-licensed workstation.

## 2. `em_copilot` domain-extension tools (to be implemented)

Official `analyze_design` calls `app_instance.analyze(..., blocking=False)` and returns immediately, but ships no submit/status/log/cancel/collect companions. The Job model (roadmap §4.1) is implemented in `em_copilot` (namespace `em_copilot.jobs`), wrapping `analyze_design` + `export_results` + `get_pyaedt_logs`:

| Tool | Semantics | Wraps |
|---|---|---|
| `submit_analysis` | validate task, copy-before-run, start async solve, return `job_id` | analyze_design |
| `get_analysis_status` | query Job sub-state machine | (poll PyAEDT solver state) |
| `get_analysis_log` | fetch incremental log | get_pyaedt_logs |
| `cancel_analysis` | request cancel | (Desktop stop solve) |
| `collect_analysis_results` | post-solve export of Touchstone/profile/convergence/mesh | export_results |

Job sub-states: `QUEUED → STARTING → SOLVING → EXPORTING → SUCCEEDED`; exceptions `TIMED_OUT / FAILED / CANCEL_REQUESTED / CANCELLED / ORPHANED`.

Task states: `DRAFT → VALIDATING → NEEDS_INPUT → READY_FOR_REVIEW → APPROVED → RUNNING → POST_PROCESSING → COMPLETED`; exceptions `REJECTED / FAILED / CANCELLED / PARTIAL_SUCCESS`.

The Touchstone/SI analysis layer (`em_copilot.touchstone` / `mixed_mode` / `analysis` / `reporting`) is HFSS-license-free and CI-able; it is the V0.1 post-processing and reporting path.

## 3. Safe Call Order

1. `check_aedt_installed` — confirm binary present (offline).
2. `check_aedt_status` — discover live sessions / confirm MCP connection (real MCP session only).
3. If multiple sessions: ask user attach-vs-new. If one: ask attach-vs-new. Use `launch_aedt` only after explicit user confirmation.
4. `connect_to_aedt` (attach by machine + gRPC port) or `launch_aedt` (new instance). State license risk before calling.
5. `list_projects` / `list_designs` / `get_model_info` to inspect; `open_project` to load a **copy** (never the read-only input); `export_config` to persist setup/sweep.
6. `get_guidelines_for` (requires `--include-context`) before generating PyAEDT code or invoking a workflow tool — returns authoritative patterns for workflow/hfss/maxwell/icepak/circuit/geometry/mesh/boundaries/postprocessing/parametric.
7. Smoke solve then full solve: `analyze_design` (async, `blocking=False`) → poll via `em_copilot` Job model (`submit_analysis` → `get_analysis_status` → `get_analysis_log`) → `collect_analysis_results` / `export_results`.
8. `get_pyaedt_logs` after suspicious behavior or long operations.
9. `save_project` to persist the **copy** to a new file (never over the input).
10. `disconnect_from_aedt` at the end unless the user wants the session left open; confirm PID + license released.

## 4. Scripting discipline (L4)

`run_python_code` / `run_python_script` share a persistent Python session (variables/imports persist across calls). They are **admin-diagnostic only (L4)**, never the default business path (roadmap §8.4). Rules:

- Keep snippets short, explicit, reversible.
- Show the user the snippet before running any write-side code.
- Never use them to bypass the Job model for production solves.
- Prefer `get_guidelines_for` + the workflow tools over hand-rolled API scripts.

## 5. FastMCP API notes (for in-tooling/tests)

- `await srv.list_tools()` — correct; returns Tool objects. (`srv.get_tools()` does not exist.)
- `await srv.call_tool(name, args)` — do **not** pass `context=` kwarg (TypeError); context is injected by the server lifespan.
- `check_aedt_status` cannot be validated via in-process `call_tool` (lifespan None); validate over a real stdio/http MCP session.

## 6. Recommended MCP connection config

```json
{
  "mcpServers": {
    "pyaedt-em": {
      "command": "D:\\workspace\\HFSS\\em-copilot\\.venv\\Scripts\\ansys-aedt-mcp.exe",
      "args": ["--include-context"],
      "env": {"HTTP_PROXY": "", "HTTPS_PROXY": "", "NO_PROXY": "*"}
    }
  }
}
```

- `--include-context`: enables `get_guidelines_for`.
- No `--connect`: the skill workflow connects explicitly, preserving session elasticity.
- No `--dynamic-tool-discovery`: static full surface for planning; connection gating is the skill's responsibility.
- **Proxy env must be cleared** in the MCP server subprocess or stdio is cut by the enterprise proxy.

## 7. Failure handling

- On any tool error: stop the automation sequence, summarize the failure, load `error-recovery.md` if relevant, propose the smallest safe recovery.
- Do not blindly retry the same call after a failure.
- License/orphan/timeout/mesh/port failures each have a specific recovery path — see `error-recovery.md`.
