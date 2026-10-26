"""em-copilot-mcp tools: enterprise extension tools (roadmap §8.2/差距-1).

Each tool connects to the SAME AEDT gRPC process the official ``pyaedt-em``
server launched — pass the port returned by the official ``check_aedt_status``.
No extra license is consumed (AEDT licensing is per-process, not per-gRPC-client).

Tools here are high-level and domain-specific; they never expose arbitrary code
execution (roadmap §8.4). Business path tools only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastmcp.server import Context

from em_copilot.mcp.server import app, EmCopilotContext

logger = None  # set lazily; server.py owns logging config


def _get_logger():
    global logger
    if logger is None:
        from ansys.common.mcp import get_logger
        logger = get_logger(__name__)
    return logger


def _connect_aedt(port: int, machine: str = "localhost"):
    """Attach to an already-running AEDT gRPC process (no new license).

    Returns the PyAEDT ``Desktop`` handle. The caller must NOT release it —
    the official server owns the AEDT process lifecycle.
    """
    from ansys.aedt.core import Desktop

    from ansys.aedt.core.generic.settings import settings
    settings.use_grpc_api = True
    settings.release_on_exception = False

    desktop = Desktop(
        new_desktop=False,
        non_graphical=True,
        machine=machine,
        port=port,
    )
    return desktop


def _resolve_port(ctx: Context, port: int | None) -> int:
    """Use the explicit port arg, else fall back to the context-cached port."""
    if port is not None:
        return int(port)
    lifespan = ctx.request_context.lifespan_context
    cached = getattr(lifespan, "aedt_port", None)
    if cached is not None:
        return int(cached)
    raise ValueError(
        "No AEDT gRPC port. Pass `port` (from the official check_aedt_status "
        "response's connectable_sessions[].port), or start this server with "
        "--aedt-port."
    )


def _cache_port(ctx: Context, port: int) -> None:
    """Remember the port so subsequent calls can omit it."""
    lifespan = ctx.request_context.lifespan_context
    if isinstance(lifespan, EmCopilotContext):
        lifespan.aedt_port = int(port)


def _count_touchstone_freqs(path: str, n_ports: int) -> int:
    """Count frequency points in a Touchstone file by data rows / rows-per-freq.

    A .sNp file has n_ports rows per frequency point (each row = one port's
    S-parameters across all ports). Return the number of distinct frequencies.
    """
    p = Path(path)
    if not p.is_file():
        return 0
    rows_per_freq = max(n_ports, 1)
    data_rows = 0
    with p.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("!") or s.startswith("#"):
                continue
            # data rows start with a digit or sign (after leading whitespace)
            if re.match(r"^[+-]?[0-9.]", s):
                data_rows += 1
    return max(data_rows // rows_per_freq, 0)


@app.tool(tags={"em_copilot", "results"})
def export_touchstone_sweep(
    ctx: Context,
    port: int,
    setup_name: str,
    sweep_name: str,
    output_file: str,
    project_name: str | None = None,
    design_name: str | None = None,
    machine: str = "localhost",
) -> str:
    """Export a frequency-sweep Touchstone (.sNp) from a solved HFSS design.

    Connects to the SAME AEDT gRPC process the official ``pyaedt-em`` server
    launched — pass the ``port`` from that server's ``check_aedt_status``
    response (``connectable_sessions[].port`` or the connected status). No
    extra license is consumed.

    Unlike the official ``export_results`` (which defaults to the
    ``LastAdaptive`` single-frequency solution), this tool exports the named
    frequency **sweep** — the full 0–10 GHz (or whatever the sweep covers)
    multi-point S-matrix. The output is validated to contain more than one
    frequency point before being returned.

    Parameters
    ----------
    ctx : Context
        MCP context (used to cache the port for subsequent calls).
    port : int
        AEDT gRPC port, from the official server's ``check_aedt_status``.
    setup_name : str
        Setup name, e.g. ``"3GHz"`` (as shown by ``list_designs`` / the project).
    sweep_name : str
        Sweep name, e.g. ``"DC_10GHz"``. Use ``setup.get_sweep_names()`` via
        ``run_python_code`` if unsure — but this tool validates the name.
    output_file : str
        Destination ``.sNp`` path. Must NOT be inside the input project dir.
    project_name : str, default None
        Project name; ``None`` = active project.
    design_name : str, default None
        Design name; ``None`` = active design.
    machine : str, default "localhost"
        AEDT machine hostname/IP.

    Returns
    -------
    str
        JSON string with ``output_file``, ``n_freqs``, ``n_ports``,
        ``freq_range``, ``setup``, ``sweep``. Raises a descriptive error if
        the setup/sweep is missing or the output has fewer than 2 freq points.
    """
    log = _get_logger()
    port = _resolve_port(ctx, port)
    _cache_port(ctx, port)

    try:
        desktop = _connect_aedt(port, machine)
        from ansys.aedt.core import get_pyaedt_app

        app_inst = get_pyaedt_app(
            project_name=project_name,
            design_name=design_name,
            desktop=desktop,
        )

        # Validate setup exists
        setup_names = [s.name for s in app_inst.setups]
        if setup_name not in setup_names:
            return json.dumps({
                "success": False,
                "error": f"setup {setup_name!r} not found; available: {setup_names}",
            })
        setup = next(s for s in app_inst.setups if s.name == setup_name)

        # Validate sweep exists (get_sweep_names is the authoritative source —
        # setup.sweeps list is known to return [] even when sweeps exist).
        sweep_names = setup.get_sweep_names()
        if sweep_name not in sweep_names:
            return json.dumps({
                "success": False,
                "error": f"sweep {sweep_name!r} not found in setup {setup_name!r}; "
                         f"available: {sweep_names}",
            })

        # Export the sweep (NOT LastAdaptive)
        result = app_inst.export_touchstone(
            setup=setup_name,
            sweep=sweep_name,
            output_file=output_file,
        )

        # Post-validate: ensure >1 freq point (guard against silently getting
        # a LastAdaptive single-point file).
        n_ports = len(getattr(app_inst, "port_list", []) or []) or _guess_ports_from_snppath(output_file)
        n_freqs = _count_touchstone_freqs(output_file, n_ports)
        if n_freqs < 2:
            return json.dumps({
                "success": False,
                "error": f"exported file has only {n_freqs} freq point(s); expected a sweep. "
                         f"Did setup={setup_name!r} sweep={sweep_name!r} resolve correctly?",
                "output_file": output_file,
            })

        payload: dict[str, Any] = {
            "success": True,
            "output_file": str(Path(output_file).resolve()).replace("\\", "/"),
            "n_freqs": n_freqs,
            "n_ports": n_ports,
            "setup": setup_name,
            "sweep": sweep_name,
        }
        log.info("export_touchstone_sweep: %s (%d freqs, %d ports)",
                  output_file, n_freqs, n_ports)
        return json.dumps(payload, indent=2)

    except Exception as e:
        log.error("export_touchstone_sweep failed: %s", e, exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


def _guess_ports_from_snppath(path: str) -> int:
    """Infer port count from a .sNp filename (e.g. foo.s4p → 4)."""
    m = re.search(r"\.s(\d+)p?\b", Path(path).name, re.IGNORECASE)
    return int(m.group(1)) if m else 2
