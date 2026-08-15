"""Conservative, source-aware persistence for Story Designer sessions.

The editor hands this module complete semantic source documents.  This module
only checks their on-disk baselines and writes each YAML document atomically;
it never serializes widgets or reconstructs a document from one definition.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from engine.story_core.serialization import dump_document_yaml


@dataclass(frozen=True)
class SourceState:
    """Content identity for one source file at a loaded-project boundary."""

    exists: bool
    size: int = 0
    mtime_ns: int = 0
    digest: str = ""

    @classmethod
    def from_path(cls, path: str | Path) -> "SourceState":
        target = Path(path)
        try:
            stat = target.stat()
        except FileNotFoundError:
            return cls(False)
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return cls(True, stat.st_size, stat.st_mtime_ns, digest.hexdigest())


class PersistenceError(RuntimeError):
    """A source document could not be safely persisted."""


class ExternalChangeConflict(PersistenceError):
    """One or more dirty source files changed since the project was loaded."""

    def __init__(self, paths: Iterable[str | Path]) -> None:
        self.paths = tuple(Path(path) for path in paths)
        rendered = ", ".join(str(path) for path in self.paths)
        super().__init__(f"Source file changed on disk since it was opened: {rendered}")


class ProjectValidationError(PersistenceError):
    """The projected semantic documents contain Core validation errors."""

    def __init__(self, diagnostics: Any) -> None:
        self.diagnostics = diagnostics
        super().__init__("Projected story contains validation errors")


def capture_source_baseline(root: str | Path, relative_paths: Iterable[str]) -> dict[str, SourceState]:
    """Capture content hashes for the loaded source documents."""

    story_root = Path(root)
    return {
        _relative_key(relative): SourceState.from_path(story_root / Path(relative))
        for relative in relative_paths
    }


def changed_source_paths(
    root: str | Path,
    relative_paths: Iterable[str],
    baseline: Mapping[str, SourceState],
) -> tuple[Path, ...]:
    """Return dirty targets whose current disk state differs from baseline."""

    story_root = Path(root)
    changed: list[Path] = []
    for relative in relative_paths:
        key = _relative_key(relative)
        target = story_root / Path(key)
        current = SourceState.from_path(target)
        if current != baseline.get(key, SourceState(False)):
            changed.append(target)
    return tuple(changed)


def atomic_write_yaml(path: str | Path, value: Any) -> Path:
    """Serialize one semantic YAML document using a beside-target replace."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = dump_document_yaml(value)
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
        return target
    except (OSError, TypeError, ValueError) as exc:
        raise PersistenceError(f"Could not atomically save {target}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def save_documents(root: str | Path, documents: Mapping[str, Any], relative_paths: Iterable[str]) -> tuple[Path, ...]:
    """Atomically write the selected complete semantic source documents."""

    story_root = Path(root)
    written: list[Path] = []
    for relative in relative_paths:
        key = _relative_key(relative)
        if key not in documents:
            raise PersistenceError(f"No semantic document was produced for {key!r}")
        written.append(atomic_write_yaml(story_root / Path(key), documents[key]))
    return tuple(written)


def _relative_key(value: str | Path) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise PersistenceError(f"Source path must remain inside the story: {value!r}")
    normalized = path.as_posix()
    if not normalized or normalized == ".":
        raise PersistenceError("Source path must be non-empty")
    return normalized


__all__ = [
    "ExternalChangeConflict",
    "PersistenceError",
    "ProjectValidationError",
    "SourceState",
    "atomic_write_yaml",
    "capture_source_baseline",
    "changed_source_paths",
    "save_documents",
]
