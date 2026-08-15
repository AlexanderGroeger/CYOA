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
from engine.battle.qte_harness import QteTestRuntimeError, run_qte_test  # noqa: E402
from engine.core.developer_test import (  # noqa: E402
    DeveloperTestConfigError,
    QteTestConfiguration,
    load_developer_test_configuration,
)
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
        "--battle",
        help="Start the fresh game directly in this battle (requires --developer)",
    )
    parser.add_argument(
        "--developer-test-config",
        help="JSON launch-time state overrides (requires --developer)",
    )
    parser.add_argument(
        "--qte-move",
        help="Test one global combat move directly (requires --developer)",
    )
    parser.add_argument(
        "--qte-level",
        type=int,
        help="Difficulty level for --qte-move (requires --developer)",
    )
    parser.add_argument(
        "--qte-seed",
        type=int,
        help="Optional deterministic seed for --qte-move",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.scene and not args.developer:
        parser.error("--scene requires --developer")
    if args.battle and not args.developer:
        parser.error("--battle requires --developer")
    if args.scene and args.battle:
        parser.error("--scene and --battle are mutually exclusive")
    if args.developer_test_config and not args.developer:
        parser.error("--developer-test-config requires --developer")
    if args.qte_move and not args.developer:
        parser.error("--qte-move requires --developer")
    if args.qte_level is not None and not args.developer:
        parser.error("--qte-level requires --developer")
    if args.qte_level is not None and not args.qte_move:
        parser.error("--qte-level requires --qte-move")
    if args.qte_seed is not None and not args.qte_move:
        parser.error("--qte-seed requires --qte-move")
    if args.qte_move and (args.scene or args.battle):
        parser.error("--qte-move cannot be combined with --scene or --battle")
    if args.qte_move and args.developer_test_config:
        parser.error("--qte-move cannot be combined with --developer-test-config")
    return args


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)

    try:
        if args.qte_move:
            run_qte_test(
                args.story,
                args.shared_assets,
                QteTestConfiguration(args.qte_move, args.qte_level, args.qte_seed),
            )
            return
        if args.developer_test_config:
            configuration = load_developer_test_configuration(args.developer_test_config)
            if isinstance(configuration, QteTestConfiguration):
                if args.scene or args.battle:
                    raise DeveloperTestConfigError(
                        "A QTE test configuration cannot be combined with --scene or --battle"
                    )
                run_qte_test(args.story, args.shared_assets, configuration)
                return
        engine = GameEngine(
            args.story,
            shared_dir=args.shared_assets,
            save_slot=args.save_slot,
            developer_mode=args.developer,
            start_scene_override=args.scene if args.developer else None,
            start_battle_override=args.battle if args.developer else None,
            developer_test_config_path=args.developer_test_config if args.developer else None,
        )
        engine.run()
    except DeveloperTestConfigError as e:
        print(f"Developer test configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    except QteTestRuntimeError as e:
        print(f"QTE runtime error: {e}", file=sys.stderr)
        sys.exit(1)
    except EngineError as e:
        print(f"Story error: {e}", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye!")
        sys.exit(0)


if __name__ == "__main__":
    main()
