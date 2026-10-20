import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from string import Template

import yaml
from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..models import PromptVersion


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    system: str
    user: str
    params: dict = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def render(self, **values: str) -> tuple[str, str]:
        return Template(self.system).safe_substitute(values), Template(self.user).safe_substitute(values)


_cache: dict[str, tuple[float, Prompt]] = {}
_lock = threading.Lock()


def _prompts_dir() -> Path:
    p = Path(get_settings().prompts_dir)
    return p if p.is_absolute() else Path(__file__).resolve().parents[2] / p


def load_file(name: str) -> dict:
    path = _prompts_dir() / f"{name}.yaml"
    if not path.is_file():
        raise KeyError(f"unknown prompt {name}")
    return yaml.safe_load(path.read_text())


def _from_file(name: str, version: str | None = None) -> Prompt:
    data = load_file(name)
    v = version or data["active"]
    spec = data["versions"][v]
    return Prompt(name, v, spec["system"], spec["user"], spec.get("params", {}))


def get(name: str) -> Prompt:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(name)
        if hit and now - hit[0] < get_settings().prompt_cache_ttl_s:
            return hit[1]
    with SessionLocal() as db:
        row = db.scalar(select(PromptVersion).where(PromptVersion.name == name, PromptVersion.is_active.is_(True)))
    prompt = (Prompt(name, row.version, row.system, row.user_template, row.params or {}) if row else _from_file(name))
    with _lock:
        _cache[name] = (now, prompt)
    return prompt


def invalidate(name: str | None = None) -> None:
    with _lock:
        if name:
            _cache.pop(name, None)
        else:
            _cache.clear()


def list_names() -> list[str]:
    return sorted(p.stem for p in _prompts_dir().glob("*.yaml"))
