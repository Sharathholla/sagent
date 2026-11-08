"""Append-only audit log at ~/.sagent/logs/audit-YYYY-MM-DD.jsonl (secrets redacted)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from . import config, redact


def log(event: str, **fields: Any) -> None:
    now = datetime.now(timezone.utc)
    record = {"ts": now.isoformat(timespec="seconds"), "event": event, "pid": os.getpid()}
    record.update(fields)
    line = json.dumps(redact.mask_obj(record), ensure_ascii=False, default=str)
    try:
        directory = config.logs_dir()
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
            config.secure_path(directory, is_dir=True)
        path = directory / f"audit-{now:%Y-%m-%d}.jsonl"
        is_new = not path.exists()
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if is_new:
            config.secure_path(path)
    except OSError:
        pass  # auditing must never break the command
