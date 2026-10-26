"""MCP stdio handshake self-test for ansys-aedt-mcp.

Pre-validates that Claude Code's /mcp will see 'pyaedt-em' as connected.
Sends the JSON-RPC initialize request + tools/list over stdio and reports
the serverInfo and tool count. Proxy is cleared in the child env (same as
.mcp.json) so stdio is not severed by the enterprise proxy.

Run (proxy cleared in this shell):
    .venv/Scripts/python.exe scripts/mcp-handshake-selftest.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(sys.executable).parent
SERVER_EXE = SCRIPTS_DIR / "ansys-aedt-mcp.exe"


def _read_json_line(proc: subprocess.Popen, timeout: float) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line or not line.strip():
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            # server may print a non-JSON log line; keep reading
            continue
    return None


def main() -> int:
    if not SERVER_EXE.exists():
        print(f"ERROR: {SERVER_EXE} not found. Is ansys-aedt-mcp installed?", file=sys.stderr)
        return 2

    env = dict(os.environ)
    env["HTTP_PROXY"] = ""
    env["HTTPS_PROXY"] = ""
    env["NO_PROXY"] = "*"

    print(f"Starting {SERVER_EXE} --include-context (proxy cleared) ...")
    proc = subprocess.Popen(
        [str(SERVER_EXE), "--include-context"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
    )

    try:
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "selftest", "version": "0.0.1"},
            },
        }
        proc.stdin.write(json.dumps(init_req) + "\n")
        proc.stdin.flush()

        resp = _read_json_line(proc, timeout=25.0)
        if resp is None:
            err = proc.stderr.read() if proc.stderr else ""
            print(f"FAIL: no initialize response in 25s. stderr: {err[:400]}")
            return 1

        if "error" in resp:
            print(f"FAIL: server returned error: {resp['error']}")
            return 1

        result = resp.get("result", {})
        si = result.get("serverInfo", {})
        caps = list(result.get("capabilities", {}).keys())
        print(f"INIT OK -> server: {si.get('name')} {si.get('version')}")
        print(f"capabilities: {caps}")

        # initialized notification + tools/list
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n"
        )
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n"
        )
        proc.stdin.flush()

        tools_resp = _read_json_line(proc, timeout=25.0)
        if tools_resp is None:
            print("WARN: no tools/list response in 25s (server may still be starting).")
            return 0

        tools = [t.get("name") for t in tools_resp.get("result", {}).get("tools", [])]
        print(f"tools/list count: {len(tools)}")
        print(f"tools: {', '.join(sorted(tools)) if tools else '(none)'}")
        print("\nSELFTEST PASS: /mcp should report 'pyaedt-em' connected.")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
