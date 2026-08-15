"""Qt-independent asset discovery and filtering for Story Designer."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from engine.story_core import AssetRecord, StorySource


class AssetBrowserModel:
    """Small in-memory catalog backed by Story/Core's source rules."""

    def __init__(self, source: StorySource | None = None) -> None:
        self.source = source
        self.records: tuple[AssetRecord, ...] = ()
        self.refresh()

    def set_source(self, source: StorySource | None) -> None:
        self.source = source
        self.refresh()

    def refresh(self) -> tuple[AssetRecord, ...]:
        self.records = self.source.discover_assets() if self.source is not None else ()
        return self.records

    def record_for_reference(self, reference: str, asset_kind: str | None = None) -> AssetRecord | None:
        if self.source is None:
            return None
        return self.source.asset_record_for_reference(reference, asset_kind)

    def filtered(
        self,
        query: str = "",
        *,
        asset_kind: str | None = None,
        source_kind: str | None = None,
        expected_kind: str | None = None,
    ) -> tuple[AssetRecord, ...]:
        needle = str(query).strip().casefold()
        kind = str(asset_kind or "").strip().casefold()
        origin = str(source_kind or "").strip().casefold()
        values: list[AssetRecord] = []
        for record in self.records:
            searchable = " ".join((record.display_name, record.reference, record.asset_kind, record.source_kind)).casefold()
            if needle and needle not in searchable:
                continue
            if kind and kind != "all" and not _kind_matches(record, kind):
                continue
            if origin and origin != "all" and record.source_kind.casefold() != origin:
                continue
            values.append(record)
        if expected_kind:
            values.sort(key=lambda record: (0 if _kind_matches(record, expected_kind.casefold()) else 1, record.reference.casefold()))
        return tuple(values)

    def compatible(self, expected_kind: str | None) -> tuple[AssetRecord, ...]:
        return self.filtered(expected_kind=expected_kind)

    def canonical_reference_for_file(self, path: str | Path, asset_kind: str | None = None) -> str | None:
        if self.source is None:
            return None
        return self.source.authored_asset_reference(path, asset_kind)


def _kind_matches(record: AssetRecord, expected: str) -> bool:
    expected = expected.rstrip("s")
    actual = record.asset_kind.casefold().rstrip("s")
    if expected in {"", "all", "asset"}:
        return True
    if expected in {"image", "text-art", "textart"}:
        return record.is_image or actual in {"background", "sprite", "item"}
    if expected in {"audio", "sound"}:
        return actual in {"music", "sfx"}
    return actual == expected


__all__ = ["AssetBrowserModel", "AssetRecord"]
