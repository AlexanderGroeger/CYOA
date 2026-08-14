"""Current-project state for the Story Designer.

This module deliberately has no Qt dependency.  It owns editor/document
state while ``StoryProject`` remains an immutable authored-content model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.story_core import ContentKind, Diagnostics, StoryProject, load_story_project


@dataclass(frozen=True)
class DefinitionSelection:
    """Stable identity for one authored definition in the browser."""

    kind: ContentKind | str
    id: str
    source: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ContentKind.coerce(self.kind))
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "source", Path(self.source) if self.source is not None else None)

    @property
    def content_type(self) -> ContentKind:
        return self.kind


class ProjectSession:
    """The currently opened story and lightweight editor state."""

    def __init__(self) -> None:
        self.project: StoryProject | None = None
        self.story_root: Path | None = None
        self.selection: DefinitionSelection | None = None
        self.dirty = False
        self.diagnostics = Diagnostics()

    @classmethod
    def from_path(
        cls,
        story_path: str | Path,
        shared_assets_root: str | Path | None = None,
    ) -> "ProjectSession":
        session = cls()
        session.load(story_path, shared_assets_root)
        return session

    @property
    def current_project(self) -> StoryProject | None:
        return self.project

    @property
    def current_selection(self) -> DefinitionSelection | None:
        return self.selection

    @property
    def selected_definition(self) -> Any | None:
        return self.definition()

    @property
    def validation_diagnostics(self) -> Diagnostics:
        return self.diagnostics

    def open(
        self,
        story_path: str | Path,
        shared_assets_root: str | Path | None = None,
    ) -> StoryProject:
        """Application-friendly alias for :meth:`load`."""

        return self.load(story_path, shared_assets_root)

    def load(
        self,
        story_path: str | Path,
        shared_assets_root: str | Path | None = None,
    ) -> StoryProject:
        """Replace the current project after a successful Core load."""

        root = normalize_story_root(story_path)
        if shared_assets_root is None:
            project = load_story_project(root)
        else:
            project = load_story_project(root, shared_assets_root)

        previous_selection = self.selection
        self.project = project
        self.story_root = root
        self.dirty = False
        self.diagnostics = project.validate()
        self.selection = self._restore_selection(previous_selection)
        return project

    def reload(self, shared_assets_root: str | Path | None = None) -> StoryProject:
        """Build a new ``StoryProject`` from the current source files."""

        if self.story_root is None:
            raise RuntimeError("No story is open")
        return self.load(self.story_root, shared_assets_root)

    def close(self) -> None:
        """Release all current document and selection state."""

        self.project = None
        self.story_root = None
        self.selection = None
        self.dirty = False
        self.diagnostics = Diagnostics()

    def validate(self) -> Diagnostics:
        """Refresh validation diagnostics for the current immutable project."""

        self.diagnostics = self.project.validate() if self.project is not None else Diagnostics()
        return self.diagnostics

    def select(self, selection: DefinitionSelection | None) -> Any | None:
        """Set the current selection, clearing it for missing definitions."""

        if selection is None or self.project is None:
            self.selection = None
            return None
        definition = self.definition(selection)
        self.selection = selection if definition is not None else None
        return definition

    def definition(self, selection: DefinitionSelection | None = None) -> Any | None:
        """Resolve a selection through the Core project index."""

        selection = self.selection if selection is None else selection
        project = self.project
        if project is None or selection is None:
            return None
        if selection.kind is ContentKind.AUDIO:
            return project.audio_config
        assert project.index is not None
        candidates = project.index.candidates(selection.kind, selection.id)
        if selection.source is not None:
            for entry in candidates:
                if entry.source == selection.source:
                    return entry.definition
        return candidates[0].definition if candidates else None

    def _restore_selection(self, selection: DefinitionSelection | None) -> DefinitionSelection | None:
        if selection is None or self.definition(selection) is None:
            return None
        return selection


def normalize_story_root(path: str | Path) -> Path:
    """Normalize a directory or ``story.yaml`` selection to the story root."""

    selected = Path(path).expanduser()
    if selected.name.lower() == "story.yaml":
        selected = selected.parent
    return selected.resolve()


__all__ = ["DefinitionSelection", "ProjectSession", "normalize_story_root"]
