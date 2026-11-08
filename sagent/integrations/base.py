"""Shared building blocks for integrations: Tool, Integration, REST and CLI helpers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union
from urllib.parse import urlparse

import requests

from .. import redact
from ..config import Config
from ..errors import IntegrationError

MAX_OUTPUT_CHARS = 20000


# --- small helpers ---------------------------------------------------------


def clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def to_text(obj: Any) -> str:
    if isinstance(obj, str):
        return clip(obj)
    return clip(json.dumps(obj, ensure_ascii=False, default=str))


def dig(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def to_int(value: Any, default: int, lo: int = 1, hi: int = 100) -> int:
    if value in (None, ""):
        return default
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        raise IntegrationError(f"Expected a number, got {value!r}")


def require(args: Dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise IntegrationError(f"Missing required argument '{key}'")
    return value.strip()


def scope_query(query: str, clause: str) -> str:
    """AND a scope clause onto a JQL/CQL query while keeping any ORDER BY at the end."""
    match = re.search(r"\border\s+by\b", query, re.I)
    where = query[: match.start()] if match else query
    order = query[match.start():] if match else ""
    combined = f"{clause} AND ({where.strip()})" if where.strip() else clause
    return f"{combined} {order}".strip()


def s(description: str, **extra: Any) -> Dict[str, Any]:
    return {"type": "string", "description": description, **extra}


def i(description: str) -> Dict[str, Any]:
    return {"type": "integer", "description": description}


def strs(description: str) -> Dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "description": description}


def schema(properties: Dict[str, Any], required: Sequence[str] = ()) -> Dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required)}


# --- tools and integrations ------------------------------------------------


@dataclass
class Tool:
    name: str
    description: str
    schema: Dict[str, Any]
    handler: Callable[[Dict[str, Any]], Any]
    # True, False, or a callable deciding per call whether the action changes state.
    write: Union[bool, Callable[[Dict[str, Any]], bool]] = False
    # Human-readable description of a write, shown in confirmations and dry-runs.
    summary: Optional[Callable[[Dict[str, Any]], str]] = None
    # Raises IntegrationError to refuse a call before anything runs.
    validate: Optional[Callable[[Dict[str, Any]], None]] = None
    integration: Optional["Integration"] = None

    def is_write(self, args: Dict[str, Any]) -> bool:
        return bool(self.write(args)) if callable(self.write) else bool(self.write)

    def describe(self, args: Dict[str, Any]) -> str:
        if self.summary:
            return self.summary(args)
        return f"{self.name} {json.dumps(redact.mask_obj(args), ensure_ascii=False)}"


class Integration:
    """Subclass this, set the class attributes, and export `INTEGRATION = YourClass`."""

    name = ""
    title = ""
    section = ""
    config_help = ""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.conf = cfg.section(self.section)

    def configured(self) -> bool:
        raise NotImplementedError

    def writes_enabled(self) -> bool:
        return self.cfg.get_bool(self.section, "allow_writes", False)

    def tools(self) -> List[Tool]:
        raise NotImplementedError

    def check(self) -> str:
        """Cheap authenticated call; returns a short identity string or raises IntegrationError."""
        raise NotImplementedError


# --- REST ------------------------------------------------------------------


class RestClient:
    def __init__(
        self,
        service: str,
        base_url: str,
        auth: Optional[Tuple[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ):
        self.service = service
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        if auth:
            self.session.auth = auth
        self.session.headers.update({"Accept": "application/json", "User-Agent": "sagent"})
        if headers:
            self.session.headers.update(headers)

    def request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Any = None,
        data: Any = None,
        text: bool = False,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        try:
            resp = self.session.request(
                method,
                self.base + path,
                params=params,
                json=json_body,
                data=data,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout:
            raise IntegrationError(f"{self.service}: request timed out")
        except requests.exceptions.RequestException as exc:
            raise IntegrationError(f"{self.service}: cannot reach the API ({type(exc).__name__})")
        if resp.status_code >= 400:
            raise IntegrationError(self._explain(resp))
        if text:
            return resp.text
        if not resp.content:
            return {}
        if "text/html" in resp.headers.get("content-type", "").lower():
            raise IntegrationError(f"{self.service}: got a web page instead of API data; check base_url in your sagent config")
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def _explain(self, resp: "requests.Response") -> str:
        code = resp.status_code
        hints = {
            401: "authentication failed; check the credentials in your sagent config",
            403: "permission denied; the token/user lacks the required scope",
            404: "not found (or no access)",
            429: f"rate limited; retry after {resp.headers.get('Retry-After', 'a while')}s",
        }
        detail = ""
        try:
            body = resp.json()
            if isinstance(body, dict):
                err = body.get("error")
                detail = (
                    (err.get("message") if isinstance(err, dict) else err)
                    or body.get("message")
                    or "; ".join(body.get("errorMessages") or [])
                    or json.dumps(body.get("errors") or "")
                )
        except ValueError:
            detail = "" if resp.text.lstrip().startswith("<") else resp.text
        detail = redact.mask(str(detail or ""))[:300]
        base = hints.get(code, "request failed")
        return f"{self.service}: HTTP {code}, {base}" + (f" ({detail})" if detail else "")


def atlassian_base(conf: Dict[str, Any], service: str, suffix: str = "") -> str:
    raw = str(conf.get("base_url", "")).strip()
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise IntegrationError(f"{service}: base_url must look like https://your-site.atlassian.net")
    # Users often paste a page link; only the site root is meaningful.
    return f"https://{parsed.netloc}{suffix}"


# --- external CLIs (aws / az) ----------------------------------------------


def clean_env() -> Dict[str, str]:
    """Process env without sagent/Anthropic variables."""
    return {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(("SAGENT_", "ANTHROPIC_"))
    }


_CMD_META = set('&|<>^%"\r\n')


def run_cli(
    binary: str,
    argv: List[str],
    env: Dict[str, str],
    service: str,
    install_hint: str,
    timeout: int = 120,
) -> "subprocess.CompletedProcess[str]":
    exe = shutil.which(binary)
    if not exe:
        raise IntegrationError(f"{service}: '{binary}' not found on PATH. {install_hint}")
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        if any(ch in _CMD_META for a in argv for ch in a):
            raise IntegrationError(f"{service}: arguments contain characters that are unsafe on Windows cmd")
    try:
        return subprocess.run(
            [exe] + argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise IntegrationError(f"{service}: command timed out after {timeout}s")
    except OSError as exc:
        raise IntegrationError(f"{service}: cannot run '{binary}': {exc.strerror or exc}")


def check_cli_args(args: Any, blocked_flags: Sequence[str], service: str) -> List[str]:
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise IntegrationError("'args' must be a list of strings")
    for arg in args:
        flag = arg.split("=", 1)[0].lower()
        if flag in blocked_flags:
            raise IntegrationError(f"{service}: option {flag} is not allowed")
        if arg.startswith("@") or "file://" in arg or "fileb://" in arg or re.match(r"(?i)https?://", arg):
            raise IntegrationError(f"{service}: file and URL arguments are not allowed")
    return args
