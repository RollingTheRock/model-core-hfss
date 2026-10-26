# Connector Sweep Workflow

> Roadmap §9 / §11. HFSS primary (3D full-wave). Circuit for downstream cascade.
> Use this reference at Workflow step 3 when task type = connector sweep.

## 1. Objective

Sweep a connector (board-to-board, cable-to-board, socket) across geometry parameters (gap, length, contact depth, pin pattern, pad size) and characterize S-parameters / mode conversion / crosstalk, with a converged mesh and consistent port definitions across runs.

## 2. Inputs

- Geometry: native `.aedt` with a parametric connector model, or a CAD import (step) + parametrization in HFSS.
- Pin map: which pins are signal, which are ground/reference, differential pair assignments.
- Materials: contact alloy, plating, dielectric body ε_r/tanδ.
- Sweep variables: parameter name(s), values, and whether a parametric sweep or an optimization is required.
- Frequency range and step.
- Reference impedance.

## 3. Steps

1. **Load a copy** of the connector project via `open_project` (copy-before-run).
2. **Confirm geometry & materials** with `get_model_info` / `get_guidelines_for("geometry")`. Verify the contact gap and pin geometry are parameterized where intended.
3. **Pin map → ports**: assign ports to the signal pins (wave ports on the pin cross-section, or lumped ports across the contact gap). Confirm reference conductor for each port (the nearest ground pin / reference plane).
4. **Mesh**: connectors are electrically small but geometrically complex — the contact region needs a fine mesh. Set max passes and ΔS (see `mesh-and-convergence.md`). Expect more passes than a PCB cutout.
5. **Sweep setup**: parametric sweep over the geometry variables. Each design point is a separate solve; use the Job model so each is submittable/cancelable.
6. **Smoke solve first**: one design point, 2–3 passes, coarse sweep — to validate port directions and symmetry before launching the full sweep.
7. **Submit full sweep** via `submit_analysis` (Job model); poll `get_analysis_status`, stream `get_analysis_log`. Support per-point cancel and orphan detection.
8. **Collect** each design point's Touchstone + convergence + profile + mesh via `collect_analysis_results` / `export_results`.
9. **Post-process** with `em_copilot`: for each design point, `to_mixed_mode` → `compute_metrics` → `evaluate_acceptance`; aggregate across the sweep (worst-case per metric, sensitivity to parameter).
10. **Report**: report.md + metrics.json + plots, with the sweep parameter as the independent axis. Numbers from deterministic code.

## 4. Common pitfalls

- **Port reference mis-assigned**: a signal pin's reference is the nearest ground pin, not a far plane. Wrong reference → wrong Sdd21 and spurious mode conversion.
- **Contact mesh too coarse**: the gap region dominates impedance; if the mesh there is coarse, results don't converge. Check the mesh in the contact region specifically.
- **Inconsistent ports across sweep**: if a parametric change moves the port location, comparing design points is invalid. Fix port reference geometry parametrically.
- **Over-tight ΔS**: connector solves are small but each pass is costly; an over-tight ΔS wastes passes without changing the result. Use ΔS = 0.01 as a starting point.
- **Single design point treated as "the answer"**: a connector is swept precisely because single-point numbers are misleading. Always report worst-case across the sweep.

## 5. Acceptance (typical)

- IL within budget at all design points (worst-case reported).
- RL below threshold across band at all design points.
- Mode conversion below symmetry threshold.
- Sensitivity: the IL vs. parameter curve slope matches physical expectation (e.g., larger gap → more IL if capacitive coupling dominates).
