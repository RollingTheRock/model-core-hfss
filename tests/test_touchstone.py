"""Tests for the Touchstone reader (roadmap §10.3, §15.1).

Covers the HFSS-license-free parse path: option-line handling, the three data
formats (MA/RI/DB), flat-token accumulation across wrapped physical lines,
comment handling, port-count inference, NaN/Inf flagging, and preflight checks.
The round-trip case uses the demo synthetic .s4p generator so the whole read
path is exercised against a real Touchstone file, not a fixture.
"""

from __future__ import annotations

import numpy as np
import pytest

from em_copilot.demo import write_demo_touchstone
from em_copilot.touchstone import (
    _check_invalid_values,
    _freq_multiplier,
    _infer_n_ports,
    _parse_number,
    check_port_and_freq,
    read_touchstone,
)

# --------------------------------------------------------------------------- #
# _parse_number
# --------------------------------------------------------------------------- #

class TestParseNumber:
    def test_plain_float(self):
        assert _parse_number("1.234") == 1.234
        assert _parse_number("1.234E-2") == 0.01234

    def test_signed(self):
        assert _parse_number("+5") == 5.0
        assert _parse_number("-3.5") == -3.5

    def test_inf(self):
        for tok in ("inf", "+inf", "-inf"):
            assert np.isinf(_parse_number(tok))

    def test_nan(self):
        assert np.isnan(_parse_number("nan"))

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            _parse_number("   ")

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError):
            _parse_number("abc")


# --------------------------------------------------------------------------- #
# _freq_multiplier / _infer_n_ports
# --------------------------------------------------------------------------- #

class TestHelpers:
    def test_freq_multipliers(self):
        assert _freq_multiplier("hz") == 1.0
        assert _freq_multiplier("kHz") == 1e3
        assert _freq_multiplier("MHz") == 1e6
        assert _freq_multiplier("GHz") == 1e9

    def test_unknown_unit_raises(self):
        with pytest.raises(ValueError, match="unknown frequency unit"):
            _freq_multiplier("THz")

    def test_infer_n_ports(self, tmp_path):
        assert _infer_n_ports(tmp_path / "foo.s2p") == 2
        assert _infer_n_ports(tmp_path / "foo.s4p") == 4
        assert _infer_n_ports(tmp_path / "foo.s3p") == 3
        # Multi-digit port counts must be supported (I-01).
        assert _infer_n_ports(tmp_path / "foo.s10p") == 10
        assert _infer_n_ports(tmp_path / "foo.s16p") == 16
        assert _infer_n_ports(tmp_path / "foo.s128p") == 128

    def test_infer_bad_extension_raises(self, tmp_path):
        with pytest.raises(ValueError, match="cannot infer port count"):
            _infer_n_ports(tmp_path / "foo.txt")
        # ".sNp" with a non-numeric middle does not match the regex.
        with pytest.raises(ValueError, match="cannot infer port count"):
            _infer_n_ports(tmp_path / "foo.sNp")


# --------------------------------------------------------------------------- #
# read_touchstone — format parsing
# --------------------------------------------------------------------------- #

# A known NON-RECIPROCAL 2-port (S21 != S12) so the on-disk data order is
# visible and a transposing reader cannot hide behind reciprocity (I-01).
# On-disk Touchstone 1.0 order: S11, S21, S12, S22.
# Package convention: s[response, stimulus] => matrix [[S11, S12], [S21, S22]].
_S11 = 0.1 * np.exp(1j * np.deg2rad(10.0))
_S21 = 0.8 + 0.0j   # forward gain
_S12 = 0.3 + 0.0j   # reverse gain, deliberately != S21
_S22 = 0.2 * np.exp(1j * np.deg2rad(-15.0))
_EXPECTED_S = np.array([[_S11, _S12], [_S21, _S22]], dtype=complex)


def _write_s2p(path, fmt: str, freq_ghz: float = 1.0) -> None:
    """Write a one-frequency .s2p in MA/RI/DB so formats can be cross-checked."""
    if fmt == "MA":
        def pair(c):
            return f"{np.abs(c):.6E} {np.rad2deg(np.angle(c)):.4f}"
    elif fmt == "RI":
        def pair(c):
            return f"{c.real:.6E} {c.imag:.6E}"
    elif fmt == "DB":
        def pair(c):
            mag = np.abs(c)
            mag = max(mag, 1e-12)
            return f"{20 * np.log10(mag):.6E} {np.rad2deg(np.angle(c)):.4f}"
    else:  # pragma: no cover - test guard
        raise ValueError(fmt)
    order = [_S11, _S21, _S12, _S22]
    data = " ".join(pair(c) for c in order)
    txt = (
        "! test s2p\n"
        f"# GHz S {fmt} R 50\n"
        f"{freq_ghz:.6g} {data}\n"
    )
    path.write_text(txt, encoding="utf-8")


class TestReadFormats:
    @pytest.mark.parametrize("fmt", ["MA", "RI", "DB"])
    def test_format_round_trips(self, tmp_path, fmt):
        path = tmp_path / "dut.s2p"
        _write_s2p(path, fmt)
        ts = read_touchstone(path)
        assert ts.n_ports == 2
        assert ts.format == fmt
        assert ts.z0 == 50.0
        assert ts.freqs.size == 1
        # Frequency unit GHz → 1e9 multiplier applied.
        assert ts.freqs[0] == pytest.approx(1e9)
        assert ts.s.shape == (1, 2, 2)
        np.testing.assert_allclose(ts.s[0], _EXPECTED_S, atol=1e-6)
        # Non-reciprocal direction must be preserved (I-01): S21 != S12.
        assert ts.s[0, 1, 0] == pytest.approx(_S21, abs=1e-6)  # response1, stim0
        assert ts.s[0, 0, 1] == pytest.approx(_S12, abs=1e-6)  # response0, stim1

    def test_all_three_formats_agree(self, tmp_path):
        """MA, RI, DB encodings of the same S-matrix must parse identically."""
        parsed = {}
        for fmt in ("MA", "RI", "DB"):
            path = tmp_path / f"dut_{fmt}.s2p"
            _write_s2p(path, fmt)
            parsed[fmt] = read_touchstone(path).s[0]
        np.testing.assert_allclose(parsed["MA"], parsed["RI"], atol=1e-6)
        np.testing.assert_allclose(parsed["MA"], parsed["DB"], atol=1e-6)

    def test_freq_unit_applied(self, tmp_path):
        # Same numbers, different declared unit → different Hz magnitude.
        for unit, mult in [("hz", 1.0), ("MHz", 1e6), ("GHz", 1e9)]:
            path = tmp_path / f"dut_{unit}.s2p"
            path.write_text(
                f"# {unit} S MA R 50\n1.0 0.1 0 0.9 0 0.9 0 0.1 0\n",
                encoding="utf-8",
            )
            ts = read_touchstone(path)
            assert ts.freqs[0] == pytest.approx(1.0 * mult), unit


# --------------------------------------------------------------------------- #
# read_touchstone — option line defaults (ERR-005)
# --------------------------------------------------------------------------- #

class TestOptionLineDefaults:
    """Per the Touchstone spec, when the option line omits the frequency unit
    the default is GHz (not Hz). A file with ``# S MA R 50`` must interpret
    bare frequency numbers as GHz, not Hz — otherwise the sweep is off by
    10^9 (ERR-005)."""

    def test_option_line_missing_fields_default_to_ghz_s_ma_r50(self, tmp_path):
        path = tmp_path / "nounit.s2p"
        path.write_text("# S MA R 50\n0.1 0.1 0 0.9 0 0.9 0 0.1 0\n", encoding="utf-8")
        ts = read_touchstone(path)
        # 0.1 GHz, not 0.1 Hz.
        assert ts.freqs[0] == pytest.approx(0.1e9)
        assert ts.freq_unit == "ghz"
        assert ts.format == "MA"
        assert ts.z0 == 50.0

    def test_empty_option_line_defaults(self, tmp_path):
        # A bare ``#`` with no fields at all — spec defaults apply (GHz/S/MA/R50).
        path = tmp_path / "bare.s2p"
        path.write_text("#\n0.1 0.1 0 0.9 0 0.9 0 0.1 0\n", encoding="utf-8")
        ts = read_touchstone(path)
        assert ts.freqs[0] == pytest.approx(0.1e9)
        assert ts.freq_unit == "ghz"
        assert ts.format == "MA"
        assert ts.z0 == 50.0

    def test_explicit_hz_overrides_default(self, tmp_path):
        # An explicit ``Hz`` token must override the GHz default (no regression).
        path = tmp_path / "exphz.s2p"
        path.write_text("# Hz S MA R 50\n1.0 0.1 0 0.9 0 0.9 0 0.1 0\n", encoding="utf-8")
        ts = read_touchstone(path)
        assert ts.freqs[0] == pytest.approx(1.0)
        assert ts.freq_unit == "hz"

    def test_missing_option_line_warned(self, tmp_path):
        # No option line at all → spec default (GHz) + an audit comment.
        path = tmp_path / "noopt.s2p"
        path.write_text("0.1 0.1 0 0.9 0 0.9 0 0.1 0\n", encoding="utf-8")
        ts = read_touchstone(path)
        assert ts.freqs[0] == pytest.approx(0.1e9)
        assert any("option line" in c.lower() for c in ts.comments)


# --------------------------------------------------------------------------- #
# read_touchstone — wrapping, comments, errors
# --------------------------------------------------------------------------- #

class TestReadStructure:
    def test_multi_line_wrapping(self, tmp_path):
        """Flat-token accumulation: a row split across many physical lines
        must reassemble into one frequency row."""
        path = tmp_path / "wrap.s2p"
        # row_size = 9; split the 8 data tokens across 3 lines (3+3+2).
        # On-disk order S11,S21,S12,S22 with the non-reciprocal fixture.
        path.write_text(
            "# Hz S MA R 50\n"
            "1.0e9 0.1 10.0 0.8\n"
            "0.0 0.3 0.0\n"
            "0.2 -15.0\n",
            encoding="utf-8",
        )
        ts = read_touchstone(path)
        assert ts.freqs.size == 1
        assert ts.s.shape == (1, 2, 2)
        np.testing.assert_allclose(ts.s[0], _EXPECTED_S, atol=1e-6)

    def test_inline_comment_stripped(self, tmp_path):
        """Inline ``!`` comments after data tokens must be stripped before
        numeric parsing (I-09 / Touchstone spec)."""
        path = tmp_path / "inline.s2p"
        path.write_text(
            "# Hz S MA R 50\n"
            "1.0e9 0.1 10.0 0.8 0.0 0.3 0.0 0.2 -15.0 ! trailing inline note\n"
            "2.0e9 0.1 10.0 0.8 0.0 0.3 0.0 0.2 -15.0 ! second row note\n",
            encoding="utf-8",
        )
        ts = read_touchstone(path)
        assert ts.freqs.size == 2
        np.testing.assert_allclose(ts.s[0], _EXPECTED_S, atol=1e-6)
        # Inline comment text is captured for audit.
        assert any("trailing inline note" in c for c in ts.comments)

    def test_comments_and_blank_lines(self, tmp_path):
        path = tmp_path / "cmt.s2p"
        path.write_text(
            "! first comment\n"
            "\n"
            "! second comment\n"
            "# Hz S MA R 50 Z0=\"something\"\n"
            "\n"
            "1.0e9 0.1 10.0 0.8 0.0 0.3 0.0 0.2 -15.0\n"
            "! trailing comment\n",
            encoding="utf-8",
        )
        ts = read_touchstone(path)
        assert ts.freqs.size == 1
        np.testing.assert_allclose(ts.s[0], _EXPECTED_S, atol=1e-6)
        assert len(ts.comments) == 3  # first, second, trailing

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_touchstone(tmp_path / "nope.s2p")

    def test_truncated_raises(self, tmp_path):
        path = tmp_path / "trunc.s2p"
        # row_size=9; give only 5 tokens → not a multiple of 9.
        path.write_text("# Hz S MA R 50\n1.0 0.1 10.0 0.9 0.0\n", encoding="utf-8")
        with pytest.raises(ValueError, match="multiple of"):
            read_touchstone(path)

    def test_empty_data_raises(self, tmp_path):
        path = tmp_path / "empty.s2p"
        path.write_text("# Hz S MA R 50\n! just comments\n", encoding="utf-8")
        with pytest.raises(ValueError, match="no data rows"):
            read_touchstone(path)

    def test_non_s_parameter_raises(self, tmp_path):
        path = tmp_path / "y.s2p"
        path.write_text("# Hz S Y R 50\n1.0 0.1 0 0.9 0 0.9 0 0.1 0\n", encoding="utf-8")
        with pytest.raises(ValueError, match="only S-parameters"):
            read_touchstone(path)


# --------------------------------------------------------------------------- #
# read_touchstone — demo round-trip (real .s4p)
# --------------------------------------------------------------------------- #

class TestDemoRoundTrip:
    def test_demo_s4p_parses(self, tmp_path):
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path, fmin_ghz=0.1, fmax_ghz=40.0, n=401)
        ts = read_touchstone(path)
        assert ts.n_ports == 4
        assert ts.format == "MA"
        assert ts.z0 == 50.0
        assert ts.freqs.size == 401
        assert ts.freqs[0] == pytest.approx(0.1e9)
        assert ts.freqs[-1] == pytest.approx(40e9)
        assert ts.s.shape == (401, 4, 4)
        # S-parameters of a passive network: every singular value <= 1.
        for f in range(0, 401, 40):
            sv = np.linalg.svd(ts.s[f], compute_uv=False)
            assert sv.max() <= 1.0 + 1e-9, f"non-passive at freq index {f}"

    def test_demo_frequencies_monotonic(self, tmp_path):
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path)
        ts = read_touchstone(path)
        assert np.all(np.diff(ts.freqs) > 0)


# --------------------------------------------------------------------------- #
# _check_invalid_values
# --------------------------------------------------------------------------- #

class TestInvalidValues:
    def test_clean_data_no_warnings(self):
        freqs = np.array([1e9, 2e9])
        s = np.zeros((2, 2, 2), dtype=complex)
        assert _check_invalid_values(freqs, s, "x.s2p") == []

    def test_nan_inf_flagged(self):
        freqs = np.array([1e9, np.nan, np.inf])
        s = np.array([[[1.0, 0.0], [0.0, np.nan]]], dtype=complex)
        warnings = _check_invalid_values(freqs, s, "x.s2p")
        assert len(warnings) == 2
        assert "non-finite frequency" in warnings[0]
        assert "non-finite S-parameter" in warnings[1]


# --------------------------------------------------------------------------- #
# check_port_and_freq (preflight)
# --------------------------------------------------------------------------- #

class TestPreflight:
    def test_clean_demo_no_warnings(self, tmp_path):
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path)
        ts = read_touchstone(path)
        assert check_port_and_freq(ts, expected_ports=4) == []

    def test_port_count_mismatch(self, tmp_path):
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path)
        ts = read_touchstone(path)
        # NEW-008: a file with MORE ports than the mapping needs is a WARN
        # (extra ports ignored), not an ERROR — the common workflow of
        # analyzing a subset of ports from a larger .sNp file.
        w = check_port_and_freq(ts, expected_ports=2)
        assert len(w) == 1
        assert "extra ports ignored" in w[0]
        # A file with FEWER ports than the mapping needs is still an ERROR —
        # the mapping references ports that don't exist.
        w_err = check_port_and_freq(ts, expected_ports=6)
        assert len(w_err) == 1
        assert "file has only" in w_err[0]

    def test_range_warnings(self, tmp_path):
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path, fmin_ghz=0.1, fmax_ghz=40.0)
        ts = read_touchstone(path)
        # Demand a band wider than the file covers.
        w = check_port_and_freq(ts, fmin=0.0, fmax=50e9)
        assert any("above the required" in s for s in w)
        assert any("below the required" in s for s in w)

    def test_non_monotonic_flagged(self, tmp_path):
        path = tmp_path / "nonmono.s2p"
        # Two freqs out of order: 2 GHz then 1 GHz.
        path.write_text(
            "# GHz S MA R 50\n"
            "2.0 0.1 0 0.9 0 0.9 0 0.1 0\n"
            "1.0 0.1 0 0.9 0 0.9 0 0.1 0\n",
            encoding="utf-8",
        )
        ts = read_touchstone(path)
        w = check_port_and_freq(ts, require_monotonic=True)
        assert any("not strictly increasing" in s for s in w)

    def test_nan_flagged_in_preflight(self, tmp_path):
        path = tmp_path / "bad.s2p"
        path.write_text(
            "# GHz S MA R 50\n"
            "1.0 0.1 0 0.9 0 0.9 0 0.1 0\n"
            "2.0 nan 0 0.9 0 0.9 0 0.1 0\n",
            encoding="utf-8",
        )
        ts = read_touchstone(path)
        w = check_port_and_freq(ts)
        assert any("non-finite S-parameter" in s for s in w)


# --------------------------------------------------------------------------- #
# ERR-011: preflight ERROR/WARN grading — fatal issues block, warns surface.
# --------------------------------------------------------------------------- #

class TestPreflightGrading:
    """§9.5 / ERR-011: preflight issues are graded ERROR (block) vs WARN
    (surface). NaN/Inf, port-count mismatch, and no-frequency-points are
    ERROR; non-monotonic frequencies escalate to ERROR when TDR is on."""

    def test_port_mismatch_is_error(self, tmp_path):
        """A port-count *shortage* is ERROR — the mapping references ports that
        don't exist. (NEW-008: a port-count *surplus* is now WARN, not ERROR,
        so a subset of ports can be analyzed from a larger .sNp file.)"""
        from em_copilot.touchstone import split_preflight
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path)
        ts = read_touchstone(path)
        # 4-port file feeding a 6-port (3-pair) mapping → ERROR (too few ports).
        issues = check_port_and_freq(ts, expected_ports=6)
        errors, warns = split_preflight(issues)
        assert len(errors) == 1
        assert errors[0].level == "ERROR"
        assert "file has only" in errors[0]
        assert warns == []
        # Conversely, a 4-port file feeding a 2-port (1-pair) mapping → WARN
        # (extra ports ignored), not ERROR.
        issues2 = check_port_and_freq(ts, expected_ports=2)
        errors2, warns2 = split_preflight(issues2)
        assert errors2 == []
        assert len(warns2) == 1
        assert "extra ports ignored" in warns2[0]

    def test_nan_inf_is_error(self, tmp_path):
        """NaN/Inf in the raw data is ERROR — np.max/np.min would produce NaN
        and mislabel a corrupt file as FAIL rather than ERROR."""
        from em_copilot.touchstone import split_preflight
        path = tmp_path / "bad.s2p"
        path.write_text(
            "# GHz S MA R 50\n"
            "1.0 0.1 0 0.9 0 0.9 0 0.1 0\n"
            "2.0 nan 0 0.9 0 0.9 0 0.1 0\n",
            encoding="utf-8",
        )
        ts = read_touchstone(path)
        issues = check_port_and_freq(ts)
        errors, warns = split_preflight(issues)
        assert any("non-finite" in e for e in errors)
        assert all(e.level == "ERROR" for e in errors)

    def test_range_warning_does_not_block(self, tmp_path):
        """A sweep narrower than the acceptance band is WARN, not ERROR — it
        surfaces in the report but does not block analysis."""
        from em_copilot.touchstone import split_preflight
        path = tmp_path / "demo.s4p"
        write_demo_touchstone(path, fmin_ghz=0.1, fmax_ghz=40.0)
        ts = read_touchstone(path)
        issues = check_port_and_freq(ts, fmin=0.0, fmax=50e9)
        errors, warns = split_preflight(issues)
        assert errors == []
        assert len(warns) >= 2  # below fmax + above fmin
        assert all(w.level == "WARN" for w in warns)

    def test_non_monotonic_warns_without_tdr(self, tmp_path):
        """Non-monotonic frequencies are WARN for frequency-domain metrics
        (which tolerate them) — analysis proceeds."""
        from em_copilot.touchstone import split_preflight
        path = tmp_path / "nonmono.s2p"
        path.write_text(
            "# GHz S MA R 50\n"
            "2.0 0.1 0 0.9 0 0.9 0 0.1 0\n"
            "1.0 0.1 0 0.9 0 0.9 0 0.1 0\n",
            encoding="utf-8",
        )
        ts = read_touchstone(path)
        issues = check_port_and_freq(ts, require_monotonic=True)
        errors, warns = split_preflight(issues, tdr_enabled=False)
        assert errors == []
        assert any("not strictly increasing" in w for w in warns)

    def test_non_monotonic_blocks_when_tdr_enabled(self, tmp_path):
        """Non-monotonic frequencies escalate to ERROR when TDR is on —
        numpy.fft.irfft requires a strictly increasing uniform grid, so feeding
        it non-monotonic data silently produces a garbage impedance trace
        (ERR-011 / I-04)."""
        from em_copilot.touchstone import split_preflight
        path = tmp_path / "nonmono.s2p"
        path.write_text(
            "# GHz S MA R 50\n"
            "2.0 0.1 0 0.9 0 0.9 0 0.1 0\n"
            "1.0 0.1 0 0.9 0 0.9 0 0.1 0\n",
            encoding="utf-8",
        )
        ts = read_touchstone(path)
        issues = check_port_and_freq(ts, require_monotonic=True)
        errors, warns = split_preflight(issues, tdr_enabled=True)
        assert len(errors) == 1
        assert errors[0].level == "ERROR"
        assert "not strictly increasing" in errors[0]
        assert warns == []

    def test_preflight_error_carries_issues(self):
        """PreflightError carries the ERROR-level issues so the caller (batch
        isolation, CLI error report) can surface them verbatim."""
        from em_copilot.touchstone import PreflightError, PreflightIssue
        issues = [
            PreflightIssue("fatal: NaN in data", level="ERROR"),
            PreflightIssue("warn: narrow sweep", level="WARN"),
        ]
        err = PreflightError([i for i in issues if i.level == "ERROR"])
        assert len(err.errors) == 1
        assert "NaN" in err.errors[0]
        # It's a ValueError subclass so legacy except-ValueError paths catch it.
        assert isinstance(err, ValueError)

    def test_cli_blocks_on_preflight_error(self, tmp_path):
        """The CLI must exit 2 (ERROR) when preflight blocks, and must NOT
        proceed to compute_metrics/TDR on corrupt data."""
        from em_copilot.cli import _run_analysis
        from em_copilot.schemas import (
            ComplianceStatus,
            DifferentialPair,
            PortMapping,
            SITask,
        )
        path = tmp_path / "bad.s2p"
        path.write_text(
            "# GHz S MA R 50\n"
            "1.0 0.1 0 0.9 0 0.9 0 0.1 0\n"
            "2.0 nan 0 0.9 0 0.9 0 0.1 0\n",
            encoding="utf-8",
        )
        mapping = PortMapping(
            pairs=[DifferentialPair(name="P", positive=1, negative=2)]
        )
        task = SITask(
            task_id="t", touchstone=str(path), port_mapping=mapping,
        )
        code = _run_analysis(task, tmp_path / "out", compute_tdr_flag=False)
        assert code == ComplianceStatus.ERROR.exit_code
        # No report/metrics written when blocked.
        assert not (tmp_path / "out" / "metrics.json").exists()

    def test_batch_raises_preflight_error(self, tmp_path):
        """Batch analysis isolates per-file failures: a corrupt file raises
        PreflightError, recorded as an error (n_error+1), not a FAIL."""
        from em_copilot.batch import analyze_one
        from em_copilot.schemas import (
            DifferentialPair,
            PortMapping,
        )
        from em_copilot.touchstone import PreflightError
        path = tmp_path / "bad.s2p"
        path.write_text(
            "# GHz S MA R 50\n"
            "1.0 0.1 0 0.9 0 0.9 0 0.1 0\n"
            "2.0 nan 0 0.9 0 0.9 0 0.1 0\n",
            encoding="utf-8",
        )
        mapping = PortMapping(
            pairs=[DifferentialPair(name="P", positive=1, negative=2)]
        )
        with pytest.raises(PreflightError):
            analyze_one(
                path, mapping, [], [], tmp_path / "out",
                task_id="bad", compute_tdr_flag=False,
            )
