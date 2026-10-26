"""Single-ended → mixed-mode conversion and SI metrics (roadmap §10.3, §10.4).

Implements the Bockelman–Dixon–Wilson transform via an orthonormal mode matrix
``T`` so arbitrary differential-pair mappings are supported, not only the
canonical ``[1+,1-,2+,2-]`` ordering. Power is preserved (``T Tᵀ = I``), so the
``1/2`` factor in the textbook formulas is absorbed into ``T``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .schemas import CrosstalkCase, DifferentialChannel, PortMapping
from .touchstone import Touchstone


@dataclass
class MixedMode:
    """Mixed-mode S-parameters for the configured differential pairs.

    The mode order is ``[d0, d1, ..., c0, c1, ...]`` (all differential rows
    first, then all common rows). ``s_mm[f, i, j]`` is response ``i``,
    stimulus ``j`` — same convention as :class:`Touchstone`.

    Attributes
    ----------
    pair_names : list[str]
        Differential pair names, in order.
    freqs : ndarray (n_freqs,)
        Frequencies in Hz.
    s_mm : ndarray (n_freqs, 2*n_pairs, 2*n_pairs), complex
        Mixed-mode S-parameters.
    sdd, scc, scd, sdc : ndarray (n_freqs, n_pairs, n_pairs), complex
        Quadrants of ``s_mm`` for convenience.
    """

    pair_names: list[str]
    freqs: np.ndarray
    s_mm: np.ndarray
    sdd: np.ndarray
    scc: np.ndarray
    scd: np.ndarray
    sdc: np.ndarray


def _build_mode_matrix(pairs: PortMapping, n_ports: int) -> np.ndarray:
    """Build the orthonormal single-ended → mixed-mode matrix ``T``.

    ``T`` has shape ``(2*n_pairs, n_ports)``. For pair ``k`` with positive port
    ``p`` and negative port ``q``:

    - differential row ``d_k``: ``+1/√2`` at ``p``, ``-1/√2`` at ``q``
    - common row ``c_k``:      ``+1/√2`` at ``p``, ``+1/√2`` at ``q``

    Rows are orthonormal, so ``T @ T.T = I`` and power is preserved.
    """
    n_pairs = len(pairs.pairs)
    T = np.zeros((2 * n_pairs, n_ports), dtype=float)
    for k, pair in enumerate(pairs.pairs):
        p = pair.positive - 1  # 0-based
        q = pair.negative - 1
        inv_sqrt2 = 1.0 / np.sqrt(2.0)
        T[k, p] = inv_sqrt2  # differential row
        T[k, q] = -inv_sqrt2
        T[n_pairs + k, p] = inv_sqrt2  # common row
        T[n_pairs + k, q] = inv_sqrt2
    return T


def to_mixed_mode(ts: Touchstone, mapping: PortMapping) -> MixedMode:
    """Convert single-ended S-parameters to mixed mode.

    Parameters
    ----------
    ts : Touchstone
        Parsed single-ended data.
    mapping : PortMapping
        Differential pair definitions. All referenced ports must exist.

    Returns
    -------
    MixedMode
        Mixed-mode S-parameters split into Sdd/Scc/Scd/Sdc quadrants.

    Raises
    ------
    ValueError
        If a referenced port is out of range for the Touchstone file.
    """
    n_ports = ts.n_ports
    for pair in mapping.pairs:
        if pair.positive > n_ports or pair.negative > n_ports:
            raise ValueError(
                f"pair {pair.name!r} references port > {n_ports} "
                f"(file has {n_ports} ports)"
            )

    T = _build_mode_matrix(mapping, n_ports)
    n_pairs = len(mapping.pairs)
    n_freqs = ts.freqs.size

    # S_mm[f] = T @ S[f] @ T.T   (vectorized over freq)
    S = ts.s  # (n_freqs, n_ports, n_ports)
    s_mm = np.empty((n_freqs, 2 * n_pairs, 2 * n_pairs), dtype=complex)
    for f in range(n_freqs):
        s_mm[f] = T @ S[f] @ T.T

    d = slice(0, n_pairs)
    c = slice(n_pairs, 2 * n_pairs)
    return MixedMode(
        pair_names=[p.name for p in mapping.pairs],
        freqs=ts.freqs,
        s_mm=s_mm,
        sdd=s_mm[:, d, d],
        scc=s_mm[:, c, c],
        scd=s_mm[:, c, d],  # common response, differential stimulus
        sdc=s_mm[:, d, c],  # differential response, common stimulus
    )


def _db20(x: np.ndarray) -> np.ndarray:
    """Magnitude in dB, with a floor to avoid ``-inf`` at exact zeros."""
    mag = np.abs(x)
    mag = np.where(mag < 1e-12, 1e-12, mag)
    return 20.0 * np.log10(mag)


@dataclass
class SIMetrics:
    """Computed SI metrics for one differential channel (§10.4).

    All dB quantities are magnitude-in-dB. Frequencies are in Hz.

    When the channel has only one differential pair there is no far-end port,
    so transmission metrics (Sdd21/Scc21/Scd21/Sdc21) are *not applicable*:
    ``transmission_applicable`` is ``False`` and the transmission arrays hold
    NaN (not the ``0 dB`` = perfect-transmission misreport of ERR-003). The
    snapshot omits the transmission keys in that case so an acceptance rule
    targeting them surfaces as ERROR (metric not available), not FAIL.

    Reverse-direction quantities (Sdd12/Scc12/Scd12/Sdc12) are reported
    alongside the forward ones (ERR-006). For a reciprocal network forward ==
    reverse; for a non-reciprocal or asymmetrically-coupled network they
    differ, and both directions must be available for compliance.
    """

    pair_name: str
    freqs: np.ndarray
    sdd11_db: np.ndarray
    sdd21_db: np.ndarray
    sdd22_db: np.ndarray
    scc11_db: np.ndarray
    scc21_db: np.ndarray
    scd21_db: np.ndarray
    sdc21_db: np.ndarray
    il_margin_db: np.ndarray | None = None
    rl_margin_db: np.ndarray | None = None
    next_db: list[tuple[str, np.ndarray]] = field(default_factory=list)
    fext_db: list[tuple[str, np.ndarray]] = field(default_factory=list)
    # True when a genuine near→far transmission path exists (a distinct far
    # pair was identified). False for a single differential port.
    transmission_applicable: bool = True
    # Reverse-direction transmission (stimulus at far, response at near).
    # Equal to the forward arrays for a reciprocal network; kept separately so
    # non-reciprocal channels can be analyzed in both directions (ERR-006).
    sdd12_db: np.ndarray | None = None
    scc12_db: np.ndarray | None = None
    scd12_db: np.ndarray | None = None
    sdc12_db: np.ndarray | None = None
    # Indices of the near and far pairs this channel was computed for, so the
    # report can state the physical direction explicitly (ERR-006).
    near_pair_index: int = 0
    far_pair_index: int | None = None
    # Frequency tolerance for at_frequency (ERR-010): a requested frequency is
    # "in tolerance" when |actual - requested| <= max(abs, rel*|requested|).
    # Out-of-tolerance requests surface as ERROR in the acceptance evaluator.
    # Defaults tightened in NEW-005: 1 % rel / 1 MHz abs (was 5 % / 1 MHz), so
    # a 16 GHz request no longer accepts a ~15.3 GHz bin. The SITask model can
    # override both per analysis; compute_metrics forwards the task values.
    frequency_tolerance_abs_hz: float = 1e6
    frequency_tolerance_rel: float = 0.01

    def at_frequency(self, freq_hz: float) -> dict:
        """Nearest-frequency tabulated snapshot of all metrics (§10.4).

        Records both the requested and actual frequency plus the error and an
        in-tolerance flag (ERR-010), so an off-grid request can never be
        silently presented as if it were the requested frequency.
        """
        if self.freqs.size == 0:
            raise ValueError("no frequency points in metrics")
        idx = int(np.argmin(np.abs(self.freqs - freq_hz)))
        actual = float(self.freqs[idx])
        err_hz = abs(actual - float(freq_hz))
        tol = max(
            self.frequency_tolerance_abs_hz,
            self.frequency_tolerance_rel * abs(float(freq_hz)),
        )
        in_tol = err_hz <= tol
        out = {
            "frequency_hz": actual,
            "frequency_requested_hz": float(freq_hz),
            "frequency_actual_hz": actual,
            "frequency_error_hz": err_hz,
            "frequency_in_tolerance": in_tol,
            "frequency_index": idx,
            "Sdd11_dB": float(self.sdd11_db[idx]),
            "Sdd22_dB": float(self.sdd22_db[idx]),
            "Scc11_dB": float(self.scc11_db[idx]),
        }
        # Transmission / mode-conversion metrics only exist when a genuine
        # near→far path exists. Omit the keys for a single differential port so
        # an acceptance rule on Sdd21/Scd21 surfaces as "metric not available"
        # → ERROR, not a misleading 0 dB PASS/FAIL (ERR-003).
        if self.transmission_applicable:
            out["Sdd21_dB"] = float(self.sdd21_db[idx])
            out["Scc21_dB"] = float(self.scc21_db[idx])
            out["Scd21_dB"] = float(self.scd21_db[idx])
            out["Sdc21_dB"] = float(self.sdc21_db[idx])
            # Reverse direction (stimulus at far, response at near) — ERR-006.
            # Equal to the forward values for a reciprocal network; reported
            # separately so a non-reciprocal channel can be checked both ways.
            if self.sdd12_db is not None:
                out["Sdd12_dB"] = float(self.sdd12_db[idx])
            if self.scc12_db is not None:
                out["Scc12_dB"] = float(self.scc12_db[idx])
            if self.scd12_db is not None:
                out["Scd12_dB"] = float(self.scd12_db[idx])
            if self.sdc12_db is not None:
                out["Sdc12_dB"] = float(self.sdc12_db[idx])
        if self.il_margin_db is not None:
            out["IL_margin_dB"] = float(self.il_margin_db[idx])
        if self.rl_margin_db is not None:
            out["RL_margin_dB"] = float(self.rl_margin_db[idx])
        for name, arr in self.next_db:
            out[f"NEXT_{name}_dB"] = float(arr[idx])
        for name, arr in self.fext_db:
            out[f"FEXT_{name}_dB"] = float(arr[idx])
        return out


def compute_metrics(
    mm: MixedMode,
    *,
    pair_index: int = 0,
    far_pair: int | None = None,
    il_limit_db: float | None = None,
    rl_limit_db: float | None = None,
    frequency_tolerance_rel: float | None = None,
    frequency_tolerance_abs_hz: float | None = None,
) -> SIMetrics:
    """Compute the §10.4 metric set for one differential channel.

    Parameters
    ----------
    mm : MixedMode
    pair_index : int
        Which differential pair is the channel **near** end (its Sdd11). Its
        Sdd21 is the forward transmission from this near end to the far end.
    far_pair : int, optional
        Which differential pair is the channel **far** end. When omitted the
        historic fallback ``1 if pair_index == 0 else 0`` is used *only* for
        ``n_pairs == 2``; for ``n_pairs >= 3`` the far end is ambiguous and
        must be named explicitly (ERR-006). A single differential pair
        (``n_pairs == 1``) has no far end → transmission is not applicable.
    il_limit_db, rl_limit_db : float, optional
        Insertion-loss / return-loss limits for margin computation. ``il_limit``
        is a *minimum* acceptable Sdd21 (e.g. ``-12``); margin = Sdd21 - limit.
        ``rl_limit`` is a *maximum* acceptable Sdd11 (e.g. ``-10``); margin =
        limit - Sdd11 (positive = passing).
    frequency_tolerance_rel, frequency_tolerance_abs_hz : float, optional
        Per-task frequency tolerance forwarded into the :class:`SIMetrics`
        result (NEW-005). When ``None`` the new tighter SIMetrics defaults
        (1 % rel / 1 MHz abs) apply; pass the task's values to override.
    """
    n_pairs = len(mm.pair_names)
    if pair_index >= n_pairs:
        raise ValueError(f"pair_index {pair_index} >= n_pairs {n_pairs}")
    if pair_index < 0:
        raise ValueError(
            f"pair_index {pair_index} < 0; Python negative indexing would wrap to "
            f"pair {n_pairs + pair_index} silently — use a non-negative index (NEW-009)"
        )

    # Resolve the far-end pair explicitly (ERR-006). The legacy ``far = 1 if
    # pair_index == 0 else 0`` silently fixed the channel to Pair0→Pair1 and
    # broke for n_pairs >= 3 (pair_index=2 still picked Pair0 as the far end).
    # Now the caller names the far pair; the fallback is kept only for the
    # unambiguous 2-pair case so existing callers/tests keep working.
    if far_pair is not None and far_pair >= n_pairs:
        raise ValueError(f"far_pair {far_pair} >= n_pairs {n_pairs}")
    if far_pair is not None and far_pair < 0:
        raise ValueError(
            f"far_pair {far_pair} < 0; Python negative indexing would wrap to "
            f"pair {n_pairs + far_pair} silently — use a non-negative index (NEW-009)"
        )
    if far_pair is not None and far_pair == pair_index:
        raise ValueError(
            f"far_pair {far_pair} == pair_index {pair_index}; near and far "
            "must be distinct pairs"
        )
    if far_pair is None:
        if n_pairs == 1:
            far = None  # single differential port — no far end
        elif n_pairs == 2:
            far = 1 if pair_index == 0 else 0  # unambiguous fallback
        else:
            raise ValueError(
                f"n_pairs={n_pairs} >= 3: the far-end pair is ambiguous — "
                f"pass far_pair= explicitly (pair_index={pair_index}). ERR-006"
            )
    else:
        far = far_pair

    name = mm.pair_names[pair_index]
    freqs = mm.freqs
    transmission_applicable = far is not None

    # Matrix index convention is ``s[response, stimulus]`` with near=pair_index
    # and far. Thus:
    #   Forward (stimulus at near, response at far):
    #     Sdd21 = [:, far, near]   Scc21 = [:, far, near]
    #     Scd21 = [:, far, near]   Sdc21 = [:, far, near]
    #   Reverse (stimulus at far, response at near):
    #     Sdd12 = [:, near, far]   Scc12 = [:, near, far]
    #     Scd12 = [:, near, far]   Sdc12 = [:, near, far]
    #   Reflections: Sdd11 = [:, near, near], Sdd22 = [:, far, far]
    # The legacy implementation read [:, near, far] for Sdd21 (which is actually
    # Sdd12, the reverse direction — hidden for reciprocal networks) and
    # duplicated Sdd11 for Sdd22. Both are corrected here (I-02 / I-03, ERR-006).
    sdd11 = _db20(mm.sdd[:, pair_index, pair_index])
    scc11 = _db20(mm.scc[:, pair_index, pair_index])
    sdd12_db = scc12_db = scd12_db = sdc12_db = None
    if transmission_applicable:
        # Forward direction: response at far, stimulus at near.
        sdd21 = _db20(mm.sdd[:, far, pair_index])
        scc21 = _db20(mm.scc[:, far, pair_index])
        # Scd = common resp / diff stim; Sdc = diff resp / common stim.
        scd21 = _db20(mm.scd[:, far, pair_index])
        sdc21 = _db20(mm.sdc[:, far, pair_index])
        # Reverse direction: response at near, stimulus at far (ERR-006).
        sdd12_db = _db20(mm.sdd[:, pair_index, far])
        scc12_db = _db20(mm.scc[:, pair_index, far])
        scd12_db = _db20(mm.scd[:, pair_index, far])
        sdc12_db = _db20(mm.sdc[:, pair_index, far])
        # Far-end return loss is the reflection at the far port, not a copy of
        # the near-end reflection (I-03).
        sdd22 = _db20(mm.sdd[:, far, far])
    else:
        # Single differential port: there is no far-end partner, so the forward
        # transmission (Sdd21/Scc21) and mode-conversion-through (Scd21/Sdc21)
        # quantities are *not applicable*. Fabricating 0 dB would misreport
        # "no such path" as "|S21|=1 = perfect lossless transmission" (ERR-003).
        # Fill with NaN so the arrays stay shaped for worst_case()/at_frequency
        # and mark transmission_applicable=False so at_frequency omits those keys
        # (→ an acceptance rule on Sdd21/Scd21 surfaces as ERROR "metric not
        # available", not a misleading PASS/FAIL).
        nan = np.full_like(freqs, np.nan, dtype=float)
        sdd21 = nan
        scc21 = nan
        scd21 = nan
        sdc21 = nan
        sdd22 = _db20(mm.sdd[:, pair_index, pair_index])

    il_margin = (sdd21 - il_limit_db) if il_limit_db is not None else None
    rl_margin = (rl_limit_db - sdd11) if rl_limit_db is not None else None

    # NEXT / FEXT (differential inter-channel crosstalk) only make sense when
    # there are differential ports beyond the near/far ends of the channel of
    # interest. With n_pairs == 2 the two pairs are the near and far ends of a
    # single link, so the off-diagonal Sdd terms are insertion loss (Sdd21 /
    # Sdd12), already captured above — reporting them as "NEXT/FEXT" would be a
    # mislabel. Genuine pair-to-pair crosstalk needs ≥3 differential ports
    # (e.g. one victim link + ≥1 aggressor link with its own near/far).
    next_db: list[tuple[str, np.ndarray]] = []
    fext_db: list[tuple[str, np.ndarray]] = []
    if n_pairs >= 3 and far is not None:
        for k in range(n_pairs):
            if k == pair_index or k == far:
                continue
            # Topology: pair_index = victim near, far = victim far, k =
            # aggressor near (aggressor stimulated at its near end). The S-matrix
            # convention is s[response, stimulus]:
            #   NEXT = Sdd[victim_near, aggressor_near] = Sdd[:, pair_index, k]
            #     — near-end crosstalk observed at the victim's near end.
            #   FEXT = Sdd[victim_far, aggressor_near] = Sdd[:, far, k]
            #     — far-end crosstalk observed at the victim's far end.
            # The legacy code read Sdd[:, k, pair_index] for FEXT, which is the
            # *reverse* direction (aggressor response, victim stimulus) and is
            # only equal under reciprocity — for non-reciprocal or mildly
            # asymmetric coupling it is wrong by tens of dB (ERR-001).
            next_db.append((mm.pair_names[k], _db20(mm.sdd[:, pair_index, k])))
            fext_db.append((mm.pair_names[k], _db20(mm.sdd[:, far, k])))

    return SIMetrics(
        pair_name=name,
        freqs=freqs,
        sdd11_db=sdd11,
        sdd21_db=sdd21,
        sdd22_db=sdd22,
        scc11_db=scc11,
        scc21_db=scc21,
        scd21_db=scd21,
        sdc21_db=sdc21,
        il_margin_db=il_margin,
        rl_margin_db=rl_margin,
        next_db=next_db,
        fext_db=fext_db,
        transmission_applicable=transmission_applicable,
        sdd12_db=sdd12_db,
        scc12_db=scc12_db,
        scd12_db=scd12_db,
        sdc12_db=sdc12_db,
        near_pair_index=pair_index,
        far_pair_index=far,
        frequency_tolerance_abs_hz=(
            frequency_tolerance_abs_hz
            if frequency_tolerance_abs_hz is not None
            else SIMetrics.frequency_tolerance_abs_hz
        ),
        frequency_tolerance_rel=(
            frequency_tolerance_rel
            if frequency_tolerance_rel is not None
            else SIMetrics.frequency_tolerance_rel
        ),
    )


@dataclass
class CrosstalkResult:
    """Per-aggressor-channel crosstalk for one :class:`CrosstalkCase` (NEW-003).

    Each entry in ``next_db`` / ``fext_db`` is keyed by the aggressor
    *channel* name (a complete near+far channel), not by a bare pair index.
    This is the physically correct model for a multi-channel Touchstone: an
    8-port / 4-pair file is two channels (Victim + Aggressor), and the
    aggressor's far end is *not* a second independent aggressor.
    """

    victim_channel: str
    stimulus_end: str
    next_db: list[tuple[str, np.ndarray]] = field(default_factory=list)
    fext_db: list[tuple[str, np.ndarray]] = field(default_factory=list)


def compute_crosstalk(
    mm: MixedMode,
    case: CrosstalkCase,
    channels: list[DifferentialChannel],
) -> CrosstalkResult:
    """Compute per-aggressor-channel NEXT/FEXT (NEW-003).

    The legacy per-pair loop in :func:`compute_metrics` treats every remaining
    pair as an independent aggressor. For a real 8-port / 4-pair file that is
    two *channels* (Victim near+far, Aggressor near+far), so the aggressor's
    far end gets mislabeled as a second independent NEXT source. This function
    takes the true channel topology: each aggressor is a point-to-point channel
    stimulated at ``case.stimulus_end``, and only that stimulus end contributes
    to NEXT (same end as stimulus) / FEXT (opposite end).

    Topology (``s[response, stimulus]`` convention, ``stimulus_end='near'``):
      NEXT = Sdd[victim_near, aggressor_near]  — same-end coupling
      FEXT = Sdd[victim_far,  aggressor_near]  — opposite-end coupling

    With ``stimulus_end='far'`` the stimulus end is the aggressor's far pair, so
    NEXT is observed at the victim's far end and FEXT at the victim's near end.
    """
    by_name = {c.name: c for c in channels}
    if case.victim_channel not in by_name:
        raise ValueError(
            f"victim channel {case.victim_channel!r} not in channels "
            f"{list(by_name)}"
        )
    pair_index = {name: i for i, name in enumerate(mm.pair_names)}
    victim = by_name[case.victim_channel]
    v_near = pair_index[victim.near_pair]
    v_far = pair_index[victim.far_pair]
    next_db: list[tuple[str, np.ndarray]] = []
    fext_db: list[tuple[str, np.ndarray]] = []
    for agg_name in case.aggressor_channels:
        if agg_name not in by_name:
            raise ValueError(
                f"aggressor channel {agg_name!r} not in channels {list(by_name)}"
            )
        agg = by_name[agg_name]
        a_near = pair_index[agg.near_pair]
        a_far = pair_index[agg.far_pair]
        if case.stimulus_end == "near":
            stim = a_near
            # NEXT: same end as stimulus → observed at victim near.
            next_db.append((agg_name, _db20(mm.sdd[:, v_near, stim])))
            # FEXT: opposite end → observed at victim far.
            fext_db.append((agg_name, _db20(mm.sdd[:, v_far, stim])))
        else:  # "far"
            stim = a_far
            # NEXT: same end as stimulus (far) → observed at victim far.
            next_db.append((agg_name, _db20(mm.sdd[:, v_far, stim])))
            # FEXT: opposite end (near) → observed at victim near.
            fext_db.append((agg_name, _db20(mm.sdd[:, v_near, stim])))
    return CrosstalkResult(
        victim_channel=case.victim_channel,
        stimulus_end=case.stimulus_end,
        next_db=next_db,
        fext_db=fext_db,
    )


def worst_case(metrics: SIMetrics) -> dict:
    """Return worst-case (over frequency) for each metric (§10.4).

    For reflection/crosstalk/mode-conversion, "worst" = the value closest to
    0 dB (i.e. the maximum, since these are negative quantities and a larger
    value = more energy = worse). For insertion loss Sdd21, worst = the
    minimum (most negative = most loss).

    NaN (single-differential-port, transmission not applicable — ERR-003) is
    propagated as ``None`` so the report shows "N/A" rather than a fabricated
    number, and aggregation never crashes on ``np.max``/``np.min`` of an
    all-NaN array (which would otherwise warn and return NaN).
    """
    def _worst_min(arr: np.ndarray):
        if not metrics.transmission_applicable and np.all(np.isnan(arr)):
            return None
        return float(np.nanmin(arr))

    def _worst_max(arr: np.ndarray):
        if not metrics.transmission_applicable and np.all(np.isnan(arr)):
            return None
        return float(np.nanmax(arr))

    out: dict = {
        "Sdd11_dB_worst": float(np.max(metrics.sdd11_db)),
        "Sdd21_dB_worst": _worst_min(metrics.sdd21_db),
        "Scd21_dB_worst": _worst_max(metrics.scd21_db),
        "Sdc21_dB_worst": _worst_max(metrics.sdc21_db),
    }
    # Reverse-direction worst cases (ERR-006). For a reciprocal network these
    # equal the forward values; kept separate so a non-reciprocal channel's
    # reverse IL/RL can be checked independently. Omitted (not N/A) when the
    # metrics object has no reverse arrays (single-port / legacy callers).
    if metrics.sdd12_db is not None:
        out["Sdd12_dB_worst"] = _worst_min(metrics.sdd12_db)
    if metrics.scd12_db is not None:
        out["Scd12_dB_worst"] = _worst_max(metrics.scd12_db)
    if metrics.sdc12_db is not None:
        out["Sdc12_dB_worst"] = _worst_max(metrics.sdc12_db)
    if not metrics.transmission_applicable:
        # No valid Sdd21 → no frequency where the worst loss occurs.
        out["worst_Sdd21_freq_hz"] = None
    else:
        idx = int(np.argmin(metrics.sdd21_db))
        out["worst_Sdd21_freq_hz"] = float(metrics.freqs[idx])
    if metrics.il_margin_db is not None:
        out["IL_margin_worst_dB"] = _worst_min(metrics.il_margin_db)
    if metrics.rl_margin_db is not None:
        out["RL_margin_worst_dB"] = float(np.min(metrics.rl_margin_db))
    for name, arr in metrics.next_db:
        out[f"NEXT_{name}_worst_dB"] = float(np.max(arr))
    for name, arr in metrics.fext_db:
        out[f"FEXT_{name}_worst_dB"] = float(np.max(arr))
    return out
