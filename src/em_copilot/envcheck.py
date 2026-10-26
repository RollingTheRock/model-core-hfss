"""阶段0 environment verification (roadmap §17 Phase 0, §3.3).

A dependency-light, CI-friendly check that reports the state of the local
environment for the PyAEDT EM Copilot toolchain. It never launches AEDT and
never consumes a license. Run as:

    python -m em_copilot.envcheck            # human report, exit 0 if ok
    python -m em_copilot.envcheck --json     # machine-readable, for CI

Exit codes:
    0 — offline subset healthy (Python, deps, em-copilot-core, MCP CLI,
        tool enumeration, offline MCP tools). AEDT-not-installed is NOT a
        failure here; it is reported as a blocked check.
    1 — one or more offline checks failed (import error, MCP CLI missing,
        tool enumeration failed).
    2 — invoked with bad arguments.

AEDT/HFSS license probing is intentionally OUT OF SCOPE for this script:
that requires a workstation with AEDT installed and is tracked separately
as a blocked 阶段0 item (roadmap §17, handover §7).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

# Packages whose presence/version matters for the toolchain. Order = report
# order. ``optional`` packages are reported but not required for exit code.
_CORE_PKGS = [
    ("em-copilot-core", "em_copilot", False),
    ("ansys-aedt-mcp", "ansys.aedt.mcp", False),
    ("ansys-common-mcp", "ansys.common.mcp", False),
    ("pyaedt", "ansys.aedt.core", False),
    ("pyedb", "pyedb", False),
    ("ansys-edb-core", "ansys.edb.core", False),
    ("fastmcp", "fastmcp", False),
    ("mcp", "mcp", False),
    ("numpy", "numpy", False),
    ("scipy", "scipy", False),
    ("matplotlib", "matplotlib", False),
    ("pydantic", "pydantic", False),
    ("typer", "typer", True),  # needed by pyaedt CLI session discovery
    ("click", "click", True),
]


@dataclass
class Check:
    name: str
    status: str  # "pass" | "fail" | "blocked" | "warn"
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def _pkg_version(dist: str, mod: str) -> tuple[str | None, str | None]:
    """Return (installed_version, import_error). version is None if absent."""
    try:
        return version(dist), None
    except PackageNotFoundError:
        return None, None
    except Exception as exc:  # defensive: metadata corruption
        return None, f"metadata error: {exc}"


def _check_python() -> Check:
    return Check(
        name="python",
        status="pass",
        detail=f"{sys.version.split()[0]} on {sys.platform}",
        data={"version": sys.version.split()[0], "platform": sys.platform},
    )


def _check_packages() -> list[Check]:
    checks: list[Check] = []
    for dist, mod, optional in _CORE_PKGS:
        v, err = _pkg_version(dist, mod)
        if v is not None:
            checks.append(
                Check(
                    name=f"pkg:{dist}",
                    status="pass",
                    detail=v,
                    data={"version": v, "optional": optional},
                )
            )
            continue
        # dist not found; try importing the module to catch namespace issues
        try:
            import_module(mod)
            status = "warn" if optional else "fail"
            detail = f"{dist} not in metadata but {mod} imports (namespace/extra)"
        except Exception as exc:
            status = "warn" if optional else "fail"
            detail = f"{dist} not installed ({type(exc).__name__}: {exc})"
        checks.append(
            Check(name=f"pkg:{dist}", status=status, detail=detail, data={"optional": optional})
        )
    return checks


def _check_em_copilot_regression() -> Check:
    """Confirm em-copilot-core imports and exposes its public surface."""
    try:
        import em_copilot  # noqa: F401
        from em_copilot import (  # noqa: F401
            __version__,
            analysis,
            cli,
            demo,
            mixed_mode,
            reporting,
            schemas,
            touchstone,
        )
        return Check(
            name="em_copilot.import",
            status="pass",
            detail=f"em_copilot {__version__} imports cleanly",
            data={"version": __version__},
        )
    except Exception as exc:
        return Check(
            name="em_copilot.import",
            status="fail",
            detail=f"{type(exc).__name__}: {exc}",
        )


def _check_mcp_cli() -> Check:
    """Confirm the ansys-aedt-mcp console entry is on PATH and --help works."""
    exe = shutil.which("ansys-aedt-mcp")
    if not exe:
        # fall back to the venv Scripts dir
        cand = Path(sys.executable).parent / "ansys-aedt-mcp.exe"
        exe = str(cand) if cand.exists() else None
    if not exe:
        return Check(
            name="mcp.cli",
            status="fail",
            detail="ansys-aedt-mcp console entry not found on PATH",
        )
    try:
        # --help must not require AEDT; clear proxy so it never hits the net.
        # Timeout 120s: importing pyaedt on a cold start can take >60s on some
        # machines (the CLI entry imports the full ansys.aedt.core stack before
        # argparse runs). 60s was too tight and caused intermittent FAIL on first
        # run of the day; warm runs pass in ~10s.
        env = {k: v for k, v in __import__("os").environ.items()}
        env["HTTP_PROXY"] = ""
        env["HTTPS_PROXY"] = ""
        env["NO_PROXY"] = "*"
        proc = subprocess.run(
            [exe, "--help"], capture_output=True, text=True, timeout=120, env=env
        )
        ok = proc.returncode == 0 and "--transport" in proc.stdout
        return Check(
            name="mcp.cli",
            status="pass" if ok else "fail",
            detail=(
                f"ansys-aedt-mcp --help exit {proc.returncode} "
                f"({'has --transport' if '--transport' in proc.stdout else 'missing --transport'})"
            ),
            data={"exe": exe, "returncode": proc.returncode},
        )
    except subprocess.TimeoutExpired:
        return Check(
            name="mcp.cli",
            status="fail",
            detail="ansys-aedt-mcp --help timed out (>120s; cold pyaedt import can be slow)",
        )
    except Exception as exc:
        return Check(name="mcp.cli", status="fail", detail=f"{type(exc).__name__}: {exc}")


def _check_mcp_tool_enumeration() -> Check:
    """List registered MCP tools offline (no AEDT, no license)."""
    try:
        import asyncio
        importlib_items = [
            "ansys.aedt.mcp.tools",
            "ansys.aedt.mcp.contexts",
            "ansys.aedt.mcp.toolsets",
        ]
        from ansys.aedt.mcp import app as srv  # noqa: WPS433

        for m in importlib_items:
            import_module(m)

        async def _list_tools() -> list[str]:
            tools = await srv.list_tools()
            return sorted(t.name for t in tools)

        names = asyncio.run(_list_tools())
        expected_offline = {"check_aedt_installed", "check_aedt_status", "get_pyaedt_logs"}
        missing_offline = expected_offline - set(names)
        status = "pass" if not missing_offline else "warn"
        detail = f"{len(names)} tools registered"
        if missing_offline:
            detail += f"; missing offline tools: {sorted(missing_offline)}"
        return Check(
            name="mcp.tools",
            status=status,
            detail=detail,
            data={"count": len(names), "tools": names},
        )
    except Exception as exc:
        return Check(name="mcp.tools", status="fail", detail=f"{type(exc).__name__}: {exc}")


def _check_aedt_installed() -> Check:
    """Probe AEDT install + version via pyaedt (no license consumed)."""
    try:
        from ansys.aedt.core.internal.aedt_versions import aedt_versions

        installed = dict(aedt_versions.installed_versions)
        if not installed:
            return Check(
                name="aedt.install",
                status="blocked",
                detail="No AEDT versions found in ANSYSEM_ROOTxxx / AWP_ROOTxxx env",
                data={"installed_versions": []},
            )
        return Check(
            name="aedt.install",
            status="pass",
            detail=f"AEDT installed: {sorted(installed)}",
            data={
                "installed_versions": sorted(installed),
                "current_version": aedt_versions.current_version,
                "latest_version": aedt_versions.latest_version,
            },
        )
    except Exception as exc:
        return Check(
            name="aedt.install",
            status="blocked",
            detail=f"AEDT probe failed ({type(exc).__name__}: {exc})",
        )


def run_all() -> list[Check]:
    checks: list[Check] = [_check_python()]
    checks.extend(_check_packages())
    checks.append(_check_em_copilot_regression())
    checks.append(_check_mcp_cli())
    checks.append(_check_mcp_tool_enumeration())
    checks.append(_check_aedt_installed())
    return checks


def _summarize(checks: list[Check]) -> tuple[str, int]:
    fail = sum(1 for c in checks if c.status == "fail")
    blocked = sum(1 for c in checks if c.status == "blocked")
    warn = sum(1 for c in checks if c.status == "warn")
    if fail:
        return f"{fail} fail / {warn} warn / {blocked} blocked", 1
    return f"0 fail / {warn} warn / {blocked} blocked", 0


def _render_human(checks: list[Check]) -> str:
    # ASCII-only glyphs: the Windows console defaults to GBK and cannot encode
    # the unicode check/cross marks. Keep this report portable across locales.
    glyph = {"pass": "+", "fail": "x", "blocked": "-", "warn": "!"}
    lines = ["em-copilot Phase 0 environment check", "=" * 48, ""]
    for c in checks:
        lines.append(f"  [{glyph[c.status]}] {c.name:28s} {c.status.upper():7s} {c.detail}")
    summary, _ = _summarize(checks)
    lines.append("")
    lines.append(f"  summary: {summary}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="em-copilot-envcheck",
        description="阶段0 environment verification (offline, no AEDT license).",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    checks = run_all()
    if args.json:
        payload = {
            "checks": [asdict(c) for c in checks],
            "summary": dict(zip(["fail", "warn", "blocked", "pass"], [
                sum(1 for c in checks if c.status == s) for s in ("fail", "warn", "blocked", "pass")
            ])),
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(_render_human(checks))

    _, code = _summarize(checks)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
