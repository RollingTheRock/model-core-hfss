# Validation Gates

> Roadmap §7.5 / §14.2. Hard stops that must pause and ask the user before proceeding.
> These are non-negotiable; violating them risks wrong physics, wasted license time, or data loss.

## 1. L0–L4 permission gates (roadmap §14.2)

| Level | Tools / actions | Who | Gate |
|---|---|---|---|
| L0 — inspect | `check_aedt_*`, `get_pyaedt_logs`, `list_projects`/`list_designs`/`get_model_info`, `screenshot` (read-only) | all users | no extra gate; read-only |
| L1 — export | `export_results`, `export_config` (non-mutating export) | all users | confirm output path is not the input dir |
| L2 — modify-copy | modify the **copy** (setup, ports, mesh, variables) | all users | confirm modification plan before applying |
| L3 — solve | `analyze_design` / `submit_analysis` (long solve) | all users, but **explicit confirmation required** | state license + estimated cost; get yes/no |
| L4 — custom code | `run_python_code` / `run_python_script` | admin/trusted-dev only | show snippet; never the default business path |

Normal users get L0–L2 by default. L3 requires explicit per-task confirmation. L4 is never auto-invoked.

## 2. Pre-solve gates (must all pass before `submit_analysis`)

Before submitting any solve, all of the following must be true:

1. **SimulationTask is `APPROVED`** — the draft passed validation, the user reviewed the Preflight report, and approved. Not `DRAFT`, not `VALIDATING`, not `NEEDS_INPUT`.
2. **Copy-before-run done** — the input project was copied to the task run dir; the input is untouched; the input hash is recorded.
3. **Ports validated** — every port has a confirmed type, reference conductor, and plausible sizing (see `boundaries-and-ports.md`).
4. **Materials complete** — no "default" or placeholder materials in the signal path; dielectric/conductor properties defined and valid over the sweep band.
5. **Mesh plan sane** — max passes, ΔS, and energy-error target set; no known sliver/degenerate geometry unaddressed.
6. **Frequency range valid** — within the material-model validity band; DC point present if TDR is required.
7. **Output path confirmed** — outputs go to the run dir, not the input dir.
8. **License risk stated** — the user knows AEDT will start and a license will be held for the duration.

If any gate fails → mark the task back to `NEEDS_INPUT` or `REJECTED`; do not solve.

## 3. Mid-solve gates (must pause if triggered)

- **Cost surprise**: if the smoke solve reveals the full solve will be dramatically more expensive than the Preflight estimate (e.g., 10× the tets), pause and reconfirm with the user before the full solve.
- **Unphysical smoke result**: if the smoke solve's S-parameters are unphysical (Sdd21 > 0 dB through, Sdd11 = 0 dB matched), stop — the setup is wrong, more solves won't fix it.
- **Non-convergence on smoke**: if even the smoke solve doesn't converge, fix the mesh/geometry before the full run.

## 4. Post-solve gates (must hold before reporting "done")

Before declaring a task `COMPLETED`:

1. **Convergence confirmed** — ≥ 2 consecutive passes under ΔS, low energy error (see `mesh-and-convergence.md`).
2. **Results physically plausible** — DC/low-f S-parameters match expectations; no NaN/Inf in the Touchstone.
3. **Artifacts collected** — Touchstone, convergence, profile, mesh all exported to the run dir.
4. **Deterministic post-processing done** — `em_copilot` produced `metrics.json` + `report.md` + plots; numbers unchanged from the compute path.
5. **Session released** — `disconnect_from_aedt` called (unless user wants it open); no orphan process.

If any gate fails → mark `FAILED` or `PARTIAL_SUCCESS` with the reason; do not declare success.

## 5. Hard stops (stop and ask, always)

- Any write-side action on the input project (not the copy) → STOP.
- Any output written back to the input directory → STOP.
- Any L4 custom code invoked as the default business path → STOP and use the Job model instead.
- Any LLM modification of a computed number in the report → STOP.
- Any geometry/stackup/material sent to an external model service → STOP (sensitive data stays intranet).
- Any solve launched without explicit L3 confirmation → STOP.
