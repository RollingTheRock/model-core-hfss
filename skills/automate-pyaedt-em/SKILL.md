---
name: automate-pyaedt-em
description: Guide Ansys HFSS / AEDT electromagnetic simulation workflows with the official ansys-aedt-mcp tool-use discipline and the em-copilot domain layer. Use when Claude Code works on HFSS, PyAEDT, PyEDB, AEDT, PCB EM simulation, connector simulation, signal integrity, S-parameters, Touchstone .sNp files, mixed-mode conversion, TDR, crosstalk, mode conversion, wave/lumped ports, adaptive meshing, convergence, boundary conditions, simulation reports, or validation of Ansys HFSS results.
---

# Automate PyAEDT EM

Use this skill as the workflow and judgment layer for Ansys HFSS / AEDT electromagnetic simulation work. Keep the PyAEDT/AEDT documentation and the roadmap (`pyaedt-em-copilot-technical-roadmap.md`) as the fact source, use the local `ansys-aedt-mcp` MCP server (the official Ansys PyAEDT-MCP, used **as-is, unmodified**) as the execution layer for live AEDT operations, use the `em_copilot` domain package for the HFSS-license-free Touchstone/SI analysis and for the Job/session/file-safety domain layer, and use this skill to decide the order of operations, checks, and validation criteria.

> Tool names below are the short names exposed by the official `ansys-aedt-mcp` server (v0.1.4) and, where marked, by the `em_copilot` domain extension. In Claude Code they appear as `mcp__pyaedt-em__<tool>` (the `pyaedt-em` prefix is set by the MCP server name in `.mcp.json`); the model resolves the prefix automatically, so the skill text uses the short names.
>
> **Namespace discipline (roadmap §2.2 / §8.2)**: the official `ansys.aedt.mcp` namespace is used as-is and is **never** occupied or modified. All enterprise extensions live in the independent `em_copilot` / `em-copilot-mcp` namespace. Forbidden package structures: `src/ansys/aedt/mcp/`, `ansys-pyaedt-mcp`, `ansys-aedt-mcp` (as our own package name).

## Operating Rules

- Define the physics before touching the solver: objective quantity (insertion loss, return loss, crosstalk, TDR, mode conversion), design type, geometry/scale, materials/stackup, frequency range, port definitions, solver type, and expected validation evidence.
- Do not let the Agent directly assemble large blocks of low-level AEDT API parameters. Generate a structured `SimulationTask` first, validate it, and get human confirmation before any solve (roadmap §6, §7.5).
- Do not treat a single residual or ΔS value as convergence. Require adaptive pass stabilization, energy-error criteria, and port/quantity sanity checks for the metrics that matter.
- Do not skip port confirmation, reference-conductor checks, material completeness, and frequency-vs-material-model range checks.
- Original input projects are **read-only**. Every task works on a **copy** (copy-before-run); outputs are never written back to the input directory; file hashes are recorded (roadmap §14.1).
- Numerical discipline (roadmap §13.3): all report numbers come from deterministic `em_copilot` code; the LLM only generates explanations/summaries and **must never modify computed numbers**; reports clearly separate "computed results" from "AI suggestions"; `metrics.json` is the machine-readable form of the same data.
- Before launching AEDT or consuming a license, state that the action may start AEDT and may occupy a license session. Long solves go through the Job model (submit → status → log → collect), never a multi-hour blocking call (roadmap §4.1).
- `run_python_code` / `run_python_script` (official MCP scripting tools) are **admin-diagnostic only (L4)**, never the default business path. Normal users get L0–L2 only; long solves (L3) need explicit confirmation (roadmap §14.2).
- End live AEDT sessions with `disconnect_from_aedt` when the task is complete unless the user asks to keep the session open; confirm the AEDT PID and license are released and no orphan process remains (roadmap §16.4).

## Workflow

1. Frame the case: identify task type (existing-project inspection, Touchstone/SI analysis, PCB channel extraction, connector sweep), objective metrics, known inputs, missing data, expected outputs, and acceptance criteria. Load `references/task-intake.md`.
2. Choose design/solver type: decide HFSS vs HFSS 3D Layout vs Q3D vs Circuit based on the physical structure. Load `references/design-type-selection.md`.
3. Check the input and setup basis: for an AEDT project — Design, Setup, Sweep, ports, variables, materials; for a Touchstone file — port count, frequency range, monotonicity, valid values. Load the relevant workflow reference (`pcb-workflow.md` / `connector-workflow.md` / `signal-integrity-workflow.md`).
4. Generate a structured `SimulationTask` draft, validate it against engineering rules, and list assumptions + items needing confirmation. **Do not solve yet.**
5. Produce a Preflight report (version, input hash, Design/Setup/port/material summary, modification plan, risks) and get human approval before any write-side or solve operation.
6. Copy the project (copy-before-run) and apply any modifications to the copy only.
7. Run a smoke solve first (2–3 adaptive passes or a short sweep) to catch setup errors before a full solve.
8. Submit the full solve as a Job (`submit_analysis` → poll `get_analysis_status` → stream `get_analysis_log`); support timeout, cancel, and orphan detection.
9. Collect results (`collect_analysis_results` / `export_results`): Touchstone, convergence, profile, mesh.
10. Post-process deterministically with `em_copilot` (mixed-mode, TDR, acceptance, worst-case) and generate the report (Markdown/HTML + `metrics.json` + plots).
11. Report validation status, remaining risks, and next checks; save the case/data to a **new** file, not over the input.

## Reference Loading

Load only the reference files needed for the current task:

- `references/task-intake.md`: general input checklist and natural-language task examples.
- `references/design-type-selection.md`: HFSS / HFSS 3D Layout / Q3D / Circuit selection rules.
- `references/pcb-workflow.md`: stackup, nets, cutout, ports, and solve rules for PCB channel extraction.
- `references/connector-workflow.md`: pin map, materials, contact, ports, and mesh rules for connector sweeps.
- `references/signal-integrity-workflow.md`: mixed-mode, TDR, cascading, eye diagram, and acceptance metrics.
- `references/boundaries-and-ports.md`: wave ports, lumped ports, terminals, reference conductors, and boundary rules.
- `references/mesh-and-convergence.md`: adaptive meshing, ΔS, energy error, and convergence criteria.
- `references/error-recovery.md`: license, orphan process, timeout, mesh failure, and port error recovery.
- `references/validation-gates.md`: conditions that must stop and ask the user.
- `references/pyaedt-mcp.md`: official `ansys-aedt-mcp` tools, `em_copilot` Job/session tools, call order, safety limits, and failure handling.
- `references/existing-project-one-shot.md`: for an already-built complete `.aedt` project, the "one sentence → solve → export → report" orchestration (dynamic setup/sweep discovery, single-vs-differential report by port count, multi-point sweep export to avoid the LastAdaptive trap).

## MCP Tool Discipline

When a task requires real AEDT execution through the local `pyaedt-em` MCP server (official `ansys-aedt-mcp` 0.1.4):

1. Call `check_aedt_installed` first to confirm the AEDT binary is on disk (offline, no license).
2. Call `check_aedt_status` to see whether this MCP already has a live AEDT connection and to discover local AEDT sessions that can be attached. This tool reads the MCP lifespan context, so it must run over a real stdio/http MCP session — it cannot be smoke-tested by calling the function in-process.
3. If multiple connectable sessions are reported, ask the user which to connect to, with the option to open a new instance. If one is reported, ask whether to attach or open new. Only use `launch_aedt` after the user explicitly confirms a new instance is needed.
4. Connect with `connect_to_aedt` (attach to a running session by machine + gRPC port) or `launch_aedt` (new instance). State that this may start AEDT and consume a license before calling either.
5. Use `list_projects` / `list_designs` / `get_model_info` to inspect the loaded project; use `open_project` to load a `.aedt/.aedtz` **copy** (never the read-only input). Use `export_config` to persist the setup/sweep config for reproducibility.
6. For guidelines before generating PyAEDT code or invoking a workflow tool, call `get_guidelines_for` (only registered when the server is launched with `--include-context`). It returns authoritative scripting patterns for the requested topic (workflow, hfss, maxwell, icepak, circuit, geometry, mesh, boundaries, postprocessing, parametric).
7. For a smoke solve or longer run: `analyze_design` (async, `blocking=False`) → poll via the `em_copilot` Job model (`submit_analysis` → `get_analysis_status` → `get_analysis_log`) → `collect_analysis_results` / `export_results`. Prefer a short smoke run before long runs.
8. Use `get_pyaedt_logs` after suspicious behavior or long operations to retrieve recent log lines.
9. Use `save_project` to persist the **copy** (never the input) when state should be preserved; save to a new file, not over the input case.
10. Call `disconnect_from_aedt` at the end unless the user explicitly wants the session left open; confirm the AEDT PID and license are released.

`run_python_code` / `run_python_script` are L4 admin-diagnostic tools. They share a persistent Python session (variables/imports persist across calls). Treat them as privileged: keep snippets short, explicit, and reversible, and show them to the user before running write-side code. They are **never** the default business path (roadmap §8.4).

If a tool returns an error, stop the automation sequence, summarize the failure, read `references/error-recovery.md` if relevant, and propose the smallest safe recovery. Do not blindly retry the same call after a failure.
