"""Confirmation, dry-run and audit wrapper around every tool call."""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Tuple

from . import audit, redact, style
from .errors import SagentError
from .integrations.base import Tool, to_text


def confirm(message: str) -> bool:
    """Ask the human on the controlling terminal (stdin may be the MCP pipe)."""
    try:
        if os.name == "nt":
            inp, out = open("CONIN$", "r"), open("CONOUT$", "w")
        else:
            inp, out = open("/dev/tty", "r"), open("/dev/tty", "w")
    except OSError:
        if sys.stdin is not None and sys.stdin.isatty():
            print(style.yellow(message, sys.stderr), file=sys.stderr)
            return input(style.bold("Proceed? [y/N]: ", sys.stderr)).strip().lower() in ("y", "yes")
        return False
    with inp, out:
        out.write(f"\n{style.yellow(message, out)}\n{style.bold('Proceed? [y/N]: ', out)}")
        out.flush()
        return inp.readline().strip().lower() in ("y", "yes")


def execute(tool: Tool, args: Dict[str, Any], dry_run: bool, assume_yes: bool) -> Tuple[str, bool]:
    """Run a tool. Returns (text for the model, is_error). Never raises."""
    base = {"tool": tool.name, "args": args}
    try:
        if tool.validate:
            tool.validate(args)
        if tool.is_write(args):
            summary = redact.mask(tool.describe(args))
            if dry_run:
                audit.log("tool", status="dry_run", write=True, **base)
                return f"[DRY RUN] Not executed. Would perform: {summary}", False
            integration = tool.integration
            if integration is not None and not integration.writes_enabled():
                audit.log("tool", status="refused_writes_disabled", write=True, **base)
                return (
                    f"Refused: writes are disabled for {integration.name}. The user must set "
                    f"allow_writes = true in [{integration.section}] of ~/.sagent/config. "
                    f"Action was: {summary}",
                    True,
                )
            if not assume_yes and not confirm(f"[sagent] State-changing action requested:\n  {summary}"):
                audit.log("tool", status="declined", write=True, **base)
                return "DECLINED: the user did not approve this action. Do not retry it.", True
            status_ok = "executed_auto_yes" if assume_yes else "executed_confirmed"
            result = tool.handler(args)
            audit.log("tool", status=status_ok, write=True, **base)
            return redact.mask(to_text(result)), False
        result = tool.handler(args)
        audit.log("tool", status="ok", write=False, **base)
        return redact.mask(to_text(result)), False
    except SagentError as exc:
        audit.log("tool", status="error", error=str(exc), **base)
        return redact.mask(str(exc)), True
    except Exception as exc:  # noqa: BLE001 - tool failures must not crash the server
        audit.log("tool", status="crash", error=f"{type(exc).__name__}: {exc}", **base)
        return f"Unexpected error in {tool.name}: {type(exc).__name__}", True
