"""Tests for single-ended → mixed-mode conversion and SI metrics (roadmap §10.3, §10.4, §15.1).

Covers:
- ``_build_mode_matrix`` orthonormality (T @ T.T == I, power preserved).
- ``to_mixed_mode`` quadrant slicing (Sdd/Scc/Scd/Sdc), port-out-of-range.
- ``compute_metrics`` metric set for the 2-pair demo channel.
- ``SIMetrics.at_frequency`` nearest-frequency snapshot + key naming.
- ``worst_case`` reduction semantics (max for reflection, min for IL).
"""

from __future__ import annotations

import numpy as np
import pytest

from em_copilot.demo import write_demo_touchstone
from em_copilot.mixed_mode import (
    MixedMode,
    _build_mode_matrix,
    compute_metrics,
    to_mixed_mode,
    worst_case,
)
from em_copilot.schemas import DifferentialPair, PortMapping
from em_copilot.touchstone import read_touchstone

# --------------------------------------------------------------------------- #
# Port-mapping fixtures
# --------------------------------------------------------------------------- #

def _canonical_mapping() -> PortMapping:
    """Demo .s4p port order is [p1+, p1-, p2+, p2-] → two pairs 1/2 and 3/4."""
    return PortMapping(
        pairs=[
            DifferentialPair(name="RX0", positive=1, negative=2),
            DifferentialPair(name="RX1", positive=3, negative=4),
        ]
    )


def _shifted_mapping() -> PortMapping:
    """A non-canonical mapping to prove T is general, not hardcoded."""
    return PortMapping(
        pairs=[
            DifferentialPair(name="A", positive=3, negative=4),
            DifferentialPair(name="B", positive=1, negative=2),
        ]
    )


# --------------------------------------------------------------------------- #
# _build_mode_matrix
# --------------------------------------------------------------------------- #

class TestModeMatrix:
    def test_orthonormal_rows(self):
        m = _canonical_mapping()
        T = _build_mode_matrix(m, n_ports=4)
        assert T.shape == (4, 4)
        # T @ T.T must be the identity (orthonormal → power preserved).
        prod = T @ T.T
        np.testing.assert_allclose(prod, np.eye(4), atol=1e-12)

    def test_differential_and_common_signs(self):
        m = _canonical_mapping()
        T = _build_mode_matrix(m, n_ports=4)
        inv = 1.0 / np.sqrt(2.0)
        # Pair 0: diff row 0 (+ at port1, - at port2); common row 2 (+ at both).
        assert T[0, 0] == pytest.approx(inv)
        assert T[0, 1] == pytest.approx(-inv)
        assert T[2, 0] == pytest.approx(inv)
        assert T[2, 1] == pytest.approx(inv)
        # Pair 1: diff row 1 (+ at port3, - at port4); common row 3.
        assert T[1, 2] == pytest.approx(inv)
        assert T[1, 3] == pytest.approx(-inv)
        assert T[3, 2] == pytest.approx(inv)
        assert T[3, 3] == pytest.approx(inv)

    def test_orthonormality_holds_for_arbitrary_mapping(self):
        T = _build_mode_matrix(_shifted_mapping(), n_ports=4)
        np.testing.assert_allclose(T @ T.T, np.eye(4), atol=1e-12)

    def test_single_pair(self):
        m = PortMapping(pairs=[DifferentialPair(name="P", positive=1, negative=2)])
        T = _build_mode_matrix(m, n_ports=2)
        assert T.shape == (2, 2)
        np.testing.assert_allclose(T @ T.T, np.eye(2), atol=1e-12)


# --------------------------------------------------------------------------- #
# to_mixed_mode
# --------------------------------------------------------------------------- #

class TestToMixedMode:
    def test_shapes_and_quadrants(self, tmp_path):
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path)
        ts = read_touchstone(path)
        mm = to_mixed_mode(ts, _canonical_mapping())
        assert isinstance(mm, MixedMode)
        assert mm.pair_names == ["RX0", "RX1"]
        assert mm.s_mm.shape == (ts.freqs.size, 4, 4)
        n = ts.freqs.size
        for q in (mm.sdd, mm.scc, mm.scd, mm.sdc):
            assert q.shape == (n, 2, 2)
        # sdd is the top-left block of s_mm.
        np.testing.assert_allclose(mm.sdd, mm.s_mm[:, 0:2, 0:2])
        # scc is the bottom-right block.
        np.testing.assert_allclose(mm.scc, mm.s_mm[:, 2:4, 2:4])

    def test_identity_for_single_ended_pair(self, tmp_path):
        """A 2-port whose S is already 'differential' identity: Sdd11 should be
        the single-ended s11 mapped through T. Construct S = I (perfect match)."""
        path = tmp_path / "id.s2p"
        path.write_text(
            "# Hz S RI R 50\n"
            "1.0e9 1.0 0.0 0.0 0.0 0.0 0.0 1.0 0.0\n",
            encoding="utf-8",
        )
        ts = read_touchstone(path)
        m = PortMapping(pairs=[DifferentialPair(name="P", positive=1, negative=2)])
        mm = to_mixed_mode(ts, m)
        # T @ I @ T.T = T @ T.T = I, so Sdd11 = 1, Scc11 = 1.
        np.testing.assert_allclose(np.abs(mm.sdd[0, 0, 0]), 1.0, atol=1e-12)
        np.testing.assert_allclose(np.abs(mm.scc[0, 0, 0]), 1.0, atol=1e-12)

    def test_port_out_of_range_raises(self, tmp_path):
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path)
        ts = read_touchstone(path)
        bad = PortMapping(
            pairs=[DifferentialPair(name="X", positive=5, negative=6)]  # > 4
        )
        with pytest.raises(ValueError, match="port > 4"):
            to_mixed_mode(ts, bad)


def _write_nonreciprocal_s4p(path) -> np.ndarray:
    """Write a non-reciprocal 4-port .s4p and return the planted single-ended
    S-matrix so mixed-mode indices can be checked against ground truth.

    Port order [p1+, p1-, p2+, p2-]. We plant distinct forward/reverse
    differential gains so Sdd21 != Sdd12 and Sdd11 != Sdd22 become visible.
    """
    f = 1e9
    inv = 1.0 / np.sqrt(2.0)
    T = np.array([
        [inv, -inv, 0, 0],
        [0, 0, inv, -inv],
        [inv, inv, 0, 0],
        [0, 0, inv, inv],
    ])

    # Build the target mixed-mode S (2 differential pairs) at DC.
    # Sdd: [[Sdd11, Sdd12],[Sdd21, Sdd22]] deliberately non-reciprocal.
    sdd = np.array([
        [0.30 + 0.0j, 0.05 + 0.0j],  # Sdd11=0.30, Sdd12=0.05 (reverse, small)
        [0.60 + 0.0j, 0.20 + 0.0j],  # Sdd21=0.60 (forward, large), Sdd22=0.20
    ])
    scc = np.array([
        [0.10, 0.0],
        [0.0, 0.10],
    ], dtype=complex)
    scd = np.zeros((2, 2), dtype=complex)
    sdc = np.zeros((2, 2), dtype=complex)
    s_mm = np.block([[sdd, scd], [sdc, scc]])
    se = T.conj().T @ s_mm @ T  # back to single-ended
    # Enforce passivity (largest singular value <= 1); it already is here.
    sv = np.linalg.svd(se, compute_uv=False).max()
    if sv > 1.0:
        se = se / sv

    lines = ["# Hz S RI R 50"]
    flat = []
    # Touchstone n-port (n>=3) data order is row-major: response (first index)
    # outer, stimulus (second index) inner. The reader's reshape(n,n) (C-order)
    # maps flat[i*n+j] -> s[i,j] = response i, stimulus j.
    for i in range(4):
        for j in range(4):
            flat.append(se[i, j].real)
            flat.append(se[i, j].imag)
    lines.append(f"{f:.6e} " + " ".join(f"{x:.6e}" for x in flat))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return se


class TestMixedModeDirections:
    """Lock down the corrected Sdd21/Sdd22 indices (I-02 / I-03) and the
    package ``s[response, stimulus]`` convention using a non-reciprocal
    planted .s4p where Sdd21 != Sdd12 and Sdd11 != Sdd22."""

    def test_sdd21_is_forward_not_reverse(self, tmp_path):
        path = tmp_path / "nonrec.s4p"
        _write_nonreciprocal_s4p(path)
        ts = read_touchstone(path)
        mm = to_mixed_mode(ts, _canonical_mapping())
        m = compute_metrics(mm, pair_index=0)
        near, far = 0, 1
        # Sdd21 = response far, stimulus near = large forward gain 0.60 → ~-4.4 dB
        sdd21 = 20 * np.log10(np.abs(mm.sdd[0, far, near]))
        # Sdd12 = response near, stimulus far = small reverse gain 0.05 → ~-26 dB
        sdd12 = 20 * np.log10(np.abs(mm.sdd[0, near, far]))
        assert sdd21 == pytest.approx(20 * np.log10(0.60), abs=1e-6)
        assert sdd12 == pytest.approx(20 * np.log10(0.05), abs=1e-6)
        # The reported Sdd21 must equal the forward value, not the reverse.
        assert m.sdd21_db[0] == pytest.approx(sdd21, abs=1e-6)

    def test_sdd22_distinct_from_sdd11(self, tmp_path):
        path = tmp_path / "nonrec.s4p"
        _write_nonreciprocal_s4p(path)
        ts = read_touchstone(path)
        mm = to_mixed_mode(ts, _canonical_mapping())
        m = compute_metrics(mm, pair_index=0)
        near, far = 0, 1
        sdd11 = 20 * np.log10(np.abs(mm.sdd[0, near, near]))
        sdd22 = 20 * np.log10(np.abs(mm.sdd[0, far, far]))
        assert sdd11 != sdd22
        assert m.sdd11_db[0] == pytest.approx(sdd11, abs=1e-6)
        assert m.sdd22_db[0] == pytest.approx(sdd22, abs=1e-6)
        # Planted values: Sdd11=0.30, Sdd22=0.20.
        assert m.sdd11_db[0] == pytest.approx(20 * np.log10(0.30), abs=1e-6)
        assert m.sdd22_db[0] == pytest.approx(20 * np.log10(0.20), abs=1e-6)


# --------------------------------------------------------------------------- #
# compute_metrics + SIMetrics.at_frequency
# --------------------------------------------------------------------------- #

class TestComputeMetrics:
    def test_metric_shapes_and_keys(self, tmp_path):
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path)
        ts = read_touchstone(path)
        mm = to_mixed_mode(ts, _canonical_mapping())
        m = compute_metrics(mm, pair_index=0, il_limit_db=-12.0, rl_limit_db=-10.0)
        n = ts.freqs.size
        for arr in (m.sdd11_db, m.sdd21_db, m.sdd22_db, m.scc11_db, m.scc21_db,
                    m.scd21_db, m.sdc21_db):
            assert arr.shape == (n,)
        assert m.il_margin_db is not None
        assert m.rl_margin_db is not None
        # With 2 pairs there is no genuine aggressor → NEXT/FEXT empty.
        assert m.next_db == []
        assert m.fext_db == []

    def test_at_frequency_snapshot_keys(self, tmp_path):
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path)
        ts = read_touchstone(path)
        mm = to_mixed_mode(ts, _canonical_mapping())
        m = compute_metrics(mm, pair_index=0, il_limit_db=-12.0)
        snap = m.at_frequency(16e9)  # resonance dip lives near 16 GHz
        for key in ("frequency_hz", "frequency_index",
                    "Sdd11_dB", "Sdd21_dB", "Sdd22_dB",
                    "Scc11_dB", "Scc21_dB", "Scd21_dB", "Sdc21_dB",
                    "IL_margin_dB"):
            assert key in snap, key
        # Nearest frequency to 16 GHz must be within one grid step.
        assert abs(snap["frequency_hz"] - 16e9) < 0.2e9

    def test_pair_index_out_of_range_raises(self, tmp_path):
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path)
        ts = read_touchstone(path)
        mm = to_mixed_mode(ts, _canonical_mapping())
        with pytest.raises(ValueError, match="pair_index"):
            compute_metrics(mm, pair_index=5)

    def test_sdd21_has_local_dip_near_16ghz(self, tmp_path):
        """The demo channel has a resonance dip near 16 GHz. The global Sdd21
        minimum lives at the band edge (HF roll-off dominates there), so the
        dip is a *local* feature: the bin nearest 16 GHz must be lower than its
        neighbors at 14 GHz and 18 GHz."""
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path)
        ts = read_touchstone(path)
        mm = to_mixed_mode(ts, _canonical_mapping())
        m = compute_metrics(mm, pair_index=0)

        def idx_near(ghz: float) -> int:
            return int(np.argmin(np.abs(ts.freqs - ghz * 1e9)))

        i16 = idx_near(16.0)
        i14 = idx_near(14.0)
        i18 = idx_near(18.0)
        assert m.sdd21_db[i16] < m.sdd21_db[i14], "expected local dip at 16 GHz"
        assert m.sdd21_db[i16] < m.sdd21_db[i18], "expected local dip at 16 GHz"
        # And the dip bin itself should be within 14–18 GHz.
        assert 14e9 <= ts.freqs[i16] <= 18e9


# --------------------------------------------------------------------------- #
# worst_case
# --------------------------------------------------------------------------- #

class TestWorstCase:
    def test_worst_case_semantics(self, tmp_path):
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path)
        ts = read_touchstone(path)
        mm = to_mixed_mode(ts, _canonical_mapping())
        m = compute_metrics(mm, pair_index=0, il_limit_db=-12.0, rl_limit_db=-10.0)
        wc = worst_case(m)
        # Reflection worst = max (closest to 0 dB).
        assert wc["Sdd11_dB_worst"] == pytest.approx(float(np.max(m.sdd11_db)))
        # IL worst = min (most loss).
        assert wc["Sdd21_dB_worst"] == pytest.approx(float(np.min(m.sdd21_db)))
        # Worst IL frequency reported.
        assert wc["worst_Sdd21_freq_hz"] == pytest.approx(
            float(ts.freqs[int(np.argmin(m.sdd21_db))])
        )
        assert "IL_margin_worst_dB" in wc
        assert "RL_margin_worst_dB" in wc


# --------------------------------------------------------------------------- #
# ERR-001 / ERR-006: explicit channel topology, forward/reverse NEXT/FEXT
# --------------------------------------------------------------------------- #

def _write_nonreciprocal_s6p(path) -> None:
    """Write a non-reciprocal 6-port (.s6p) with 3 differential pairs so
    NEXT/FEXT direction can be locked against ground truth.

    Pair order [P0+,P0-, P1+,P1-, P2+,P2-]. Topology:
      P0 = victim near, P1 = victim far, P2 = aggressor near.

    We plant distinct forward/reverse differential crosstalk so
    Sdd[victim_near, agg_near] (NEXT, forward) != Sdd[agg_near, victim_near]
    (reverse), making the ERR-001 direction bug visible.
    """
    f = 1e9
    inv = 1.0 / np.sqrt(2.0)
    # 3 diff pairs → 6 single-ended ports, mode matrix T is (6,6).
    T = np.zeros((6, 6), dtype=float)
    for k in range(3):
        p, q = 2 * k, 2 * k + 1
        T[k, p] = inv
        T[k, q] = -inv
        T[3 + k, p] = inv
        T[3 + k, q] = inv

    # Mixed-mode S (3 diff + 3 common). Only fill the differential block.
    # Indices: 0=victim near, 1=victim far, 2=aggressor near.
    sdd = np.zeros((3, 3), dtype=complex)
    sdd[0, 0] = 0.30  # Sdd11 victim near reflection
    sdd[1, 1] = 0.20  # Sdd22 victim far reflection
    sdd[2, 2] = 0.25  # aggressor near reflection
    sdd[1, 0] = 0.60  # Sdd21 forward transmission (victim near → far)
    sdd[0, 1] = 0.05  # Sdd12 reverse transmission (deliberately != Sdd21)
    # NEXT forward: response at victim_near, stimulus at agg_near = sdd[0,2]
    sdd[0, 2] = 0.10  # NEXT forward (victim near sees aggressor near)
    # reverse direction: sdd[2,0] — aggressor response, victim stimulus
    sdd[2, 0] = 0.02  # deliberately != sdd[0,2] → non-reciprocal crosstalk
    # FEXT forward: response at victim_far, stimulus at agg_near = sdd[1,2]
    sdd[1, 2] = 0.08  # FEXT forward (victim far sees aggressor near)
    sdd[2, 1] = 0.01  # reverse, deliberately != sdd[1,2]
    scc = np.eye(3, dtype=complex) * 0.10
    scd = np.zeros((3, 3), dtype=complex)
    sdc = np.zeros((3, 3), dtype=complex)
    s_mm = np.block([[sdd, scd], [sdc, scc]])
    se = T.conj().T @ s_mm @ T
    sv = np.linalg.svd(se, compute_uv=False).max()
    if sv > 1.0:
        se = se / sv

    lines = ["# Hz S RI R 50"]
    flat = []
    for i in range(6):
        for j in range(6):
            flat.append(se[i, j].real)
            flat.append(se[i, j].imag)
    lines.append(f"{f:.6e} " + " ".join(f"{x:.6e}" for x in flat))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestCrosstalkDirection:
    """ERR-001: NEXT/FEXT must read the forward-direction Sdd entries
    (s[response, stimulus] with the victim as response, aggressor as
    stimulus), not the reverse. Locked with a non-reciprocal 6-port where
    sdd[victim, agg] != sdd[agg, victim]."""

    def test_next_forward_direction(self, tmp_path):
        path = tmp_path / "nonrec.s6p"
        _write_nonreciprocal_s6p(path)
        ts = read_touchstone(path)
        mapping = PortMapping(
            pairs=[
                DifferentialPair(name="VNEAR", positive=1, negative=2),
                DifferentialPair(name="VFAR", positive=3, negative=4),
                DifferentialPair(name="AGG", positive=5, negative=6),
            ]
        )
        mm = to_mixed_mode(ts, mapping)
        # Victim near=pair0, victim far=pair1, aggressor near=pair2.
        m = compute_metrics(mm, pair_index=0, far_pair=1)
        # NEXT[AGG] must equal sdd[victim_near=0, agg_near=2], not sdd[2,0].
        expected_next = 20 * np.log10(np.abs(mm.sdd[0, 0, 2]))
        reverse_next = 20 * np.log10(np.abs(mm.sdd[0, 2, 0]))
        assert expected_next != pytest.approx(reverse_next, abs=1e-6)
        assert m.next_db[0][0] == "AGG"
        assert m.next_db[0][1][0] == pytest.approx(expected_next, abs=1e-6)

    def test_fext_forward_direction(self, tmp_path):
        path = tmp_path / "nonrec.s6p"
        _write_nonreciprocal_s6p(path)
        ts = read_touchstone(path)
        mapping = PortMapping(
            pairs=[
                DifferentialPair(name="VNEAR", positive=1, negative=2),
                DifferentialPair(name="VFAR", positive=3, negative=4),
                DifferentialPair(name="AGG", positive=5, negative=6),
            ]
        )
        mm = to_mixed_mode(ts, mapping)
        m = compute_metrics(mm, pair_index=0, far_pair=1)
        # FEXT[AGG] = sdd[victim_far=1, agg_near=2], not sdd[2,1].
        expected_fext = 20 * np.log10(np.abs(mm.sdd[0, 1, 2]))
        reverse_fext = 20 * np.log10(np.abs(mm.sdd[0, 2, 1]))
        assert expected_fext != pytest.approx(reverse_fext, abs=1e-6)
        assert m.fext_db[0][0] == "AGG"
        assert m.fext_db[0][1][0] == pytest.approx(expected_fext, abs=1e-6)

    def test_nonreciprocal_crosstalk_matrix(self, tmp_path):
        """The full non-reciprocal crosstalk matrix: NEXT/FEXT forward values
        must not equal their reverse counterparts, and the reported metrics
        must match the forward entries exactly (ERR-001 regression guard)."""
        path = tmp_path / "nonrec.s6p"
        _write_nonreciprocal_s6p(path)
        ts = read_touchstone(path)
        mapping = PortMapping(
            pairs=[
                DifferentialPair(name="VNEAR", positive=1, negative=2),
                DifferentialPair(name="VFAR", positive=3, negative=4),
                DifferentialPair(name="AGG", positive=5, negative=6),
            ]
        )
        mm = to_mixed_mode(ts, mapping)
        m = compute_metrics(mm, pair_index=0, far_pair=1)
        # Forward vs reverse at the raw S-matrix level.
        assert mm.sdd[0, 0, 2] != mm.sdd[0, 2, 0]  # NEXT fwd != rev
        assert mm.sdd[0, 1, 2] != mm.sdd[0, 2, 1]  # FEXT fwd != rev
        # Reported metrics use the forward entries.
        assert m.next_db[0][1][0] == pytest.approx(
            20 * np.log10(np.abs(mm.sdd[0, 0, 2])), abs=1e-6
        )
        assert m.fext_db[0][1][0] == pytest.approx(
            20 * np.log10(np.abs(mm.sdd[0, 1, 2])), abs=1e-6
        )

    def test_next_reverse_direction(self, tmp_path):
        """Reverse the channel orientation (near=VF, far=VN). NEXT must read
        Sdd[new_near, agg] — the near-end crosstalk at the *new* near end —
        not the transposed Sdd[agg, new_near] (ERR-001 reverse direction)."""
        path = tmp_path / "nonrec.s6p"
        _write_nonreciprocal_s6p(path)
        ts = read_touchstone(path)
        mapping = PortMapping(
            pairs=[
                DifferentialPair(name="VNEAR", positive=1, negative=2),
                DifferentialPair(name="VFAR", positive=3, negative=4),
                DifferentialPair(name="AGG", positive=5, negative=6),
            ]
        )
        mm = to_mixed_mode(ts, mapping)
        # Reversed channel: near=pair1(VF), far=pair0(VN), aggressor=pair2.
        m = compute_metrics(mm, pair_index=1, far_pair=0)
        expected = 20 * np.log10(np.abs(mm.sdd[0, 1, 2]))  # Sdd[new_near=1, agg=2]
        transposed = 20 * np.log10(np.abs(mm.sdd[0, 2, 1]))  # Sdd[agg=2, new_near=1]
        assert expected != pytest.approx(transposed, abs=1e-6)
        assert m.next_db[0][1][0] == pytest.approx(expected, abs=1e-6)

    def test_fext_reverse_direction(self, tmp_path):
        """Reversed channel: FEXT reads Sdd[new_far=0, agg=2] — the far-end
        crosstalk at the *new* far end (ERR-001 reverse direction)."""
        path = tmp_path / "nonrec.s6p"
        _write_nonreciprocal_s6p(path)
        ts = read_touchstone(path)
        mapping = PortMapping(
            pairs=[
                DifferentialPair(name="VNEAR", positive=1, negative=2),
                DifferentialPair(name="VFAR", positive=3, negative=4),
                DifferentialPair(name="AGG", positive=5, negative=6),
            ]
        )
        mm = to_mixed_mode(ts, mapping)
        m = compute_metrics(mm, pair_index=1, far_pair=0)
        expected = 20 * np.log10(np.abs(mm.sdd[0, 0, 2]))  # Sdd[new_far=0, agg=2]
        transposed = 20 * np.log10(np.abs(mm.sdd[0, 2, 0]))
        assert expected != pytest.approx(transposed, abs=1e-6)
        assert m.fext_db[0][1][0] == pytest.approx(expected, abs=1e-6)


# --------------------------------------------------------------------------- #
# ERR-006: explicit near/far channel selection
# --------------------------------------------------------------------------- #

class TestExplicitChannel:
    def test_far_pair_must_be_named_for_three_pairs(self, tmp_path):
        """With n_pairs >= 3 the far end is ambiguous — compute_metrics must
        refuse to guess and require an explicit far_pair (ERR-006)."""
        path = tmp_path / "nonrec.s6p"
        _write_nonreciprocal_s6p(path)
        ts = read_touchstone(path)
        mapping = PortMapping(
            pairs=[
                DifferentialPair(name="A", positive=1, negative=2),
                DifferentialPair(name="B", positive=3, negative=4),
                DifferentialPair(name="C", positive=5, negative=6),
            ]
        )
        mm = to_mixed_mode(ts, mapping)
        with pytest.raises(ValueError, match="ambiguous"):
            compute_metrics(mm, pair_index=0)

    def test_far_pair_equals_near_raises(self, tmp_path):
        path = tmp_path / "nonrec.s6p"
        _write_nonreciprocal_s6p(path)
        ts = read_touchstone(path)
        mapping = PortMapping(
            pairs=[
                DifferentialPair(name="A", positive=1, negative=2),
                DifferentialPair(name="B", positive=3, negative=4),
                DifferentialPair(name="C", positive=5, negative=6),
            ]
        )
        mm = to_mixed_mode(ts, mapping)
        with pytest.raises(ValueError, match="near and far"):
            compute_metrics(mm, pair_index=0, far_pair=0)

    def test_two_complete_differential_channels(self, tmp_path):
        """A 4-pair file defines two independent channels (CH0: P0→P1,
        CH1: P2→P3). Each must be analyzable on its own with explicit
        near/far (ERR-006)."""
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path)
        ts = read_touchstone(path)
        mapping = PortMapping(
            pairs=[
                DifferentialPair(name="CH0_N", positive=1, negative=2),
                DifferentialPair(name="CH0_F", positive=3, negative=4),
            ]
        )
        mm = to_mixed_mode(ts, mapping)
        m = compute_metrics(mm, pair_index=0, far_pair=1)
        assert m.transmission_applicable is True
        assert m.far_pair_index == 1
        assert "Sdd21_dB" in m.at_frequency(16e9)

    def test_reverse_direction_available(self, tmp_path):
        """Reverse-direction transmission (Sdd12/Scc12/Scd12/Sdc12) must be
        reported alongside the forward values (ERR-006). For the reciprocal
        demo they are equal; the keys must still be present."""
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path)
        ts = read_touchstone(path)
        mapping = PortMapping(
            pairs=[
                DifferentialPair(name="A", positive=1, negative=2),
                DifferentialPair(name="B", positive=3, negative=4),
            ]
        )
        mm = to_mixed_mode(ts, mapping)
        m = compute_metrics(mm, pair_index=0, far_pair=1)
        snap = m.at_frequency(16e9)
        for key in ("Sdd12_dB", "Scc12_dB", "Scd12_dB", "Sdc12_dB"):
            assert key in snap, key
        # Reciprocal demo → forward == reverse.
        assert snap["Sdd21_dB"] == pytest.approx(snap["Sdd12_dB"], abs=1e-6)

    def test_multiple_aggressor_channels(self, tmp_path):
        """With 4 differential pairs (victim + 2 aggressors) both aggressors
        must appear in the NEXT/FEXT lists (ERR-006 multi-aggressor)."""
        # Build a reciprocal 8-port (.s8p) passive-ish network with 4 diff pairs.
        f = 1e9
        inv = 1.0 / np.sqrt(2.0)
        T = np.zeros((8, 8), dtype=float)
        for k in range(4):
            p, q = 2 * k, 2 * k + 1
            T[k, p] = inv
            T[k, q] = -inv
            T[4 + k, p] = inv
            T[4 + k, q] = inv
        sdd = np.zeros((4, 4), dtype=complex)
        sdd[0, 0] = 0.3
        sdd[1, 1] = 0.2
        sdd[2, 2] = 0.3
        sdd[3, 3] = 0.2
        sdd[1, 0] = 0.6
        sdd[0, 1] = 0.6
        sdd[0, 2] = 0.05  # NEXT to two aggressors
        sdd[0, 3] = 0.04
        sdd[1, 2] = 0.03  # FEXT to two aggressors
        sdd[1, 3] = 0.02
        scc = np.eye(4, dtype=complex) * 0.1
        s_mm = np.block([[sdd, np.zeros((4, 4), dtype=complex)],
                         [np.zeros((4, 4), dtype=complex), scc]])
        se = T.conj().T @ s_mm @ T
        sv = np.linalg.svd(se, compute_uv=False).max()
        if sv > 1.0:
            se = se / sv
        lines = ["# Hz S RI R 50"]
        flat = []
        for i in range(8):
            for j in range(8):
                flat.append(se[i, j].real)
                flat.append(se[i, j].imag)
        lines.append(f"{f:.6e} " + " ".join(f"{x:.6e}" for x in flat))
        path = tmp_path / "multi.s8p"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ts = read_touchstone(path)
        mapping = PortMapping(
            pairs=[
                DifferentialPair(name="VN", positive=1, negative=2),
                DifferentialPair(name="VF", positive=3, negative=4),
                DifferentialPair(name="AG0", positive=5, negative=6),
                DifferentialPair(name="AG1", positive=7, negative=8),
            ]
        )
        mm = to_mixed_mode(ts, mapping)
        m = compute_metrics(mm, pair_index=0, far_pair=1)
        assert [name for name, _ in m.next_db] == ["AG0", "AG1"]
        assert [name for name, _ in m.fext_db] == ["AG0", "AG1"]


# --------------------------------------------------------------------------- #
# ERR-003: single differential port — transmission not applicable
# --------------------------------------------------------------------------- #

class TestSingleDifferentialPort:
    def test_single_differential_port_sdd21_not_applicable(self, tmp_path):
        """A 2-port .s2p with one differential pair has no far-end partner.
        Sdd21 must be NaN (not 0 dB = perfect transmission) and
        transmission_applicable must be False (ERR-003)."""
        path = tmp_path / "one.s2p"
        path.write_text(
            "# Hz S RI R 50\n"
            "1.0e9 0.3 0.0 0.0 0.0 0.0 0.0 0.3 0.0\n",
            encoding="utf-8",
        )
        ts = read_touchstone(path)
        mapping = PortMapping(
            pairs=[DifferentialPair(name="P", positive=1, negative=2)]
        )
        mm = to_mixed_mode(ts, mapping)
        m = compute_metrics(mm, pair_index=0)
        assert m.transmission_applicable is False
        assert np.all(np.isnan(m.sdd21_db))
        assert np.all(np.isnan(m.scd21_db))

    def test_single_differential_port_report_shows_na(self, tmp_path):
        """The worst-case table must show N/A for Sdd21 on a single port, not
        a fabricated 0 dB (ERR-003)."""
        path = tmp_path / "one.s2p"
        path.write_text(
            "# Hz S RI R 50\n"
            "1.0e9 0.3 0.0 0.0 0.0 0.0 0.0 0.3 0.0\n",
            encoding="utf-8",
        )
        ts = read_touchstone(path)
        mapping = PortMapping(
            pairs=[DifferentialPair(name="P", positive=1, negative=2)]
        )
        mm = to_mixed_mode(ts, mapping)
        m = compute_metrics(mm, pair_index=0)
        wc = worst_case(m)
        assert wc["Sdd21_dB_worst"] is None
        assert wc["worst_Sdd21_freq_hz"] is None

    def test_single_differential_port_acceptance_is_error(self, tmp_path):
        """An acceptance rule on Sdd21 for a single-port (transmission not
        applicable) must surface as ERROR (metric not available), not a
        misleading 0 dB PASS/FAIL (ERR-003)."""
        from em_copilot.analysis import evaluate_acceptance
        from em_copilot.reporting import build_report_data
        from em_copilot.schemas import AcceptanceRule, FrequencyPoint, SITask

        path = tmp_path / "one.s2p"
        path.write_text(
            "# Hz S RI R 50\n"
            "1.0e9 0.3 0.0 0.0 0.0 0.0 0.0 0.3 0.0\n",
            encoding="utf-8",
        )
        ts = read_touchstone(path)
        mapping = PortMapping(
            pairs=[DifferentialPair(name="P", positive=1, negative=2)]
        )
        mm = to_mixed_mode(ts, mapping)
        m = compute_metrics(mm, pair_index=0)
        rule = AcceptanceRule(
            expression="Sdd21", operator=">=", value="-12dB",
            frequency="1GHz", description="IL floor",
        )
        out = evaluate_acceptance(m, [rule])[0]
        assert out["passed"] is False
        assert "error" in out
        assert "not available" in out["error"]
        # At the report level, ERROR takes precedence (ERR-004).
        task = SITask(
            task_id="t", touchstone=str(path), port_mapping=mapping,
            acceptance=[rule], points_of_interest=[FrequencyPoint(frequency="1GHz")],
        )
        data = build_report_data(task, ts, mm, m, [])
        from em_copilot.schemas import ComplianceStatus
        assert data.compliance == ComplianceStatus.ERROR

