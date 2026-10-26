"""CLI entry point: ``em-copilot analyze <touchstone>`` and ``em-copilot demo``.

Examples
--------
  em-copilot demo --out runs/demo
  em-copilot analyze demo.s4p --pair TX0 1 2 --pair RX0 3 4 \\
      --accept "Sdd21 >= -12dB @ 16GHz" --poi 4GHz --poi 16GHz --out runs/demo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis import compute_tdr, derive_limits_from_rules
from .mixed_mode import compute_metrics, to_mixed_mode
from .reporting import build_report_data, render_markdown
from .schemas import (
    AcceptanceRule,
    ComplianceStatus,
    DifferentialPair,
    FrequencyPoint,
    PortMapping,
    SITask,
)
from .touchstone import check_port_and_freq, read_touchstone, split_preflight


def _parse_pair_arg(items: list[str]) -> list[DifferentialPair]:
    """Parse repeated --pair NAME POS NEG into DifferentialPair objects."""
    pairs: list[DifferentialPair] = []
    i = 0
    while i + 2 < len(items) + 1 and len(items) - i >= 3:
        name = items[i]
        try:
            pos = int(items[i + 1])
            neg = int(items[i + 2])
        except ValueError as exc:
            raise SystemExit(f"invalid --pair ports: {items[i:i+3]}") from exc
        pairs.append(DifferentialPair(name=name, positive=pos, negative=neg))
        i += 3
    if i != len(items):
        raise SystemExit("--pair requires NAME POS NEG (triples); got leftover tokens")
    return pairs


def _resolve_pair_names(
    mapping: PortMapping,
    near_name: str | None,
    far_name: str | None,
) -> tuple[int, int | None]:
    """Resolve --near-pair/--far-pair names to 0-based indices (NEW-001).

    The CLI accepts the human-readable pair name (e.g. ``TX0``) so the user does
    not have to count; the core API needs indices. An unknown name is a hard
    error. ``near`` defaults to 0; ``far`` defaults to ``None`` so the 2-pair
    unambiguous fallback in :func:`compute_metrics` still applies.
    """
    names = [p.name for p in mapping.pairs]
    near_idx = 0
    if near_name is not None:
        if near_name not in names:
            raise SystemExit(
                f"--near-pair {near_name!r} not found among pairs {names}"
            )
        near_idx = names.index(near_name)
    far_idx: int | None = None
    if far_name is not None:
        if far_name not in names:
            raise SystemExit(
                f"--far-pair {far_name!r} not found among pairs {names}"
            )
        far_idx = names.index(far_name)
    return near_idx, far_idx


def _parse_accept_arg(rules_text: list[str]) -> list[AcceptanceRule]:
    """Parse repeated --accept "EXPR OP VALUE FREQ" strings.

    Each ``--accept`` takes one quoted rule, e.g. ``"Sdd21 >= -12dB 16GHz"``.
    A leading ``@`` on the frequency is optional (``... @ 16GHz`` or
    ``... 16GHz``). Whitespace-only splitting is used so values like
    ``-12dB`` (which would otherwise be read by argparse as an option flag
    under ``nargs='+'``) survive intact.
    """
    rules: list[AcceptanceRule] = []
    for text in rules_text:
        toks = text.replace("@", " ").split()
        toks = [t for t in toks if t]
        if len(toks) != 4:
            raise SystemExit(
                f"--accept expects 4 tokens 'EXPR OP VALUE FREQ', got {toks!r}"
            )
        expr, op, value, freq = toks
        rules.append(
            AcceptanceRule(
                expression=expr,  # validated by Literal
                operator=op,  # validated by Literal
                value=value,
                frequency=freq,
                description=f"{expr} {op} {value} @ {freq}",
            )
        )
    return rules


def cmd_demo(args: argparse.Namespace) -> int:
    """Generate a synthetic .s4p then analyze it end-to-end."""
    from .demo import write_demo_touchstone

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    s4p = out_dir / "demo_channel.s4p"
    print(f"[demo] writing synthetic touchstone → {s4p}")
    write_demo_touchstone(s4p)

    pairs = [
        DifferentialPair(name="TX0", positive=1, negative=2),
        DifferentialPair(name="RX0", positive=3, negative=4),
    ]
    mapping = PortMapping(pairs=pairs, reference_impedance=50.0)
    rules = [
        AcceptanceRule(
            expression="Sdd21",
            operator=">=",
            value="-12dB",
            frequency="16GHz",
            description="IL at 16 GHz must be ≥ -12 dB",
        ),
        AcceptanceRule(
            expression="Sdd11",
            operator="<=",
            value="-10dB",
            frequency="4GHz",
            description="RL at 4 GHz must be ≤ -10 dB",
        ),
    ]
    poi = [FrequencyPoint(frequency="4GHz"), FrequencyPoint(frequency="16GHz")]
    task = SITask(
        task_id=f"demo-{Path(args.out).name}",
        touchstone=str(s4p),
        port_mapping=mapping,
        acceptance=rules,
        points_of_interest=poi,
        compute_tdr=True,
        tdr_max_time="2ns",
    )
    return _run_analysis(task, out_dir, compute_tdr_flag=True)


def cmd_analyze(args: argparse.Namespace) -> int:
    s4p = Path(args.touchstone)
    pairs = _parse_pair_arg(args.pair)
    if not pairs:
        raise SystemExit("at least one --pair NAME POS NEG is required")
    mapping = PortMapping(pairs=pairs)
    rules = _parse_accept_arg(args.accept) if args.accept else []
    poi = [FrequencyPoint(frequency=f) for f in (args.poi or [])]
    # NEW-001: resolve --near-pair/--far-pair (pair *names*) to 0-based indices
    # into port_mapping.pairs. The core API needs indices; the CLI accepts the
    # human-readable pair names so the user does not have to count. When omitted
    # near defaults to 0 and far to None (the 2-pair unambiguous fallback).
    near_idx, far_idx = _resolve_pair_names(
        mapping, args.near_pair, args.far_pair
    )
    task = SITask(
        task_id=args.task_id or s4p.stem,
        touchstone=str(s4p),
        port_mapping=mapping,
        acceptance=rules,
        points_of_interest=poi,
        compute_tdr=args.tdr,
        near_pair_index=near_idx,
        far_pair_index=far_idx,
    )
    out_dir = Path(args.out)
    return _run_analysis(task, out_dir, compute_tdr_flag=args.tdr)


def _run_analysis(task: SITask, out_dir: Path, compute_tdr_flag: bool) -> int:
    print(f"[analyze] reading {task.touchstone}")
    ts = read_touchstone(task.touchstone)
    # NEW-008: pass the explicit list of 1-based mapped ports so the preflight
    # can verify each referenced port exists (a mapping into a larger file is
    # allowed, but a mapping that references a port the file doesn't have is a
    # fatal ERROR). expected_ports stays the mapping size; the check now treats
    # it as a minimum rather than an exact match so an .s8p can feed a 4-port
    # mapping.
    mapped_ports = [
        port for pair in task.port_mapping.pairs
        for port in (pair.positive, pair.negative)
    ]
    issues = check_port_and_freq(
        ts,
        expected_ports=sum(2 for _ in task.port_mapping.pairs),
        require_monotonic=True,
        required_ports=mapped_ports,
    )
    # ERR-011: preflight is graded ERROR vs WARN. Fatal conditions (NaN/Inf,
    # port-count mismatch, no frequency points, non-monotonic freqs when TDR is
    # on) block analysis — the pipeline must NOT proceed to compute_metrics/TDR
    # on corrupt data and mislabel a corrupt file as FAIL. Recoverable issues
    # (narrow sweep, non-monotonic freqs in freq-domain mode) still surface in
    # the report §3 but do not block.
    errors, warns = split_preflight(issues, tdr_enabled=compute_tdr_flag)
    if errors:
        print("[analyze] ✗ preflight blocked — fatal issues:")
        for e in errors:
            print(f"  ✗ {e}")
        for w in warns:
            print(f"  ⚠️ {w}")
        return ComplianceStatus.ERROR.exit_code
    warnings: list[str] = list(warns)
    print(f"[analyze] preflight warnings: {len(warnings)}")
    for w in warnings:
        print(f"  ⚠️ {w}")

    print(f"[analyze] converting to mixed mode: {task.port_mapping.model_dump_json()}")
    mm = to_mixed_mode(ts, task.port_mapping)
    # Derive IL/RL margin baselines from the user's acceptance rules (ERR-009):
    # a rule "Sdd21 >= -12dB" sets the IL floor, "Sdd11 <= -10dB" sets the RL
    # ceiling. When no such rule exists the limit is None and compute_metrics
    # skips that margin — never fabricate a -12/-10 baseline that contradicts
    # the actual spec.
    il_limit, rl_limit = derive_limits_from_rules(task.acceptance)
    # NEW-001: the near/far pair must come from the task/CLI, not a hardcoded
    # pair_index=0. The CLI exposes --near-pair/--far-pair (0-based pair index
    # into the port_mapping); batch reads them from the task. Default near=0
    # keeps the legacy 2-pair single-link behavior.
    near_pair = getattr(task, "near_pair_index", 0) or 0
    far_pair = getattr(task, "far_pair_index", None)
    metrics = compute_metrics(
        mm,
        pair_index=near_pair,
        far_pair=far_pair,
        il_limit_db=il_limit,
        rl_limit_db=rl_limit,
        frequency_tolerance_rel=task.frequency_tolerance_rel,
        frequency_tolerance_abs_hz=task.frequency_tolerance_abs_hz,
    )

    tdr = None
    if compute_tdr_flag:
        print("[analyze] computing TDR")
        # TDR impedance requires the reflection coefficient ρ(f) seen at the
        # near-end port (differential Sdd11), not a transmission parameter.
        # Using Sdd21/Sdd21 would yield a TDT, not a TDR (I-04).
        sdd11 = mm.sdd[:, near_pair, near_pair]
        # Parse the task's TDR time window (ERR-007): SITask.tdr_max_time is a
        # string like "2ns"; resolve it to seconds. Fall back to the historic
        # default if unset/unparseable so old task files keep working.
        tdr_max_time = _parse_time_str(task.tdr_max_time, default=2e-9)
        # Reference impedance: prefer the Touchstone file's z0 (ERR-012) over
        # the legacy hardcoded 100Ω, so the TDR is referenced to the actual
        # system impedance the S-parameters were normalized to. For a
        # *differential* channel the differential reference impedance is
        # Zdiff = 2*Zse (single-ended), so the TDR reference must be doubled
        # when the channel mode is differential (NEW-002) — otherwise a 100Ω
        # differential load reads as ~50Ω (exactly 2× off).
        tdr_z0_se = float(ts.z0) if ts.z0 else 100.0
        tdr_z0 = 2.0 * tdr_z0_se
        t, z, tdr_warns = compute_tdr(
            sdd11, mm.freqs, z0=tdr_z0, max_time=tdr_max_time
        )
        tdr = (t, z)
        # Surface TDR quality warnings into the report (ERR-008): interpolation,
        # DC extrapolation, and window-strategy caveats must not be silently
        # discarded.
        warnings.extend(f"[TDR] {w}" for w in tdr_warns)

    print("[analyze] building report data")
    data = build_report_data(task, ts, mm, metrics, warnings)
    data.tdr = tdr

    report_path = render_markdown(data, out_dir)
    print(f"\n[done] report  → {report_path}")
    print(f"[done] metrics → {out_dir / 'metrics.json'}")
    print(f"[done] plots  → {out_dir / 'plots'}")
    print(f"[done] compliance → {data.compliance.banner}")
    # Three-state exit code (I-06 / §7): PASS/NOT_EVALUATED → 0, FAIL → 1,
    # ERROR → 2. A pure-analysis run (no rules) must not exit as a failure.
    return data.compliance.exit_code


def cmd_batch(args: argparse.Namespace) -> int:
    """Analyze many Touchstone files, isolating per-file failures."""
    from .batch import collect_touchstones, run_batch

    pairs = _parse_pair_arg(args.pair)
    if not pairs:
        raise SystemExit("at least one --pair NAME POS NEG is required")
    mapping = PortMapping(pairs=pairs)
    rules = _parse_accept_arg(args.accept) if args.accept else []
    poi = [FrequencyPoint(frequency=f) for f in (args.poi or [])]
    # NEW-001: resolve pair names to indices once for the whole batch.
    near_idx, far_idx = _resolve_pair_names(
        mapping, args.near_pair, args.far_pair
    )

    files = collect_touchstones(args.paths)
    if not files:
        raise SystemExit("no Touchstone files found in the given paths")

    print(f"[batch] {len(files)} file(s) to analyze")
    summary = run_batch(
        files,
        mapping,
        rules,
        poi,
        args.out,
        compute_tdr_flag=args.tdr,
        near_pair_index=near_idx,
        far_pair_index=far_idx,
    )
    print(f"[batch] ok={summary.n_ok} pass={summary.n_pass} "
          f"fail={summary.n_fail} not_evaluated={summary.n_not_evaluated} "
          f"error={summary.n_error}")
    print(f"[done] summary → {Path(args.out) / 'summary.md'}")
    print(f"[done] summary → {Path(args.out) / 'summary.json'}")
    # Three-state exit code (I-06 / §7): a batch exits 0 when every file either
    # passed or had no rules to evaluate (NOT_EVALUATED) and none failed or
    # errored; 1 if any file failed a rule; 2 if any file errored. A pure-
    # analysis batch (no rules) must not look like a compliance failure.
    if summary.n_error > 0:
        return 2
    if summary.n_fail > 0:
        return 1
    return 0


def _ensure_utf8_stdout() -> None:
    """Best-effort: force stdout to UTF-8 so emoji banners (✅ ❌ ➖ →) don't
    crash on a default Windows GBK console. Silent no-op when reconfigure is
    unavailable or the stream isn't a real TextIOWrapper (e.g. captured test
    buffers). ``errors='replace'`` keeps the CLI alive even on exotic codepages.
    """
    recon = getattr(sys.stdout, "reconfigure", None)
    if callable(recon):
        try:
            recon(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _parse_time_str(text: str | None, *, default: float) -> float:
    """Resolve a human time string ("2ns", "1.5us", "500ps") to seconds (ERR-007).

    Falls back to ``default`` on an empty/unparseable value so legacy task
    files (which may carry a bare "2ns" or nothing at all) keep working. A
    *non-empty* string that fails to parse now prints a warning to stderr
    instead of silently substituting the default (NEW-010) — so a typo like
    "2nss" is surfaced rather than quietly becoming 2 ns.
    """
    if not text:
        return default
    t = text.strip().lower()
    multipliers = {
        "ps": 1e-12,
        "ns": 1e-9,
        "us": 1e-6,
        "ms": 1e-3,
        "s": 1.0,
    }
    try:
        for suf, mult in multipliers.items():
            if t.endswith(suf):
                return float(t[: -len(suf)]) * mult
        return float(t)  # bare number → seconds
    except ValueError:
        print(
            f"[analyze] ⚠️ cannot parse tdr_max_time={text!r}; "
            f"falling back to default {default*1e9:.0f}ns (NEW-010)",
            file=sys.stderr,
        )
        return default


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    p = argparse.ArgumentParser(prog="em-copilot", description="em-copilot-core CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_demo = sub.add_parser("demo", help="Generate a synthetic .s4p and run the full analysis.")
    p_demo.add_argument("--out", default="runs/demo", help="output directory")
    p_demo.set_defaults(func=cmd_demo)

    p_an = sub.add_parser("analyze", help="Analyze a Touchstone .sNp file.")
    p_an.add_argument("touchstone", help="path to .sNp file")
    p_an.add_argument("--pair", nargs="+", action="append", default=[],
                      help="differential pair: NAME POS NEG (1-based); repeatable")
    p_an.add_argument("--accept", action="append", default=[],
                      help='rule string "EXPR OP VALUE FREQ", e.g. '
                           '"Sdd21 >= -12dB 16GHz"; repeatable')
    p_an.add_argument("--poi", nargs="*", default=[],
                      help="frequency points of interest, e.g. 4GHz")
    p_an.add_argument("--tdr", action="store_true", help="compute TDR")
    # NEW-001: explicit near/far pair selection for multi-pair files. The core
    # API already required far_pair for n_pairs >= 3; the CLI now surfaces it.
    p_an.add_argument("--near-pair", dest="near_pair", default=None,
                      help="name of the near-end differential pair (default first)")
    p_an.add_argument("--far-pair", dest="far_pair", default=None,
                      help="name of the far-end differential pair (required for >=3 pairs)")
    p_an.add_argument("--task-id", default=None)
    p_an.add_argument("--out", default="runs/analysis", help="output directory")
    p_an.set_defaults(func=cmd_analyze)

    p_bt = sub.add_parser("batch", help="Analyze many Touchstone files at once.")
    p_bt.add_argument("paths", nargs="+",
                      help="Touchstone files and/or directories to scan for .sNp")
    p_bt.add_argument("--pair", nargs="+", action="append", default=[],
                      help="differential pair: NAME POS NEG (1-based); repeatable")
    p_bt.add_argument("--accept", action="append", default=[],
                      help='rule string "EXPR OP VALUE FREQ", e.g. '
                           '"Sdd21 >= -12dB 16GHz"; repeatable')
    p_bt.add_argument("--poi", nargs="*", default=[],
                      help="frequency points of interest, e.g. 4GHz")
    p_bt.add_argument("--tdr", action="store_true", help="compute TDR")
    # NEW-001: batch also threads near/far pair selection so an 8/16-port sweep
    # analyzes the intended channel rather than failing on the ambiguous-far
    # case.
    p_bt.add_argument("--near-pair", dest="near_pair", default=None,
                      help="name of the near-end differential pair (default first)")
    p_bt.add_argument("--far-pair", dest="far_pair", default=None,
                      help="name of the far-end differential pair (required for >=3 pairs)")
    p_bt.add_argument("--out", default="runs/batch", help="output directory")
    p_bt.set_defaults(func=cmd_batch)

    args = p.parse_args(argv)

    # --pair is nargs='+' (repeatable triples); --accept is action='append'
    # of one quoted string per rule.
    if args.cmd in ("analyze", "batch"):
        flat_pair: list[str] = []
        for chunk in args.pair:
            flat_pair.extend(chunk)
        args.pair = flat_pair
        # args.accept is already a list[str] (one quoted rule per --accept)

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
