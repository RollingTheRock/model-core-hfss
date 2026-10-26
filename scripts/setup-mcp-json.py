"""Generate a project-local `.mcp.json` for the official ansys-aedt-mcp server.

Roadmap §16.4 / docs/smoke-test-record.md §4. The MCP server is stdio-transport;
its child-process env MUST clear the enterprise proxy or stdio is severed. This
script emits that config deterministically from the current venv location, so a
cloud-desktop user does not hand-edit JSON and risk leaving the proxy set.

Run (from the project root, with the venv active or by absolute python):

    python scripts/setup-mcp-json.py            # writes ./.mcp.json
    python scripts/setup-mcp-json.py --check    # dry-run: print, do not write
    python scripts/setup-mcp-json.py --show     # print existing .mcp.json

The generated file targets the venv's ansys-aedt-mcp.exe (or linux bin) by
absolute path, enables --include-context (get_guidelines_for), and clears
HTTP_PROXY / HTTPS_PROXY / NO_PROXY="*". It does NOT add --connect (preserve
session elasticity) or --dynamic-tool-discovery (keep all 20 tools visible).

This script never launches AEDT and never consumes a license.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVER_NAME = "pyaedt-em"
SCRIPTS_DIR = Path(sys.executable).parent  # .venv/Scripts or .venv/bin
# Windows: ansys-aedt-mcp.exe ; Linux/macOS: ansys-aedt-mcp
_CANDIDATES = [
    SCRIPTS_DIR / "ansys-aedt-mcp.exe",
    SCRIPTS_DIR / "ansys-aedt-mcp",
]
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # em-copilot/
MCP_JSON = PROJECT_ROOT / ".mcp.json"

# Proxy values that MUST be cleared in the MCP server child env. Empty string,
# not omitted — Claude Code passes env to the child process; empty string
# overwrites any inherited proxy, NO_PROXY="*" disables proxying outright.
ENV_PROXY_CLEARED = {
    "HTTP_PROXY": "",
    "HTTPS_PROXY": "",
    "NO_PROXY": "*",
}


def _find_server_exe() -> Path | None:
    for cand in _CANDIDATES:
        if cand.exists():
            return cand
    return None


def build_config(server_exe: Path, include_context: bool = True) -> dict:
    args: list[str] = []
    if include_context:
        args.append("--include-context")
    return {
        "mcpServers": {
            SERVER_NAME: {
                "command": str(server_exe),
                "args": args,
                "env": dict(ENV_PROXY_CLEARED),
            }
        }
    }


def _print_config(cfg: dict) -> None:
    print(json.dumps(cfg, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="setup-mcp-json",
        description="Generate .mcp.json for the official ansys-aedt-mcp server "
        "(proxy cleared, --include-context). Does not launch AEDT.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Dry-run: print the config that would be written, do not write.",
    )
    parser.add_argument(
        "--show", action="store_true",
        help="Print the existing .mcp.json (if any) and exit.",
    )
    parser.add_argument(
        "--no-include-context", action="store_true",
        help="Omit --include-context (disables get_guidelines_for). "
        "Not recommended for the full-pipeline skill workflow.",
    )
    args = parser.parse_args(argv)

    if args.show:
        if not MCP_JSON.exists():
            print(f"No existing {MCP_JSON.name} at {MCP_JSON}", file=sys.stderr)
            return 1
        _print_config(json.loads(MCP_JSON.read_text(encoding="utf-8")))
        return 0

    exe = _find_server_exe()
    if exe is None:
        print(
            f"ERROR: ansys-aedt-mcp console entry not found under {SCRIPTS_DIR}. "
            "Is the venv active and is ansys-aedt-mcp installed? "
            "Run: pip install -r requirements-cloud.txt",
            file=sys.stderr,
        )
        return 2

    cfg = build_config(exe, include_context=not args.no_include_context)

    if args.check:
        print(f"# would write to: {MCP_JSON}")
        _print_config(cfg)
        return 0

    # Write. Never silently clobber without telling the user what changed.
    if MCP_JSON.exists():
        old = json.loads(MCP_JSON.read_text(encoding="utf-8"))
        if old == cfg:
            print(f"{MCP_JSON.name} already up to date — no change.")
            return 0
        print(f"{MCP_JSON.name} exists and will be overwritten:")
        print(f"  old: {json.dumps(old, ensure_ascii=False)}")
        print(f"  new: {json.dumps(cfg, ensure_ascii=False)}")
    MCP_JSON.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {MCP_JSON}")
    print(
        "Next: in Claude Code run /mcp and confirm 'pyaedt-em' is connected. "
        "Then call check_aedt_installed / check_aedt_status."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
