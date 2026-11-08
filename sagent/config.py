"""Locating, reading and protecting ~/.sagent/config (TOML)."""

from __future__ import annotations

import getpass
import os
import stat
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from . import redact
from .errors import ConfigError

ENV_PREFIX = "SAGENT_"
REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = REPO_ROOT / "config.example.toml"
_EXTRA_SECRET_ENV = (
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_CLIENT_SECRET",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
)


def home_dir() -> Path:
    override = os.environ.get("SAGENT_HOME")
    return Path(override).expanduser() if override else Path.home() / ".sagent"


def config_path() -> Path:
    return home_dir() / "config"


def logs_dir() -> Path:
    return home_dir() / "logs"


def user_skills_dir() -> Path:
    return home_dir() / "skills"


class Config:
    def __init__(self, data: Dict[str, Any], path: Path):
        self.data = data
        self.path = path

    def section(self, name: str) -> Dict[str, Any]:
        """File values for a section, overridden by SAGENT_<SECTION>_<KEY> env vars."""
        raw = self.data.get(name)
        merged: Dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
        prefix = f"{ENV_PREFIX}{name.upper()}_"
        for key, value in os.environ.items():
            if key.upper().startswith(prefix) and value != "":
                merged[key[len(prefix):].lower()] = value
        # Template placeholders count as "not set".
        return {k: v for k, v in merged.items() if not (isinstance(v, str) and "REPLACE" in v.upper())}

    def get(self, section: str, key: str, default: Any = None) -> Any:
        value = self.section(section).get(key)
        return default if value in (None, "") else value

    def get_list(self, section: str, key: str) -> List[str]:
        value = self.get(section, key)
        if value is None:
            return []
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return [str(v).strip() for v in value if str(v).strip()]

    def get_bool(self, section: str, key: str, default: bool = False) -> bool:
        value = self.get(section, key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def secret_values(self) -> Set[str]:
        values: Set[str] = set()
        for section in self.data.values():
            if isinstance(section, dict):
                for key, value in section.items():
                    if isinstance(value, str) and redact.SENSITIVE_KEY.search(key):
                        values.add(value)
        for key, value in os.environ.items():
            upper = key.upper()
            if upper in _EXTRA_SECRET_ENV or (
                upper.startswith(ENV_PREFIX) and redact.SENSITIVE_KEY.search(upper)
            ):
                values.add(value)
        return values


def load(path: Optional[Path] = None) -> Config:
    target = path or config_path()
    data: Dict[str, Any] = {}
    if target.exists():
        try:
            with open(target, "rb") as handle:
                data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(
                f"{target} is not valid TOML: {exc}. Strings and list items need double quotes, "
                'e.g. allowed_channels = ["C0123456789"].'
            )
        except OSError as exc:
            raise ConfigError(f"Cannot read {target}: {exc.strerror or exc}")
    cfg = Config(data, target)
    redact.register(cfg.secret_values())
    return cfg


# --- file protection -------------------------------------------------------


def secure_path(path: Path, is_dir: bool = False) -> bool:
    """Restrict access to the current user. Returns False if it could not be applied."""
    try:
        if os.name == "nt":
            grant = f"{getpass.getuser()}:(OI)(CI)F" if is_dir else f"{getpass.getuser()}:F"
            result = subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        os.chmod(path, 0o700 if is_dir else 0o600)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def permission_warnings(path: Path) -> List[str]:
    if not path.exists():
        return []
    try:
        if os.name == "nt":
            out = subprocess.run(["icacls", str(path)], capture_output=True, text=True).stdout
            broad = ("Everyone:", "BUILTIN\\Users:", "Authenticated Users:")
            if any(marker in out for marker in broad):
                return [f"{path} is readable by other users; run: icacls \"{path}\" /inheritance:r /grant:r \"%USERNAME%:F\""]
            return []
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            return [f"{path} has permissions {mode:04o} (too open); run: chmod 600 \"{path}\""]
    except (OSError, subprocess.SubprocessError):
        pass
    return []


def ensure_home() -> Path:
    home = home_dir()
    created = not home.exists()
    home.mkdir(parents=True, exist_ok=True)
    if created:
        secure_path(home, is_dir=True)
    for sub in (logs_dir(), user_skills_dir()):
        sub.mkdir(parents=True, exist_ok=True)
    return home


def create_config_file() -> bool:
    """Write the config template with restrictive permissions. Never overwrites."""
    path = config_path()
    if path.exists():
        return False
    if not TEMPLATE_PATH.exists():
        raise ConfigError(f"Template {TEMPLATE_PATH} is missing; reinstall sagent from a full clone.")
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(str(path), flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    secure_path(path)
    return True
