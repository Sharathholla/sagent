"""External CLIs sagent can use (AI backends, AWS CLI, Azure CLI) and their per-OS install commands. Stdlib only."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from . import providers, style

AWS_DOCS = "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
AZURE_DOCS = "https://learn.microsoft.com/cli/azure/install-azure-cli"


@dataclass
class ExternalTool:
    key: str
    label: str
    purpose: str
    find: Callable[[], Optional[str]]
    command: Callable[[], Optional[str]]
    docs: str
    after: str


def _winget(package: str) -> Optional[str]:
    return f"winget install -e --id {package}" if shutil.which("winget") else None


def _aws_command() -> Optional[str]:
    if os.name == "nt":
        return _winget("Amazon.AWSCLI")
    if sys.platform == "darwin":
        if shutil.which("brew"):
            return "brew install awscli"
        return ('curl -fsSL "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o /tmp/AWSCLIV2.pkg'
                " && sudo installer -pkg /tmp/AWSCLIV2.pkg -target /")
    if sys.platform.startswith("linux") and shutil.which("unzip"):
        return ('curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o /tmp/awscliv2.zip'
                " && unzip -q -o /tmp/awscliv2.zip -d /tmp && sudo /tmp/aws/install --update")
    return None


def _azure_command() -> Optional[str]:
    if os.name == "nt":
        return _winget("Microsoft.AzureCLI")
    if sys.platform == "darwin":
        return "brew install azure-cli" if shutil.which("brew") else None
    if sys.platform.startswith("linux") and Path("/etc/debian_version").exists():
        return "curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash"
    return None


def _ai_tool(name: str, purpose: str) -> ExternalTool:
    return ExternalTool(
        key=name, label=providers.LABEL[name], purpose=purpose,
        find=lambda: providers.find(name), command=lambda: providers.install_command(name),
        docs=providers.DOWNLOAD_URL.get(name, "https://claude.com/claude-code"),
        after=f"Then sign in: {providers.LOGIN_HINT[name]}.",
    )


def all_tools() -> List[ExternalTool]:
    return [
        _ai_tool(providers.AGY, "AI backend using your Google account; select with: sagent use agy"),
        _ai_tool(providers.CLAUDE, "AI backend using your Claude subscription; select with: sagent use claude"),
        ExternalTool("aws", "AWS CLI v2", "needed for the aws integration", lambda: shutil.which("aws"),
                     _aws_command, AWS_DOCS, "Then sign in: aws configure  (or aws sso login)."),
        ExternalTool("azure", "Azure CLI", "needed for the azure integration", lambda: shutil.which("az"),
                     _azure_command, AZURE_DOCS, "Then sign in: az login."),
    ]


def ask(prompt: str, default_yes: bool) -> bool:
    try:
        raw = input(style.bold(prompt)).strip().lower()
    except EOFError:
        return False
    return default_yes if not raw else raw in ("y", "yes")


def run_install(tool: ExternalTool, cmd: str) -> bool:
    """Run a fixed vendor install command the user approved. Returns True if the CLI is found afterwards."""
    try:
        if os.name == "nt":
            proc = subprocess.run(["powershell", "-NoProfile", "-Command", cmd])
        else:
            proc = subprocess.run(cmd, shell=True)
        ok = proc.returncode == 0
    except OSError as exc:
        print(style.red(f"  Could not run the installer: {exc.strerror or exc}"))
        ok = False
    if tool.find():
        print(style.green(f"  {tool.label} installed.") + " " + style.dim(tool.after))
        return True
    if ok:
        print(style.yellow(f"  Installer finished, but {tool.label} is not on PATH yet. Open a new terminal."))
    else:
        print(style.red("  Installation did not complete.") + f" Run it manually: {style.cyan(cmd)}")
    return False


def offer(tool: ExternalTool, interactive: bool, default_yes: bool = False, counter: str = "") -> bool:
    """Offer to install one missing tool. Returns True if it is available afterwards."""
    cmd = tool.command()
    prefix = style.paint(counter, "bold", "cyan") + " " if counter else ""
    print(prefix + style.yellow(f"{tool.label} is not installed") + style.dim(f" ({tool.purpose})"))
    if cmd is None:
        print(f"  Install it manually: {style.cyan(tool.docs)}")
        return False
    if not interactive:
        print(f"  Install it with: {style.cyan(cmd)}")
        return False
    hint = "[Y/n]" if default_yes else "[y/N]"
    if not ask(f"  Install it now by running `{cmd}`? {hint}: ", default_yes):
        print(style.dim(f"  Skipped. Later: {cmd}"))
        return False
    return run_install(tool, cmd)


def offer_missing(interactive: bool) -> None:
    """Go through every optional CLI and offer to install the missing ones, one at a time."""
    missing = [t for t in all_tools() if not t.find()]
    if not missing:
        print(style.green("All optional CLIs (agy, claude, aws, az) are already installed."))
        return
    for i, tool in enumerate(missing, 1):
        print()
        offer(tool, interactive, counter=f"[{i}/{len(missing)}]")
