"""AI backend wrapper (Claude Code `claude -p` or Antigravity `agy -p`) with sagent tools attached over MCP."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import audit, config as config_mod, providers, redact, skills as skills_mod
from .config import Config
from .errors import SagentError

# Claude's own tools are all disabled: it must act only through sagent's MCP tools,
# and must not be able to read ~/.sagent/config.
BUILTIN_TOOLS = [
    "Bash", "BashOutput", "KillShell", "KillBash", "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep",
    "LS", "NotebookRead", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite", "SlashCommand",
]

INSTALL_HELP = """Claude Code is not installed (or not on PATH).
  Install it with one of:
    macOS / Linux : curl -fsSL https://claude.ai/install.sh | bash
    Windows (PowerShell): irm https://claude.ai/install.ps1 | iex
    Any OS (Node 18+): npm install -g @anthropic-ai/claude-code
  Then run `sagent doctor` again. To use Antigravity instead: sagent use agy"""

LOGIN_HELP = """Claude Code is installed but you are not logged in.
  Run `claude` once and sign in with your Claude subscription (type /login),
  or run `claude auth login`. Then retry. To use Antigravity instead: sagent use agy"""

AGY_INSTALL_HELP = """Antigravity CLI (agy) is not installed (or not on PATH).
  macOS / Linux : curl -fsSL https://antigravity.google/cli/install.sh | bash
  Windows       : download from https://antigravity.google/download#antigravity-cli
  Then run `sagent doctor` again. To use Claude Code instead: sagent use claude"""

AGY_LOGIN_HELP = """Antigravity (agy) is installed but not signed in.
  Run `agy` once and sign in with your Google account, then retry.
  To use Claude Code instead: sagent use claude"""

# agy has no per-run MCP flag: sagent registers itself once under this name in agy's MCP config.
AGY_SERVER = "sagent"
# Defence in depth: agy's own file/shell/web tools are denied; headless mode also auto-denies anything unlisted.
AGY_DENY = [
    "run_command", "write_file", "read_file", "view_file", "list_dir", "grep_search", "find_by_name",
    "execute_url", "search_web",
]

SYSTEM_RULES = """You are sagent, a command-line assistant. You act only through the tools of the "sagent" MCP server.
You have no shell, filesystem or web access.

Rules:
1. Never ask the user for credentials and never try to read or reveal secrets; the tools handle credentials.
2. Prefer read-only tools. Before any state-changing tool, state your plan (what, where, why). The human will be
   asked to confirm. If a tool result says DECLINED, Refused, or [DRY RUN], do not retry or work around it; report it.
3. Follow the skill instructions below. Their guardrails override the request if they conflict.
4. If a needed tool is missing, say so and suggest running `sagent doctor`.
5. Be concise. Use the list_skills / load_skill tools if you need instructions for another area."""

DRY_RUN_NOTE = "DRY-RUN MODE: write tools will not execute; they only report what they would do. Present the plan."

_AUTH_ERROR = re.compile(
    r"(not logged in|not signed in|please run /login|invalid api key|oauth token has expired|"
    r"authentication_error|unauthenticated|sign in|log ?in required|401)", re.I)


def _env_for_claude() -> Dict[str, str]:
    # Drop API keys so Claude Code uses the subscription login, not pay-per-token billing.
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


def find_claude(cfg: Config) -> Optional[str]:
    return providers.find(providers.CLAUDE, str(cfg.get("claude", "command", "claude")))


def find_agy(cfg: Config) -> Optional[str]:
    return providers.find(providers.AGY, str(cfg.get("agy", "command", "agy")))


def claude_version(exe: str) -> Optional[str]:
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30, env=_env_for_claude())
        return proc.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def claude_login_state(exe: str) -> Tuple[Optional[bool], str]:
    """(logged_in, auth_method). logged_in is None when it cannot be determined."""
    try:
        proc = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=30,
                              env=_env_for_claude(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    try:
        data = json.loads(proc.stdout)
        if isinstance(data, dict) and "loggedIn" in data:
            return bool(data["loggedIn"]), str(data.get("authMethod") or "")
    except ValueError:
        pass
    if proc.returncode == 0:
        return True, ""
    low = (proc.stdout + proc.stderr).lower()
    if "not logged in" in low or "/login" in low or "auth login" in low:
        return False, ""
    return None, ""


def agy_workspace() -> Path:
    """Empty private directory used as agy's workspace so its own file tools see nothing of value."""
    path = config_mod.home_dir() / "agy-workspace"
    path.mkdir(parents=True, exist_ok=True)
    return path


def agy_version(exe: str) -> Optional[str]:
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30,
                              stdin=subprocess.DEVNULL)
        return proc.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def agy_login_state(exe: str) -> Tuple[Optional[bool], str]:
    """agy has no status command, so send a one-word prompt. (logged_in or None, detail)."""
    try:
        proc = subprocess.run(
            [exe, "--output-format", "json", "--print=Reply with the single word OK"],
            capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL, cwd=str(agy_workspace()),
        )
    except (OSError, subprocess.SubprocessError):
        return None, ""
    data = _parse_result(proc.stdout)
    if data and str(data.get("status")) == "SUCCESS":
        return True, ""
    text = (proc.stdout + proc.stderr)
    if _AUTH_ERROR.search(text):
        return False, ""
    return None, redact.mask(text.strip())[:200]


def ensure_agy_mcp(exe: str) -> bool:
    """Make sure agy knows the sagent MCP server. Returns True if it had to (re)register it."""
    try:
        listing = subprocess.run([exe, "mcp", "list"], capture_output=True, text=True, timeout=30,
                                 stdin=subprocess.DEVNULL, cwd=str(agy_workspace())).stdout
    except (OSError, subprocess.SubprocessError):
        listing = ""
    for line in listing.splitlines():
        if line.split()[:1] == [AGY_SERVER] and sys.executable in line and "disabled" not in line.lower():
            return False
    result = subprocess.run(
        [exe, "mcp", "add", AGY_SERVER, sys.executable, "-m", "sagent", "mcp-serve"],
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, cwd=str(agy_workspace()),
    )
    if result.returncode != 0:
        raise SagentError(f"Could not register sagent's tools with agy: {redact.mask((result.stderr or result.stdout).strip())}")
    return True


def ensure_ready(cfg: Config) -> Tuple[str, str]:
    """Return (provider, executable) for the selected AI backend, or raise with install/login help."""
    provider = providers.current()
    if provider == providers.AGY:
        exe = find_agy(cfg)
        if not exe:
            raise SagentError(AGY_INSTALL_HELP)
        ensure_agy_mcp(exe)
        return provider, exe
    exe = find_claude(cfg)
    if not exe:
        raise SagentError(INSTALL_HELP)
    logged_in, _ = claude_login_state(exe)
    if logged_in is False:
        raise SagentError(LOGIN_HELP)
    return provider, exe


def _parse_result(stdout: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    if isinstance(data, list):
        data = next((d for d in reversed(data) if isinstance(d, dict) and d.get("type") == "result"), None)
    return data if isinstance(data, dict) else None


class Session:
    """One conversation with the selected AI backend. Each turn is a `-p` call, resumed by session id."""

    def __init__(self, cfg: Config, dry_run: bool = False, assume_yes: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.assume_yes = assume_yes
        self.provider, self.exe = ensure_ready(cfg)
        self.catalog = skills_mod.discover()
        self.session_id: Optional[str] = None
        self.loaded: set = set()

    def _mcp_config(self) -> Dict[str, Any]:
        args = ["-m", "sagent", "mcp-serve"]
        if self.dry_run:
            args.append("--dry-run")
        if self.assume_yes:
            args.append("--yes")
        # Pass overrides by reference (${VAR}) so no secret value is written to disk.
        env = {k: "${%s}" % k for k in os.environ if k.upper().startswith("SAGENT_")}
        if "PATH" in os.environ:
            env["PATH"] = "${PATH}"
        return {"mcpServers": {"sagent": {"command": sys.executable, "args": args, "env": env}}}

    def _preface(self, selected: List[skills_mod.Skill]) -> str:
        parts: List[str] = []
        if self.session_id is None:
            parts.append(SYSTEM_RULES)
            if self.dry_run:
                parts.append(DRY_RUN_NOTE)
            parts.append("Available skills:\n" + skills_mod.catalog_text(self.catalog))
        for skill in selected:
            if skill.name not in self.loaded:
                self.loaded.add(skill.name)
                parts.append(f'<skill name="{skill.name}">\n{skill.text}\n</skill>')
        return "\n\n".join(parts)

    def ask(self, message: str, selected: Optional[List[skills_mod.Skill]] = None) -> str:
        preface = self._preface(selected or [])
        prompt = (preface + "\n\n# User request\n" if preface else "") + message
        audit.log("ai_turn", provider=self.provider, skills=sorted(self.loaded), message=message[:500],
                  dry_run=self.dry_run)
        if self.provider == providers.AGY:
            return self._ask_agy(prompt)
        return self._ask_claude(prompt)

    def _ask_agy(self, prompt: str) -> str:
        if os.name == "nt" and len(prompt) > 28000:
            raise SagentError("The request plus loaded skills exceeds the Windows command-line limit for agy; "
                              "shorten the request or load fewer skills.")
        settings = {"permissions": {"allow": [f"mcp({AGY_SERVER}/*)"], "deny": AGY_DENY}}
        cmd = [self.exe, "--output-format", "json", "--settings", json.dumps(settings)]
        model = self.cfg.get("agy", "model")
        if model:
            cmd += ["--model", str(model)]
        if self.session_id:
            cmd += ["--conversation", self.session_id]
        cmd.append("--print=" + prompt)  # prompt is argv-only; the = form is safe for text starting with '-'
        env = dict(os.environ)
        env.pop("SAGENT_MCP_DRY_RUN", None)
        env.pop("SAGENT_MCP_YES", None)
        if self.dry_run:
            env["SAGENT_MCP_DRY_RUN"] = "1"
        if self.assume_yes:
            env["SAGENT_MCP_YES"] = "1"
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                env=env, cwd=str(agy_workspace()), stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise SagentError(f"Could not run agy: {exc.strerror or exc}")

        data = _parse_result(proc.stdout)
        if data is None:
            detail = redact.mask((proc.stderr or proc.stdout).strip())[:1500]
            hint = f"\n{AGY_LOGIN_HELP}" if _AUTH_ERROR.search(detail) else ""
            raise SagentError(f"agy failed (exit {proc.returncode}): {detail}{hint}")
        if str(data.get("status")) != "SUCCESS" or data.get("error"):
            detail = redact.mask(str(data.get("error") or data.get("status")))[:1500]
            hint = f"\n{AGY_LOGIN_HELP}" if _AUTH_ERROR.search(detail) else ""
            raise SagentError(f"agy reported an error: {detail}{hint}")
        self.session_id = data.get("conversation_id") or self.session_id
        text = str(data.get("response") or "")
        if not text and data.get("denied_actions"):
            names = ", ".join(sorted({str(a.get("action")) for a in data["denied_actions"] if isinstance(a, dict)}))
            return f"(agy did not answer: it tried to use blocked tools: {names}. sagent only allows its own MCP tools.)"
        return text or "(agy returned no response)"

    def _ask_claude(self, prompt: str) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        try:
            json.dump(self._mcp_config(), handle)
            handle.close()
            cmd = [
                self.exe, "-p", "--output-format", "json",
                "--mcp-config", handle.name, "--strict-mcp-config",
                "--allowedTools", "mcp__sagent",
                "--disallowedTools", ",".join(BUILTIN_TOOLS),
            ]
            model = self.cfg.get("claude", "model")
            if model:
                cmd += ["--model", str(model)]
            if self.session_id:
                cmd += ["--resume", self.session_id]
            try:
                proc = subprocess.run(
                    cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", env=_env_for_claude(),
                )
            except OSError as exc:
                raise SagentError(f"Could not run Claude Code: {exc.strerror or exc}")
        finally:
            try:
                os.unlink(handle.name)
            except OSError:
                pass

        data = _parse_result(proc.stdout)
        if data is None or proc.returncode != 0 and not data.get("result"):
            detail = redact.mask((proc.stderr or proc.stdout).strip())[:1500]
            hint = f"\n{LOGIN_HELP}" if _AUTH_ERROR.search(detail) else ""
            raise SagentError(f"Claude Code failed (exit {proc.returncode}): {detail}{hint}")
        self.session_id = data.get("session_id") or self.session_id
        text = str(data.get("result") or "")
        if data.get("is_error"):
            hint = f"\n{LOGIN_HELP}" if _AUTH_ERROR.search(text) else ""
            raise SagentError(f"Claude Code reported an error: {redact.mask(text)[:1500]}{hint}")
        return text or f"(Claude stopped early: {data.get('subtype', 'no result')})"
