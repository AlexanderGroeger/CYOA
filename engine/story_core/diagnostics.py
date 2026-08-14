"""Structured, source-qualified diagnostics for the headless Story Core."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, TypeAlias, overload


FieldPath: TypeAlias = tuple[str | int, ...]


class DiagnosticSeverity(str, Enum):
    """The stable severity vocabulary exposed to automation and tooling."""

    ERROR = "error"
    WARNING = "warning"
    INFORMATION = "information"
    # Common concise spelling used by callers and editor integrations.
    INFO = "information"
    ADVISORY = "advisory"

    @classmethod
    def coerce(cls, value: "DiagnosticSeverity | str") -> "DiagnosticSeverity":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        aliases = {"info": cls.INFORMATION, "information": cls.INFORMATION}
        if normalized in aliases:
            return aliases[normalized]
        return cls(normalized)


def format_field_path(path: Sequence[str | int] | None) -> str:
    """Render a tuple field path in familiar YAML/property notation."""

    if not path:
        return ""
    result = ""
    for component in path:
        if isinstance(component, int):
            result += f"[{component}]"
        elif result:
            result += f".{component}"
        else:
            result = str(component)
    return result


@dataclass(frozen=True)
class Diagnostic:
    """One independent validation or compatibility finding.

    ``source`` intentionally identifies a file rather than an in-memory
    model.  PyYAML's normal parser does not retain source marks, so
    :attr:`path` provides the precise author-facing location for this phase.
    """

    source: Path | None
    path: FieldPath
    code: str
    severity: DiagnosticSeverity
    message: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", Path(self.source) if self.source is not None else None)
        object.__setattr__(self, "path", tuple(self.path))
        object.__setattr__(self, "code", str(self.code))
        object.__setattr__(self, "severity", DiagnosticSeverity.coerce(self.severity))
        object.__setattr__(self, "message", str(self.message))

    @property
    def source_path(self) -> Path | None:
        return self.source

    @property
    def field_path(self) -> FieldPath:
        return self.path

    @property
    def path_text(self) -> str:
        return format_field_path(self.path)

    @property
    def is_error(self) -> bool:
        return self.severity is DiagnosticSeverity.ERROR

    @property
    def is_warning(self) -> bool:
        return self.severity is DiagnosticSeverity.WARNING

    @property
    def is_information(self) -> bool:
        return self.severity in {DiagnosticSeverity.INFORMATION, DiagnosticSeverity.ADVISORY}

    @property
    def location(self) -> str:
        source = str(self.source) if self.source is not None else "<project>"
        return f"{source}:{self.path_text}" if self.path_text else source

    def with_prefix(self, *prefix: str | int) -> "Diagnostic":
        """Return the same finding relocated beneath a parent field path."""

        return Diagnostic(self.source, tuple(prefix) + self.path, self.code, self.severity, self.message)

    def format(self) -> str:
        """Produce a compact, stable human-readable rendering."""

        return f"{self.location}: {self.severity.value} [{self.code}] {self.message}"

    def __str__(self) -> str:
        return self.format()


class Diagnostics(Sequence[Diagnostic]):
    """A compact list-like diagnostic collection with severity helpers."""

    def __init__(self, entries: Iterable[Diagnostic] = ()):
        self._entries = list(entries)

    def __len__(self) -> int:
        return len(self._entries)

    @overload
    def __getitem__(self, index: int) -> Diagnostic:
        ...

    @overload
    def __getitem__(self, index: slice) -> list[Diagnostic]:
        ...

    def __getitem__(self, index: int | slice) -> Diagnostic | list[Diagnostic]:
        return self._entries[index]

    def __iter__(self) -> Iterator[Diagnostic]:
        return iter(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)

    def append(self, diagnostic: Diagnostic) -> Diagnostic:
        if not isinstance(diagnostic, Diagnostic):
            raise TypeError("Diagnostics can contain Diagnostic instances only")
        self._entries.append(diagnostic)
        return diagnostic

    add = append

    def extend(self, diagnostics: Iterable[Diagnostic]) -> None:
        for diagnostic in diagnostics:
            self.append(diagnostic)

    def emit(
        self,
        severity: DiagnosticSeverity | str,
        code: str,
        message: str,
        *,
        source: Path | str | None = None,
        path: Sequence[str | int] = (),
    ) -> Diagnostic:
        diagnostic = Diagnostic(Path(source) if source is not None else None, tuple(path), code, DiagnosticSeverity.coerce(severity), message)
        self.append(diagnostic)
        return diagnostic

    def error(self, code: str, message: str, *, source: Path | str | None = None, path: Sequence[str | int] = ()) -> Diagnostic:
        return self.emit(DiagnosticSeverity.ERROR, code, message, source=source, path=path)

    def warning(self, code: str, message: str, *, source: Path | str | None = None, path: Sequence[str | int] = ()) -> Diagnostic:
        return self.emit(DiagnosticSeverity.WARNING, code, message, source=source, path=path)

    def information(self, code: str, message: str, *, source: Path | str | None = None, path: Sequence[str | int] = ()) -> Diagnostic:
        return self.emit(DiagnosticSeverity.INFORMATION, code, message, source=source, path=path)

    info = information

    def advisory(self, code: str, message: str, *, source: Path | str | None = None, path: Sequence[str | int] = ()) -> Diagnostic:
        return self.emit(DiagnosticSeverity.ADVISORY, code, message, source=source, path=path)

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(item for item in self if item.is_error)

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(item for item in self if item.is_warning)

    @property
    def information_items(self) -> tuple[Diagnostic, ...]:
        return tuple(item for item in self if item.is_information)

    @property
    def advisories(self) -> tuple[Diagnostic, ...]:
        return tuple(item for item in self if item.severity is DiagnosticSeverity.ADVISORY)

    @property
    def has_errors(self) -> bool:
        return any(item.is_error for item in self)

    def copy(self) -> "Diagnostics":
        return type(self)(self)

    def as_list(self) -> list[Diagnostic]:
        return list(self._entries)


DiagnosticBag = Diagnostics


class StoryCoreError(Exception):
    """Base exception for unrecoverable headless Story-Core failures."""


class StorySourceError(StoryCoreError):
    """An unreadable/malformed source document with a usable diagnostic."""

    def __init__(
        self,
        message: str,
        *,
        source: Path | str | None = None,
        path: Path | str | Sequence[str | int] | None = None,
        field_path: Sequence[str | int] = (),
        code: str = "source_error",
    ) -> None:
        super().__init__(message)
        # ``path`` historically described a diagnostic field path in this
        # module.  StorySource naturally needs it to mean the physical file,
        # so retain both forms without forcing source callers to use a second
        # exception type.
        if source is None and isinstance(path, (str, Path)):
            source = path
        elif not field_path and isinstance(path, Sequence) and not isinstance(path, (str, bytes, Path)):
            field_path = path
        self.diagnostic = Diagnostic(
            Path(source) if source is not None else None,
            tuple(field_path),
            code,
            DiagnosticSeverity.ERROR,
            message,
        )

    @property
    def source(self) -> Path | None:
        return self.diagnostic.source

    @property
    def path(self) -> Path | None:
        """Physical source file, when the failure came from a document."""

        return self.diagnostic.source

    @property
    def field_path(self) -> FieldPath:
        return self.diagnostic.path

    @property
    def code(self) -> str:
        return self.diagnostic.code


__all__ = [
    "Diagnostic",
    "DiagnosticBag",
    "Diagnostics",
    "DiagnosticSeverity",
    "FieldPath",
    "format_field_path",
    "StoryCoreError",
    "StorySourceError",
]
