"""em-copilot-mcp server: lifespan, context, and CLI launcher.

Subclasses ``PyAnsysBaseMCP`` (from ``ansys.common.mcp``) to reuse the official
transport/CLI machinery, but carries a minimal context — we do not run inline
Python (roadmap §8.4: business path must not depend on L4 code execution), so
no ``PersistentPythonSession`` is needed. AEDT connections are made per-tool
against the gRPC port the official ``pyaedt-em`` server already launched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ansys.common.mcp import PyAnsysBaseMCP, get_logger
from ansys.common.mcp.context import PyAnsysBaseAppContext

logger = get_logger(__name__)


@dataclass
class EmCopilotContext(PyAnsysBaseAppContext):
    """Context for em-copilot-mcp.

    Holds an optional cached AEDT gRPC port so repeated tool calls can omit it.
    A ``port`` argument on a tool always overrides the cached value.
    """

    aedt_port: int | None = None

    @property
    def product_instance(self) -> Any | None:  # noqa: D401 — abstract property satisfied
        return None

    @product_instance.setter
    def product_instance(self, value: Any) -> None:
        pass


class EmCopilotMCP(PyAnsysBaseMCP):
    """FastMCP server for em-copilot enterprise extension tools."""

    def __init__(self, name: str = "em-copilot-mcp", *args, **kwargs):
        # need_python=False: we never run inline Python; tools import PyAEDT
        # directly and operate against a shared AEDT gRPC process.
        super().__init__(name=name, need_python=False, *args, **kwargs)

    def create_context(self) -> EmCopilotContext:
        context = EmCopilotContext()
        # Populate from CLI config if present (e.g. --aedt-port prefill).
        cli_cfg = getattr(self, "_cli_config", None)
        if cli_cfg is not None and cli_cfg.get("aedt_port") is not None:
            context.aedt_port = int(cli_cfg["aedt_port"])
        self.context = context
        return context

    def product_startup(self) -> None:
        logger.info("em-copilot-mcp starting up. Tools connect to AEDT on demand.")

    def product_cleanup(self) -> None:
        # We do not own the AEDT process; the official server's disconnect tool
        # releases it. Nothing to clean here.
        pass


# Module-level app instance — tools.py registers @app.tool against this.
app = EmCopilotMCP(name="em-copilot-mcp")


def launcher(argv: list[str] | None = None) -> None:
    """Entry point for the ``em-copilot-mcp`` console script.

    Parses transport selection and an optional ``--aedt-port`` prefill, imports
    the tools module so registrations land on ``app``, then runs the server.
    """
    import importlib

    # Import tools so @app.tool decorators execute and register on `app`.
    importlib.import_module("em_copilot.mcp.tools")

    # Reuse the base-class CLI (transport/http-host/http-port/cors-origins) and
    # add our own --aedt-port via _add_cli_arguments / _configure_from_cli.
    app.run_cli(argv)
