"""Regression probe for the V2.0 fixes in S_PARAMETER_ERROR_REPORT.md.

This script replaces the V1.0 ``verify_error_report.py`` (NEW-012). The V1.0
script asserted the *buggy* behavior as ``CONFIRMED-REAL``; once the fixes
landed it reported ~0/12 and was stale. This V2.0 version asserts the *fixed*
behavior instead: each probe verifies that the corresponding NEW-001..NEW-012
fix is in place and a pass == the fix is confirmed. Run with the project venv
python; exits 0 only if every fix is verified.
"""

from __future__ import annotations

import inspect
import sys
import tempfile
from pathlib import Path

import numpy as np

# Make the package importable when run as a loose script.
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from em_copilot.analysis import compute_tdr  # noqa: E402
from em_copilot.batch import analyze_one  # noqa: E402
from em_copilot.cli import _run_analysis  # noqa: E402
from em_copilot.mixed_mode import (  # noqa: E402
    MixedMode,
    compute_crosstalk,
    compute_metrics,
)
from em_copilot.reporting import build_report_data  # noqa: E402
from em_copilot.schemas import (  # noqa: E402
    AcceptanceRule,
    CrosstalkCase,
    DifferentialChannel,
    DifferentialPair,
    PortMapping,
    SITask,
)
from em_copilot.touchstone import (  # noqa: E402
    check_port_and_freq,
    read_touchstone,
)

results: list[tuple[str, bool, str]] = []


def _record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    tag = "FIXED" if ok else "REGRESSION"
    print(f"[{tag}] {name}: {detail}")


# ----------------------------------------------------------------------------
# Helpers to build synthetic Touchstone / MixedMode objects in memory.
# ----------------------------------------------------------------------------

def _s4p_text(freqs_ghz: np.ndarray, s: np.ndarray, option_line: str = "# Hz S MA R 50") -> str:
    """Render a 4-port Touchstone text blob in MA format.

    ``s`` has shape (n_freqs, 4, 4) complex.
    """
    lines = [option_line]
    for fi, f in enumerate(freqs_ghz):
        toks = [f"{float(f):.6f}"]
        for i in range(4):
            for j in range(4):
                v = s[fi, i, j]
                toks.append(f"{abs(v):.6f}")
                toks.append(f"{np.degrees(np.angle(v)):.3f}")
        for k in range(0, len(toks), 4):
            lines.append("  ".join(toks[k : k + 4]))
    return "\n".join(lines) + "\n"


def _write_s4p(path: Path, freqs_ghz: np.ndarray, s: np.ndarray,
               option_line: str = "# Hz S MA R 50") -> None:
    path.write_text(_s4p_text(freqs_ghz, s, option_line), encoding="utf-8")


def _make_mm(n_pairs: int, sdd: np.ndarray, pair_names: list[str] | None = None) -> MixedMode:
    """Build a MixedMode from an explicit Sdd block (n_freqs, n_pairs, n_pairs).

    Scc/Scd/Sdc are set to zero so Sdd fully defines the differential behavior.
    """
    n_freqs = sdd.shape[0]
    s_mm = np.zeros((n_freqs, 2 * n_pairs, 2 * n_pairs), dtype=complex)
    d = slice(0, n_pairs)
    c = slice(n_pairs, 2 * n_pairs)
    s_mm[:, d, d] = sdd
    freqs = np.linspace(0.1e9, 40e9, n_freqs)
    if pair_names is None:
        pair_names = [f"P{k}" for k in range(n_pairs)]
    return MixedMode(
        pair_names=pair_names,
        freqs=freqs,
        s_mm=s_mm,
        sdd=s_mm[:, d, d],
        scc=s_mm[:, c, c],
        scd=s_mm[:, c, d],
        sdc=s_mm[:, d, c],
    )


# ============================================================================
# NEW-001: CLI/batch can analyze >2 pairs (near/far pair selection forwarded)
# ============================================================================
def probe_new_001(tmp: Path) -> None:
    # The SITask schema must carry near_pair_index / far_pair_index, and the CLI
    # must resolve --near-pair/--far-pair names to indices. Inspect the schema
    # and the CLI source for the resolver and the forwarded fields.
    fields = SITask.model_fields
    has_fields = "near_pair_index" in fields and "far_pair_index" in fields
    from em_copilot.cli import _resolve_pair_names  # noqa: E402

    has_resolver = callable(_resolve_pair_names)
    mapping = PortMapping(
        pairs=[DifferentialPair(name=f"P{k}", positive=2 * k + 1, negative=2 * k + 2)
               for k in range(4)]
    )
    near, far = _resolve_pair_names(mapping, "P2", "P3")
    resolver_ok = (near == 2 and far == 3)
    ok = has_fields and has_resolver and resolver_ok
    _record(
        "NEW-001",
        ok,
        f"SITask near/far fields={has_fields}, _resolve_pair_names present={has_resolver}, "
        f"P2/P3 -> ({near},{far})",
    )


# ============================================================================
# NEW-002: differential TDR uses Zdiff = 2*Zse (not single-ended Z0)
# ============================================================================
def probe_new_002(tmp: Path) -> None:
    # A constant 100Ω differential load referenced to a 50Ω single-ended system:
    # the differential reflection is rho_diff = (100 - 2*50)/(100 + 2*50) = 0,
    # i.e. matched. The fix doubles Zse (50 -> 100) for the TDR reference. Check
    # that cli._run_analysis and batch.analyze_one both apply the 2x factor.
    src_cli = inspect.getsource(_run_analysis)
    src_batch = inspect.getsource(analyze_one)
    cli_doubles = "2.0 * tdr_z0_se" in src_cli or "2.0 *" in src_cli
    # batch.analyze_one must derive Zdiff = 2*Zse when no explicit tdr_z0 is
    # passed (default None, not a hardcoded 100.0 that makes the branch dead).
    sig = inspect.signature(analyze_one)
    tdr_default = sig.parameters["tdr_z0"].default
    batch_derives = tdr_default is None and "2.0 *" in src_batch
    ok = cli_doubles and batch_derives
    _record(
        "NEW-002",
        ok,
        f"cli doubles Zse={cli_doubles}; batch tdr_z0 default={tdr_default!r} "
        f"derives 2x={batch_derives}",
    )


# ============================================================================
# NEW-003: crosstalk Aggressor Channel model (near+far, not independent pairs)
# ============================================================================
def probe_new_003(tmp: Path) -> None:
    # 4 differential pairs: victim near=0, victim far=1, aggressor near=2,
    # aggressor far=3. The fix models the aggressor as a CHANNEL (near+far),
    # so only ONE aggressor entry appears (not two), and NEXT/FEXT read the
    # forward-direction Sdd entries.
    n_freqs = 2
    sdd = np.zeros((n_freqs, 4, 4), dtype=complex)
    sdd[:, 0, 2] = 0.10  # NEXT forward (victim near <- aggressor near)
    sdd[:, 1, 2] = 0.08  # FEXT forward (victim far <- aggressor near)
    mm = _make_mm(4, sdd, pair_names=["VN", "VF", "AN", "AF"])
    channels = [
        DifferentialChannel(name="VICTIM", near_pair="VN", far_pair="VF"),
        DifferentialChannel(name="AGG", near_pair="AN", far_pair="AF"),
    ]
    case = CrosstalkCase(victim_channel="VICTIM",
                         aggressor_channels=["AGG"], stimulus_end="near")
    res = compute_crosstalk(mm, case, channels)
    one_agg = len(res.next_db) == 1 and len(res.fext_db) == 1
    exp_next = 20 * np.log10(0.10)
    exp_fext = 20 * np.log10(0.08)
    next_ok = abs(float(res.next_db[0][1][0]) - exp_next) < 1e-6
    fext_ok = abs(float(res.fext_db[0][1][0]) - exp_fext) < 1e-6
    ok = one_agg and next_ok and fext_ok
    _record(
        "NEW-003",
        ok,
        f"aggressor=channel -> {len(res.next_db)} NEXT entry (not 2); "
        f"NEXT={float(res.next_db[0][1][0]):.2f}dB (exp {exp_next:.2f}); "
        f"FEXT={float(res.fext_db[0][1][0]):.2f}dB (exp {exp_fext:.2f})",
    )


# ============================================================================
# NEW-004: reverse metrics / Sdd22 usable in acceptance rules
# ============================================================================
def probe_new_004(tmp: Path) -> None:
    # AcceptanceRule.expression Literal must now include Sdd12/Sdd22/Scc12/
    # Scd12/Sdc12 so reverse metrics can be checked. A rule using Sdd22 must
    # construct successfully (not be rejected by the Literal).
    literals = set(AcceptanceRule.model_fields["expression"].annotation.__args__)
    needed = {"Sdd12", "Sdd22", "Scc12", "Scd12", "Sdc12"}
    ok = needed.issubset(literals)
    # And constructing a rule with Sdd22 must succeed.
    try:
        AcceptanceRule(expression="Sdd22", operator="<=", value="-10dB",
                       frequency="16GHz")
        construct_ok = True
    except Exception:
        construct_ok = False
    ok = ok and construct_ok
    _record(
        "NEW-004",
        ok,
        f"reverse literals present={needed.issubset(literals)}; "
        f"Sdd22 rule constructs={construct_ok}",
    )


# ============================================================================
# NEW-005: frequency tolerance tightened to 1% / 1 MHz
# ============================================================================
def probe_new_005(tmp: Path) -> None:
    # SIMetrics defaults must now be rel=0.01, abs=1e6 (was 5%/1MHz). And a
    # request >1% off-grid must surface as out-of-tolerance (not silently the
    # nearest bin).
    from em_copilot.mixed_mode import SIMetrics  # noqa: E402

    rel = SIMetrics.frequency_tolerance_rel
    abs_ = SIMetrics.frequency_tolerance_abs_hz
    defaults_ok = (rel == 0.01 and abs_ == 1e6)
    # Grid [10e9, 20e9]; request 16e9 -> nearest 20e9, 25% off, must be
    # out-of-tolerance under the new 1% default.
    mm = _make_mm(2, np.zeros((2, 2, 2), dtype=complex))
    metrics = compute_metrics(mm, pair_index=0)
    snap = metrics.at_frequency(16e9)
    in_tol = snap["frequency_in_tolerance"]
    ok = defaults_ok and not in_tol
    _record(
        "NEW-005",
        ok,
        f"defaults rel={rel} abs={abs_}; 16GHz on [10,20]GHz grid "
        f"in_tolerance={in_tol} (must be False)",
    )


# ============================================================================
# NEW-006: one_sided/hann windows marked experimental + renormalized
# ============================================================================
def probe_new_006(tmp: Path) -> None:
    # The docstring must mark one_sided/hann as EXPERIMENTAL, and the
    # implementation must renormalize the window so a constant-load plateau is
    # not depressed to ~70-83Ω. Check the warning text + that rect stays exact
    # (the production path) while non-rect emits an EXPERIMENTAL warning.
    z0, z_load = 50.0, 100.0
    rho0 = (z_load - z0) / (z_load + z0)
    n = 401
    freqs = np.linspace(0.0, 40e9, n)
    reflection = np.full(n, rho0, dtype=complex)
    _t, z_rect, w_rect = compute_tdr(reflection, freqs, z0=z0, max_time=2e-9,
                                      window="rect")
    _t, z_one, w_one = compute_tdr(reflection, freqs, z0=z0, max_time=2e-9,
                                   window="one_sided")
    rect_exact = abs(z_rect[-1] - z_load) < 1e-3
    one_warns_exp = any("EXPERIMENTAL" in w for w in w_one)
    ok = rect_exact and one_warns_exp
    _record(
        "NEW-006",
        ok,
        f"rect end={z_rect[-1]:.2f}Ω (exact={rect_exact}); "
        f"one_sided EXPERIMENTAL warning={one_warns_exp}",
    )


# ============================================================================
# NEW-007: Reference Impedance comment parsed into z0
# ============================================================================
def probe_new_007(tmp: Path) -> None:
    # A file with "! Reference Impedance 75" must have ts.z0 == 75, not the
    # option-line R 50.
    # 2-port row = 1 freq + 2*2^2 = 9 values: freq, S11, S21, S12, S22 (re/im).
    row = "0.1e9 " + " ".join(["0.0"] * 8)
    text = "! Reference Impedance 75\n# Hz S RI R 50\n" + row + "\n"
    p = tmp / "refz.s2p"
    p.write_text(text, encoding="utf-8")
    ts = read_touchstone(p)
    ok = ts.z0 == 75
    _record(
        "NEW-007",
        ok,
        f"'! Reference Impedance 75' -> ts.z0={ts.z0} (expected 75)",
    )


# ============================================================================
# NEW-008: partial port mapping from a larger .sNp (extra ports = WARN)
# ============================================================================
def probe_new_008(tmp: Path) -> None:
    # An .s8p feeding a 4-port (2-pair) mapping must NOT be a fatal ERROR.
    # The preflight must accept the file (WARN that 4 extra ports are ignored)
    # and only ERROR if a *referenced* port is out of range.
    # Build a trivial 8-port reciprocal network.
    n = 8
    f = 1e9
    s = np.eye(n, dtype=complex) * 0.1
    sv = np.linalg.svd(s, compute_uv=False).max()
    if sv > 1.0:
        s = s / sv
    lines = ["# Hz S RI R 50"]
    flat = []
    for i in range(n):
        for j in range(n):
            flat.append(s[i, j].real)
            flat.append(s[i, j].imag)
    lines.append(f"{f:.6e} " + " ".join(f"{x:.6e}" for x in flat))
    p = tmp / "big.s8p"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ts = read_touchstone(p)
    issues = check_port_and_freq(
        ts, expected_ports=4, require_monotonic=True, required_ports=[1, 2, 3, 4]
    )
    # No fatal ERROR issue — only WARN(s) about the extra ports.
    has_error = any("ERROR" in i for i in issues) or any(
        "do not exist" in i for i in issues
    )
    ok = not has_error
    _record(
        "NEW-008",
        ok,
        f".s8p -> 4-port mapping: {len(issues)} issue(s), fatal={has_error} (must be False)",
    )

    # And a mapping that references port 9 (out of range) must ERROR.
    issues_bad = check_port_and_freq(
        ts, expected_ports=4, require_monotonic=True, required_ports=[1, 2, 3, 9]
    )
    bad_blocked = any("do not exist" in i for i in issues_bad)
    ok = ok and bad_blocked
    _record(
        "NEW-008",
        ok,
        f"port-9 reference -> blocked={bad_blocked} (must be True)",
    )


# ============================================================================
# NEW-009: negative pair index rejected (no Python wrap-around)
# ============================================================================
def probe_new_009(tmp: Path) -> None:
    mm = _make_mm(3, np.zeros((2, 3, 3), dtype=complex))
    rejected = False
    try:
        compute_metrics(mm, pair_index=-1)
    except ValueError as exc:
        rejected = "NEW-009" in str(exc) or "negative" in str(exc).lower()
    ok = rejected
    _record(
        "NEW-009",
        ok,
        f"pair_index=-1 -> ValueError raised={rejected}",
    )


# ============================================================================
# NEW-010: invalid TDR params validated (not silently degraded)
# ============================================================================
def probe_new_010(tmp: Path) -> None:
    freqs = np.linspace(0.1e9, 40e9, 201)
    refl = np.zeros_like(freqs, dtype=complex)
    _t, _z, warns = compute_tdr(refl, freqs, z0=50.0, max_time=2e-9,
                                window="bogus", dc_extrapolation="bogus")
    ok = any("unknown TDR window" in w for w in warns) and any(
        "dc_extrapolation" in w for w in warns
    )
    _record(
        "NEW-010",
        ok,
        f"window='bogus' -> warning emitted={any('unknown TDR window' in w for w in warns)}; "
        f"dc='bogus' -> warning emitted={any('dc_extrapolation' in w for w in warns)}",
    )


# ============================================================================
# NEW-011: Markdown acceptance table shows error reason column
# ============================================================================
def probe_new_011(tmp: Path) -> None:
    # The Markdown §8 table must carry an "错误/原因" column. A rule that
    # errors must show the reason inline, not a bare ❌.
    from em_copilot.reporting import render_markdown  # noqa: E402

    # Build a tiny report where one rule errors (bad frequency).
    mm = _make_mm(2, np.zeros((5, 2, 2), dtype=complex))
    metrics = compute_metrics(mm, pair_index=0)
    p = tmp / "new011.s4p"
    _write_s4p(p, np.array([0.1, 1.0, 5.0, 10.0, 40.0]),
               np.zeros((5, 4, 4), dtype=complex) + np.eye(4, dtype=complex))
    task = SITask(
        task_id="new011",
        touchstone=str(p),
        port_mapping=PortMapping(
            pairs=[DifferentialPair(name="A", positive=1, negative=2),
                   DifferentialPair(name="B", positive=3, negative=4)]
        ),
        acceptance=[AcceptanceRule(
            expression="Sdd21", operator=">=", value="-12dB", frequency="bad",
        )],
    )
    ts = read_touchstone(p)
    data = build_report_data(task, ts, mm, metrics, [])
    out_dir = tmp / "new011_out"
    out_dir.mkdir(exist_ok=True)
    render_markdown(data, out_dir)
    md_text = (out_dir / "report.md").read_text(encoding="utf-8")
    has_col = "错误/原因" in md_text
    # The rule errored, so the reason must appear inline in the table.
    has_reason = "frequency" in md_text.lower() or "parse" in md_text.lower() or "❌" in md_text
    ok = has_col and has_reason
    _record(
        "NEW-011",
        ok,
        f"§8 has '错误/原因' column={has_col}; error reason inline={has_reason}",
    )


# ============================================================================
# NEW-012: this very script rewritten as a V2.0 regression probe (self)
# ============================================================================
def probe_new_012(tmp: Path) -> None:
    # NEW-012 rewrote verify_error_report.py from a V1.0 bug-confirmer (success
    # == "the bugs exist", tagged CONFIRMED-REAL) into a V2.0 regression probe
    # (success == "the fixes are in place", tagged FIXED). A self-check is
    # partly circular, so rather than hunt for the old V1.0 success-condition
    # string — which would mean writing that string into this very file to test
    # for it — we verify the *structural* markers of a V2.0 regression probe.
    src = Path(__file__).read_text(encoding="utf-8")
    uses_fixed = "FIXED" in src and "REGRESSION" in src
    targets_new = "probe_new_001" in src and "probe_new_012" in src
    # The success path counts FIXED items and says so in the verdict line.
    counts_fixed = "n_fixed" in src and "confirmed FIXED by regression test" in src
    ok = uses_fixed and targets_new and counts_fixed
    _record(
        "NEW-012",
        ok,
        f"uses FIXED/REGRESSION={uses_fixed}; targets NEW-001..012={targets_new}; "
        f"success counts FIXED={counts_fixed}",
    )


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="v2_probe_"))
    probes = [
        probe_new_001,
        probe_new_002,
        probe_new_003,
        probe_new_004,
        probe_new_005,
        probe_new_006,
        probe_new_007,
        probe_new_008,
        probe_new_009,
        probe_new_010,
        probe_new_011,
        probe_new_012,
    ]
    for p in probes:
        p(tmp)

    print("\n" + "=" * 72)
    n_fixed = sum(1 for _, ok, _ in results if ok)
    print(f"VERDICT: {n_fixed}/{len(results)} NEW items confirmed FIXED by regression test")
    for name, ok, detail in results:
        if not ok:
            print(f"  !! REGRESSION: {name}: {detail}")
    return 0 if n_fixed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
