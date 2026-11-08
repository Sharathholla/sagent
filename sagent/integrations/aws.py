"""AWS via the AWS CLI. Read-only by default; secret-revealing commands are always blocked."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from .. import redact
from ..errors import IntegrationError
from .base import Integration, Tool, check_cli_args, clean_env, clip, s, schema, strs, run_cli

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_AWS_DIR = Path.home() / ".aws"
_HINT = "Install the AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"

_READ_PREFIXES = (
    "describe-", "list-", "get-", "batch-get-", "head-", "lookup-", "search-", "validate-",
    "estimate-", "check-", "simulate-",
)
_READ_EXACT = {("s3", "ls"), ("dynamodb", "scan"), ("dynamodb", "query"), ("logs", "filter-log-events"),
               ("logs", "tail"), ("sts", "get-caller-identity")}

# Commands that return secrets/credentials to the caller (and therefore to the model).
_BLOCKED = {
    ("secretsmanager", "get-secret-value"), ("secretsmanager", "batch-get-secret-value"),
    ("kms", "decrypt"), ("sts", "assume-role"), ("sts", "get-session-token"),
    ("sts", "get-federation-token"), ("sts", "assume-role-with-web-identity"),
    ("sts", "assume-role-with-saml"), ("iam", "create-access-key"), ("iam", "create-login-profile"),
    ("iam", "get-login-profile"), ("s3", "presign"), ("ecr", "get-login-password"),
    ("ecr", "get-authorization-token"), ("codeartifact", "get-authorization-token"),
    ("rds", "generate-db-auth-token"), ("ec2", "get-password-data"), ("ssm", "start-session"),
    ("lightsail", "download-default-key-pair"), ("cognito-idp", "admin-get-device"),
}
_BLOCKED_SERVICES = {"configure", "sso", "login", "logout"}
_BLOCKED_FLAGS = ("--profile", "--endpoint-url", "--ca-bundle", "--no-verify-ssl", "--debug",
                  "--cli-input-json", "--cli-input-yaml", "--with-decryption")


def _is_read(service: str, op: str) -> bool:
    return (service, op) in _READ_EXACT or op.startswith(_READ_PREFIXES) or op == "help"


class Aws(Integration):
    name = "aws"
    title = "AWS"
    section = "aws"
    config_help = "[aws]: nothing if ~/.aws is set up (aws configure / aws sso login); else access_key_id + secret_access_key or profile"

    def configured(self) -> bool:
        c = self.conf
        has_files = any((_AWS_DIR / name).exists() for name in ("credentials", "config"))
        return bool((c.get("access_key_id") and c.get("secret_access_key")) or c.get("profile") or has_files)

    def _env(self) -> Dict[str, str]:
        env = clean_env()
        c = self.conf
        if c.get("access_key_id") and c.get("secret_access_key"):
            env["AWS_ACCESS_KEY_ID"] = str(c["access_key_id"])
            env["AWS_SECRET_ACCESS_KEY"] = str(c["secret_access_key"])
            if c.get("session_token"):
                env["AWS_SESSION_TOKEN"] = str(c["session_token"])
            env.pop("AWS_PROFILE", None)
        elif c.get("profile"):
            env["AWS_PROFILE"] = str(c["profile"])
        # Default: the AWS CLI's own files in the home directory (the MCP child env may lack HOME).
        for var, name in (("AWS_SHARED_CREDENTIALS_FILE", "credentials"), ("AWS_CONFIG_FILE", "config")):
            if (_AWS_DIR / name).exists():
                env.setdefault(var, str(_AWS_DIR / name))
        if c.get("region"):
            env["AWS_DEFAULT_REGION"] = env["AWS_REGION"] = str(c["region"])
        env["AWS_PAGER"] = ""
        return env

    def _parse(self, a: Dict[str, Any]):
        service, op = str(a.get("service", "")).lower(), str(a.get("operation", "")).lower()
        if not _NAME.match(service) or not _NAME.match(op):
            raise IntegrationError("service and operation must look like 'ec2' and 'describe-instances'")
        args = check_cli_args(a.get("args") or [], _BLOCKED_FLAGS, "AWS")
        if service in _BLOCKED_SERVICES or (service, op) in _BLOCKED:
            raise IntegrationError(f"AWS: '{service} {op}' is blocked because it can expose credentials")
        return service, op, args

    def validate(self, a: Dict[str, Any]) -> None:
        self._parse(a)

    def is_write(self, a: Dict[str, Any]) -> bool:
        service, op, _ = self._parse(a)
        return not _is_read(service, op)

    def run(self, a: Dict[str, Any]) -> str:
        service, op, args = self._parse(a)
        argv = [service, op] + args + ["--no-cli-pager"]
        if "--output" not in args:
            argv += ["--output", "json"]
        proc = run_cli("aws", argv, self._env(), "AWS", _HINT)
        if proc.returncode != 0:
            raise IntegrationError("AWS CLI error: " + clip(redact.mask(proc.stderr.strip()), 1500))
        return proc.stdout

    def tools(self) -> List[Tool]:
        return [
            Tool(
                "aws_cli",
                "Run an AWS CLI command (no shell). Read operations (describe-/list-/get-...) run directly; "
                "anything else needs allow_writes and human confirmation. Example: service='ec2', "
                "operation='describe-instances', args=['--filters','Name=tag:Env,Values=dev'].",
                schema({"service": s("AWS service, e.g. ec2, s3api, iam"),
                        "operation": s("Operation, e.g. describe-instances"),
                        "args": strs("Extra CLI arguments, one element per token")}, ["service", "operation"]),
                self.run, write=self.is_write, validate=self.validate,
                summary=lambda a: "AWS CLI: aws " + " ".join(
                    [str(a.get("service")), str(a.get("operation"))] + [str(x) for x in (a.get("args") or [])]),
            )
        ]

    def check(self) -> str:
        proc = run_cli("aws", ["sts", "get-caller-identity", "--output", "text", "--query", "Arn"],
                       self._env(), "AWS", _HINT, timeout=30)
        if proc.returncode != 0:
            raise IntegrationError("AWS CLI error: " + clip(redact.mask(proc.stderr.strip()), 400))
        return f"identity {proc.stdout.strip()}"


INTEGRATION = Aws
