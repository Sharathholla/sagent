"""Integration registry: every module in this package exporting INTEGRATION is picked up."""

from __future__ import annotations

import importlib
import pkgutil
from typing import List, Type

from ..config import Config
from .base import Integration


def available() -> List[Type[Integration]]:
    classes = []
    for info in pkgutil.iter_modules(__path__):  # type: ignore[name-defined]
        if info.name.startswith("_") or info.name == "base":
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        cls = getattr(module, "INTEGRATION", None)
        if cls is not None:
            classes.append(cls)
    return classes


def load(cfg: Config) -> List[Integration]:
    return [cls(cfg) for cls in available()]
