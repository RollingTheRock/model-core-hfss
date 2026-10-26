# Mesh and Convergence

> Roadmap §5 / §8.1. Adaptive meshing convergence criteria and what "converged" really means.
> Use this reference whenever you set up a solve or judge whether results are trustworthy.

## 1. Adaptive meshing in HFSS

HFSS refines the tetrahedral mesh adaptively: each pass solves, estimates where the field error is largest, refines there, and re-solves. "Convergence" is when the change between passes falls below a threshold.

Two independent convergence criteria:

- **ΔS**: maximum change in any S-matrix entry between consecutive passes. Default 0.02 (i.e., Δ|S| < 0.02); tighter (0.01) for SI work.
- **Energy error**: the field-energy-based error estimate per tetrahedron. A converged solve should have low global energy error, not just a small ΔS.

**A single ΔS below threshold is NOT convergence.** Require ≥ 2 consecutive passes under threshold AND a low energy error before trusting the S-parameters. Require the mesh to have stabilized (the last pass's refinement should be small).

## 2. Settings

| Parameter | Typical SI | Notes |
|---|---|---|
| Max passes | 12–20 | More for connectors / sharp features; cap to bound cost. |
| ΔS | 0.01 | 0.02 is the default but loose for SI. |
| Min passes | 2–3 | Prevents premature "convergence" on a coarse mesh. |
| Energy error | < 1% target | Confirm low at the final pass. |
| Mesh linkage | per design | Link mesh across swept design points only if the geometry is truly comparable. |

## 3. Frequency sweep types

| Sweep | Use | Cost | Accuracy |
|---|---|---|---|
| Discrete | sparse points, each a full solve | high per point | exact at each point |
| Fast (ALPS) | broad band, interpolated | lower | good for smooth responses |
| Interpolating | broad band | lower | good default for SI sweeps |

For TDR, you need a **dense, low-frequency-inclusive** sweep (the time-domain resolution needs DC + dense low-frequency points). A coarse fast sweep with no DC point gives a noisy, unreliable TDR.

## 4. Mesh density & geometry

- **Edge/sliver geometry** (thin metals, sharp pin corners) → sliver tets, slow solve, noisy S-parameters. Use mesh operations or geometry cleanup.
- **Multi-scale structure** (connector pin + large PCB region) → mesh must resolve both; expect many tets.
- **Thin layers** (dielectric, metal thickness) → use layered mesh / sheet models where appropriate rather than volume-meshing a 17 µm copper layer.

## 5. What can go wrong

- **Premature convergence**: ΔS passes threshold on a coarse mesh. Use min passes + check energy error + visually confirm mesh in critical regions.
- **No convergence within max passes**: ΔS never under threshold. Raise max passes, or inspect geometry for a meshing problem (sliver, near-degenerate tet).
- **Converged but wrong**: mesh converged but ports are wrong → S-parameters are stable and wrong. Convergence validates the mesh, not the setup. Always pair with port validation (see `boundaries-and-ports.md`).
- **Sweep too coarse for TDR**: TDR needs dense DC-inclusive data; a coarse fast sweep gives noisy impedance.
- **Linked mesh across unlike geometries**: in a parametric sweep, linking the mesh between design points whose geometry changed significantly gives bad results.

## 6. Validation

After a solve, before trusting results:

1. Confirm ≥ 2 consecutive passes under ΔS.
2. Confirm energy error is low at the final pass.
3. Inspect the mesh in the critical region (port, contact, via field) — `screenshot` + `get_guidelines_for("mesh")`.
4. Confirm the S-parameters are physically plausible at DC / low frequency (through → Sdd21 ≈ 0 dB; matched port → Sdd11 ≪ 0 dB).
5. If any of the above fails, do not report the result; refine mesh / fix setup / ask the user.
