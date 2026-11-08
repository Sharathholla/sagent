"""Secret masking used for every piece of output, log line and tool result."""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Tuple

MASK = "********"

SENSITIVE_KEY = re.compile(
    r"(token|secret|password|passwd|api_?key|access_?key|private_?key|credential)", re.I
)

# (pattern, replacement) pairs for well-known secret shapes.
_PATTERNS: List[Tuple["re.Pattern[str]", str]] = [
    (re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"), MASK),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bAT(?:ATT|BB)[A-Za-z0-9_\-=+/]{20,}"), MASK),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"), r"\1" + MASK),
    (
        re.compile(r"(?i)((?:password|secret|token|api[_-]?key)[\"']?\s*[=:]\s*[\"']?)[^\s\"',}]+"),
        r"\1" + MASK,
    ),
]

_secrets: List[str] = []


def register(values: Iterable[str]) -> None:
    """Register literal secret values to be masked wherever they appear."""
    global _secrets
    unique = {v for v in values if isinstance(v, str) and len(v) >= 6}
    _secrets = sorted(unique, key=len, reverse=True)


def mask(text: Any) -> str:
    text = text if isinstance(text, str) else str(text)
    for secret in _secrets:
        text = text.replace(secret, MASK)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def mask_obj(obj: Any) -> Any:
    """Recursively mask a JSON-like structure."""
    if isinstance(obj, dict):
        return {
            k: (MASK if isinstance(k, str) and SENSITIVE_KEY.search(k) and v else mask_obj(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [mask_obj(v) for v in obj]
    if isinstance(obj, str):
        return mask(obj)
    return obj
