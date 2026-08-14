#!/usr/bin/env python3
"""
main.py

CLI entry point for the CYOA engine.

Usage:
    python main.py --story stories/demo_story
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.core.game_engine import GameEngine  # noqa: E402
from engine.errors import EngineError  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="A data-driven choose-your-own-adventure engine")
    parser.add_argument("--story", required=True, help="Path to a story directory, e.g. stories/demo_story")
    parser.add_argument("--shared-assets", default="shared_assets", help="Path to the shared_assets directory")
    parser.add_argument("--save-slot", default="slot1", help="Save slot name to use for /save and /load")
    args = parser.parse_args()

    try:
        engine = GameEngine(args.story, shared_dir=args.shared_assets, save_slot=args.save_slot)
        engine.run()
    except EngineError as e:
        print(f"Story error: {e}", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye!")
        sys.exit(0)


if __name__ == "__main__":
    main()
