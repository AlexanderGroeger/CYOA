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
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.core.game_engine import GameEngine  # noqa: E402
from engine.core.developer_test import DeveloperTestConfigError  # noqa: E402
from engine.errors import EngineError  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A data-driven choose-your-own-adventure engine")
    parser.add_argument("--story", required=True, help="Path to a story directory, e.g. stories/demo_story")
    parser.add_argument("--shared-assets", default="shared_assets", help="Path to the shared_assets directory")
    parser.add_argument("--save-slot", default="slot1", help="Save slot name to use for /save and /load")
    parser.add_argument(
        "--developer",
        action="store_true",
        help="Enable explicit developer/test startup options",
    )
    parser.add_argument(
        "--scene",
        help="Start the fresh game in this scene (requires --developer)",
    )
    parser.add_argument(
        "--developer-test-config",
        help="JSON launch-time state overrides (requires --developer)",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.scene and not args.developer:
        parser.error("--scene requires --developer")
    if args.developer_test_config and not args.developer:
        parser.error("--developer-test-config requires --developer")
    return args


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)

    try:
        engine = GameEngine(
            args.story,
            shared_dir=args.shared_assets,
            save_slot=args.save_slot,
            developer_mode=args.developer,
            start_scene_override=args.scene if args.developer else None,
            developer_test_config_path=args.developer_test_config if args.developer else None,
        )
        engine.run()
    except DeveloperTestConfigError as e:
        print(f"Developer test configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    except EngineError as e:
        print(f"Story error: {e}", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye!")
        sys.exit(0)


if __name__ == "__main__":
    main()
