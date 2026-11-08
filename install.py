#!/usr/bin/env python3
"""Cross-platform installer for sagent.

    python install.py              install (or update)
    python install.py --uninstall  remove the venv and the `sagent` command
    python install.py --uninstall --purge   also delete ~/.sagent (config, logs)

Works the same on macOS, Linux and Windows. Uses only the standard library.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

MIN_PYTHON = (3, 9)
REPO = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"
HOME = Path(os.environ.get("SAGENT_HOME") or Path.home() / ".sagent")
VENV = HOME / "venv"
VENV_PYTHON = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
BIN_DIR = HOME / "bin" if IS_WINDOWS else Path.home() / ".local" / "bin"
LAUNCHER = BIN_DIR / ("sagent.cmd" if IS_WINDOWS else "sagent")

sys.path.insert(0, str(REPO))
from sagent import style  # noqa: E402  stdlib-only


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    say(style.paint(msg[:5], "bold", "cyan") + msg[5:])


def fail(msg: str) -> "None":
    print("\n" + style.paint("ERROR:", "bold", "red", stream=sys.stderr) + " " + style.red(msg, sys.stderr),
          file=sys.stderr)
    sys.exit(1)


def run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], **kwargs)


# --- Windows PATH (user scope, no admin needed) -----------------------------


def _win_path_edit(add: bool) -> None:
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        parts = [p for p in current.split(";") if p]
        same = [p for p in parts if os.path.normcase(p) == os.path.normcase(str(BIN_DIR))]
        if add and not same:
            parts.append(str(BIN_DIR))
        elif not add and same:
            parts = [p for p in parts if p not in same]
        else:
            return
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(parts))
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)


# --- POSIX PATH (shell profile block) ---------------------------------------

MARK_BEGIN = "# >>> sagent >>>"
MARK_END = "# <<< sagent <<<"
PATH_LINE = 'export PATH="$HOME/.local/bin:$PATH"'
FISH_LINE = 'fish_add_path -g "$HOME/.local/bin"'


def _profile_candidates() -> list:
    home = Path.home()
    return [
        home / ".zshrc", home / ".bashrc", home / ".bash_profile", home / ".profile",
        home / ".config" / "fish" / "config.fish",
    ]


def _active_profile() -> Path:
    home = Path.home()
    shell = Path(os.environ.get("SHELL", "")).name
    if shell == "zsh":
        return home / ".zshrc"
    if shell == "fish":
        return home / ".config" / "fish" / "config.fish"
    if shell == "bash":
        # macOS terminals start login shells, which read .bash_profile and not .bashrc.
        if sys.platform == "darwin" or (home / ".bash_profile").exists():
            return home / ".bash_profile"
        return home / ".bashrc"
    return home / ".profile"


def _strip_block(text: str) -> str:
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        if line.strip() == MARK_BEGIN:
            skipping = True
        elif skipping and line.strip() == MARK_END:
            skipping = False
        elif not skipping:
            out.append(line)
    return "".join(out).rstrip("\n") + "\n" if out else ""


def add_posix_path() -> "Path | None":
    """Append a marked PATH block to the user's shell profile. Returns the file edited, if any."""
    profile = _active_profile()
    existing = profile.read_text(encoding="utf-8") if profile.exists() else ""
    if MARK_BEGIN in existing:
        return profile
    line = FISH_LINE if profile.name == "config.fish" else PATH_LINE
    block = f"\n{MARK_BEGIN}\n{line}\n{MARK_END}\n"
    profile.parent.mkdir(parents=True, exist_ok=True)
    with open(profile, "a", encoding="utf-8") as handle:
        handle.write(block)
    return profile


def remove_posix_path() -> None:
    for profile in _profile_candidates():
        try:
            if not profile.exists():
                continue
            text = profile.read_text(encoding="utf-8")
            if MARK_BEGIN in text:
                profile.write_text(_strip_block(text), encoding="utf-8")
                say(f"Removed PATH entry from {profile}")
        except OSError as exc:
            say(f"Could not clean {profile}: {exc.strerror or exc}")


# --- install ----------------------------------------------------------------


def install(reinstall: bool, assume_yes: bool = False) -> None:
    if sys.version_info < MIN_PYTHON:
        fail(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required (you have {sys.version.split()[0]}).")
    step(f"[1/4] Python {sys.version.split()[0]} OK")

    if reinstall and VENV.exists():
        shutil.rmtree(VENV)
    if not VENV_PYTHON.exists():
        step(f"[2/4] Creating virtual environment at {VENV}")
        HOME.mkdir(parents=True, exist_ok=True)
        try:
            venv.EnvBuilder(with_pip=True).create(str(VENV))
        except Exception as exc:  # noqa: BLE001
            hint = " On Debian/Ubuntu: sudo apt install python3-venv" if sys.platform.startswith("linux") else ""
            fail(f"Could not create a virtual environment ({exc}).{hint}")
    else:
        step(f"[2/4] Reusing virtual environment at {VENV}")

    step("[3/4] Installing sagent and its dependencies")
    run([VENV_PYTHON, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    result = run([VENV_PYTHON, "-m", "pip", "install", "--quiet", "--upgrade", "-e", REPO])
    if result.returncode != 0:
        fail("pip failed to install sagent (see output above). Check your network connection and retry.")

    step(f"[4/4] Adding the `sagent` command ({LAUNCHER})")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        LAUNCHER.write_text(f'@echo off\r\n"{VENV_PYTHON}" -m sagent %*\r\n', encoding="ascii")
        _win_path_edit(add=True)
    else:
        if LAUNCHER.is_symlink() or LAUNCHER.exists():
            LAUNCHER.unlink()
        LAUNCHER.symlink_to(VENV / "bin" / "sagent")
        if not _dir_on_path():
            try:
                global PROFILE_EDITED
                PROFILE_EDITED = add_posix_path()
            except OSError as exc:
                say(f"Could not update your shell profile automatically: {exc.strerror or exc}")

    check = run([VENV_PYTHON, "-m", "sagent", "--version"], capture_output=True, text=True)
    if check.returncode != 0:
        fail("Installed, but `sagent --version` failed:\n" + (check.stderr or check.stdout))
    say("\n" + style.paint(f"Installed: {check.stdout.strip()}", "bold", "green"))
    choose_ai_backend(assume_yes)
    run_doctor()
    offer_optional_tools(assume_yes)
    print_next_steps()


PROFILE_EDITED = None


def _interactive() -> bool:
    return bool(sys.stdin and sys.stdin.isatty() and sys.stdout.isatty())


def run_doctor() -> None:
    say("\n" + style.heading("Running sagent doctor"))
    # Failures are expected on a fresh machine (no config, CLIs missing); they are not install errors.
    run([VENV_PYTHON, "-m", "sagent", "doctor"])


def offer_optional_tools(assume_yes: bool) -> None:
    """Offer to install each missing CLI (agy, claude, aws, az), one at a time."""
    from sagent import installers

    say("\n" + style.heading("Optional tools"))
    installers.offer_missing(_interactive() and not assume_yes)


def choose_ai_backend(assume_yes: bool) -> None:
    """Ask which AI CLI sagent should drive (first install only); `sagent use` changes it later."""
    from sagent import providers  # stdlib-only module, importable before the venv is used

    if providers.provider_file().exists():
        say(f"\n{style.bold('AI backend:')} {style.green(providers.LABEL[providers.current()])} (unchanged).")
        return
    # Installing the chosen CLI is offered after `sagent doctor`, with the other optional tools.
    providers.configure(assume_yes=assume_yes, offer=False)


def _dir_on_path() -> bool:
    return any(
        os.path.normcase(os.path.expanduser(p)) == os.path.normcase(str(BIN_DIR))
        for p in os.environ.get("PATH", "").split(os.pathsep)
    )


def print_next_steps() -> None:
    say("\n" + style.heading("Next steps"))
    n = 1
    if IS_WINDOWS:
        say(f"  {n}. Open a NEW terminal so the updated PATH takes effect."); n += 1
    elif PROFILE_EDITED:
        say(f"  {n}. {BIN_DIR} was added to PATH in {PROFILE_EDITED}.")
        say(f"     Open a new terminal, or run: source {PROFILE_EDITED}"); n += 1
    elif not _dir_on_path():
        say(f"  {n}. {BIN_DIR} is not on your PATH. Add this to your shell profile:")
        say(f"       {PATH_LINE}"); n += 1
    from sagent import providers

    active = providers.current()
    if not providers.find(active):
        say(f"  {n}. Install {providers.LABEL[active]}: "
            f"{providers.install_command(active) or providers.DOWNLOAD_URL.get(active, '')}"); n += 1
    say(f"  {n}. Sign in to {providers.LABEL[active]}: {providers.LOGIN_HINT[active]}."); n += 1
    say(f"  {n}. sagent init      # creates ~/.sagent and a config template"); n += 1
    say(f"  {n}. Edit ~/.sagent/config with your service tokens"); n += 1
    say(f"  {n}. sagent doctor    # re-check after installing tools or editing the config"); n += 1
    say(f"  {n}. sagent run \"What can you do?\"")
    say(f"\nSwitch AI any time:  {style.cyan('sagent use claude')}   |   {style.cyan('sagent use agy')}")
    say("\nKeep this repository folder: sagent runs from it (editable install). Uninstall: python install.py --uninstall")


# --- uninstall --------------------------------------------------------------


def uninstall(purge: bool, assume_yes: bool) -> None:
    if LAUNCHER.is_symlink() or LAUNCHER.exists():
        LAUNCHER.unlink()
        say(f"Removed {LAUNCHER}")
    if IS_WINDOWS:
        _win_path_edit(add=False)
    else:
        remove_posix_path()
    try:
        BIN_DIR.rmdir()  # only succeeds when empty
    except OSError:
        pass
    if VENV.exists():
        shutil.rmtree(VENV)
        say(f"Removed {VENV}")
    if purge and HOME.exists():
        if not assume_yes:
            answer = input(f"Delete {HOME} including config and audit logs? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                say("Kept your config and logs.")
                return
        shutil.rmtree(HOME)
        say(f"Removed {HOME}")
    elif HOME.exists():
        say(f"Kept {HOME} (config, logs, skills). Use --purge to delete it too.")
    say(style.green("sagent uninstalled."))


def main() -> None:
    parser = argparse.ArgumentParser(description="Install or uninstall sagent.")
    parser.add_argument("--uninstall", action="store_true", help="remove sagent")
    parser.add_argument("--purge", action="store_true", help="with --uninstall: also delete ~/.sagent")
    parser.add_argument("--reinstall", action="store_true", help="recreate the virtual environment")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args()
    if args.uninstall:
        uninstall(args.purge, args.yes)
    else:
        install(args.reinstall, args.yes)


if __name__ == "__main__":
    main()
