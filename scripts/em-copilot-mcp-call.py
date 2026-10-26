"""Call an em-copilot-mcp tool directly over stdio (no Claude Code needed).

Mirror of mcp-tool-call.py but for the em-copilot-mcp server (launched via
``python -m em_copilot.mcp`` since the venv has no pip/em-copilot-mcp.exe).

Usage:
    .venv/Scripts/python.exe scripts/em-copilot-mcp-call.py <tool_name> [json_args]

    json_args: a JSON object string of tool arguments (or "{}" if none).

Proxy is cleared in the child env (same as .mcp.json) so stdio is not severed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

VENV_PY = Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe"


def _read_json_line(proc: subprocess.Popen, timeout: float) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line or not line.strip():
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: em-copilot-mcp-call.py <tool_name> [json_args]", file=sys.stderr)
        return 2
    tool_name = sys.argv[1]
    raw_args = sys.argv[2] if len(sys.argv) > 2 else "{}"
    try:
        tool_args = json.loads(raw_args)
    except json.JSONDecodeError as e:
        print(f"ERROR: bad JSON args: {e}", file=sys.stderr)
        return 2

    env = dict(os.environ)
    env["HTTP_PROXY"] = ""
    env["HTTPS_PROXY"] = ""
    env["NO_PROXY"] = "*"

    proc = subprocess.Popen(
        [str(VENV_PY), "-m", "em_copilot.mcp", "--transport", "stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
    )

    try:
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "tool-call", "version": "0.0.1"},
                    },
                }
            )
            + "\n"
        )
        proc.stdin.flush()
        resp = _read_json_line(proc, timeout=25.0)
        if resp is None:
            err = proc.stderr.read() if proc.stderr else ""
            print(f"FAIL: no initialize response. stderr: {err[:400]}")
            return 1
        if "error" in resp:
            print(f"init error: {resp['error']}")
            return 1

        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n"
        )
        proc.stdin.flush()

        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": tool_args},
                }
            )
            + "\n"
        )
        proc.stdin.flush()

        out = _read_json_line(proc, timeout=90.0)
        if out is None:
            err = proc.stderr.read() if proc.stderr else ""
            print(f"FAIL: no tools/call response in 90s. stderr: {err[:500]}")
            return 1
        if "error" in out:
            print(f"tool error: {json.dumps(out['error'], indent=2, ensure_ascii=False)}")
            return 1

        result = out.get("result", {})
        content = result.get("content", [])
        for block in content:
            if block.get("type") == "text":
                print(block.get("text", ""))
            else:
                print(json.dumps(block, ensure_ascii=False, indent=2))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
