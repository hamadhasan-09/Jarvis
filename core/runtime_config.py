"""Runtime configuration helpers for the active JARVIS architecture.

Environment variables take precedence over config.yaml. Missing optional YAML
support or a malformed file never prevents the local HUD from starting; callers
receive their explicit defaults instead.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable


def load_runtime_config(project_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(project_root or Path.cwd()).resolve()
    path = root / "config.yaml"
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def config_value(config: dict[str, Any] | None, path: Iterable[str], default: Any = None) -> Any:
    value: Any = config or {}
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return default if value is None else value


def env_or_config(
    config: dict[str, Any] | None,
    env_name: str,
    path: Iterable[str],
    default: Any = None,
) -> Any:
    value = os.getenv(env_name)
    if value is not None and value.strip() != "":
        return value
    return config_value(config, path, default)


def env_bool_or_config(
    config: dict[str, Any] | None,
    env_name: str,
    path: Iterable[str],
    default: bool = False,
) -> bool:
    value = env_or_config(config, env_name, path, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
