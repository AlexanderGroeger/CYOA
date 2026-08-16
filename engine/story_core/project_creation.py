"""Safe, headless creation of new Story/Core projects and definitions.

The Designer owns the dialogs, but this module owns the meaning of a minimum
valid project.  Keeping the document construction here also gives tests and
other authoring tools a Qt-free creation API.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from .index import ContentKind
from .project import StoryProject, load_story_project
from .serialization import dump_document_yaml


DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 360
DEFAULT_START_SCENE = "start"
DEFAULT_QTE_TYPE = "precision_bar"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


class ProjectCreationError(ValueError):
    """A requested project or definition cannot be created safely."""


@dataclass(frozen=True)
class NewStorySpec:
    """Qt-independent values collected by the New Story dialog.

    ``destination`` is the existing parent directory.  The new project is
    created at ``destination / title``; ``story_id`` remains a separate,
    filesystem-safe engine identifier.
    """

    title: str
    story_id: str
    destination: Path
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    start_scene_id: str = DEFAULT_START_SCENE

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", str(self.title).strip())
        object.__setattr__(self, "story_id", str(self.story_id).strip())
        object.__setattr__(self, "destination", Path(self.destination).expanduser())
        object.__setattr__(self, "start_scene_id", str(self.start_scene_id).strip())
        object.__setattr__(self, "width", int(self.width) if isinstance(self.width, int) and not isinstance(self.width, bool) else self.width)
        object.__setattr__(self, "height", int(self.height) if isinstance(self.height, int) and not isinstance(self.height, bool) else self.height)

    @property
    def project_root(self) -> Path:
        return self.destination / self.title

    @property
    def root(self) -> Path:
        """Alias used by callers that describe the result as the root."""

        return self.project_root

    def validate(self) -> None:
        if not self.title:
            raise ProjectCreationError("Story name cannot be empty")
        if self.title in {".", ".."} or Path(self.title).name != self.title:
            raise ProjectCreationError("Story name must be one safe directory name")
        if "\x00" in self.title or any(char in self.title for char in '<>:"/\\|?*') or self.title.endswith((".", " ")):
            raise ProjectCreationError("Story name contains an invalid filesystem character")
        if _is_reserved_name(self.title):
            raise ProjectCreationError(f"Story name {self.title!r} is reserved by Windows")
        for label, value in (("Story ID", self.story_id), ("Start scene ID", self.start_scene_id)):
            validate_identifier(value, label=label)
        for label, value in (("width", self.width), ("height", self.height)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ProjectCreationError(f"{label} must be a positive integer")
        if not self.destination.exists() or not self.destination.is_dir():
            raise ProjectCreationError(f"Project location does not exist: {self.destination}")
        if self.project_root.exists():
            raise ProjectCreationError(f"A project already exists at {self.project_root}; choose another location")


@dataclass(frozen=True)
class DefinitionCreationResult:
    """Files added by a top-level definition mutation."""

    project_root: Path
    kind: ContentKind
    identifier: str
    paths: tuple[Path, ...]
    added_dependency_ids: tuple[str, ...] = ()


def validate_identifier(value: str, *, label: str = "ID") -> str:
    """Validate an ID that will become a filename or registry key."""

    value = str(value).strip()
    if not _IDENTIFIER_RE.fullmatch(value) or _is_reserved_name(value):
        raise ProjectCreationError(
            f"{label} must start with a letter and contain only letters, numbers, '_' or '-'")
    return value


def slugify_story_id(title: str) -> str:
    """Return a conservative default engine ID for a display title."""

    value = re.sub(r"[^A-Za-z0-9]+", "_", str(title).strip()).strip("_").lower()
    value = value or "new_story"
    if value[0].isdigit():
        value = f"story_{value}"
    return value


def create_story_project(
    spec: NewStorySpec,
    *,
    shared_assets_root: str | Path = "shared_assets",
) -> Path:
    """Create and validate a minimum project, returning its root directory.

    All documents are built in memory and written below a new staging
    directory.  The staging directory is validated before it is moved into
    the requested destination, and is the only directory this function ever
    removes during cleanup.
    """

    spec.validate()
    target = spec.project_root.resolve()
    destination = spec.destination.resolve()
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=str(destination)))
    documents = minimum_story_documents(spec)
    try:
        _write_documents(staging, documents)
        project = load_story_project(staging, shared_assets_root)
        errors = project.validate().errors
        if errors:
            detail = "; ".join(item.format() for item in errors)
            raise ProjectCreationError(f"Generated story failed validation: {detail}")
        if target.exists():
            raise ProjectCreationError(f"A project already exists at {target}; choose another location")
        os.replace(staging, target)
    except ProjectCreationError:
        _remove_staging(staging)
        raise
    except (OSError, TypeError, ValueError) as exc:
        _remove_staging(staging)
        raise ProjectCreationError(f"Could not create project {target}: {exc}") from exc
    return target


def create_new_story(spec: NewStorySpec, *, shared_assets_root: str | Path = "shared_assets") -> Path:
    """Descriptive alias for :func:`create_story_project`."""

    return create_story_project(spec, shared_assets_root=shared_assets_root)


# Short aliases make the service convenient for scripts and tests without
# making the descriptive API less discoverable.
create_project = create_story_project


def minimum_story_documents(spec: NewStorySpec) -> dict[str, Any]:
    """Build the canonical minimum document set without touching disk."""

    spec.validate()
    return {
        "story.yaml": {
            "id": spec.story_id,
            "title": spec.title,
            "version": "1.0",
            "start_scene": spec.start_scene_id,
            "display": {"width": spec.width, "height": spec.height},
        },
        "player.yaml": {
            "stats": {"hp": 1, "max_hp": 1},
            "inventory": {},
            "equipment": {},
            "known_moves": [],
        },
        f"scenes/{spec.start_scene_id}.yaml": {
            "id": spec.start_scene_id,
            "text": "",
        },
    }


def create_top_level_definition(
    story_root: str | Path,
    kind: ContentKind | str,
    identifier: str,
    *,
    qte_type: str = DEFAULT_QTE_TYPE,
    shared_assets_root: str | Path = "shared_assets",
) -> DefinitionCreationResult:
    """Create one safe, canonical definition and reload-validate it.

    Creation is intentionally an explicit persisted project mutation.  The
    returned files have normal loader provenance after the caller reloads the
    project; no synthetic working-copy provenance is invented.
    """

    root = Path(story_root).expanduser().resolve()
    if not root.is_dir():
        raise ProjectCreationError(f"Story root does not exist: {root}")
    target_kind = ContentKind.coerce(kind)
    identifier = validate_identifier(identifier, label=f"{target_kind.value.replace('_', ' ').title()} ID")
    project = load_story_project(root, shared_assets_root)
    if project.index is None:
        raise ProjectCreationError("Story project has no content index")
    if project.index.contains(target_kind, identifier):
        raise ProjectCreationError(f"A {target_kind.value.replace('_', ' ')} named {identifier!r} already exists")

    documents = {key: _thaw(value) for key, value in project.source_documents.items()}
    relative_paths: list[str] = []
    dependencies: list[str] = []
    if target_kind is ContentKind.SCENE:
        relative = f"scenes/{identifier}.yaml"
        _require_new_file(root, relative)
        documents[relative] = {"id": identifier, "text": ""}
        relative_paths.append(relative)
    elif target_kind is ContentKind.ITEM:
        relative = "items/items.yaml"
        registry = dict(documents.get(relative, {}))
        if not isinstance(registry, dict):
            raise ProjectCreationError(f"Cannot add an item to malformed {relative}")
        registry[identifier] = {"name": identifier, "type": "item"}
        documents[relative] = registry
        relative_paths.append(relative)
    elif target_kind is ContentKind.MOVE:
        relative = _move_creation_source(project, documents)
        documents[relative] = _append_move(documents.get(relative), _minimal_move(identifier, qte_type))
        relative_paths.append(relative)
    elif target_kind is ContentKind.BATTLE:
        relative = f"battles/{identifier}.yaml"
        _require_new_file(root, relative)
        if not project.moves:
            move_id = _unique_id(project, "starter_move")
            move_relative = _move_creation_source(project, documents)
            documents[move_relative] = _append_move(documents.get(move_relative), _minimal_move(move_id, qte_type))
            relative_paths.append(move_relative)
            dependencies.append(move_id)
        documents[relative] = {
            "id": identifier,
            "enemy": {"name": "Training Dummy", "hp": 1, "attack": 0, "defense": 0},
            "enemy_moves": [
                {"id": "idle", "name": "Idle", "legacy_damage": [0, 0], "weight": 1},
            ],
        }
        relative_paths.append(relative)
    elif target_kind is ContentKind.EVENT_POOL:
        relative = f"events/{identifier}.yaml"
        _require_new_file(root, relative)
        documents[relative] = {"id": identifier, "chance": 0.0, "events": []}
        relative_paths.append(relative)
    elif target_kind is ContentKind.ANIMATION:
        raise ProjectCreationError("Animations need at least one real frame asset; create them through asset tooling")
    else:
        raise ProjectCreationError(f"Cannot create {target_kind.value}")

    _validate_and_commit(root, documents, relative_paths, shared_assets_root)
    return DefinitionCreationResult(root, target_kind, identifier, tuple(root / path for path in relative_paths), tuple(dependencies))


create_definition = create_top_level_definition


def _minimal_move(identifier: str, qte_type: str) -> dict[str, Any]:
    from engine.battle.qte import minimal_qte_payload

    payload = minimal_qte_payload(qte_type)
    if payload is None:
        raise ProjectCreationError(f"Unknown QTE type {qte_type!r}")
    return {
        "id": identifier,
        "name": identifier,
        "common": {"base_power": 1, "qte": payload},
        "difficulty_levels": {1: {}},
        "initial_level": 1,
    }


def _move_creation_source(project: StoryProject, documents: Mapping[str, Any]) -> str:
    candidates = sorted(key for key in documents if key.startswith("moves/") and key.endswith(".yaml"))
    return candidates[0] if candidates else "moves/moves.yaml"


def _append_move(document: Any, move: Mapping[str, Any]) -> Any:
    if document is None:
        return {"moves": [dict(move)]}
    if isinstance(document, Mapping) and "moves" in document:
        result = dict(document)
        entries = result.get("moves")
        if isinstance(entries, list):
            result["moves"] = [*entries, dict(move)]
            return result
        if isinstance(entries, Mapping) and "id" in entries:
            result["moves"] = [dict(entries), dict(move)]
            return result
        raise ProjectCreationError("Existing moves document has an invalid moves collection")
    if isinstance(document, list):
        return [*document, dict(move)]
    if isinstance(document, Mapping) and "id" in document:
        return {"moves": [dict(document), dict(move)]}
    raise ProjectCreationError("Existing moves document has an unsupported root shape")


def _unique_id(project: StoryProject, base: str) -> str:
    candidate = base
    number = 2
    while project.index is not None and project.index.contains(ContentKind.MOVE, candidate):
        candidate = f"{base}_{number}"
        number += 1
    return candidate


def _validate_and_commit(
    root: Path,
    documents: Mapping[str, Any],
    relative_paths: list[str],
    shared_assets_root: str | Path,
) -> None:
    staging_parent = root.parent
    staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.update.", dir=str(staging_parent)))
    try:
        staged_root = staging / root.name
        shutil.copytree(root, staged_root)
        for relative in relative_paths:
            _write_document(staged_root / relative, documents[relative])
        projected = load_story_project(staged_root, shared_assets_root)
        errors = projected.validate().errors
        if errors:
            detail = "; ".join(item.format() for item in errors)
            raise ProjectCreationError(f"Generated definition failed validation: {detail}")
        originals: dict[Path, bytes | None] = {}
        committed: list[Path] = []
        try:
            for relative in relative_paths:
                target = root / relative
                originals[target] = target.read_bytes() if target.exists() else None
                _write_document(target, documents[relative])
                committed.append(target)
        except (OSError, TypeError, ValueError):
            for target in reversed(committed):
                _restore_bytes(target, originals[target])
            raise
    except ProjectCreationError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise ProjectCreationError(f"Could not persist definition: {exc}") from exc
    finally:
        _remove_staging(staging)


def _write_documents(root: Path, documents: Mapping[str, Any]) -> None:
    for relative, document in documents.items():
        _write_document(root / relative, document)


def _write_document(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(dump_document_yaml(document))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _restore_bytes(path: Path, value: bytes | None) -> None:
    if value is None:
        path.unlink(missing_ok=True)
        return
    path.write_bytes(value)


def _require_new_file(root: Path, relative: str) -> None:
    target = root / relative
    if target.exists():
        raise ProjectCreationError(f"Source file already exists: {target}")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _remove_staging(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _is_reserved_name(value: str) -> bool:
    """Check Windows device names without importing platform-specific UI code."""

    stem = value.rstrip(" .").split(".", 1)[0].upper()
    return stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}


__all__ = [
    "DEFAULT_HEIGHT",
    "DEFAULT_QTE_TYPE",
    "DEFAULT_START_SCENE",
    "DEFAULT_WIDTH",
    "DefinitionCreationResult",
    "NewStorySpec",
    "ProjectCreationError",
    "create_new_story",
    "create_project",
    "create_story_project",
    "create_definition",
    "create_top_level_definition",
    "minimum_story_documents",
    "slugify_story_id",
    "validate_identifier",
]
