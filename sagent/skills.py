"""Skill files: Markdown instructions with a small front matter header."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import config
from .errors import SagentError

REPO_SKILLS_DIR = config.REPO_ROOT / "skills"


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    text: str
    keywords: List[str] = field(default_factory=list)
    integrations: List[str] = field(default_factory=list)

    @property
    def source(self) -> str:
        return "user" if config.user_skills_dir() in self.path.parents else "built-in"


def _split_list(value: str) -> List[str]:
    value = value.strip().strip("[]")
    return [v.strip().strip("\"'") for v in value.split(",") if v.strip().strip("\"'")]


def parse(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    meta: Dict[str, str] = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip().lower()] = value.strip()
    default_name = re.sub(r"-skills?$", "", path.stem)
    return Skill(
        name=(meta.get("name") or default_name).strip().strip("\"'"),
        description=meta.get("description", "").strip().strip("\"'"),
        path=path,
        text=text,
        keywords=[k.lower() for k in _split_list(meta.get("keywords", ""))],
        integrations=[i.lower() for i in _split_list(meta.get("integrations", ""))],
    )


def discover() -> Dict[str, Skill]:
    """Built-in skills first; same-named files in ~/.sagent/skills override them."""
    found: Dict[str, Skill] = {}
    for directory in (REPO_SKILLS_DIR, config.user_skills_dir()):
        if directory.is_dir():
            for path in sorted(directory.glob("*.md")):
                try:
                    skill = parse(path)
                except (OSError, UnicodeDecodeError):
                    continue
                found[skill.name.lower()] = skill
    return found


def resolve(ref: str, catalog: Optional[Dict[str, Skill]] = None) -> Skill:
    """Resolve a skill by name ('jira', 'jira-skills', 'jira-skills.md') or file path."""
    catalog = catalog if catalog is not None else discover()
    key = re.sub(r"(-skills)?(\.md)?$", "", ref.strip().lower())
    if key in catalog:
        return catalog[key]
    candidate = Path(ref).expanduser()
    if candidate.is_file():
        return parse(candidate)
    available = ", ".join(sorted(catalog)) or "none found"
    raise SagentError(f"Unknown skill '{ref}'. Available skills: {available}")


def select(task: str, catalog: Dict[str, Skill], limit: int = 3) -> List[Skill]:
    """Pick skills whose name or keywords occur in the task text."""
    lowered = task.lower()
    scored = []
    for skill in catalog.values():
        score = 0
        for term in [skill.name.lower()] + skill.integrations:
            if re.search(r"\b" + re.escape(term) + r"\b", lowered):
                score += 3
        for kw in skill.keywords:
            if re.search(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", lowered):
                score += 1
        if score:
            scored.append((score, skill))
    scored.sort(key=lambda pair: -pair[0])
    return [skill for _, skill in scored[:limit]]


def catalog_text(catalog: Dict[str, Skill]) -> str:
    if not catalog:
        return "No skills installed."
    return "\n".join(f"- {s.name}: {s.description}" for s in catalog.values())
