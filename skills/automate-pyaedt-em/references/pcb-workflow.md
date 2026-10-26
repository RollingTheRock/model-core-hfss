# PCB Channel Extraction Workflow

> Roadmap §9 / §10. HFSS 3D Layout primary; HFSS for 3D via fields; Circuit for cascade.
> Use this reference at Workflow step 3 when task type = PCB channel extraction.

## 1. Objective

Extract an S-parameter model of a PCB channel (differential pair, single-ended net, or power/ground pair) from a layout, with correct ports, a sensible cutout, and a converged mesh — then post-process with `em_copilot` (license-free).

## 2. Inputs

- Layout source: `.aedb`/EDB, `.brd`, ODB++, or an existing `.aedt` with HFSS 3D Layout design.
- Stackup: layer order, dielectric ε_r/tanδ/thickness, conductor thickness/roughness.
- Nets of interest: positive/negative of the differential pair (or single-ended net), reference nets (ground).
- Frequency range and step.
- Reference impedance (usually 50 Ω single-ended, 100 Ω differential).

## 3. Steps

1. **Load a copy** of the layout into HFSS 3D Layout via `open_project` (copy-before-run; the input layout is read-only).
2. **Confirm stackup**: use `get_model_info` / `get_guidelines_for("geometry")` to list layers and materials. Flag missing or default materials.
3. **Define nets**: identify positive/negative nets and reference conductor(s). Validate that the reference is a true reference (ground plane / return path), not a signal net.
4. **Cutout**: restrict the simulation to the nets of interest plus a keep-in region of reference copper. A too-small cutout changes the return path and corrupts S-parameters; a too-large one wastes mesh. Rule of thumb: ≥ 3× dielectric thickness of reference copper on either side of the signal.
5. **Ports**: assign wave or wave-ported ports on the trace cross-sections at the channel ends (see `boundaries-and-ports.md`). For a differential pair, assign two single-ended ports then combine to mixed mode in post-processing, or define a differential port directly.
6. **Mesh & solve**: set adaptive passes, ΔS, and frequency sweep (see `mesh-and-convergence.md`). Run a **smoke solve** (2–3 passes, coarse sweep) first via the Job model.
7. **Collect**: `collect_analysis_results` / `export_results` → Touchstone `.sNp`, convergence, profile, mesh.
8. **Post-process** with `em_copilot`: `read_touchstone` → `to_mixed_mode` → `compute_metrics` → `compute_tdr` → `evaluate_acceptance` → `render_markdown`. This is license-free and CI-able.
9. **Report**: report.md + metrics.json + plots. Numbers are from deterministic code; the LLM only explains them.

## 4. Common pitfalls

- **Return-path truncation**: cutout removes return copper → Sdd21 degrades artificially. Verify reference continuity.
- **Port reference wrong**: wave port reference conductor mis-assigned → mode conversion appears where there is none. Confirm reference.
- **No DC point**: sweep starts above 0 → TDR is unreliable (needs DC-extended spectrum). Start at 0 Hz or let `em_copilot` DC-extend.
- **Material model out of range**: dielectric tanδ given as a single number but used past its measured band. Flag.
- **Roughness**: copper roughness (Huray/Groisse) models change IL meaningfully above ~5 GHz; confirm whether the model is on and which parameters.

## 5. Acceptance (typical, adjust per task)

- IL (Sdd21) ≥ target at f_max.
- RL (Sdd11) ≤ target across band.
- NEXT/FEXT within budget (if multi-pair).
- TDR impedance within ±X% of target over the relevant length.
- Mode conversion (Sdc21) below threshold (differential symmetry).
