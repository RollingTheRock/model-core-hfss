"""Batch Touchstone analysis (roadmap §17 阶段2 — 批量文件分析).

Runs the single-file SI pipeline (read → preflight → mixed-mode → metrics →
TDR → report) across many ``.sNp`` files, isolating per-file failures so one
bad file cannot abort the whole batch. Produces:

- one ``report.md`` + ``metrics.json`` + ``plots/`` per file, under a
  per-file subdirectory of the output root;
- a batch-level ``summary.json`` and ``summary.md`` aggregating pass/fail,
  worst-case metrics, and any per-file errors.

File-safety (roadmap §14): per-file output directories live strictly under
the batch output root, never inside an input directory; inputs are read-only.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .analysis import compute_tdr, derive_limits_from_rules
from .mixed_mode import compute_metrics, to_mixed_mode
from .reporting import build_report_data, render_markdown
from .schemas import (
    AcceptanceRule,
    ComplianceStatus,
    FrequencyPoint,
    PortMapping,
    SITask,
)
from .touchstone import PreflightError, check_port_and_freq, read_touchstone, split_preflight

# Per-file pipeline reused from the single-file path. Imported lazily-free:
# these are the same building blocks the ``analyze`` CLI subcommand uses.


@dataclass
class FileResult:
    """Outcome of analyzing one Touchstone file in a batch."""

    touchstone: str
    ok: bool
    pass_fail: bool | None
    error: str | None = None
    input_hash: str | None = None
    n_freqs: int | None = None
    worst: dict = field(default_factory=dict)
    acceptance: list[dict] = field(default_factory=list)
    out_dir: str | None = None
    compliance: ComplianceStatus | None = None


@dataclass
class BatchSummary:
    """Aggregated batch result."""

    n_total: int = 0
    n_ok: int = 0  # files that completed analysis (no exception)
    n_pass: int = 0  # files that completed AND passed all acceptance rules
    n_fail: int = 0  # files that completed but failed at least one rule
    n_not_evaluated: int = 0  # files that completed but had no rules to evaluate
    n_error: int = 0  # files that raised during analysis
    results: list[FileResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "n_total": self.n_total,
            "n_ok": self.n_ok,
            "n_pass": self.n_pass,
            "n_fail": self.n_fail,
            "n_not_evaluated": self.n_not_evaluated,
            "n_error": self.n_error,
            "results": [r.__dict__ for r in self.results],
        }


def _safe_slug(path: Path) -> str:
    """A filesystem-safe subdirectory name derived from a Touchstone path.

    Uses the file stem; if that collides across directories, the parent dir
    name is prepended to disambiguate.
    """
    return path.stem


def _disambiguate(slug: str, taken: set[str]) -> str:
    """Ensure ``slug`` is unique within ``taken`` by suffixing -1, -2, ..."""
    if slug not in taken:
        taken.add(slug)
        return slug
    i = 1
    while f"{slug}-{i}" in taken:
        i += 1
    name = f"{slug}-{i}"
    taken.add(name)
    return name


def analyze_one(
    touchstone: str | Path,
    port_mapping: PortMapping,
    acceptance: list[AcceptanceRule],
    points_of_interest: list[FrequencyPoint],
    out_dir: Path,
    *,
    task_id: str | None = None,
    compute_tdr_flag: bool = False,
    tdr_z0: float | None = None,
    tdr_max_time: float = 2e-9,
    near_pair_index: int = 0,
    far_pair_index: int | None = None,
) -> FileResult:
    """Analyze a single Touchstone file, writing its report under ``out_dir``.

    Raises are not caught here — the caller (:func:`run_batch`) isolates
    per-file failures. This keeps the happy-path logic identical to the
    single-file ``analyze`` CLI.

    ``near_pair_index`` / ``far_pair_index`` (NEW-001) select which
    differential pair is the channel near/far end for multi-pair files,
    forwarding the core API's explicit-far-pair requirement so 6/8/16-port
    files analyze the intended channel instead of raising on ambiguity.
    """
    tpath = Path(touchstone)
    ts = read_touchstone(tpath)
    # NEW-008: verify each referenced port exists; allow the file to carry extra
    # unmapped ports (a WARN) so an .s8p can feed a 4-port lane mapping.
    mapped_ports = [
        port for pair in port_mapping.pairs
        for port in (pair.positive, pair.negative)
    ]
    issues = check_port_and_freq(
        ts,
        expected_ports=sum(2 for _ in port_mapping.pairs),
        require_monotonic=True,
        required_ports=mapped_ports,
    )
    # ERR-011: preflight grading. Fatal issues block analysis — the batch must
    # not compute metrics/TDR on corrupt data and record a FAIL that is really
    # a data-quality ERROR. Recoverable warnings still surface in the report.
    errors, warns = split_preflight(issues, tdr_enabled=compute_tdr_flag)
    if errors:
        raise PreflightError(errors)
    warnings: list[str] = list(warns)
    mm = to_mixed_mode(ts, port_mapping)
    # Derive IL/RL margin baselines from the acceptance rules (ERR-009) instead
    # of hardcoding -12/-10, so the margin reflects the user's actual spec and
    # is None (not computed) when no such rule is defined.
    il_limit, rl_limit = derive_limits_from_rules(acceptance)
    # NEW-001: forward the near/far pair indices instead of a hardcoded 0.
    metrics = compute_metrics(
        mm,
        pair_index=near_pair_index,
        far_pair=far_pair_index,
        il_limit_db=il_limit,
        rl_limit_db=rl_limit,
    )

    tdr = None
    if compute_tdr_flag:
        # TDR impedance requires the reflection coefficient ρ(f) at the near
        # port (differential Sdd11), not the transmission Sdd21 — using Sdd21
        # would compute a TDT, not a TDR (I-04).
        sdd11 = mm.sdd[:, near_pair_index, near_pair_index]
        # NEW-002: for a differential channel the differential reference
        # impedance is Zdiff = 2*Zse, so the TDR reference must be doubled —
        # otherwise a 100Ω differential load reads as ~50Ω (2× off). When the
        # caller supplied an explicit tdr_z0 it is used as-is (it is already
        # the intended differential impedance); otherwise derive it from the
        # Touchstone file's single-ended z0.
        if tdr_z0 is None:
            tdr_z0_eff = 2.0 * (float(ts.z0) if ts.z0 else 100.0)
        else:
            tdr_z0_eff = tdr_z0
        t, z, tdr_warns = compute_tdr(
            sdd11, mm.freqs, z0=tdr_z0_eff, max_time=tdr_max_time
        )
        tdr = (t, z)
        # Surface TDR quality warnings into the report (ERR-008).
        warnings.extend(f"[TDR] {w}" for w in tdr_warns)

    task = SITask(
        task_id=task_id or tpath.stem,
        touchstone=str(tpath),
        port_mapping=port_mapping,
        acceptance=acceptance,
        points_of_interest=points_of_interest,
        compute_tdr=compute_tdr_flag,
        near_pair_index=near_pair_index,
        far_pair_index=far_pair_index,
    )
    data = build_report_data(task, ts, mm, metrics, warnings)
    data.tdr = tdr
    render_markdown(data, out_dir)

    return FileResult(
        touchstone=str(tpath),
        ok=True,
        pass_fail=data.pass_fail,
        compliance=data.compliance,
        input_hash=data.input_hash,
        n_freqs=int(metrics.freqs.size),
        worst=data.worst,
        acceptance=data.acceptance_results,
        out_dir=str(out_dir),
    )


def run_batch(
    touchstones: list[str | Path],
    port_mapping: PortMapping,
    acceptance: list[AcceptanceRule],
    points_of_interest: list[FrequencyPoint],
    out_root: str | Path,
    *,
    task_id_prefix: str = "batch",
    compute_tdr_flag: bool = False,
    near_pair_index: int = 0,
    far_pair_index: int | None = None,
) -> BatchSummary:
    """Analyze many Touchstone files, isolating per-file failures.

    Each file gets its own subdirectory under ``out_root`` (named after the
    file stem, disambiguated on collision). A batch ``summary.json`` and
    ``summary.md`` are written at the end.

    File-safety: a per-file output dir is rejected if it would resolve inside
    the file's *own* input directory (roadmap §14 — outputs never written back
    to the input dir). Such a file is recorded as an error, not analyzed.

    ``near_pair_index`` / ``far_pair_index`` (NEW-001) forward the core API's
    explicit-far-pair requirement so multi-pair files analyze the intended
    channel across the whole batch.
    """
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    taken: set[str] = set()
    summary = BatchSummary(n_total=len(touchstones))

    for idx, ts_path in enumerate(touchstones):
        tpath = Path(ts_path)
        slug = _disambiguate(_safe_slug(tpath), taken)
        per_dir = out_root / slug

        # File-safety: never write outputs back into the input file's dir.
        try:
            _check_output_not_inside_input(per_dir, tpath)
        except ValueError as exc:
            summary.results.append(
                FileResult(
                    touchstone=str(tpath),
                    ok=False,
                    pass_fail=None,
                    error=f"file-safety: {exc}",
                )
            )
            summary.n_error += 1
            continue

        try:
            res = analyze_one(
                tpath,
                port_mapping,
                acceptance,
                points_of_interest,
                per_dir,
                task_id=f"{task_id_prefix}-{slug}",
                compute_tdr_flag=compute_tdr_flag,
                near_pair_index=near_pair_index,
                far_pair_index=far_pair_index,
            )
            summary.results.append(res)
            summary.n_ok += 1
            # Three-state counting (I-06 / §7): NOT_EVALUATED (no rules) is
            # neither a pass nor a fail — a pure-analysis run must not look
            # like a compliance failure.
            if res.compliance == ComplianceStatus.PASS:
                summary.n_pass += 1
            elif res.compliance == ComplianceStatus.FAIL:
                summary.n_fail += 1
            else:  # NOT_EVALUATED (ERROR shouldn't reach here — exception path)
                summary.n_not_evaluated += 1
        except Exception as exc:  # noqa: BLE001 — isolate per-file failures
            summary.results.append(
                FileResult(
                    touchstone=str(tpath),
                    ok=False,
                    pass_fail=None,
                    compliance=ComplianceStatus.ERROR,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            summary.n_error += 1

    _write_summary(summary, out_root)
    return summary


def _check_output_not_inside_input(out_dir: Path, input_file: Path) -> None:
    """Reject an output dir that resolves inside the input file's directory."""
    try:
        out_resolved = out_dir.resolve()
        input_parent = input_file.resolve().parent
    except OSError:
        return  # path cannot be resolved (non-existent root) — let mkdir fail later
    if out_resolved == input_parent or input_parent in out_resolved.parents:
        raise ValueError(
            f"output dir {out_resolved} is inside the input directory "
            f"{input_parent}; outputs must not be written back to the input dir"
        )


def _write_summary(summary: BatchSummary, out_root: Path) -> None:
    """Write summary.json + summary.md (numbers from code, §13.3)."""
    payload = summary.to_dict()
    (out_root / "summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    md: list[str] = []
    md.append("# Batch Analysis Summary\n")
    md.append("> 数值由确定性代码生成，AI 仅生成解释（路线图 §13.3）。\n")
    md.append("## 1. 汇总\n")
    md.append("| 指标 | 数值 |")
    md.append("|---|---|")
    md.append(f"| 总文件数 | {summary.n_total} |")
    md.append(f"| 成功分析 | {summary.n_ok} |")
    md.append(f"| 验收通过 | {summary.n_pass} |")
    md.append(f"| 验收失败 | {summary.n_fail} |")
    md.append(f"| 未定义规则 | {summary.n_not_evaluated} |")
    md.append(f"| 分析报错 | {summary.n_error} |")
    md.append("")

    md.append("## 2. 每文件结果\n")
    md.append("| 文件 | 状态 | 合规 | input_hash | 频点数 | 说明 |")
    md.append("|---|---|---|---|---|---|")
    for r in summary.results:
        if not r.ok:
            status = "❌ error"
            pf = "⚠️ ERROR"
            note = r.error or ""
            h = "—"
            n = "—"
        else:
            status = "✅ ok"
            pf = (r.compliance or ComplianceStatus.NOT_EVALUATED).banner
            note = ""
            h = r.input_hash or "—"
            n = r.n_freqs if r.n_freqs is not None else "—"
        md.append(f"| {r.touchstone} | {status} | {pf} | {h} | {n} | {note} |")
    md.append("")

    md.append("## 3. 最差情形汇总\n")
    md.append("| 文件 | 指标 | 最差值 |")
    md.append("|---|---|---|")
    for r in summary.results:
        if not r.ok or not r.worst:
            continue
        for k, v in r.worst.items():
            if v is None:
                cell = "N/A"
            else:
                try:
                    fv = float(v)
                    cell = "N/A" if np.isnan(fv) else f"{fv:.3f}"
                except (TypeError, ValueError):
                    cell = str(v)
            md.append(f"| {r.touchstone} | {k} | {cell} |")
    md.append("")

    md.append("## 4. 可复现信息\n")
    md.append("- 每文件报告写入以其文件名命名的子目录。")
    md.append("- 结构化汇总同时写入 `summary.json`。")
    md.append("- 本汇总中所有数值由 `em_copilot` 确定性代码生成，未经 AI 修改。\n")

    (out_root / "summary.md").write_text("\n".join(md), encoding="utf-8")


def hash_touchstone(path: str | Path) -> str:
    """SHA-256 (first 16 hex) of a Touchstone file, for audit/de-dup."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def collect_touchstones(
    paths: list[str | Path],
) -> list[Path]:
    """Expand a mix of files and directories into a sorted, de-duplicated list.

    Directories are globbed for ``.sNp`` files (case-insensitive, including
    multi-digit counts such as ``.s10p`` / ``.s16p`` / ``.s128p``). Non-``.sNp``
    files passed directly are kept as-is so the reader can raise a clear error.
    """
    snp_re = re.compile(r"\.s\d*p$", re.IGNORECASE)
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        pth = Path(p)
        if pth.is_dir():
            for f in sorted(pth.iterdir()):
                if not f.is_file():
                    continue
                if not snp_re.search(f.name.lower()):
                    continue
                key = str(f.resolve())
                if key not in seen:
                    seen.add(key)
                    out.append(f)
            continue
        key = str(pth.resolve())
        if key not in seen:
            seen.add(key)
            out.append(pth)
    return out
