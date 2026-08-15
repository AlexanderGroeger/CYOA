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
    QteTestConfiguration,
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
        return _runtime_command(
            script=self.script, story_root=self.story_root, start_flag="--scene",
            start_id=self.scene_id, shared_assets_root=self.shared_assets_root,
            python_executable=self.python_executable, working_directory=self.cwd,
            test_config_path=self.test_config_path,
        )


@dataclass(frozen=True)
class BattleTestLaunch:
    """Complete configuration for one fresh-game battle test."""

    story_root: Path
    battle_id: str
    shared_assets_root: Path | None = None
    python_executable: str = sys.executable
    runtime_script: Path | None = None
    working_directory: Path | None = None
    test_config_path: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "story_root", Path(self.story_root).expanduser().resolve())
        object.__setattr__(self, "battle_id", str(self.battle_id))
        if self.shared_assets_root is not None:
            shared_root = Path(self.shared_assets_root).expanduser()
            if not shared_root.is_absolute():
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
        return _runtime_command(
            script=self.script, story_root=self.story_root, start_flag="--battle",
            start_id=self.battle_id, shared_assets_root=self.shared_assets_root,
            python_executable=self.python_executable, working_directory=self.cwd,
            test_config_path=self.test_config_path,
        )


@dataclass(frozen=True)
class QteTestLaunch:
    """Complete configuration for one isolated global combat-move test."""

    story_root: Path
    move_id: str
    difficulty_level: int
    shared_assets_root: Path | None = None
    python_executable: str = sys.executable
    runtime_script: Path | None = None
    working_directory: Path | None = None
    seed: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "story_root", Path(self.story_root).expanduser().resolve())
        object.__setattr__(self, "move_id", str(self.move_id))
        if self.shared_assets_root is not None:
            shared_root = Path(self.shared_assets_root).expanduser()
            if not shared_root.is_absolute():
                shared_root = (Path.cwd() / shared_root).resolve()
            object.__setattr__(self, "shared_assets_root", shared_root)
        if self.runtime_script is not None:
            object.__setattr__(self, "runtime_script", Path(self.runtime_script).expanduser().resolve())
        if self.working_directory is not None:
            object.__setattr__(self, "working_directory", Path(self.working_directory).expanduser().resolve())
        QteTestConfiguration(self.move_id, self.difficulty_level, self.seed)

    @property
    def script(self) -> Path:
        return self.runtime_script or Path(__file__).resolve().parents[2] / "main.py"

    @property
    def cwd(self) -> Path:
        return self.working_directory or self.script.parent

    def command(self) -> tuple[str, list[str], str]:
        arguments = [
            str(self.script), "--story", str(self.story_root), "--developer",
            "--qte-move", self.move_id, "--qte-level", str(self.difficulty_level),
        ]
        if self.shared_assets_root is not None:
            arguments[3:3] = ["--shared-assets", str(self.shared_assets_root)]
        if self.seed is not None:
            arguments.extend(["--qte-seed", str(self.seed)])
        return str(self.python_executable), arguments, str(self.cwd)


def _runtime_command(
    *, script: Path, story_root: Path, start_flag: str, start_id: str,
    shared_assets_root: Path | None, python_executable: str,
    working_directory: Path, test_config_path: Path | None,
) -> tuple[str, list[str], str]:
    arguments = [str(script), "--story", str(story_root), "--developer", start_flag, start_id]
    if shared_assets_root is not None:
        arguments[3:3] = ["--shared-assets", str(shared_assets_root)]
    if test_config_path is not None:
        arguments[3:3] = ["--developer-test-config", str(test_config_path)]
    return str(python_executable), arguments, str(working_directory)


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


def build_battle_runtime_command(
    story_root: str | Path,
    battle_id: str,
    *,
    shared_assets_root: str | Path | None = None,
    python_executable: str | None = None,
    runtime_script: str | Path | None = None,
    working_directory: str | Path | None = None,
    test_config_path: str | Path | None = None,
    developer_test_config_path: str | Path | None = None,
) -> tuple[str, list[str], str]:
    """Build the battle equivalent of ``build_runtime_command``."""

    if test_config_path is not None and developer_test_config_path is not None:
        raise ValueError("Provide only one of test_config_path and developer_test_config_path")
    config_path = test_config_path if test_config_path is not None else developer_test_config_path
    launch = BattleTestLaunch(
        story_root=Path(story_root), battle_id=battle_id,
        shared_assets_root=Path(shared_assets_root) if shared_assets_root is not None else None,
        python_executable=python_executable or sys.executable,
        runtime_script=Path(runtime_script) if runtime_script is not None else None,
        working_directory=Path(working_directory) if working_directory is not None else None,
        test_config_path=Path(config_path) if config_path is not None else None,
    )
    return launch.command()


def build_qte_runtime_command(
    story_root: str | Path,
    move_id: str,
    difficulty_level: int,
    *,
    shared_assets_root: str | Path | None = None,
    python_executable: str | None = None,
    runtime_script: str | Path | None = None,
    working_directory: str | Path | None = None,
    seed: int | None = None,
) -> tuple[str, list[str], str]:
    launch = QteTestLaunch(
        story_root=Path(story_root), move_id=move_id, difficulty_level=difficulty_level,
        shared_assets_root=Path(shared_assets_root) if shared_assets_root is not None else None,
        python_executable=python_executable or sys.executable,
        runtime_script=Path(runtime_script) if runtime_script is not None else None,
        working_directory=Path(working_directory) if working_directory is not None else None,
        seed=seed,
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


def resolve_battle_id(selection: Any, project: Any | None = None) -> str | None:
    """Resolve a Battle or any nested Battle Editor selection to its parent."""

    if selection is None:
        return None
    battle_id = getattr(selection, "battle_id", None)
    if isinstance(battle_id, str) and battle_id:
        return _known_battle(battle_id, project)
    kind = getattr(selection, "kind", None)
    kind_value = getattr(kind, "value", kind)
    if kind_value == "battle":
        identifier = getattr(selection, "id", None)
        if isinstance(identifier, str) and identifier:
            return _known_battle(identifier, project)
    return None


def resolve_qte_move_id(selection: Any, project: Any | None = None) -> str | None:
    """Resolve a move or nested Combat Move Editor selection to its parent."""

    if selection is None:
        return None
    move_id = getattr(selection, "move_id", None)
    if isinstance(move_id, str) and move_id:
        return _known_move(move_id, project)
    kind = getattr(selection, "kind", None)
    kind_value = getattr(kind, "value", kind)
    if kind_value == "move":
        identifier = getattr(selection, "id", None)
        if isinstance(identifier, str) and identifier:
            return _known_move(identifier, project)
    return None


def _known_scene(scene_id: str, project: Any | None) -> str | None:
    if project is not None:
        scenes = getattr(project, "scenes", ())
        if scene_id not in scenes:
            return None
    return scene_id


def _known_battle(battle_id: str, project: Any | None) -> str | None:
    if project is not None:
        battles = getattr(project, "battles", ())
        if battle_id not in battles:
            return None
    return battle_id


def _known_move(move_id: str, project: Any | None) -> str | None:
    if project is not None:
        moves = getattr(project, "moves", ())
        if move_id not in moves:
            return None
    return move_id


__all__ = [
    "BattleTestLaunch",
    "DeveloperTestConfigError",
    "QteTestConfiguration",
    "QteTestLaunch",
    "SceneTestConfiguration",
    "SceneTestLaunch",
    "build_battle_runtime_command",
    "build_qte_runtime_command",
    "build_runtime_command",
    "resolve_battle_id",
    "resolve_qte_move_id",
    "resolve_scene_id",
]
