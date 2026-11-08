"""Azure via the Azure CLI with a service principal in an isolated CLI profile."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .. import config, redact
from ..errors import IntegrationError
from .base import Integration, Tool, check_cli_args, clean_env, clip, run_cli, s, schema, strs

_HINT = "Install the Azure CLI: https://learn.microsoft.com/cli/azure/install-azure-cli"
_AZURE_DIR = Path.home() / ".azure"
_READ_VERBS = {"list", "show", "get", "exists", "query", "check"}
# Tokens that return secrets/keys/tokens, or mint credentials.
_BLOCKED_TOKENS = {
    "login", "logout", "get-access-token", "keys", "credential", "credentials", "list-keys", "list-key",
    "list-credentials", "list-connection-strings", "show-connection-string", "list-publishing-credentials",
    "list-publishing-profiles", "generate-sas", "create-for-rbac", "get-credentials", "rest",
    "configure", "extension",
}
_BLOCKED_FLAGS = ("--debug", "--password", "-p", "--secret", "--client-secret", "--certificate")
_FLAG_NAME = re.compile(r"^-")


def _tokens(args: List[str]) -> List[str]:
    out = []
    for a in args:
        if _FLAG_NAME.match(a):
            break
        out.append(a.lower())
    return out


class Azure(Integration):
    name = "azure"
    title = "Azure"
    section = "azure"
    config_help = "[azure]: nothing if you ran `az login` (uses ~/.azure); else tenant_id, client_id, client_secret"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._logged_in = False

    def _uses_sp(self) -> bool:
        return all(self.conf.get(k) for k in ("tenant_id", "client_id", "client_secret"))

    def configured(self) -> bool:
        return self._uses_sp() or (_AZURE_DIR / "azureProfile.json").exists()

    def _env(self) -> Dict[str, str]:
        env = clean_env()
        if not self._uses_sp():
            # Reuse the user's own `az login` state.
            env["AZURE_CONFIG_DIR"] = str(_AZURE_DIR)
            env["AZURE_CORE_NO_COLOR"] = "true"
            return env
        profile = config.home_dir() / "azure"
        if not profile.exists():
            profile.mkdir(parents=True, exist_ok=True)
            config.secure_path(profile, is_dir=True)
        env["AZURE_CONFIG_DIR"] = str(profile)
        env["AZURE_CORE_LOGIN_EXPERIENCE_V2"] = "off"
        env["AZURE_CORE_NO_COLOR"] = "true"
        return env

    def _ensure_login(self) -> None:
        if self._logged_in or not self._uses_sp():
            return
        env, c = self._env(), self.conf
        probe = run_cli("az", ["account", "show", "--query", "user.name", "--output", "tsv"], env, "Azure", _HINT, 60)
        if probe.returncode != 0 or probe.stdout.strip().lower() != str(c["client_id"]).lower():
            # The Azure CLI only accepts the secret as an argument; it lives in an isolated profile afterwards.
            login = run_cli(
                "az",
                ["login", "--service-principal", "-u", str(c["client_id"]), "-p", str(c["client_secret"]),
                 "--tenant", str(c["tenant_id"]), "--output", "none"],
                env, "Azure", _HINT, 120,
            )
            if login.returncode != 0:
                first = redact.mask(login.stderr.strip()).splitlines()[:2]
                raise IntegrationError("Azure login failed: " + clip(" ".join(first), 400))
        if c.get("subscription_id"):
            run_cli("az", ["account", "set", "--subscription", str(c["subscription_id"])], env, "Azure", _HINT, 60)
        self._logged_in = True

    def _parse(self, a: Dict[str, Any]) -> Tuple[List[str], str]:
        args = check_cli_args(a.get("args"), _BLOCKED_FLAGS, "Azure")
        tokens = _tokens(args)
        if not tokens:
            raise IntegrationError("Azure: args must start with a command, e.g. ['vm', 'list']")
        if any(t in _BLOCKED_TOKENS for t in tokens):
            raise IntegrationError("Azure: this command is blocked because it can expose credentials or bypass controls")
        if "keyvault" in tokens and "secret" in tokens and tokens[-1] in ("show", "download", "backup"):
            raise IntegrationError("Azure: reading Key Vault secret values is blocked")
        return args, tokens[-1]

    def validate(self, a: Dict[str, Any]) -> None:
        self._parse(a)

    def is_write(self, a: Dict[str, Any]) -> bool:
        _, verb = self._parse(a)
        return not (verb in _READ_VERBS or verb.startswith(("list-", "show-", "get-", "check-")))

    def run(self, a: Dict[str, Any]) -> str:
        args, _ = self._parse(a)
        self._ensure_login()
        argv = list(args)
        if not any(x in ("--output", "-o") for x in argv):
            argv += ["--output", "json"]
        proc = run_cli("az", argv, self._env(), "Azure", _HINT)
        if proc.returncode != 0:
            raise IntegrationError("Azure CLI error: " + clip(redact.mask(proc.stderr.strip()), 1500))
        return proc.stdout

    def tools(self) -> List[Tool]:
        return [
            Tool(
                "azure_cli",
                "Run an Azure CLI (az) command without the leading 'az' (no shell). list/show/get commands run "
                "directly; anything else needs allow_writes and human confirmation. "
                "Example: args=['vm','list','--resource-group','rg1'].",
                schema({"args": strs("az arguments, one element per token, e.g. ['group','list']")}, ["args"]),
                self.run, write=self.is_write, validate=self.validate,
                summary=lambda a: "Azure CLI: az " + " ".join(str(x) for x in (a.get("args") or [])),
            )
        ]

    def check(self) -> str:
        self._ensure_login()
        proc = run_cli("az", ["account", "show", "--query", "{sub:name,tenant:tenantId}", "--output", "tsv"],
                       self._env(), "Azure", _HINT, 60)
        if proc.returncode != 0:
            raise IntegrationError("Azure CLI error: " + clip(redact.mask(proc.stderr.strip()), 400))
        return "logged in; subscription/tenant: " + proc.stdout.strip().replace("\t", " / ")


INTEGRATION = Azure
