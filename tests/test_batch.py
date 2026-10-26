"""Tests for batch Touchstone analysis (roadmap §17 阶段2 — 批量文件分析).

Covers:
- ``run_batch`` produces one report + metrics.json per file under a per-file
  subdirectory, and a batch ``summary.json`` / ``summary.md``.
- Per-file failure isolation: one corrupt file does not abort the batch.
- File-safety: an output dir resolving inside an input file's directory is
  rejected and recorded as an error (roadmap §14).
- ``collect_touchstones`` expands directories and de-duplicates, keeping the
  order stable.
- Slug disambiguation: two files with the same stem get distinct output dirs.
"""

from __future__ import annotations

import json
from pathlib import Path

from em_copilot.batch import (
    BatchSummary,
    collect_touchstones,
    run_batch,
)
from em_copilot.demo import write_demo_touchstone
from em_copilot.schemas import AcceptanceRule, DifferentialPair, PortMapping

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _mapping() -> PortMapping:
    return PortMapping(
        pairs=[
            DifferentialPair(name="RX0", positive=1, negative=2),
            DifferentialPair(name="RX1", positive=3, negative=4),
        ]
    )


def _rules() -> list[AcceptanceRule]:
    return [
        AcceptanceRule(
            expression="Sdd21", operator=">=", value="-12dB",
            frequency="16GHz", description="IL floor",
        ),
        AcceptanceRule(
            expression="Sdd11", operator="<=", value="-5dB",
            frequency="16GHz", description="RL ceiling",
        ),
    ]


def _write_three(tmp_path: Path) -> list[Path]:
    """Write three demo .s4p files in separate subdirectories."""
    files = []
    for i, name in enumerate(("chan_a", "chan_b", "chan_c")):
        d = tmp_path / f"inputs_{i}" / name
        d.mkdir(parents=True)
        f = d / f"{name}.s4p"
        write_demo_touchstone(f)
        files.append(f)
    return files


# --------------------------------------------------------------------------- #
# run_batch — happy path
# --------------------------------------------------------------------------- #

class TestBatchHappyPath:
    def test_each_file_gets_report_and_metrics(self, tmp_path):
        files = _write_three(tmp_path)
        out = tmp_path / "batch_out"
        summary = run_batch(files, _mapping(), _rules(), [], out)

        assert isinstance(summary, BatchSummary)
        assert summary.n_total == 3
        assert summary.n_ok == 3
        assert summary.n_pass == 3
        assert summary.n_fail == 0
        assert summary.n_error == 0

        for f in files:
            per_dir = out / f.stem
            assert (per_dir / "report.md").is_file(), per_dir
            assert (per_dir / "metrics.json").is_file(), per_dir
            assert (per_dir / "plots" / "s_parameters.png").is_file(), per_dir
            # metrics.json is valid JSON and carries the per-file hash.
            m = json.loads((per_dir / "metrics.json").read_text(encoding="utf-8"))
            assert m["task_id"] == f"batch-{f.stem}"
            assert "input_hash" in m

    def test_summary_json_and_md_written(self, tmp_path):
        files = _write_three(tmp_path)
        out = tmp_path / "batch_out"
        run_batch(files, _mapping(), _rules(), [], out)

        sj = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        assert sj["n_total"] == 3
        assert sj["n_pass"] == 3
        assert len(sj["results"]) == 3
        assert all(r["ok"] for r in sj["results"])

        sm = (out / "summary.md").read_text(encoding="utf-8")
        assert "Batch Analysis Summary" in sm
        assert "总文件数" in sm
        # Numbers are from code, not AI (§13.3 banner present).
        assert "确定性代码生成" in sm

    def test_worst_case_aggregated_in_summary(self, tmp_path):
        files = _write_three(tmp_path)
        out = tmp_path / "batch_out"
        run_batch(files, _mapping(), _rules(), [], out)
        sm = (out / "summary.md").read_text(encoding="utf-8")
        # Each file contributes its worst-case rows.
        assert sm.count("Sdd21_dB_worst") == 3

    def test_tdr_enabled_per_file(self, tmp_path):
        files = _write_three(tmp_path)
        out = tmp_path / "batch_out"
        run_batch(files, _mapping(), _rules(), [], out, compute_tdr_flag=True)
        for f in files:
            assert (out / f.stem / "plots" / "tdr.png").is_file(), f


# --------------------------------------------------------------------------- #
# run_batch — error isolation
# --------------------------------------------------------------------------- #

class TestBatchErrorIsolation:
    def test_one_corrupt_file_does_not_abort_batch(self, tmp_path):
        files = _write_three(tmp_path)
        # Overwrite the middle file with garbage.
        files[1].write_text("# Hz S MA R 50\nnot enough tokens\n", encoding="utf-8")
        out = tmp_path / "batch_out"
        summary = run_batch(files, _mapping(), _rules(), [], out)

        assert summary.n_total == 3
        assert summary.n_ok == 2
        assert summary.n_error == 1
        # The two good files still got their reports.
        assert (out / files[0].stem / "report.md").is_file()
        assert (out / files[2].stem / "report.md").is_file()
        # The bad file has no report dir (analysis raised before rendering).
        assert not (out / files[1].stem / "report.md").is_file()
        # The error is recorded with its exception type.
        err_result = [r for r in summary.results if not r.ok][0]
        assert err_result.touchstone == str(files[1])
        assert "ValueError" in err_result.error

    def test_missing_file_is_isolated(self, tmp_path):
        files = _write_three(tmp_path)
        files.append(tmp_path / "does_not_exist.s4p")
        out = tmp_path / "batch_out"
        summary = run_batch(files, _mapping(), _rules(), [], out)

        assert summary.n_total == 4
        assert summary.n_ok == 3
        assert summary.n_error == 1
        assert summary.n_pass == 3

    def test_pass_fail_mixed(self, tmp_path):
        files = _write_three(tmp_path)
        out = tmp_path / "batch_out"
        # A rule no demo channel can satisfy at 16 GHz → all fail, none error.
        impossible = [
            AcceptanceRule(
                expression="Sdd21", operator=">=", value="0dB",
                frequency="16GHz", description="impossible",
            ),
        ]
        summary = run_batch(files, _mapping(), impossible, [], out)
        assert summary.n_ok == 3
        assert summary.n_pass == 0
        assert summary.n_fail == 3
        assert summary.n_error == 0

    def test_no_rules_is_not_evaluated(self, tmp_path):
        """Three-state compliance (I-06 / §7): a batch run with no acceptance
        rules must report NOT_EVALUATED for every file — not FAIL. A pure-
        analysis run is not a compliance failure."""
        files = _write_three(tmp_path)
        out = tmp_path / "batch_out"
        summary = run_batch(files, _mapping(), [], [], out)
        assert summary.n_total == 3
        assert summary.n_ok == 3
        assert summary.n_pass == 0
        assert summary.n_fail == 0
        assert summary.n_not_evaluated == 3
        assert summary.n_error == 0
        # Per-file compliance is NOT_EVALUATED, recorded in summary.json.
        sj = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        assert all(r["compliance"] == "NOT_EVALUATED" for r in sj["results"])
        # The summary.md uses the three-state banner, not a bare FAIL.
        sm = (out / "summary.md").read_text(encoding="utf-8")
        assert "NOT EVALUATED" in sm


# --------------------------------------------------------------------------- #
# run_batch — slug disambiguation + file-safety
# --------------------------------------------------------------------------- #

class TestBatchSlugAndSafety:
    def test_same_stem_gets_disambiguated_dirs(self, tmp_path):
        # Two files named demo.s4p in different input dirs.
        d1 = tmp_path / "in1"
        d1.mkdir()
        d2 = tmp_path / "in2"
        d2.mkdir()
        f1 = d1 / "demo.s4p"
        f2 = d2 / "demo.s4p"
        write_demo_touchstone(f1)
        write_demo_touchstone(f2)
        out = tmp_path / "batch_out"

        run_batch([f1, f2], _mapping(), _rules(), [], out)

        # First gets "demo", second gets "demo-1".
        assert (out / "demo" / "report.md").is_file()
        assert (out / "demo-1" / "report.md").is_file()

    def test_output_inside_input_dir_rejected(self, tmp_path):
        f = tmp_path / "data.s4p"
        write_demo_touchstone(f)
        # Place the batch output root INSIDE the input file's directory.
        out = f.parent / "batch_out"
        summary = run_batch([f], _mapping(), _rules(), [], out)

        assert summary.n_total == 1
        assert summary.n_error == 1
        assert summary.n_ok == 0
        err = summary.results[0]
        assert "file-safety" in err.error
        # No report written.
        assert not (out / f.stem / "report.md").is_file()


# --------------------------------------------------------------------------- #
# collect_touchstones
# --------------------------------------------------------------------------- #

class TestCollectTouchstones:
    def test_directory_glob(self, tmp_path):
        d = tmp_path / "scans"
        d.mkdir()
        for name in ("a.s2p", "b.s4p", "c.s1p"):
            (d / name).write_text("! stub\n# Hz S MA R 50\n", encoding="utf-8")
        # Non-Touchstone file should be ignored by the directory glob.
        (d / "notes.txt").write_text("ignore me", encoding="utf-8")
        out = collect_touchstones([d])
        names = [p.name for p in out]
        # Order is by extension group then name; only the set is asserted.
        assert set(names) == {"a.s2p", "b.s4p", "c.s1p"}
        assert "notes.txt" not in names

    def test_directory_glob_multi_digit(self, tmp_path):
        """Multi-digit ``.sNp`` extensions must be collected (I-01)."""
        d = tmp_path / "scans"
        d.mkdir()
        for name in ("x.s10p", "y.s16p", "z.s128p"):
            (d / name).write_text("! stub\n", encoding="utf-8")
        out = collect_touchstones([d])
        assert {p.name for p in out} == {"x.s10p", "y.s16p", "z.s128p"}

    def test_mix_of_files_and_dirs(self, tmp_path):
        d = tmp_path / "scans"
        d.mkdir()
        (d / "in_dir.s4p").write_text("! x\n", encoding="utf-8")
        explicit = tmp_path / "explicit.s4p"
        explicit.write_text("! y\n", encoding="utf-8")
        out = collect_touchstones([d, explicit])
        assert {p.name for p in out} == {"in_dir.s4p", "explicit.s4p"}

    def test_dedup(self, tmp_path):
        f = tmp_path / "dup.s4p"
        f.write_text("! x\n", encoding="utf-8")
        out = collect_touchstones([f, f, str(f)])
        assert len(out) == 1

    def test_empty_returns_empty(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        assert collect_touchstones([d]) == []
        assert collect_touchstones([]) == []
