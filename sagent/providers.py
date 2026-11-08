"""AI backends sagent can drive: Claude Code (`claude`) or Antigravity (`agy`). Stdlib only; install.py imports it."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from . import style
CLAUDE = "claude"
AGY = "agy"
ALL = (CLAUDE, AGY)

_ALIASES = {
    "claude": CLAUDE, "claude-code": CLAUDE, "anthropic": CLAUDE,
    "agy": AGY, "antigravity": AGY, "gemini": AGY, "google": AGY,
}
LABEL = {CLAUDE: "Claude Code", AGY: "Antigravity CLI (agy)"}
ACCOUNT = {CLAUDE: "your Claude subscription", AGY: "your Google account"}
LOGIN_HINT = {
    CLAUDE: "run `claude`, type /login and sign in with your Claude subscription",
    AGY: "run `agy` once and sign in with your Google account",
}
DOWNLOAD_URL = {AGY: "https://antigravity.google/download#antigravity-cli"}

_POSIX_INSTALL = {
    CLAUDE: "curl -fsSL https://claude.ai/install.sh | bash",
    AGY: "curl -fsSL https://antigravity.google/cli/install.sh | bash",
}
_WINDOWS_INSTALL = {CLAUDE: "irm https://claude.ai/install.ps1 | iex"}  # agy: manual download on Windows


def home_dir() -> Path:
    override = os.environ.get("SAGENT_HOME")
    return Path(override).expanduser() if override else Path.home() / ".sagent"


def provider_file() -> Path:
    return home_dir() / "provider"


def normalize(value: Optional[str]) -> Optional[str]:
    return _ALIASES.get(str(value or "").strip().lower())


def find(name: str, override: Optional[str] = None) -> Optional[str]:
    cmd = str(override or name)
    found = shutil.which(cmd)
    if found:
        return found
    # Fresh installs often land in a directory that is not on this process's PATH yet.
    extra = os.pathsep.join(str(Path.home() / d) for d in (".local/bin", ".claude/local"))
    return shutil.which(cmd, path=extra)


def saved() -> Optional[str]:
    """The chosen backend: SAGENT_AI_PROVIDER, else ~/.sagent/provider, else None."""
    from_env = normalize(os.environ.get("SAGENT_AI_PROVIDER"))
    if from_env:
        return from_env
    try:
        return normalize(provider_file().read_text(encoding="utf-8"))
    except OSError:
        return None


def current() -> str:
    chosen = saved()
    if chosen:
        return chosen
    for name in ALL:
        if find(name):
            return name
    return CLAUDE


def save(name: str) -> None:
    home_dir().mkdir(parents=True, exist_ok=True)
    provider_file().write_text(name + "\n", encoding="utf-8")


def install_command(name: str) -> Optional[str]:
    table = _WINDOWS_INSTALL if os.name == "nt" else _POSIX_INSTALL
    return table.get(name)


def switch_lines() -> List[str]:
    return [
        f"{style.bold('AI backend:')} {style.paint(LABEL[current()], 'bold', 'green')}",
        f"  switch to Claude Code     : {style.cyan(f'sagent use {CLAUDE}')}",
        f"  switch to Antigravity agy : {style.cyan(f'sagent use {AGY}')}",
    ]


def _input(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return ""


def _prompt_choice(default: str) -> str:
    print("\n" + style.heading("Which AI should sagent use?"))
    for i, name in enumerate(ALL, 1):
        state = style.green("installed") if find(name) else style.yellow("not installed")
        print(f"  {style.bold(f'{i})')} {LABEL[name]:<22} uses {ACCOUNT[name]:<26} [{state}]")
    default_index = ALL.index(default) + 1
    while True:
        raw = _input(style.bold(f"Choose 1 or 2 [{default_index}]: ")).strip().lower()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(ALL):
            return ALL[int(raw) - 1]
        named = normalize(raw)
        if named:
            return named
        print(style.yellow("Please enter 1 or 2."))


def offer_install(name: str, interactive: bool) -> bool:
    """Offer to install the CLI for `name`. Returns True when it is available afterwards."""
    from . import installers  # installers imports this module

    tool = next(t for t in installers.all_tools() if t.key == name)
    if installers.offer(tool, interactive, default_yes=True):
        return True
    if install_command(name) is None:
        print(f"  Then run {style.cyan(f'sagent use {name}')} again.")
    return False


def configure(requested: Optional[str] = None, assume_yes: bool = False, offer: bool = True) -> str:
    """Pick (or ask for) the backend, save it, optionally offer to install its CLI, and print how to switch."""
    interactive = bool(sys.stdin and sys.stdin.isatty() and sys.stdout.isatty())
    name = normalize(requested) if requested else None
    if name is None:
        default = current()
        if interactive and not assume_yes:
            name = _prompt_choice(default)
        else:
            name = default
    save(name)
    print("\n" + style.green(f"sagent will use {LABEL[name]} ({ACCOUNT[name]})."))
    if offer and not find(name):
        offer_install(name, interactive and not assume_yes)
    print(style.dim(f"Sign in if you have not yet: {LOGIN_HINT[name]}."))
    print()
    for line in switch_lines():
        print(line)
    return name
