"""Pure source discovery, YAML loading, cache ownership, and asset lookup.

``StorySource`` deliberately separates the mutable cache compatibility needed
by ``AssetLoader`` from the isolated copies consumed by ``StoryProject``.
It has no rendering, audio, pygame, or UI dependencies.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

import yaml

from .diagnostics import StorySourceError


@dataclass(frozen=True)
class AssetCategorySpec:
    """Core-owned metadata for one Designer/runtime asset category."""

    key: str
    label: str
    extensions: frozenset[str] = frozenset()
    semantic: bool = False


IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".gif"})
AUDIO_EXTENSIONS = frozenset({".wav", ".ogg", ".mp3"})
FONT_EXTENSIONS = frozenset({".ttf", ".otf"})
TEXT_ART_EXTENSIONS = frozenset({".txt"})

# Shared metadata for Core discovery and Designer filtering.
ASSET_CATEGORY_SPECS = MappingProxyType({
    "backgrounds": AssetCategorySpec("backgrounds", "Backgrounds", IMAGE_EXTENSIONS | TEXT_ART_EXTENSIONS),
    "sprites": AssetCategorySpec("sprites", "Sprites", IMAGE_EXTENSIONS | TEXT_ART_EXTENSIONS),
    "items": AssetCategorySpec("items", "Item Images", IMAGE_EXTENSIONS),
    "music": AssetCategorySpec("music", "Music", AUDIO_EXTENSIONS),
    "sfx": AssetCategorySpec("sfx", "Sound Effects", AUDIO_EXTENSIONS),
    "fonts": AssetCategorySpec("fonts", "Fonts", FONT_EXTENSIONS),
    "animation": AssetCategorySpec("animation", "Animations", frozenset({".yaml"}), semantic=True),
})
SUPPORTED_ASSET_EXTENSIONS = MappingProxyType({
    key: spec.extensions for key, spec in ASSET_CATEGORY_SPECS.items()
})
ASSET_CATEGORY_LABELS = MappingProxyType({
    key: spec.label for key, spec in ASSET_CATEGORY_SPECS.items()
})
_ASSET_CATEGORY_ALIASES = {
    "animation": "animation",
    "animations": "animation",
    "font": "fonts",
    "fonts": "fonts",
    "item": "items",
    "items": "items",
    "background": "backgrounds",
    "backgrounds": "backgrounds",
    "sprite": "sprites",
    "sprites": "sprites",
    "sound": "sfx",
    "sfx": "sfx",
    "music": "music",
}


@dataclass(frozen=True)
class AssetRecord:
    """A discoverable authored asset and its effective source.

    ``reference`` is the value that can safely be written to story YAML.
    ``resolved_path`` is deliberately separate: it is an inspection aid and
    is never used as the authored value by the Designer.
    """

    reference: str
    resolved_path: Path | None
    source_kind: str
    asset_kind: str
    display_name: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def exists(self) -> bool:
        return self.resolved_path is not None and self.resolved_path.exists()

    @property
    def is_image(self) -> bool:
        return self.resolved_path is not None and self.resolved_path.suffix.lower() in IMAGE_EXTENSIONS

    @property
    def is_audio(self) -> bool:
        return self.asset_kind in {"music", "sfx"} and self.resolved_path is not None


@dataclass(frozen=True)
class SourceDocument:
    """A parsed YAML document together with its physical provenance."""

    path: Path
    relative_path: str
    data: Any
    root: str = "story"

    def copy_data(self) -> Any:
        return deepcopy(self.data)


class StorySource:
    """Story and shared-asset source layer.

    ``load_yaml`` returns a defensive copy by default.  ``load_yaml_legacy``
    exposes the cached object for the old ``AssetLoader`` API, whose mutable
    identity behavior is intentionally retained during the transition.
    """

    def __init__(
        self,
        story_root: str | Path,
        shared_assets_root: str | Path = "shared_assets",
        *,
        cache: dict[str, Any] | None = None,
    ) -> None:
        self.story_root = Path(story_root)
        self.shared_assets_root = Path(shared_assets_root)
        self._cache: dict[str, Any] = cache if cache is not None else {}

    # Compatibility aliases make the layer easy to introduce beside the old
    # loader without forcing callers to rename their values immediately.
    @property
    def story_dir(self) -> Path:
        return self.story_root

    @property
    def shared_dir(self) -> Path:
        return self.shared_assets_root

    @property
    def cache(self) -> dict[str, Any]:
        return self._cache

    def story_path(self, relative_path: str | Path) -> Path:
        return self.story_root / Path(relative_path)

    def has_story_file(self, relative_path: str | Path) -> bool:
        return self.story_path(relative_path).is_file()

    def load_yaml(self, relative_path: str | Path, *, isolated: bool = True) -> Any:
        """Load a story-relative YAML file, usually as an isolated snapshot."""

        data = self._load_yaml_path(self.story_path(relative_path), raw_errors=False)
        return deepcopy(data) if isolated else data

    def load_yaml_legacy(self, relative_path: str | Path) -> Any:
        """Return the cached mutable YAML value used by legacy callers."""

        path = self.story_path(relative_path)
        return self._load_yaml_path(path, raw_errors=True)

    def load_yaml_path_legacy(self, path: str | Path) -> Any:
        """Return a cached mutable YAML value from an explicit source path.

        This is the shared-animation counterpart to :meth:`load_yaml_legacy`.
        It exists for ``AssetLoader`` compatibility only; Story/Core project
        consumers should use the isolated public loading APIs instead.
        """

        return self._load_yaml_path(Path(path), raw_errors=True)

    def load_yaml_path(self, path: str | Path, *, isolated: bool = True) -> Any:
        """Load YAML from either story or shared roots with wrapped errors."""

        data = self._load_yaml_path(Path(path), raw_errors=False)
        return deepcopy(data) if isolated else data

    def load_document(self, relative_path: str | Path) -> SourceDocument:
        path = self.story_path(relative_path)
        data = self.load_yaml(relative_path, isolated=True)
        return SourceDocument(path, self.relative_path(path), data, "story")

    def load_document_path(self, path: str | Path) -> SourceDocument:
        target = Path(path)
        data = self.load_yaml_path(target, isolated=True)
        root = "story" if _is_under(target, self.story_root) else "shared"
        return SourceDocument(target, self.relative_path(target), data, root)

    def try_load_document(self, relative_path: str | Path) -> tuple[SourceDocument | None, StorySourceError | None]:
        """Return a source error as data so project discovery can continue."""

        try:
            return self.load_document(relative_path), None
        except StorySourceError as error:
            return None, error
        except yaml.YAMLError as error:
            path = self.story_path(relative_path)
            return None, StorySourceError(
                f"Malformed YAML in {path}: {error}", path=path, code="invalid_yaml"
            )

    def discover_yaml(self, relative_dir: str | Path, *, recursive: bool = True) -> tuple[Path, ...]:
        """Discover sorted YAML files under a story-local directory."""

        directory = self.story_path(relative_dir)
        if not directory.is_dir():
            return ()
        iterator: Iterable[Path] = directory.rglob("*.yaml") if recursive else directory.glob("*.yaml")
        return tuple(sorted(iterator))

    def resolve_asset_path(self, category: str, filename: str) -> Path:
        """Resolve conventional category-relative assets, story first."""

        story_path = self.story_root / "assets" / category / filename
        if story_path.exists():
            return story_path
        shared_path = self.shared_assets_root / category / filename
        if shared_path.exists():
            return shared_path
        raise StorySourceError(
            f"Asset '{filename}' not found in either '{story_path}' or '{shared_path}'",
            code="asset_not_found",
        )

    def resolve_asset_reference(self, filename: str, default_category: str) -> Path:
        """Resolve a conventional or explicitly story/shared-relative path.

        This mirrors the runtime loader's current story-local/shared-first
        lookup.  It intentionally does *not* change AudioSystem's more
        restrictive category-only playback lookup; validators can surface that
        mismatch separately as a compatibility advisory.
        """

        if not isinstance(filename, str) or not filename.strip():
            raise StorySourceError(
                f"Asset reference must be a non-empty string, got {filename!r}",
                code="invalid_asset_reference",
            )
        candidate = Path(filename.replace("\\", "/"))
        if candidate.is_absolute():
            raise StorySourceError(
                f"Absolute asset paths are not supported: {filename!r}",
                code="absolute_asset_path",
            )
        normalized = Path(str(candidate).lstrip("./"))
        for root in (self.story_root, self.shared_assets_root):
            direct = root / normalized
            if direct.exists():
                return direct
        return self.resolve_asset_path(default_category, str(normalized))

    def authored_asset_reference(self, path: str | Path, category: str | None = None) -> str | None:
        """Return the portable authored reference for a known asset path.

        Regular assets are category-relative because that is what
        ``resolve_asset_path`` and the runtime loaders consume.  Animation
        definitions use their ID (the directory below ``animations``).
        ``None`` means the path is outside all supported asset roots.
        """

        target = Path(path).resolve()
        category_name = _normalise_asset_category(category)
        for root in (self.story_root / "assets", self.shared_assets_root):
            try:
                relative = target.relative_to(root.resolve())
            except ValueError:
                continue
            parts = relative.parts
            if len(parts) < 2:
                continue
            root_category = parts[0]
            if root_category == "animations":
                if target.name.lower() == "anim.yaml" and len(parts) >= 3:
                    return Path(*parts[1:-1]).as_posix()
                continue
            if category_name is not None and _normalise_asset_category(root_category) != category_name:
                continue
            return Path(*parts[1:]).as_posix()
        return None

    # Friendly aliases for consumers that describe this operation as
    # canonicalisation/reference generation.
    canonical_asset_reference = authored_asset_reference
    asset_reference_for_path = authored_asset_reference

    def discover_assets(self) -> tuple[AssetRecord, ...]:
        """Discover effective story/shared assets without loading media."""

        records: list[AssetRecord] = []
        seen: set[tuple[str, str, str]] = set()
        for root, source_kind in ((self.story_root / "assets", "Story"), (self.shared_assets_root, "Shared")):
            if not root.is_dir():
                continue
            for category_dir in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.name.casefold()):
                category = _normalise_asset_category(category_dir.name)
                if category not in ASSET_CATEGORY_SPECS:
                    continue
                if category == "animation":
                    paths = sorted(
                        (item for item in category_dir.rglob("anim.yaml") if item.is_file()),
                        key=lambda item: item.as_posix().casefold(),
                    )
                    for path in paths:
                        reference = self.authored_asset_reference(path, "animation")
                        if not reference:
                            continue
                        frames = _animation_frame_metadata(path)
                        if frames is None:
                            continue
                        key = (source_kind.casefold(), "animation", reference.casefold())
                        if key in seen:
                            continue
                        seen.add(key)
                        records.append(AssetRecord(
                            reference=reference,
                            resolved_path=path,
                            source_kind=source_kind,
                            asset_kind="animation",
                            display_name=reference,
                            metadata=frames,
                        ))
                    continue
                for path in sorted((item for item in category_dir.rglob("*") if item.is_file()), key=lambda item: item.as_posix().casefold()):
                    if not _is_supported_asset_file(path, category):
                        continue
                    reference = self.authored_asset_reference(path, category)
                    if not reference:
                        continue
                    kind = category
                    key = (source_kind.casefold(), kind, reference.casefold())
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(AssetRecord(
                        reference=reference,
                        resolved_path=path,
                        source_kind=source_kind,
                        asset_kind=kind,
                        display_name=path.name,
                    ))
        return tuple(sorted(records, key=lambda item: (item.asset_kind.casefold(), item.reference.casefold(), item.source_kind.casefold())))

    discover_asset_records = discover_assets

    def asset_record_for_reference(self, reference: str, category: str | None = None) -> AssetRecord:
        """Represent an authored reference, including unresolved references."""

        expected = _normalise_asset_category(category) or "asset"
        for record in self.discover_assets():
            if record.reference == reference and (category is None or _asset_kinds_compatible(record.asset_kind, expected)):
                return record
        resolved: Path | None = None
        try:
            if expected == "animation":
                resolved = self.animation_directory(reference)
                if not (resolved / "anim.yaml").is_file():
                    resolved = None
            elif category:
                resolved = self.resolve_asset_reference(reference, category)
        except StorySourceError:
            resolved = None
        return AssetRecord(
            reference=str(reference),
            resolved_path=resolved,
            source_kind="Story" if resolved is not None and _is_under(resolved, self.story_root) else "Shared" if resolved is not None else "Missing",
            asset_kind=expected,
            display_name=Path(str(reference)).name or str(reference),
        )

    def load_animation(self, animation_name: str, *, isolated: bool = True) -> Any:
        """Load an animation YAML file using the runtime's fallback order."""

        story_path = self.story_root / "assets" / "animations" / animation_name / "anim.yaml"
        if story_path.exists():
            return self.load_yaml(story_path.relative_to(self.story_root), isolated=isolated)
        shared_path = self.shared_assets_root / "animations" / animation_name / "anim.yaml"
        if shared_path.exists():
            return self.load_yaml_path(shared_path, isolated=isolated)
        raise StorySourceError(
            f"Animation '{animation_name}' not found in either '{story_path}' or '{shared_path}'",
            code="animation_not_found",
        )

    def animation_directory(self, animation_name: str) -> Path:
        story_path = self.story_root / "assets" / "animations" / animation_name
        if (story_path / "anim.yaml").exists():
            return story_path
        return self.shared_assets_root / "animations" / animation_name

    def is_image_asset(self, filename: str) -> bool:
        return Path(filename).suffix.lower() in IMAGE_EXTENSIONS

    def relative_path(self, path: str | Path) -> str:
        target = Path(path)
        for root in (self.story_root, self.shared_assets_root):
            try:
                return target.relative_to(root).as_posix()
            except ValueError:
                pass
        return str(target)

    def _load_yaml_path(self, path: Path, *, raw_errors: bool) -> Any:
        cache_key = str(path)
        if cache_key in self._cache:
            return self._cache[cache_key]
        if not path.exists():
            error = StorySourceError(f"Story file not found: {path}", path=path, code="source_not_found")
            if raw_errors:
                raise FileNotFoundError(str(error))
            raise error
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except yaml.YAMLError:
            if raw_errors:
                raise
            raise StorySourceError(f"Malformed YAML in {path}", path=path, code="invalid_yaml") from None
        except OSError as exc:
            if raw_errors:
                raise
            raise StorySourceError(f"Could not read story file {path}: {exc}", path=path, code="source_unreadable") from exc
        self._cache[cache_key] = data
        return data


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _normalise_asset_category(category: str | None) -> str | None:
    if category is None:
        return None
    value = str(category).strip().lower()
    return _ASSET_CATEGORY_ALIASES.get(value, value)


def canonical_asset_category(category: str | None) -> str | None:
    """Normalize category aliases without coupling callers to folder names."""

    return _normalise_asset_category(category)


def asset_category_label(category: str | None) -> str:
    """Return the human-facing label for a canonical category key."""

    canonical = _normalise_asset_category(category)
    return ASSET_CATEGORY_LABELS.get(canonical or "", str(category or "Assets").title())


def _is_supported_asset_file(path: Path, category: str) -> bool:
    name = path.name
    if name.startswith(".") or name.endswith("~"):
        return False
    if name.casefold().endswith((".bak", ".tmp", ".swp")):
        return False
    spec = ASSET_CATEGORY_SPECS.get(category)
    return spec is not None and path.suffix.casefold() in spec.extensions


def is_supported_asset_file(path: str | Path, category: str | None) -> bool:
    """Return whether a physical file is consumable in ``category``."""

    canonical = _normalise_asset_category(category)
    return canonical is not None and _is_supported_asset_file(Path(path), canonical)


def _asset_kinds_compatible(actual: str, expected: str) -> bool:
    if expected in {"asset", "", "all"}:
        return True
    if expected == "image":
        return actual not in {"music", "sfx", "fonts", "font", "animation"}
    if expected in {"audio", "sound"}:
        return actual in {"music", "sfx"}
    return actual == expected or actual.rstrip("s") == expected.rstrip("s")


def _animation_frame_metadata(path: Path) -> Mapping[str, Any] | None:
    """Read only lightweight animation metadata for browser details."""

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, UnicodeError, yaml.YAMLError):
        return None
    if not isinstance(data, Mapping) or not isinstance(data.get("frames"), list):
        return None
    return {"frame_count": len(data["frames"])}
