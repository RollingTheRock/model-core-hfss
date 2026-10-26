"""em-copilot-mcp: enterprise extension MCP server (roadmap §3.2/§8.2/§16.5).

Independent stdio/http MCP server that adds high-level domain tools the official
``ansys-aedt-mcp`` does not provide. It shares the *same* AEDT gRPC process the
official server launched — no extra license, no rewrite of official tools.

Namespace discipline (roadmap §2.2/§8.2): this lives in ``em_copilot.mcp`` and
**never** touches ``ansys.aedt.mcp``. The official server is used as-is; we only
add tools here.
"""
