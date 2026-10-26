"""Synthetic Touchstone generator for the demo case (roadmap §10.5).

Builds a physically plausible 4-port differential channel .s4p that exercises
every metric path (IL, RL, mode conversion) without needing HFSS or a license.
Output is a real Touchstone file the reader parses, so the demo is end-to-end
real except for the S-parameter origin.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def _channel_sparams(freqs: np.ndarray) -> np.ndarray:
    """Return a (n_freqs, 4, 4) single-ended S-matrix for a differential pair.

    Port order: [p1+, p1-, p2+, p2-] (positive/negative of pair1 at near end,
    positive/negative of pair2 at far end). This maps directly to two
    differential pairs in the canonical ordering.
    """
    n = len(freqs)
    # Ideal: low IL, high RL, tiny mode conversion. Defer the passivity tweak
    # to the end; build single-ended approximations first.
    s = np.zeros((n, 4, 4), dtype=complex)

    # Transmission (near -> far): gentle low-pass roll-off with a resonance dip.
    f_ghz = freqs / 1e9
    # insertion magnitude: ~ -0.5 dB low freq, dips near 16 GHz
    mag21 = 10.0 ** (-0.5 / 20.0) * np.ones_like(f_ghz)
    # add a resonance dip around 16 GHz
    dip = 1.0 / (1.0 + ((f_ghz - 16.0) / 1.2) ** 2)
    mag21 = mag21 - 0.55 * dip * (10.0 ** (-0.5 / 20.0))
    # mild high-frequency roll-off
    mag21 = mag21 * (1.0 / np.sqrt(1.0 + (f_ghz / 20.0) ** 4))
    # phase: electrical length ~ 30 deg at 10 GHz, linear
    delay = 30.0 * (freqs / 10e9)
    phase = np.deg2rad(delay)
    s21 = mag21 * np.exp(-1j * phase)  # near+ -> far+

    # Reflection: ~ -25 dB low freq, rising with frequency
    mag11 = 10.0 ** (-25.0 / 20.0) * (1.0 + (f_ghz / 30.0) ** 2)
    s11 = mag11 * np.exp(1j * np.deg2rad(120.0))

    # Pair-internal coupling (p+ to p- of same pair): small
    mag12 = 10.0 ** (-30.0 / 20.0) * np.ones_like(f_ghz)
    s12 = mag12 * np.exp(1j * np.deg2rad(-60.0))

    # Near-end cross-pair (pair1+ -> pair2+): NEXT-ish, small
    mag13 = 10.0 ** (-45.0 / 20.0) * (1.0 + (f_ghz / 25.0))
    s13 = mag13 * np.exp(1j * np.deg2rad(20.0))

    # Far-end cross-pair (pair1+ -> pair2-): FEXT-ish
    mag14 = 10.0 ** (-50.0 / 20.0) * (f_ghz / 10.0 + 0.1)
    s14 = mag14 * np.exp(1j * np.deg2rad(-10.0))

    for f in range(n):
        m = np.zeros((4, 4), dtype=complex)
        # diagonals (reflection)
        m[0, 0] = s11[f]
        m[3, 3] = s11[f]
        m[1, 1] = s11[f] * 1.05
        m[2, 2] = s11[f] * 1.05
        # transmission near->far (0<->2, 1<->3)
        m[2, 0] = s21[f]
        m[0, 2] = s21[f]
        m[3, 1] = s21[f] * 0.98
        m[1, 3] = s21[f] * 0.98
        # pair-internal (0<->1, 2<->3)
        m[1, 0] = s12[f]
        m[0, 1] = s12[f]
        m[3, 2] = s12[f]
        m[2, 3] = s12[f]
        # cross-pair coupling (0<->3, 1<->2)
        m[3, 0] = s14[f]
        m[0, 3] = s14[f]
        m[2, 1] = s13[f]
        m[1, 2] = s13[f]
        s[f] = m

    # Enforce passivity (scaling): the above is a rough approximation. Scale all
    # singular values down to <= 1 per frequency so S is passive.
    for f in range(n):
        sv = np.linalg.svd(s[f], compute_uv=False)
        smax = sv.max()
        if smax > 1.0:
            s[f] /= smax
    return s


def write_demo_touchstone(
    out_path: str | Path,
    fmin_ghz: float = 0.1,
    fmax_ghz: float = 40.0,
    n: int = 401,
) -> Path:
    """Write a synthetic 4-port Touchstone file."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    freqs = np.linspace(fmin_ghz * 1e9, fmax_ghz * 1e9, n)
    s = _channel_sparams(freqs)

    lines: list[str] = []
    lines.append("! Synthetic demo Touchstone — em_copilot demo case")
    lines.append("! Not a real measurement; for pipeline demonstration only.")
    lines.append("# Hz S MA R 50")
    lines.append("! Port order: [p1+, p1-, p2+, p2-]")
    for i, f in enumerate(freqs):
        # MA format: freq, then 16 numbers (4x4), 4 per line conventionally
        row = [f"{f:.6g}"]
        for r in range(4):
            for c in range(4):
                mag = float(np.abs(s[i, r, c]))
                ang = float(np.rad2deg(np.angle(s[i, r, c])))
                row.append(f"{mag:.6E}")
                row.append(f"{ang:.4f}")
        # Touchstone: up to 4 data pairs per physical line; we write freq + 4
        # pairs per line for readability (5 tokens/line incl. freq).
        lines.append(" ".join(row[:5]))  # freq + first 2 mag/ang pairs
        # remaining 14 numbers (7 pairs) → 2 lines of 4 pairs
        rest = row[5:]
        for k in range(0, len(rest), 8):
            lines.append(" ".join(rest[k : k + 8]))
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
