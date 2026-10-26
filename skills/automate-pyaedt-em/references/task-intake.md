# Task Intake

> Roadmap §9 (task routing), §6.2 (SimulationTask), §7.5 (validation-before-solve).
> Use this reference at Workflow step 1 (frame the case).

## 1. Task routing

Route the incoming request to one of four task types before doing anything else. The routing determines which workflow reference to load.

| Task type | Trigger phrases | Workflow reference | Needs HFSS license? |
|---|---|---|---|
| Existing-project inspection | "看这个工程", "inspect", "这个 setup 对不对", "端口/材料/网格" | `design-type-selection.md` + inspect path | yes (to open/solve) — but read-only inspection of a closed `.aedt` may use PyEDB without a solve license |
| Touchstone / SI analysis | ".s2p", ".s4p", "Touchstone", "mixed-mode", "TDR", "crosstalk", "IL/RL" | `signal-integrity-workflow.md` | **no** — `em_copilot` post-processing is license-free |
| PCB channel extraction | "PCB", "叠层", "net", "cutout", "走线", "channel" | `pcb-workflow.md` | yes |
| Connector sweep | "连接器", "connector", "pin map", "接触", "参数扫描" | `connector-workflow.md` | yes |

If the request does not fit one of these, ask the user to clarify objective and inputs before proceeding. Do not guess a solver.

## 2. Minimum case definition

Every task must establish, before any solve or report:

- **Objective quantity**: insertion loss, return loss, crosstalk (NEXT/FEXT), TDR impedance, mode conversion (Sdc/Scd), etc.
- **Design type**: HFSS / HFSS 3D Layout / Q3D / Circuit (see `design-type-selection.md`).
- **Geometry & scale**: structure description, critical dimensions, units.
- **Materials / stackup**: dielectric ε_r/tanδ, conductor, thickness; or confirm materials are already defined in the project.
- **Frequency range**: start, stop, step (or sweep type); confirm against material-model validity range.
- **Port definitions**: port type (wave/lumped), reference conductor, terminal pairings, reference impedance.
- **Solver type & convergence target**: adaptive passes, ΔS, energy error (see `mesh-and-convergence.md`).
- **Expected validation evidence**: what result artifacts will prove the objective was met.

If any item is missing and cannot be inferred safely, mark the task `NEEDS_INPUT` and ask the user. Do not fabricate defaults for physics-critical fields (materials, port references, frequency range).

## 3. Natural-language task examples

- "分析这个 .s4p，算差分对 Sdd21、TDR，IL 限 -10 dB @ 5 GHz" → Touchstone/SI analysis, license-free, `signal-integrity-workflow.md`.
- "把这个 PCB 工程的 DDR 走线切出来，加 wave port，跑 0–10 GHz" → PCB channel extraction, `pcb-workflow.md`.
- "这个连接器扫 4 个 gap 值，看 Sdd21 变化" → connector sweep, `connector-workflow.md`.
- "看下这个工程的 setup，端口和材料是否合理" → existing-project inspection, `design-type-selection.md`.

## 4. SimulationTask draft (roadmap §6.2)

Generate a structured `SimulationTask` after step 2–3, **before solving**. Fields (YAML form):

```yaml
task_id: <stable id>
design_type: hfss | hfss3d_layout | q3d | circuit
input:
  project_path: <.aedt/.aedtz or .sNp>   # read-only; copy-before-run
  design: <name>
  setup: <name>
  sweep: <name>
objective:
  quantities: [Sdd21, TDR, ...]
  acceptance:
    - expression: Sdd21
      operator: ">="
      value: -10
      frequency: 5e9
      unit: dB
ports:
  - name: <port>
    type: wave | lumped
    reference_conductor: <net/terminal>
    positive: <terminal>
    negative: <terminal>
frequency:
  start: 0
  stop: 10e9
  step: 10e6
mesh:
  max_passes: 12
  delta_s: 0.01
  energy_error: 0.01
post_processing:
  mixed_mode_pairs: [...]
  compute_tdr: true
  tdr_max_time: 5e-9
outputs:
  - touchstone
  - convergence
  - profile
  - report_md
  - metrics_json
```

Validate the draft against engineering rules (port count vs. port mapping, frequency vs. material model, ΔS vs. passes). List assumptions and items needing confirmation. Mark `VALIDATING → READY_FOR_REVIEW`. **Do not solve until approved.**

## 5. Preflight report (before any write/solve)

Before any write-side or solve operation, produce and get approval for:

- AEDT version, input file hash (file safety, roadmap §14.1).
- Design / Setup / Sweep / port / material summary.
- Modification plan (what changes, on the copy only).
- Risks (license, mesh, port, material-model out of range).
- Estimated solve cost (passes, frequency points, expected wall time class).

Long solves (L3) need explicit confirmation. `run_python_code` (L4) is admin-only.
