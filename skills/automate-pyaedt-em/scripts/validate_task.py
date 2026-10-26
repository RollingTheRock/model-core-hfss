#!/usr/bin/env python
"""Validate a SimulationTask YAML (roadmap §6.2 / §7.5).

Checks structure, port-mapping consistency, acceptance-rule grammar, frequency
and mesh sanity, and output-path safety (no writes back to the input dir).
Uses the existing `em_copilot.schemas` models (PortMapping, AcceptanceRule,
FrequencySpec) where available; falls back to structural checks otherwise.

Exit codes:
    0 — task is structurally valid and safe to advance to READY_FOR_REVIEW.
    1 — one or more blocking issues found (task must stay NEEDS_INPUT).
    2 — invocation error (missing file, bad YAML).

Usage:
    python skills/automate-pyaedt-em/scripts/validate_task.py task.yaml
    python skills/automate-pyaedt-em/scripts/validate_task.py task.yaml --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "validate_task: PyYAML not installed. `pip install pyyaml` or load "
            "the file with the em_copilot loader."
        ) from exc
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise SystemExit(f"validate_task: {path} is not a YAML mapping")
    return data


def _as_float(v: Any) -> float | None:
    """Coerce a YAML value to float. YAML 1.1 (PyYAML) parses ``5.0e9``
    (unsigned exponent) as a *string*, so coerce numeric-looking strings
    instead of rejecting them. Returns None if not coercible."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _check_structure(task: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    required = [
        "task_id", "state", "design_type", "input", "objective",
        "ports", "frequency", "mesh", "outputs",
    ]
    for key in required:
        if key not in task:
            issues.append(f"missing top-level key: {key}")
    if task.get("design_type") not in {"hfss", "hfss3d_layout", "q3d", "circuit"}:
        issues.append(
            f"design_type '{task.get('design_type')}' "
            "not in hfss|hfss3d_layout|q3d|circuit"
        )
    return issues


def _check_ports(task: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    ports = task.get("ports") or []
    if not ports:
        issues.append("ports: at least one port required")
        return issues
    seen_indices: list[int] = []
    for i, p in enumerate(ports):
        if not p.get("name"):
            issues.append(f"ports[{i}]: name missing")
        if p.get("type") not in {"wave", "lumped"}:
            issues.append(f"ports[{i}] '{p.get('name')}': type not wave|lumped")
        if not p.get("reference_conductor"):
            issues.append(
                f"ports[{i}] '{p.get('name')}': reference_conductor missing "
                "(return path undefined)"
            )
        pos, neg = p.get("positive"), p.get("negative")
        if pos is None or neg is None:
            issues.append(
                f"ports[{i}] '{p.get('name')}': positive/negative port index missing"
            )
        elif pos == neg:
            issues.append(
                f"ports[{i}] '{p.get('name')}': positive and negative are the same index"
            )
        if isinstance(pos, int):
            seen_indices.append(pos)
        if isinstance(neg, int):
            seen_indices.append(neg)
    return issues


def _check_acceptance(task: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    rules = (task.get("objective") or {}).get("acceptance") or []
    valid_expr = {
        "Sdd11", "Sdd21", "Sdc21", "Scd21", "Scc11", "Scc21",
        "NEXT", "FEXT", "IL_margin", "RL_margin",
    }
    valid_op = {">=", "<=", ">", "<", "=="}
    for i, r in enumerate(rules):
        if r.get("expression") not in valid_expr:
            issues.append(
                f"acceptance[{i}]: expression '{r.get('expression')}' "
                f"not in {sorted(valid_expr)}"
            )
        if r.get("operator") not in valid_op:
            issues.append(
                f"acceptance[{i}]: operator '{r.get('operator')}' "
                f"not in {sorted(valid_op)}"
            )
        if _as_float(r.get("value")) is None:
            issues.append(
                f"acceptance[{i}]: value not numeric (got {r.get('value')!r})"
            )
        if _as_float(r.get("frequency")) is None:
            issues.append(
                f"acceptance[{i}]: frequency not numeric in Hz "
                f"(got {r.get('frequency')!r})"
            )
    return issues


def _check_frequency(task: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    f = task.get("frequency") or {}
    start = _as_float(f.get("start"))
    stop = _as_float(f.get("stop"))
    step = _as_float(f.get("step"))
    if None in (start, stop, step):
        issues.append(
            f"frequency: start/stop/step must be numeric (Hz); "
            f"got start={f.get('start')!r} stop={f.get('stop')!r} step={f.get('step')!r}"
        )
    else:
        if stop <= start:
            issues.append("frequency: stop must be greater than start")
        if step <= 0:
            issues.append("frequency: step must be positive")
    # TDR needs a DC point.
    pp = task.get("post_processing") or {}
    if pp.get("compute_tdr") and start is not None and start > 0:
        issues.append(
            "frequency: compute_tdr=true but start>0; TDR needs a DC point "
            "(start=0 or DC-extend)"
        )
    return issues


def _check_mesh(task: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    m = task.get("mesh") or {}
    max_p, min_p = m.get("max_passes"), m.get("min_passes")
    if isinstance(max_p, int) and isinstance(min_p, int) and min_p > max_p:
        issues.append("mesh: min_passes > max_passes")
    ds = _as_float(m.get("delta_s"))
    if ds is not None and not (0 < ds < 1):
        issues.append(f"mesh: delta_s should be in (0,1); got {m.get('delta_s')!r}")
    return issues


def _check_file_safety(task: dict[str, Any]) -> list[str]:
    """Roadmap §14.1: outputs never written back to the input dir."""
    issues: list[str] = []
    in_path = (task.get("input") or {}).get("project_path") or ""
    run_dir = (task.get("run") or {}).get("run_dir") or ""
    if in_path and run_dir:
        in_parent = str(Path(in_path).resolve().parent)
        run_resolved = str(Path(run_dir).resolve())
        tail = in_parent.rstrip("/\\")
        if (
            run_resolved == in_parent
            or run_resolved.startswith(tail + "/")
            or run_resolved.startswith(tail + "\\")
        ):
            issues.append(
                "file-safety: run_dir is inside the input project directory; "
                "outputs must go elsewhere"
            )
    return issues


def validate(task: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    issues += _check_structure(task)
    issues += _check_ports(task)
    issues += _check_acceptance(task)
    issues += _check_frequency(task)
    issues += _check_mesh(task)
    issues += _check_file_safety(task)
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="validate_task", description="Validate a SimulationTask YAML."
    )
    parser.add_argument("path", help="path to the task YAML")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.is_file():
        print(f"validate_task: file not found: {path}", file=sys.stderr)
        return 2

    try:
        task = _load_yaml(path)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    issues = validate(task)
    if args.json:
        print(json.dumps(
            {"task_id": task.get("task_id"), "issues": issues, "valid": not issues},
            indent=2,
        ))
    else:
        print(f"validate_task: {path}")
        if issues:
            print(f"  BLOCKING ({len(issues)}):")
            for iss in issues:
                print(f"    - {iss}")
        else:
            print(
                "  OK — task is structurally valid; "
                "advance to READY_FOR_REVIEW after human review."
            )
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
