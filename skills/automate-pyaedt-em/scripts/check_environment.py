#!/usr/bin/env python
"""Skill-side environment check (roadmap §5, §17 Phase 0).

Thin wrapper around the canonical `em_copilot.envcheck` so the skill and the
package share a single source of truth. Never launches AEDT, never consumes a
license. Print the human report by default; pass --json for CI.

Usage (from the repo root):

    python skills/automate-pyaedt-em/scripts/check_environment.py
    python skills/automate-pyaedt-em/scripts/check_environment.py --json

Exit codes match `em_copilot.envcheck`: 0 healthy, 1 offline check failed.
AEDT-not-installed is BLOCKED, not FAIL.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    try:
        from em_copilot import envcheck
    except ImportError:
        # Allow running the script directly before the package is installed
        # by adding the repo src dir to the path.
        repo_src = Path(__file__).resolve().parents[3] / "src"
        if repo_src.is_dir():
            sys.path.insert(0, str(repo_src))
        try:
            from em_copilot import envcheck  # type: ignore
        except ImportError as exc:
            print(f"check_environment: em_copilot not importable ({exc})", file=sys.stderr)
            print(f"  (looked for src at {repo_src})", file=sys.stderr)
            return 1
    return envcheck.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
