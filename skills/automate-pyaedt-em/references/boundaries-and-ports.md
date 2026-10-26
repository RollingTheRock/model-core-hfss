# Boundaries and Ports

> Roadmap §5 / §8.1 / §12. Port and boundary correctness is the single most common source of wrong S-parameters.
> Use this reference whenever you assign ports or boundary conditions.

## 1. Port types

| Port type | Use when | Reference | Notes |
|---|---|---|---|
| **Wave port** | Guided / TEM-like cross-section at a boundary (coax, microstrip cross-section, connector pin field) | The port's reference conductor(s) on the same plane | Solves eigenmodes of the port cross-section; most accurate for guided structures. Port must be on a boundary, not internal. |
| **Lumped port** | Small localized feed where a wave port is impractical (chip pad, short via) | A reference conductor adjacent to the feed | Excites a lumped field; less accurate at high frequencies or for electrically large ports. Simpler to set up. |
| **Gap/Floquet** | Periodic structures, unit cells | Periodic boundary | Not for typical channel work. |

## 2. Reference conductor

The reference conductor is the return path for the port. Getting it wrong produces wrong impedance, wrong Sdd21, and spurious mode conversion.

- For a **differential pair**: reference is the ground plane / nearest ground net, shared by both polarities. Each single-ended port references the same return.
- For a **connector pin**: reference is the nearest ground pin or ground plane, not a distant structure.
- For a **PCB trace**: reference is the plane layer directly above/below the trace in the stackup.

Always confirm the reference conductor with `get_model_info` / `get_guidelines_for("boundaries")` before solving. Flag any port whose reference is ambiguous or shared with a signal net.

## 3. Differential vs. single-ended ports

Two valid approaches for a differential pair:

1. **Two single-ended ports + post-processing**: assign port 1 to P, port 2 to N (each referencing the shared ground). Mixed-mode transform in `em_copilot.to_mixed_mode` combines them. **Preferred** — gives both single-ended and differential data, and is what `em_copilot` expects.
2. **One differential port**: define a differential port directly in HFSS. Simpler in the solver, but loses single-ended data and the port mapping must be conveyed to `em_copilot` separately.

For the `em_copilot` port mapping (`PortMapping`), approach 1 is natural: pairs of 1-based physical port indices, distinct, with a shared `reference_impedance`.

## 4. Wave-port sizing

A wave port's cross-section must be large enough to capture the fundamental mode but not so large it captures higher-order modes (which corrupt the S-parameters). Rules of thumb:

- **Microstrip**: port width ≈ 3× strip width (or ≥ 3× dielectric thickness of side copper); height to the reference plane.
- **Stripline**: port width ≈ 3× strip width; height to both reference planes.
- **Connector pin field**: port plane covers the signal pin and adjacent ground pins.
- **Coax**: port = the coax cross-section exactly.

Too small → the field is clipped, impedance wrong. Too large → higher-order modes appear at lower frequency. Use `get_guidelines_for("boundaries")` for the authoritative pattern.

## 5. Boundary conditions

| Boundary | Meaning | When |
|---|---|---|
| Perfect E (PEC) | perfect conductor | default for metal surfaces; also for symmetry planes when using E-symmetry |
| Radiation / ABC | open, absorbs outgoing waves | when the structure radiates (antenna, open boundary); not needed for a fully shielded channel |
| Perfect H (PMC) | perfect magnetic conductor | symmetry plane (rare for channel work) |
| Symmetry (E/H) | exploit structural symmetry to reduce model | when geometry is symmetric; halves the solve, but the port must respect the symmetry |

## 6. Common port errors

- **Port not on a boundary** → solver error or meaningless S-parameters.
- **Reference net is a signal net** → return path is wrong, Sdd21 corrupted.
- **Wave port too small** → impedance wrong (usually too high).
- **Wave port too large** → spurious modes, noisy Sdd21.
- **Inconsistent reference across ports** → differential data is asymmetric even when the geometry is symmetric → spurious mode conversion.
- **Lumped port used at high frequency / large size** → results diverge from wave-port truth; only use for genuinely small, localized feeds.

## 7. Validation before solve

- List all ports and their reference conductors; confirm each.
- Confirm port type matches the structure (wave for guided, lumped for small localized).
- Confirm port sizing against the rules of thumb.
- Confirm boundary conditions match the physics (radiation only if open, PEC for shielded).
- Run a smoke solve and check that Sdd11/Sdd21 at low frequency are physically plausible (e.g., DC Sdd21 near 0 dB for a short through, RL near 0 dB return for a matched port).
