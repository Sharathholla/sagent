"""Kubernetes via kubectl CLI. Read-only by default; destructive commands are blocked."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import redact
from ..errors import IntegrationError
from .base import Integration, Tool, check_cli_args, clean_env, clip, s, schema, strs, run_cli

_NAME = "kubectl"

# Determine kubeconfig path based on OS
def _get_kubeconfig_path() -> Path:
    system = platform.system()
    if system == "Darwin":  # macOS
        return Path.home() / ".kube" / "config"
    elif system == "Windows":
        return Path.home() / ".kube" / "config"
    elif system == "Linux":
        return Path.home() / ".kube" / "config"
    else:
        return Path.home() / ".kube" / "config"

_KUBECONFIG_PATH = _get_kubeconfig_path()
_HINT = "Install kubectl: https://kubernetes.io/docs/tasks/tools/install-kubectl/"

# Read-only operations (prefixes and exact matches)
_READ_PREFIXES = (
    "get", "list", "describe", "logs", "top", "events", "exec", "port-forward", "proxy",
)
_READ_EXACT = {"api-versions", "api-resources", "cluster-info", "version", "config"}

# Commands that are destructive and need confirmation
_BLOCKED = {
    "delete", "deletecollection", "create", "apply", "patch", "edit", "replace",
    "scale", "autoscale", "set", "rollout", "annotate", "label", "taint", "untaint",
    "cordon", "uncordon", "drain", "exec",
}


def _is_read(verb: str) -> bool:
    return verb in _READ_EXACT or any(verb.startswith(prefix) for prefix in _READ_PREFIXES)


class Kubeconfig(Integration):
    name = "kubeconfig"
    title = "Kubernetes"
    section = "kubeconfig"
    config_help = "[kubeconfig]: nothing if ~/.kube/config exists; else kubeconfig file path or context name"

    def configured(self) -> bool:
        c = self.conf
        # Check if kubeconfig file exists or if a custom path/context is configured
        return _KUBECONFIG_PATH.exists() or c.get("kubeconfig") or c.get("context")

    def _env(self) -> Dict[str, str]:
        env = clean_env()
        c = self.conf
        
        # Set KUBECONFIG if custom path is specified
        if c.get("kubeconfig"):
            kubeconfig_path = Path(c["kubeconfig"]).expanduser()
            if not kubeconfig_path.exists():
                raise IntegrationError(f"Kubeconfig file not found: {kubeconfig_path}")
            env["KUBECONFIG"] = str(kubeconfig_path)
        elif _KUBECONFIG_PATH.exists():
            env["KUBECONFIG"] = str(_KUBECONFIG_PATH)
        
        return env

    def _parse(self, a: Dict[str, Any]):
        verb = str(a.get("verb", "")).lower().strip()
        if not verb:
            raise IntegrationError("verb is required (e.g. 'get', 'describe', 'logs')")
        
        args = check_cli_args(a.get("args") or [], [], "kubectl")
        
        # Check if it's a blocked destructive operation
        if verb in _BLOCKED:
            raise IntegrationError(
                f"kubectl: '{verb}' is blocked because it can modify cluster state. "
                f"Use it only with explicit confirmation."
            )
        
        return verb, args

    def validate(self, a: Dict[str, Any]) -> None:
        self._parse(a)

    def is_write(self, a: Dict[str, Any]) -> bool:
        verb, _ = self._parse(a)
        return not _is_read(verb)

    def run(self, a: Dict[str, Any]) -> str:
        verb, args = self._parse(a)
        argv = [verb] + args
        proc = run_cli("kubectl", argv, self._env(), "kubectl", _HINT)
        if proc.returncode != 0:
            raise IntegrationError("kubectl error: " + clip(redact.mask(proc.stderr.strip()), 1500))
        return proc.stdout

    def tools(self) -> List[Tool]:
        return [
            Tool(
                "kubectl_cmd",
                "Run a kubectl command (no shell). Read operations (get/list/describe/logs/...) run directly; "
                "anything else needs allow_writes and human confirmation. Example: verb='get', "
                "resource='pods', args=['-n', 'default', '--all-namespaces'].",
                schema({
                    "verb": s("kubectl verb, e.g. get, describe, logs, delete, apply"),
                    "resource": s("Kubernetes resource type, e.g. pods, services, deployments"),
                    "args": strs("Extra arguments, one element per token (e.g. ['-n', 'default'])"),
                }, ["verb", "resource"]),
                self.run,
                write=self.is_write,
                validate=self.validate,
                summary=lambda a: "kubectl " + " ".join(
                    [str(a.get("verb")), str(a.get("resource"))] + [str(x) for x in (a.get("args") or [])]),
            )
        ]

    def check(self) -> str:
        # Test kubectl connection to cluster
        proc = run_cli("kubectl", ["cluster-info"], self._env(), "kubectl", _HINT, timeout=30)
        if proc.returncode != 0:
            raise IntegrationError(
                "kubectl error: could not connect to cluster. "
                + clip(redact.mask(proc.stderr.strip()), 400)
            )
        
        # Get current context
        context_proc = run_cli(
            "kubectl", ["config", "current-context"], self._env(), "kubectl", _HINT, timeout=10
        )
        context = context_proc.stdout.strip() if context_proc.returncode == 0 else "unknown"
        
        # Get cluster info
        version_proc = run_cli(
            "kubectl", ["version", "--short"], self._env(), "kubectl", _HINT, timeout=10
        )
        version_info = version_proc.stdout.strip() if version_proc.returncode == 0 else "unknown version"
        
        return f"connected to cluster '{context}' ({version_info})"


INTEGRATION = Kubeconfig
