"""
engine/save/save_system.py

Plain JSON save files, one per slot, under <story>/saves/. Each save
carries the story id and save-format version, so loading a save from a
different story (or an incompatible future format) fails with a clear
error instead of silently producing a corrupt GameState.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from engine.core.game_state import GameState
from engine.errors import SaveVersionError

SAVE_FORMAT_VERSION = 1


def save_game(state: GameState, story_id: str, story_version: str, save_dir: Path | str, slot: str = "slot1") -> Path:
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "save_format_version": SAVE_FORMAT_VERSION,
        "story_id": story_id,
        "story_version": story_version,
        "timestamp": time.time(),
        "state": state.to_dict(),
    }
    path = save_dir / f"{slot}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def load_game(save_dir: Path | str, slot: str, story_id: str) -> GameState:
    path = Path(save_dir) / f"{slot}.json"
    if not path.exists():
        raise SaveVersionError(f"No save found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if payload.get("story_id") != story_id:
        raise SaveVersionError(
            f"Save '{slot}' was made for story '{payload.get('story_id')}', "
            f"not '{story_id}' -- can't load it here."
        )
    if payload.get("save_format_version") != SAVE_FORMAT_VERSION:
        raise SaveVersionError(
            f"Save '{slot}' uses format version {payload.get('save_format_version')}, "
            f"engine expects {SAVE_FORMAT_VERSION}."
        )
    return GameState.from_dict(payload["state"])


def list_saves(save_dir: Path | str) -> list[dict[str, Any]]:
    """Returns metadata (slot, story_id, timestamp) for each save present,
    without fully loading GameState -- useful for a 'choose a save' menu."""
    save_dir = Path(save_dir)
    if not save_dir.is_dir():
        return []
    results = []
    for path in sorted(save_dir.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            results.append({
                "slot": path.stem,
                "story_id": payload.get("story_id"),
                "timestamp": payload.get("timestamp"),
                "current_scene": payload.get("state", {}).get("current_scene"),
            })
        except (json.JSONDecodeError, OSError):
            continue  # skip corrupt save files rather than crashing the menu
    return results
