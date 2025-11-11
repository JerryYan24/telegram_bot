from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load YAML config file."""
    resolved = _resolve_path(path)
    if not resolved or not resolved.exists():
        return {}
    with resolved.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件 {resolved} 顶层需要是一个对象。")
    return data


def _resolve_path(path: Optional[str]) -> Optional[Path]:
    if path:
        return Path(path).expanduser()
    default = Path("config.yaml")
    return default if default.exists() else None


def get_config_value(
    config: Dict[str, Any],
    key_path: str,
    env_var: str,
    default: Optional[Any] = None,
    *,
    cast=str,
) -> Optional[Any]:
    """Return env override if present, otherwise read nested config value."""
    if env_var:
        env_value = os.getenv(env_var)
        if env_value is not None:
            return env_value
    value = _deep_get(config, key_path.split("."))
    if value is None:
        return default
    if cast and value is not None:
        try:
            return cast(value)
        except Exception:
            return value
    return value


def _deep_get(data: Dict[str, Any], keys) -> Optional[Any]:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current
