"""Current-project state for the Story Designer.

This module deliberately has no Qt dependency.  It owns editor/document
state while ``StoryProject`` remains an immutable authored-content model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
from typing import Any

from engine.story_core import (
    ContentKind,
    Diagnostics,
    StoryProject,
    load_story_project,
    migrate_legacy_object_interactions,
)

from .persistence import (
    ExternalChangeConflict,
    PersistenceError,
    ProjectValidationError,
    capture_source_baseline,
    changed_source_paths,
    save_documents,
)


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
        self.shared_assets_root: Path | None = None
        self.selection: DefinitionSelection | None = None
        self.diagnostics = Diagnostics()
        self._working_copies: dict[DefinitionSelection, Any] = {}
        self._source_working_copies: dict[str, Any] = {}
        self._history: list[Any] = []
        self._redo_history: list[Any] = []
        self._source_baseline = {}

    @property
    def dirty(self) -> bool:
        """Whether any editor-owned working copy differs from its snapshot."""

        return any(copy.is_dirty for copy in self._working_copies.values()) or any(copy.is_dirty for copy in self._source_working_copies.values())

    @property
    def is_dirty(self) -> bool:
        return self.dirty

    @property
    def can_undo(self) -> bool:
        return bool(self._history)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_history)

    @property
    def dirty_definitions(self) -> frozenset[DefinitionSelection]:
        return frozenset(selection for selection, copy in self._working_copies.items() if copy.is_dirty)

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

        root = Path(story_path).expanduser().resolve()
        shared_root = self.shared_assets_root if shared_assets_root is None else Path(shared_assets_root)
        if shared_root is None:
            project = load_story_project(root)
        else:
            project = load_story_project(root, shared_root)

        previous_selection = self.selection
        self.project = project
        self.story_root = root
        self.shared_assets_root = project.shared_assets_root
        # Reload is intentionally discard-and-replace.  Callers can inspect
        # ``dirty`` before invoking it and provide a save-changes policy later.
        self._working_copies.clear()
        self._source_working_copies.clear()
        self._history.clear()
        self._redo_history.clear()
        self._migrate_legacy_scene_interactions()
        self._source_baseline = capture_source_baseline(root, project.source_documents.keys())
        self.diagnostics = project.validate()
        self.selection = self._restore_selection(previous_selection)
        return project

    def _migrate_legacy_scene_interactions(self) -> None:
        """Canonicalize retired object interaction in editor working state.

        This intentionally does not append an undo command: opening an old
        story establishes the new canonical working representation, and Save
        is the explicit persistence boundary.  Source files are untouched
        until that Save succeeds.
        """

        if self.project is None:
            return
        for scene_id, definition in self.project.scenes.items():
            original = definition.to_mapping()
            migrated = migrate_legacy_object_interactions(original)
            if migrated == original:
                continue
            selection = DefinitionSelection(ContentKind.SCENE, scene_id, getattr(definition, "source", None))
            copy = self.working_copy(selection)
            if copy is not None:
                copy.mapping = migrated

    def reload(self, shared_assets_root: str | Path | None = None) -> StoryProject:
        """Build a new ``StoryProject`` from the current source files."""

        if self.story_root is None:
            raise RuntimeError("No story is open")
        return self.load(self.story_root, shared_assets_root)

    def close(self) -> None:
        """Release all current document and selection state."""

        self.project = None
        self.story_root = None
        self.shared_assets_root = None
        self.selection = None
        self._working_copies.clear()
        self._source_working_copies.clear()
        self._history.clear()
        self._redo_history.clear()
        self._source_baseline = {}
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

    def schema_for(self, selection: DefinitionSelection | None = None):
        """Resolve a selection through the project's authoritative registry."""

        selection = self.selection if selection is None else selection
        if self.project is None or selection is None:
            return None
        return self.project.schema_for(selection.kind)

    def working_copy(self, selection: DefinitionSelection | None = None):
        """Return the one editor-owned working copy for a selected definition."""

        from .editing import DefinitionWorkingCopy

        selection = self.selection if selection is None else selection
        if self.project is None or selection is None:
            return None
        if self.definition(selection) is None:
            return None
        existing = self._working_copies.get(selection)
        if existing is not None:
            return existing
        definition = self.definition(selection)
        if selection.kind is ContentKind.AUDIO:
            mapping = self.project.audio_config
        else:
            mapping = definition.to_mapping()
        existing = DefinitionWorkingCopy.from_mapping(selection, mapping, self.schema_for(selection))
        if selection.kind is ContentKind.SCENE:
            existing.mapping = migrate_legacy_object_interactions(existing.mapping)
        self._working_copies[selection] = existing
        return existing

    editor_working_copy = working_copy

    def property_model(self, selection: DefinitionSelection | None = None):
        """Build a schema-driven, Qt-independent property adapter."""

        from .editing import PropertyModel

        selection = self.selection if selection is None else selection
        copy = self.working_copy(selection)
        if self.project is None or selection is None or copy is None:
            return None
        return PropertyModel(self.project, selection, copy)

    properties = property_model

    def working_mapping(self, selection: DefinitionSelection | None = None) -> dict[str, Any] | None:
        copy = self.working_copy(selection)
        return copy.to_mapping() if copy is not None else None

    def source_working_copy(self, relative_path: str):
        """Return a working copy for a complete file-level source document."""

        from .editing import SourceDocumentWorkingCopy

        if self.project is None:
            return None
        key = str(relative_path).replace("\\", "/")
        existing = self._source_working_copies.get(key)
        if existing is not None:
            return existing
        document = self.semantic_documents(include_source_copies=False).get(key)
        if document is None:
            return None
        existing = SourceDocumentWorkingCopy.from_mapping(key, document)
        self._source_working_copies[key] = existing
        return existing

    def source_working_mapping(self, relative_path: str) -> Any | None:
        copy = self.source_working_copy(relative_path)
        return copy.to_mapping() if copy is not None else None

    semantic_definition = working_mapping

    def apply_command(self, command: Any) -> Any:
        """Validate and apply an explicit edit command to its working copy."""

        from .editing import EditValidationError

        if self.project is None:
            raise RuntimeError("No story is open")
        source_path = getattr(command, "source_path", None)
        if source_path is not None:
            copy = self.source_working_copy(source_path)
            model = None
            if copy is None:
                raise KeyError(f"Unknown source document: {source_path!r}")
            validation = command.validate_source(copy.mapping) if hasattr(command, "validate_source") else command.validate(None) if hasattr(command, "validate") else None
        else:
            copy = self.working_copy(command.selection)
            model = self.property_model(command.selection)
            if copy is None or model is None:
                raise KeyError(f"Unknown definition selection: {command.selection!r}")
            validation = command.validate(model) if hasattr(command, "validate") else model.validate_command(command)
        if not validation.valid:
            raise EditValidationError(command.path, validation.message)
        command.apply(copy)
        if source_path is None:
            self._rebase_source_working_copies()
        self._history.append(command)
        self._redo_history.clear()
        return command

    def undo(self) -> Any | None:
        """Undo the most recent command and move it to redo history."""

        if not self._history:
            return None
        command = self._history.pop()
        copy = self._command_copy(command)
        if copy is None:
            self._history.append(command)
            raise KeyError(f"Unknown definition selection: {command.selection!r}")
        command.undo(copy)
        self._redo_history.append(command)
        return command

    def redo(self) -> Any | None:
        """Reapply the most recently undone command."""

        if not self._redo_history:
            return None
        command = self._redo_history.pop()
        copy = self._command_copy(command)
        if copy is None:
            self._redo_history.append(command)
            raise KeyError(f"Unknown definition selection: {command.selection!r}")
        command.apply(copy)
        self._history.append(command)
        return command

    def _command_copy(self, command: Any):
        source_path = getattr(command, "source_path", None)
        return self.source_working_copy(source_path) if source_path is not None else self.working_copy(command.selection)

    def _base_semantic_documents(self) -> dict[str, Any]:
        """Serialize Core plus definition working copies, excluding file copies."""

        if self.project is None:
            return {}
        from engine.story_core.serialization import serialize_project

        documents = serialize_project(self.project)
        for selection, copy in self._working_copies.items():
            if not copy.is_dirty:
                continue
            definition = self.definition(selection)
            if definition is None:
                continue
            source = getattr(definition, "source", None)
            if selection.kind is ContentKind.AUDIO:
                source = self.project.story_root / "audio.yaml"
            if source is None:
                continue
            try:
                relative = Path(source).relative_to(self.project.story_root).as_posix()
            except ValueError:
                continue
            if relative not in documents:
                documents[relative] = copy.to_mapping()
                continue
            field_path = tuple(getattr(definition, "field_path", ()))
            if not field_path:
                documents[relative] = copy.to_mapping()
            else:
                _replace_path(documents[relative], field_path, copy.to_mapping())
        return documents

    def _rebase_source_working_copies(self) -> None:
        """Keep file-level working documents in sync with sibling edits."""

        if not self._source_working_copies:
            return
        base = self._base_semantic_documents()
        for key, copy in self._source_working_copies.items():
            if key in base:
                copy.rebase(base[key])

    def revert_definition(self, selection: DefinitionSelection | None = None) -> bool:
        selection = self.selection if selection is None else selection
        copy = self._working_copies.get(selection) if selection is not None else None
        if copy is None:
            return False
        copy.revert()
        self._history = [command for command in self._history if command.selection != selection]
        self._redo_history = [command for command in self._redo_history if command.selection != selection]
        return True

    revert_selected = revert_definition

    def revert_all(self) -> None:
        for copy in self._working_copies.values():
            copy.revert()
        for copy in self._source_working_copies.values():
            copy.revert()
        self._history.clear()
        self._redo_history.clear()

    def dirty_document_paths(self) -> frozenset[str]:
        """Return source documents touched by dirty working definitions."""

        if self.project is None or self.story_root is None:
            return frozenset()
        result: set[str] = set()
        for selection in self.dirty_definitions:
            definition = self.definition(selection)
            source = self.project.story_root / "audio.yaml" if selection.kind is ContentKind.AUDIO else getattr(definition, "source", None)
            if source is None:
                raise PersistenceError(f"Definition {selection.id!r} has no source document")
            try:
                result.add(Path(source).relative_to(self.story_root).as_posix())
            except ValueError as exc:
                raise PersistenceError(f"Cannot save definition {selection.id!r} outside the story root") from exc
        result.update(key for key, copy in self._source_working_copies.items() if copy.is_dirty)
        return frozenset(result)

    def save(self, *, overwrite_external: bool = False, allow_validation_errors: bool = False) -> bool:
        """Save all dirty source documents and reload a new Core snapshot."""

        return self.save_all(
            overwrite_external=overwrite_external,
            allow_validation_errors=allow_validation_errors,
        )

    def save_all(
        self,
        *,
        overwrite_external: bool = False,
        allow_validation_errors: bool = False,
    ) -> bool:
        """Atomically save every dirty source document, then reload the story.

        Save and Save All intentionally share project-level semantics in this
        first persistence phase.  This avoids reconciling one document against
        a still-pending edit in another source file.
        """

        if self.project is None or self.story_root is None:
            return False
        relative_paths = self.dirty_document_paths()
        if not relative_paths:
            return True
        changed = changed_source_paths(self.story_root, relative_paths, self._source_baseline)
        if changed and not overwrite_external:
            raise ExternalChangeConflict(changed)
        documents = self.semantic_documents()
        if not allow_validation_errors:
            diagnostics = self._validate_projected_documents(documents)
            if diagnostics.errors:
                raise ProjectValidationError(diagnostics)
        save_documents(self.story_root, documents, sorted(relative_paths))
        # ``load`` only replaces state after Core loading succeeds.  If reload
        # fails, the old working copies and old baseline remain available to the
        # caller, and no dirty state is falsely cleared.
        self.load(self.story_root, self.shared_assets_root)
        return True

    def _validate_projected_documents(self, documents: dict[str, Any]) -> Diagnostics:
        """Run Core validation against a temporary projected story tree."""

        if self.story_root is None:
            return Diagnostics()
        with tempfile.TemporaryDirectory(prefix="story-designer-validate-") as temporary:
            projected_root = Path(temporary) / self.story_root.name
            shutil.copytree(self.story_root, projected_root)
            save_documents(projected_root, documents, documents.keys())
            projected = load_story_project(projected_root, self.shared_assets_root)
            return projected.validate()

    def is_definition_dirty(self, selection: DefinitionSelection) -> bool:
        copy = self._working_copies.get(selection)
        return bool(copy is not None and copy.is_dirty)

    @property
    def working_definitions(self):
        """Read-only selection-to-copy view of editor-owned pending mappings."""

        return MappingProxyType({
            selection: copy.to_mapping()
            for selection, copy in self._working_copies.items()
        })

    def revert(self, selection: DefinitionSelection | None = None) -> bool:
        """Revert one definition, or all definitions when no selection is given."""

        if selection is None:
            self.revert_all()
            return True
        return self.revert_definition(selection)

    def semantic_documents(self, *, include_source_copies: bool = True) -> dict[str, Any]:
        """Return project documents with pending edits applied in memory only."""
        documents = self._base_semantic_documents()
        if include_source_copies:
            for key, copy in self._source_working_copies.items():
                if copy.is_dirty:
                    copy.rebase(documents.get(key, copy.original_mapping))
                    documents[key] = copy.to_mapping()
        return documents

    updated_documents = semantic_documents


def _replace_path(document: Any, path: tuple[Any, ...], value: Any) -> None:
    """Replace a complete definition inside a mapping, list, or root document."""

    if not path:
        raise PersistenceError("A root definition must replace its source document directly")
    current = document
    for component in path[:-1]:
        if isinstance(current, dict) and component in current:
            current = current[component]
        elif isinstance(current, list) and isinstance(component, int) and 0 <= component < len(current):
            current = current[component]
        else:
            raise PersistenceError(f"Cannot locate source definition path {path!r}")
    final = path[-1]
    if isinstance(current, dict) and final in current:
        current[final] = value
    elif isinstance(current, list) and isinstance(final, int) and 0 <= final < len(current):
        current[final] = value
    else:
        raise PersistenceError(f"Cannot locate source definition path {path!r}")

__all__ = ["DefinitionSelection", "ProjectSession"]
