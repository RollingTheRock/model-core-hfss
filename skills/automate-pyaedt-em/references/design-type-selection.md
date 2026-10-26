# Design Type Selection

> Roadmap §5, §9. Pick the AEDT design type before opening the project.
> Use this reference at Workflow step 2.

## 1. Decision matrix

| Structure | Design type | Why |
|---|---|---|
| 3D passive with wave/lumped ports, arbitrary 3D geometry (connector, via field, antenna, cavity) | **HFSS** (3D, FEM) | Full-wave 3D, adaptive meshing, wave ports |
| PCB stackup + traces, planar/layered, many nets, cutouts | **HFSS 3D Layout** | Layered stackup native, efficient for planar SI/PI |
| Resistance / inductance / capacitance extraction (R/L/C, low-freq or DC) | **Q3D Extractor** | Quasi-static, fast parasitic extraction |
| S-parameter cascade, eye diagram, circuit-level channel | **Circuit** | S-parameter blocks, transient/harmonic balance, eye |

## 2. Selection rules

1. Is the structure **planar/layered** (PCB, package) with nets and stackup? → HFSS 3D Layout. It handles stackup, nets, and cutouts far more efficiently than 3D HFSS for channel extraction.
2. Is it a **3D passive** with ports needing full-wave solve (connector, via field, antenna, cavity, filter)? → HFSS.
3. Is the objective **R/L/C extraction** at low frequency or DC (bondwire inductance, plane resistance)? → Q3D.
4. Is the objective **channel-level cascade / eye / transient** from existing S-parameters? → Circuit. (Often the downstream stage after HFSS/HFSS 3D Layout produces Touchstone.)

### Mixed flows

A PCB channel job may use HFSS 3D Layout for the cutout solve, then Circuit for the cascade/eye. A connector job uses HFSS for the 3D sweep, then Circuit for the channel cascade. State which stage you are in.

## 3. Solver/physics sanity

- Confirm the solver's frequency range matches the physics: Q3D is quasi-static — only valid when the structure is electrically small at the top frequency. Do not use Q3D for a structure approaching λ/10 at f_max.
- For HFSS, confirm the port type matches the structure: wave ports for guided/TEM-like cross-sections; lumped ports for small localized feeds. See `boundaries-and-ports.md`.
- Confirm material models are valid across the requested frequency range (dispersion, loss tangent). Flag out-of-range use.

## 4. Inspection-only path

If the user only wants to inspect a closed `.aedt` (setup, ports, materials, mesh stats) **without solving**, PyEDB can read the EDB portion without a solve license in some cases. For a full solve, a license is required. State which path you are on and whether a license will be consumed.
