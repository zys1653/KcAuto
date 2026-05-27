from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class AppPaths:
    root: Path
    config: Path
    sortie_rules: Path
    templates: Path
    logs: Path


def default_paths() -> AppPaths:
    return AppPaths(
        root=PROJECT_ROOT,
        config=PROJECT_ROOT / "config" / "default.yaml",
        sortie_rules=PROJECT_ROOT / "config" / "tasks" / "sortie.yaml",
        templates=PROJECT_ROOT / "assets" / "templates",
        logs=PROJECT_ROOT / "logs",
    )


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def load_app_config(paths: AppPaths | None = None) -> dict[str, Any]:
    resolved = paths or default_paths()
    return load_yaml(resolved.config)

