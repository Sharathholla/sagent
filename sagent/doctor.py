"""`sagent doctor`: verify config, credentials and connectivity."""

from __future__ import annotations

import os
import sys
from typing import List, Tuple

from . import agent, config, integrations, providers, redact, style
from .errors import SagentError


def _line(level: str, text: str) -> None:
    print(style.status(level, text))


def _check_claude(cfg: config.Config, active: bool) -> int:
    """Report on Claude Code. Only the active backend can fail the run."""
    tag = " [active]" if active else ""
    exe = agent.find_claude(cfg)
    if not exe:
        if active:
            _line("FAIL", "Claude Code: not installed" + tag)
            print(agent.INSTALL_HELP)
            return 1
        _line("skip", f"Claude Code: not installed (install: {providers.install_command(providers.CLAUDE)})")
        return 0
    version = agent.claude_version(exe) or "unknown version"
    logged_in, method = agent.claude_login_state(exe)
    if logged_in is False:
        _line("FAIL" if active else "warn", f"Claude Code {version}: installed, not logged in{tag}")
        print(agent.LOGIN_HELP)
        return 1 if active else 0
    if logged_in is None:
        _line("warn", f"Claude Code {version}: installed, could not verify login (try `claude auth status`){tag}")
        return 0
    _line("ok", f"Claude Code {version}: installed, logged in" + (f" ({method})" if method else "") + tag)
    if "api" in method.lower() and "oauth" not in method.lower():
        _line("warn", "Login uses an API key, which is billed per token, not your subscription")
    return 0


def _check_agy(cfg: config.Config, active: bool) -> int:
    """Report on Antigravity (agy). The login check sends a one-word prompt, so it runs only when active."""
    tag = " [active]" if active else ""
    exe = agent.find_agy(cfg)
    if not exe:
        if active:
            _line("FAIL", "Antigravity agy: not installed" + tag)
            print(agent.AGY_INSTALL_HELP)
            return 1
        hint = providers.install_command(providers.AGY) or providers.DOWNLOAD_URL[providers.AGY]
        _line("skip", f"Antigravity agy: not installed (install: {hint})")
        return 0
    version = agent.agy_version(exe) or "unknown version"
    if not active:
        _line("ok", f"Antigravity agy {version}: installed (login not checked while inactive)")
        return 0
    logged_in, detail = agent.agy_login_state(exe)
    if logged_in is False:
        _line("FAIL", f"Antigravity agy {version}: installed, not signed in{tag}")
        print(agent.AGY_LOGIN_HELP)
        return 1
    if logged_in is None:
        _line("warn", f"Antigravity agy {version}: installed, could not verify sign-in{tag} {detail}".rstrip())
        return 0
    _line("ok", f"Antigravity agy {version}: installed, signed in{tag}")
    return 0


def run(cfg: config.Config) -> int:
    failures = 0
    warnings: List[str] = []

    v = sys.version_info
    _line("ok" if v >= (3, 9) else "FAIL", f"Python {v.major}.{v.minor}.{v.micro}")
    if v < (3, 9):
        failures += 1

    if cfg.path.exists():
        _line("ok", f"Config file: {cfg.path}")
        for warning in config.permission_warnings(cfg.path):
            _line("warn", redact.mask(warning))
    else:
        _line("warn", f"No config file at {cfg.path}; run `sagent init`")

    active = providers.current()
    print("\n" + style.heading("AI backends"))
    failures += _check_claude(cfg, active == providers.CLAUDE)
    failures += _check_agy(cfg, active == providers.AGY)
    print()
    for line in providers.switch_lines():
        print(line)
    if os.environ.get("ANTHROPIC_API_KEY"):
        _line("info", "ANTHROPIC_API_KEY is set; sagent ignores it so your subscription login is used")
    print("\n" + style.heading("Integrations"))
    for integ in integrations.load(cfg):
        if not integ.configured():
            _line("skip", f"{integ.name}: not configured ({integ.config_help})")
            continue
        try:
            detail = integ.check()
            mode = "writes enabled" if integ.writes_enabled() else "read-only"
            _line("ok", redact.mask(f"{integ.name}: {detail} [{mode}]"))
        except SagentError as exc:
            _line("FAIL", redact.mask(f"{integ.name}: {exc}"))
            failures += 1
        except Exception as exc:  # noqa: BLE001
            _line("FAIL", f"{integ.name}: unexpected {type(exc).__name__}")
            failures += 1

    print()
    print(style.paint("All checks passed.", "bold", "green") if not failures
          else style.paint(f"{failures} check(s) failed.", "bold", "red"))
    return 1 if failures else 0
