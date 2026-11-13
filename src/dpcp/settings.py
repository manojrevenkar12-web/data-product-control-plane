"""Platform settings from config/platform.toml. Code reads settings from here, never from literals."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")


@dataclass(frozen=True)
class Settings:
    raw: dict

    def __getitem__(self, key: str) -> dict:
        return self.raw[key]

    @property
    def gates_enabled(self) -> bool:
        return bool(self.raw["gates"]["enabled"])

    def with_gates(self, enabled: bool) -> Settings:
        raw = {k: dict(v) if isinstance(v, dict) else v for k, v in self.raw.items()}
        raw["gates"]["enabled"] = enabled
        return Settings(raw)


def load(path: Path | None = None) -> Settings:
    return Settings(tomllib.loads((path or ROOT / "config" / "platform.toml").read_text()))


def classification_rank(name: str) -> int:
    return CLASSIFICATIONS.index(name)
