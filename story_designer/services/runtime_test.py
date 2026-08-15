"""Qt-independent construction of a real game test launch.

The Designer deliberately knows only how to describe the launch.  Process
ownership stays in ``MainWindow`` so this module remains straightforward to
test without starting pygame or Qt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

from engine.core.developer_test import (
    DeveloperTestConfigError,
    SceneTestConfiguration,
)


@dataclass(frozen=True)
class SceneTestLaunch:
    """Complete configuration for one fresh-game scene test."""

    story_root: Path
    scene_id: str
    shared_assets_root: Path | None = None
    python_executable: str = sys.executable
    runtime_script: Path | None = None
    working_directory: Path | None = None
    test_config_path: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "story_root", Path(self.story_root).expanduser().resolve())
        object.__setattr__(self, "scene_id", str(self.scene_id))
        if self.shared_assets_root is not None:
            shared_root = Path(self.shared_assets_root).expanduser()
            if not shared_root.is_absolute():
                # Story/Core's default shared-assets path is interpreted from
                # the Designer process cwd, so preserve that exact root when
                # the child gets a different explicit working directory.
                shared_root = (Path.cwd() / shared_root).resolve()
            object.__setattr__(self, "shared_assets_root", shared_root)
        if self.runtime_script is not None:
            object.__setattr__(self, "runtime_script", Path(self.runtime_script).expanduser().resolve())
        if self.working_directory is not None:
            object.__setattr__(self, "working_directory", Path(self.working_directory).expanduser().resolve())
        if self.test_config_path is not None:
            object.__setattr__(self, "test_config_path", Path(self.test_config_path).expanduser().resolve())

    @property
    def script(self) -> Path:
        return self.runtime_script or Path(__file__).resolve().parents[2] / "main.py"

    @property
    def cwd(self) -> Path:
        return self.working_directory or self.script.parent

    def command(self) -> tuple[str, list[str], str]:
        """Return ``QProcess``-ready program, argument list, and cwd."""

        arguments = [
            str(self.script),
            "--story",
            str(self.story_root),
            "--developer",
            "--scene",
            self.scene_id,
        ]
        if self.shared_assets_root is not None:
            arguments[3:3] = ["--shared-assets", str(self.shared_assets_root)]
        if self.test_config_path is not None:
            # Keep the established ``--developer --scene SCENE`` suffix stable
            # for simple process launchers and diagnostics.
            arguments[3:3] = ["--developer-test-config", str(self.test_config_path)]
        return str(self.python_executable), arguments, str(self.cwd)


def build_runtime_command(
    story_root: str | Path,
    scene_id: str,
    *,
    shared_assets_root: str | Path | None = None,
    python_executable: str | None = None,
    runtime_script: str | Path | None = None,
    working_directory: str | Path | None = None,
    test_config_path: str | Path | None = None,
    developer_test_config_path: str | Path | None = None,
) -> tuple[str, list[str], str]:
    """Build a shell-free command suitable for ``QProcess.start``."""

    if test_config_path is not None and developer_test_config_path is not None:
        raise ValueError("Provide only one of test_config_path and developer_test_config_path")
    config_path = test_config_path if test_config_path is not None else developer_test_config_path
    launch = SceneTestLaunch(
        story_root=Path(story_root),
        scene_id=scene_id,
        shared_assets_root=Path(shared_assets_root) if shared_assets_root is not None else None,
        python_executable=python_executable or sys.executable,
        runtime_script=Path(runtime_script) if runtime_script is not None else None,
        working_directory=Path(working_directory) if working_directory is not None else None,
        test_config_path=Path(config_path) if config_path is not None else None,
    )
    return launch.command()


def resolve_scene_id(selection: Any, project: Any | None = None) -> str | None:
    """Resolve a scene or scene-local Designer selection to its parent scene."""

    if selection is None:
        return None
    scene_id = getattr(selection, "scene_id", None)
    if isinstance(scene_id, str) and scene_id:
        return _known_scene(scene_id, project)
    kind = getattr(selection, "kind", None)
    kind_value = getattr(kind, "value", kind)
    if kind_value == "scene":
        identifier = getattr(selection, "id", None)
        if isinstance(identifier, str) and identifier:
            return _known_scene(identifier, project)
    return None


def _known_scene(scene_id: str, project: Any | None) -> str | None:
    if project is not None:
        scenes = getattr(project, "scenes", ())
        if scene_id not in scenes:
            return None
    return scene_id


__all__ = [
    "DeveloperTestConfigError",
    "SceneTestConfiguration",
    "SceneTestLaunch",
    "build_runtime_command",
    "resolve_scene_id",
]
