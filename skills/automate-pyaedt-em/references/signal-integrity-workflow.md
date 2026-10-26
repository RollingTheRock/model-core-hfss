# Signal Integrity Workflow

> Roadmap §9 / §12. The **HFSS-license-free** path: `em_copilot` Touchstone → mixed-mode → TDR → acceptance → report.
> Use this reference at Workflow step 3 when task type = Touchstone / SI analysis.
> This workflow does **not** require AEDT; it runs on a `.sNp` file alone.

## 1. Objective

From a measured or simulated Touchstone file, compute mixed-mode S-parameters (Sdd/Scc/Scd/Sdc), TDR impedance, crosstalk (NEXT/FEXT), and acceptance against rules — all deterministically, then render a report with numbers the LLM never modifies.

## 2. Inputs

- Touchstone `.sNp` file (N = 2, 4, 8, …). N must be even and ≥ 2× number of differential pairs.
- Port mapping: which physical port index is positive/negative of each pair, and the reference impedance (default 50 Ω).
- Data rate (for eye/TDR time scale) and points of interest (frequencies to snapshot).
- Acceptance rules: expression (Sdd11/Sdd21/Sdc21/Scd21/Scc11/Scc21/NEXT/FEXT/IL_margin/RL_margin), operator, value, frequency.
- TDR max time (how far to plot impedance vs. time).

## 3. Steps (all `em_copilot`, license-free)

1. **Parse**: `read_touchstone(path)` → `Touchstone` (freqs, s[Nfreq,N,N], z0, format). Flat-token parser; handles MA/RI/DB, inf/nan.
2. **Preflight**: `check_port_and_freq(ts)` — verify N is even and ≥ 2×pairs, frequency monotonic, no all-zero rows.
3. **Mixed-mode transform**: `to_mixed_mode(ts, port_mapping)` → `MixedMode` with `s_mm` and the Sdd/Scc/Scd/Sdc quadrants. Uses the orthonormal mode matrix T (d row +1/√2 at p, −1/√2 at q; c row +1/√2 at both).
4. **Metrics**: `compute_metrics(mm, pair_index, il_limit_db, rl_limit_db)` → `SIMetrics` with full-band Sdd11/Sdd21/Scc11/Scc21/Scd/Sdc, plus NEXT/FEXT when n_pairs ≥ 3. `worst_case(metrics)` picks the pass/fail-critical value per metric (max for Sdd11/Scd/Sdc, min for Sdd21).
5. **TDR**: `compute_tdr(s21, freqs, z0, max_time)` — inverse-FFT step response: Hann window, DC-extended Hermitian spectrum, `h_step[1:] = h_win[1:]/(1j·ω[1:])`, `ρ = (1−step)/(1+step)`, `z_tdr = z0·(1+ρ)/(1−ρ)`.
6. **Acceptance**: `evaluate_acceptance(metrics, rules)` — maps each rule to its metric (Sdd21 → `Sdd21_dB`, NEXT/FEXT take max across aggressors), returns pass/fail per rule.
7. **Snapshot POIs**: for each frequency of interest, `SIMetrics.at_frequency(f)` captures the metric values at that point.
8. **Report**: `build_report_data(...)` → `render_markdown(data, out_dir)` writes `report.md` + `metrics.json` + `plots/`. The report has 9 sections; "computed results" and "AI suggestions" are clearly separated; `_hash_file` records the input hash.

## 4. Numerical discipline (roadmap §13.3)

- All numbers in the report come from the deterministic code above.
- The LLM only writes explanations, trend descriptions, and recommendations.
- The LLM **must not** round, interpolate, or replace any computed value.
- `metrics.json` is the machine-readable form of the same data shown in `report.md`.

## 5. Mixed-mode reference

| Quadrant | Meaning | Critical metric |
|---|---|---|
| Sdd | differential-in, differential-out | Sdd21 (IL), Sdd11 (RL) |
| Scc | common-in, common-out | Scc11, Scc21 |
| Scd | common-in, differential-out | mode conversion (symmetry) |
| Sdc | differential-in, common-out | mode conversion (symmetry) |

NEXT and FEXT (crosstalk between pairs) are only defined when there are ≥ 2 differential pairs (n_pairs ≥ 2; full NEXT/FEXT metrics require ≥ 3 ports per side). `compute_metrics` handles the presence/absence.

## 6. Acceptance-rule grammar

```
expression ∈ {Sdd11, Sdd21, Sdc21, Scd21, Scc11, Scc21, NEXT, FEXT, IL_margin, RL_margin}
operator   ∈ {>=, <=, >, <, ==}
value      : float (dB for S-params, Ω for TDR, etc.)
frequency  : Hz (supports GHz/MHz/kHz/Hz suffix in FrequencySpec.parse)
```

`IL_margin` / `RL_margin` are computed margins against a limit line, not raw S-parameters.

## 7. Failure / odd-data handling

- Non-monotonic frequency → `check_port_and_freq` flags; do not proceed.
- `inf`/`nan` in Touchstone → `_parse_number` captures; flag and stop.
- Port count inconsistent with port mapping → stop, ask user.
- DC point absent → TDR still computed via DC-extended Hermitian spectrum, but flag the extrapolation.
