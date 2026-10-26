"""Acceptance-rule evaluation and TDR (roadmap §6.2, §10.3, §10.4).

- :func:`evaluate_acceptance` turns each :class:`AcceptanceRule` into a
  pass/fail against the computed :class:`SIMetrics` — structured, not guessed
  from console text (§8.3).
- :func:`compute_tdr` is a dependency-light inverse-FFT TDR that operates on a
  *reflection* coefficient (S11 / Sdd11), not a transmission parameter. It uses
  :func:`numpy.fft.irfft` with an explicitly constructed Hermitian spectrum and
  a configurable DC-extrapolation strategy (I-04).
"""

from __future__ import annotations

import numpy as np
from numpy.fft import irfft

from .mixed_mode import SIMetrics
from .schemas import AcceptanceRule, DecibelSpec, FrequencySpec


def derive_limits_from_rules(
    rules: list[AcceptanceRule],
) -> tuple[float | None, float | None]:
    """Best-effort extraction of IL/RL limits from acceptance rules (ERR-009).

    The margin baselines must not be hardcoded: they should reflect the user's
    actual acceptance spec. A rule ``Sdd21 >= -12dB`` sets the insertion-loss
    floor (il_limit = -12); a rule ``Sdd11 <= -10dB`` sets the return-loss
    ceiling (rl_limit = -10). When no such rule exists the limit is ``None``
    and :func:`compute_metrics` skips that margin entirely rather than
    fabricating one from a legacy constant that contradicts the real spec.
    """
    il_limit: float | None = None
    rl_limit: float | None = None
    for rule in rules:
        try:
            val = DecibelSpec.parse(rule.value).db
        except ValueError:
            continue
        if rule.expression == "Sdd21" and rule.operator in (">=", ">"):
            if il_limit is None or val > il_limit:
                il_limit = val
        elif rule.expression == "Sdd11" and rule.operator in ("<=", "<"):
            if rl_limit is None or val < rl_limit:
                rl_limit = val
    return il_limit, rl_limit


def evaluate_acceptance(
    metrics: SIMetrics, rules: list[AcceptanceRule]
) -> list[dict]:
    """Evaluate each acceptance rule at its target frequency.

    Returns a list of result dicts, one per rule, in order.
    """
    results: list[dict] = []
    for rule in rules:
        try:
            f_hz = FrequencySpec.parse(rule.frequency).hz
        except ValueError as exc:
            results.append(
                {
                    "expression": rule.expression,
                    "frequency": rule.frequency,
                    "passed": False,
                    "error": f"cannot parse frequency: {exc}",
                }
            )
            continue

        snapshot = metrics.at_frequency(f_hz)

        # Frequency tolerance (ERR-010): if the nearest available frequency is
        # farther from the requested one than the tolerance, the rule cannot be
        # evaluated at the requested frequency — this is a configuration/data
        # error, not an electrical failure. Surface as ERROR so a report never
        # silently presents an off-grid bin as if it were the requested freq.
        if not snapshot.get("frequency_in_tolerance", True):
            results.append(
                {
                    "expression": rule.expression,
                    "frequency": rule.frequency,
                    "passed": False,
                    "error": (
                        f"requested frequency {snapshot['frequency_requested_hz']:.3e} Hz "
                        f"is not within tolerance of any data point "
                        f"(nearest {snapshot['frequency_actual_hz']:.3e} Hz, "
                        f"error {snapshot['frequency_error_hz']:.3e} Hz)"
                    ),
                }
            )
            continue

        # Acceptance-rule expressions use bare names (Sdd21, IL_margin, NEXT,
        # ...) while the snapshot keys carry a ``_dB`` suffix and, for
        # crosstalk, a pair-name infix (``NEXT_RX0_dB``). Map between them.
        direct_key = f"{rule.expression}_dB"
        if rule.expression in ("NEXT", "FEXT"):
            # Crosstalk: take the worst (max) across all aggressor pairs at
            # this frequency — higher dB = worse crosstalk.
            cands = {
                k: v for k, v in snapshot.items() if k.startswith(f"{rule.expression}_")
            }
            if not cands:
                results.append(
                    {
                        "expression": rule.expression,
                        "frequency": rule.frequency,
                        "passed": False,
                        "error": f"no {rule.expression} aggressors available",
                    }
                )
                continue
            measured = max(cands.values())
        elif direct_key in snapshot:
            measured = snapshot[direct_key]
        else:
            results.append(
                {
                    "expression": rule.expression,
                    "frequency": rule.frequency,
                    "passed": False,
                    "error": f"metric {rule.expression} not available",
                }
            )
            continue

        try:
            value_db = DecibelSpec.parse(rule.value).db
        except ValueError as exc:
            results.append(
                {
                    "expression": rule.expression,
                    "frequency": rule.frequency,
                    "passed": False,
                    "error": f"cannot parse value: {exc}",
                }
            )
            continue

        ops = {
            ">=": measured >= value_db,
            "<=": measured <= value_db,
            ">": measured > value_db,
            "<": measured < value_db,
            "==": np.isclose(measured, value_db),
        }
        passed = bool(ops[rule.operator])
        results.append(
            {
                "expression": rule.expression,
                "frequency": rule.frequency,
                "operator": rule.operator,
                "limit": rule.value,
                "measured_dB": round(float(measured), 4),
                "measured_freq_hz": snapshot["frequency_hz"],
                "passed": passed,
                "description": rule.description,
            }
        )
    return results


def _prepare_spectrum(
    freqs: np.ndarray, gamma: np.ndarray, dc_strategy: str, n_warn: list[str]
) -> tuple[np.ndarray, np.ndarray]:
    """Return a uniform positive-frequency spectrum that *includes DC*.

    :func:`numpy.fft.irfft` requires evenly spaced frequency bins starting at
    DC, so the data is resampled onto ``[0, df, 2*df, ..., M*df]``:

    - ``df`` is the native spacing when the input grid is already uniform,
      otherwise the median bin width (and the data is interpolated onto a
      uniform grid first — a warning is emitted).
    - The DC bin (``f=0``) is taken from the data when the sweep starts at 0;
      otherwise it is extrapolated per ``dc_strategy``.

    A warning is appended to ``n_warn`` whenever interpolation or DC
    extrapolation is required.
    """
    if freqs.size < 2:
        return freqs, gamma
    diffs = np.diff(freqs)
    uniform = np.allclose(diffs, diffs[0], rtol=1e-6, atol=1e-3 * abs(diffs[0]))
    f0 = float(freqs[0])
    fmax = float(freqs[-1])
    if uniform:
        df = float(diffs[0])
    else:
        n_warn.append(
            "frequencies are not on a uniform grid; TDR interpolated onto a "
            "uniform grid before irfft"
        )
        df = float(np.median(diffs))
    if df <= 0:
        df = (fmax - f0) / max(freqs.size - 1, 1) or 1.0

    # Uniform-from-DC target grid [0, df, 2df, ..., M*df] covering the sweep.
    n_bins = max(int(round(fmax / df)), 1)
    f_pos = np.arange(n_bins + 1) * df
    # Interpolate (complex, real/imag separately) onto the non-DC points.
    target = f_pos[1:]
    g_real = np.interp(target, freqs, gamma.real)
    g_imag = np.interp(target, freqs, gamma.imag)
    g_non_dc = g_real + 1j * g_imag

    # DC point: use the data when the sweep starts at 0, else extrapolate.
    if f0 == 0.0:
        dc = gamma[0]
    else:
        n_warn.append(
            f"spectrum does not include DC; TDR DC point extrapolated "
            f"({dc_strategy})"
        )
        if dc_strategy == "zero":
            dc = 0.0 + 0.0j
        elif dc_strategy == "linear" and gamma.size >= 2:
            g1 = gamma[0]
            g2 = gamma[1]
            dc_real = 2.0 * g1.real - g2.real
            dc_imag = 2.0 * g1.imag - g2.imag
            dc = dc_real + 1j * dc_imag
            mag = abs(dc)
            if mag > 1.0:  # keep the DC point passive
                dc = dc / mag
        else:  # "constant" or fallback
            dc = gamma[0]
    g_pos = np.concatenate(([dc], g_non_dc))
    return f_pos, g_pos


def compute_tdr(
    reflection: np.ndarray,
    freqs: np.ndarray,
    *,
    z0: float = 50.0,
    max_time: float = 2e-9,
    dc_extrapolation: str = "linear",
    window: str = "rect",
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Compute a step-response TDR impedance from a *reflection* coefficient.

    TDR impedance requires the reflection coefficient ρ(f) seen at the
    stimulus port (single-ended S11 or differential Sdd11), not a transmission
    parameter. Using S21/Sdd21 would yield a TDT (transmitted step), not a TDR
    (I-04).

    The spectrum is resampled onto a uniform-from-DC grid, DC-extended,
    windowed, and inverted with :func:`numpy.fft.irfft` so the time-domain
    result is real by construction. Impedance follows
    ``Z(t) = Z0 * (1 + ρ(t)) / (1 - ρ(t))``.

    Parameters
    ----------
    reflection : ndarray (n_freqs,), complex
        Reflection coefficient ρ(f) (e.g. S11 or Sdd11), magnitude ≤ 1 for a
        passive network.
    freqs : ndarray (n_freqs,), float
        Frequencies in Hz, strictly ascending.
    z0 : float
        Reference impedance in ohms.
    max_time : float
        Time window to return, in seconds.
    dc_extrapolation : str
        DC handling strategy: ``"constant"``, ``"linear"`` or ``"zero"``.
    window : str
        Window applied to reduce spectral leakage. ``"rect"`` (default and the
        only production-grade choice) leaves the spectrum untouched, which makes
        the analytic TDR limits exact (matched→Z0, open→+∞, short→0) and keeps a
        constant-load plateau flat. ``"one_sided"`` and ``"hann"`` are
        **experimental** — any non-rect window multiplies the reflection
        spectrum by a shape that varies with frequency, so for a constant load
        (flat ρ) the step response is no longer flat in time: the plateau is
        depressed/ringing even though the DC point is preserved (NEW-006). They
        are kept only for research/reproducibility and always emit an
        EXPERIMENTAL warning; do not use them for absolute-impedance compliance.

    Returns
    -------
    time : ndarray (n_t,)
        Time axis in seconds.
    z_tdr : ndarray (n_t,)
        Estimated impedance in ohms.
    warnings : list[str]
        TDR quality warnings (interpolation / DC extrapolation / strategy),
        surfaced into the report (ERR-008).
    """
    reflection = np.asarray(reflection, dtype=complex)
    freqs = np.asarray(freqs, dtype=float)
    n = reflection.size
    if n < 2:
        return np.array([0.0]), np.array([z0]), []

    n_warn: list[str] = []
    # Validate the strategy/window strings (NEW-010). An unknown value used to
    # fall through to a silent default, masking a typo in the task config. Now
    # the caller is told explicitly which default was applied and why.
    valid_windows = {"rect", "hann", "one_sided"}
    if window not in valid_windows:
        n_warn.append(
            f"unknown TDR window '{window}' — falling back to 'rect' "
            f"(valid: {sorted(valid_windows)})"
        )
        window = "rect"
    valid_dc = {"constant", "linear", "zero"}
    if dc_extrapolation not in valid_dc:
        n_warn.append(
            f"unknown TDR dc_extrapolation '{dc_extrapolation}' — falling back to "
            f"'constant' (valid: {sorted(valid_dc)})"
        )
        dc_extrapolation = "constant"
    f_pos, g_pos = _prepare_spectrum(freqs, reflection, dc_extrapolation, n_warn)
    n_pos = f_pos.size

    # Window (DC untouched). The default "rect" leaves the spectrum untouched,
    # which makes the analytic TDR limits exact (matched→Z0, open→+∞, short→0)
    # and keeps a constant-load plateau flat (NEW-006). Any non-rect window
    # multiplies the reflection spectrum by a frequency-varying shape, so for
    # a constant load (flat ρ) the step response is no longer flat in time —
    # the plateau is depressed/ringing. To keep the analytic steady state
    # correct we renormalize every window so its DC sample stays 1.0 (so the
    # final-value theorem still gives ρ(0)) AND its total spectral energy is
    # preserved relative to rect (so a flat ρ still integrates to the same
    # steady-state plateau, not a depressed one). Non-rect windows are still
    # EXPERIMENTAL for absolute-impedance compliance — see the docstring.
    if window == "hann":
        win = np.hanning(n_pos)
        win[0] = 1.0
        # Preserve total spectral energy vs rect so a flat ρ stays flat-ish.
        e_win = np.sqrt(np.sum(win[1:] ** 2))
        e_rect = np.sqrt(n_pos - 1)
        if e_win > 0:
            win[1:] *= e_rect / e_win
        n_warn.append(
            "TDR window='hann' is EXPERIMENTAL — a full Hann window reshapes "
            "the reflection spectrum and can depress/ring a constant-load "
            "plateau; use 'rect' (default) for absolute-impedance compliance "
            "(ERR-002 / NEW-006)"
        )
    elif window == "one_sided":
        # cos² taper: 1 at DC, 0 at fmax, smooth monotone descent between.
        win = np.cos(np.linspace(0.0, np.pi / 2.0, n_pos)) ** 2
        win[0] = 1.0
        # Renormalize the tapered tail to preserve total spectral energy vs
        # rect, so a flat ρ still integrates to the same steady-state plateau
        # (NEW-006): without this a 100Ω constant load reads as ~70-83Ω.
        e_win = np.sqrt(np.sum(win[1:] ** 2))
        e_rect = np.sqrt(n_pos - 1)
        if e_win > 0:
            win[1:] *= e_rect / e_win
        n_warn.append(
            "TDR window='one_sided' is EXPERIMENTAL — the cos² taper "
            "reshapes the high-frequency reflection spectrum and can still "
            "attenuate a constant-load plateau; use 'rect' (default) for "
            "absolute-impedance compliance (NEW-006)"
        )
    else:  # rect (default)
        win = np.ones(n_pos)
    h_win = g_pos * win

    # irfft needs only the non-negative-frequency half; it synthesizes the
    # conjugate negative-frequency half automatically, guaranteeing a real
    # time-domain result.
    n_time = 2 * (n_pos - 1) if n_pos > 1 else 2
    if n_time < 2:
        n_time = 2
    # Impulse response of the reflection coefficient g(t) = IFFT(Γ(f)).
    g = np.real(irfft(h_win, n=n_time))
    # Step response Γ_step(t) = ∫g dt. Because irfft carries the 1/n_time IDFT
    # scaling, the discrete cumsum already equals the continuous integral — its
    # terminal value is Σg = h_win[0] = Γ(0) (the DC reflection, i.e. the
    # steady-state value by the final-value theorem). No extra dt or
    # normalization factor is needed, which makes the analytic limits exact:
    # matched (Γ=0) → Z0, open (Γ→+1) → +∞, short (Γ→-1) → 0.
    step = np.cumsum(g)

    # Z(t) = Z0 (1 + Γ_step(t)) / (1 - Γ_step(t)). An open (step→+1) yields
    # +inf; clip to a large value. Step magnitudes slightly >1 from windowing /
    # extrapolation noise map to a large negative impedance and are clipped too.
    denom = 1.0 - step
    with np.errstate(divide="ignore", invalid="ignore"):
        z_tdr = z0 * (1.0 + step) / denom
    z_tdr = np.nan_to_num(z_tdr, posinf=1e4, neginf=-1e4, nan=z0)
    z_tdr = np.clip(z_tdr, -1e4, 1e4)

    df = float(f_pos[1] - f_pos[0]) if f_pos.size > 1 else 1.0
    dt = 1.0 / (n_time * df) if df > 0 else 1e-12
    t = np.arange(n_time) * dt
    mask = t <= max_time
    return t[mask], z_tdr[mask], n_warn
