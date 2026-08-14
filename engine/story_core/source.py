"""Pure source discovery, YAML loading, cache ownership, and asset lookup.

``StorySource`` deliberately separates the mutable cache compatibility needed
by ``AssetLoader`` from the isolated copies consumed by ``StoryProject``.
It has no rendering, audio, pygame, or UI dependencies.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .diagnostics import StorySourceError


IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".gif"})


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
