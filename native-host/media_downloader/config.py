"""Small persisted preferences file (~/.config/media-downloader/config.json).

Currently holds exactly one thing: whether to download every item of a
multi-item post (a tweet with several images, a gallery, etc.) without
asking each time. Kept separate from crypto.py's key file since this is a
plain preference, not a secret.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "media-downloader" / "config.json"


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(config: dict[str, Any], path: Path = DEFAULT_CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2))


def get_download_all_default(path: Path = DEFAULT_CONFIG_PATH) -> bool:
    return bool(load_config(path).get("download_all_by_default", False))


def set_download_all_default(value: bool, path: Path = DEFAULT_CONFIG_PATH) -> None:
    config = load_config(path)
    config["download_all_by_default"] = value
    save_config(config, path)
