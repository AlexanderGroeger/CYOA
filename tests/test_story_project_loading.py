from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from engine.story_core import Reference, load_story_project
from engine.story_core.source import StorySource
from story_core_fixture import write_fixture_story


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_project_loads_all_definition_types_and_uses_type_local_references(tmp_path: Path) -> None:
    story_root, shared_root = write_fixture_story(tmp_path)
    source = StorySource(story_root, shared_root)
    project = load_story_project(story_root, shared_root, source=source)

    assert project.source is source
    assert project.manifest.id == "fixture_story"
    assert project.player_profile.inventory["intro"] == 1
    assert project.audio_config["master_volume"] == 0.8
    assert project.scene("intro").source == story_root / "scenes" / "intro.yaml"
    assert project.item("intro").source == story_root / "items" / "items.yaml"
    assert project.move("intro").source == story_root / "moves" / "moves.yaml"
    assert project.battle("intro").source == story_root / "battles" / "intro.yaml"
    assert project.event_pool("intro").source == story_root / "events" / "intro.yaml"
    assert project.animation("intro").source == story_root / "assets" / "animations" / "intro" / "anim.yaml"
    assert project.schema_for("scene") is project.schema_registry.require("scene")

    # IDs are allowed to overlap across these definitions. The explicit
    # reference type, rather than a global ID namespace, determines lookup.
    assert project.index.resolve(Reference.scene("intro")) is project.scene("intro")
    assert project.index.resolve(Reference.item("intro")) is project.item("intro")
    assert project.index.resolve(Reference.move("intro")) is project.move("intro")
    assert project.index.resolve(Reference.battle("intro")) is project.battle("intro")
    assert project.index.resolve(Reference.event_pool("intro")) is project.event_pool("intro")
    assert project.index.resolve(Reference.animation("intro")) is project.animation("intro")
    assert project.validate().has_errors is False


@pytest.mark.parametrize("story_name", ("demo_story", "mechanics_lab"))
def test_shipped_projects_load_headlessly_without_static_errors(story_name: str) -> None:
    project = load_story_project(
        REPOSITORY_ROOT / "stories" / story_name,
        REPOSITORY_ROOT / "shared_assets",
    )

    assert project.manifest.id == story_name
    assert project.scenes
    assert project.items
    assert project.moves
    assert project.battles
    assert project.player_profile is not None
    assert project.validate().has_errors is False


def test_public_story_core_import_does_not_import_pygame() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import engine.story_core; "
            "assert 'pygame' not in sys.modules, sorted(sys.modules)",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
