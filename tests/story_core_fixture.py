"""Small authored-story fixtures shared by Story/Core tests.

The fixture intentionally gives several definition types the same "intro"
ID. That is valid content: Story/Core indexes IDs by definition type, not in
one global namespace.
"""

from __future__ import annotations

from pathlib import Path


def write_fixture_story(tmp_path: Path) -> tuple[Path, Path]:
    """Write a compact valid story and an otherwise-empty shared asset root."""

    story_root = tmp_path / "fixture_story"
    shared_root = tmp_path / "shared_assets"

    def write(relative_path: str, contents: str) -> None:
        target = story_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")

    write(
        "story.yaml",
        "id: fixture_story\n"
        "title: Fixture Story\n"
        "version: \"1.0\"\n"
        "start_scene: intro\n"
        "display: {width: 320, height: 180}\n"
        "starting_flags: {visited: false}\n"
        "starting_variables: {coins: 0}\n",
    )
    write(
        "player.yaml",
        "stats: {hp: 10, max_hp: 10}\n"
        "inventory: {intro: 1}\n"
        "equipment: {}\n"
        "known_moves: [intro]\n",
    )
    write(
        "audio.yaml",
        "master_volume: 0.8\n"
        "music_volume: 0.7\n"
        "effects_volume: 0.6\n",
    )
    write(
        "scenes/intro.yaml",
        "id: intro\n"
        "text: Welcome.\n"
        "future_scene_extension:\n"
        "  preserves: true\n"
        "actions:\n"
        "  - add_item: intro\n"
        "choices:\n"
        "  - text: Continue\n"
        "    goto: ending\n"
        "  - text: Fight\n"
        "    battle: intro\n"
        "  - text: Chance\n"
        "    random_event: intro\n",
    )
    write(
        "scenes/ending.yaml",
        "id: ending\n"
        "text: Done.\n"
        "ending: true\n",
    )
    write(
        "items/items.yaml",
        "intro:\n"
        "  name: Intro Token\n"
        "  type: key\n"
        "  future_item_extension:\n"
        "    preserves: true\n",
    )
    write(
        "moves/moves.yaml",
        "moves:\n"
        "  - id: intro\n"
        "    name: Intro Strike\n"
        "    qte: {type: precision_bar}\n"
        "    future_move_extension: {preserves: true}\n",
    )
    write(
        "battles/intro.yaml",
        "id: intro\n"
        "enemy:\n"
        "  name: Training Dummy\n"
        "  hp: 1\n"
        "  attack: 0\n"
        "  defense: 0\n"
        "  moves:\n"
        "    - name: Bump\n"
        "      damage: [0, 0]\n"
        "      weight: 1\n"
        "victory:\n"
        "  rewards:\n"
        "    items: [intro]\n"
        "future_battle_extension: {preserves: true}\n",
    )
    write(
        "events/intro.yaml",
        "id: intro\n"
        "chance: 1\n"
        "events:\n"
        "  - id: ending\n"
        "    weight: 1\n"
        "future_event_extension: {preserves: true}\n",
    )
    write(
        "assets/animations/intro/anim.yaml",
        "frames: [frame.txt]\n"
        "frame_delay_ms: 100\n"
        "loop: true\n"
        "future_animation_extension: {preserves: true}\n",
    )
    write("assets/animations/intro/frame.txt", "frame")
    shared_root.mkdir(parents=True, exist_ok=True)
    return story_root, shared_root
