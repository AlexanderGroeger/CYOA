"""Tree browser backed by ``StoryProject.index``."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from engine.story_core import ContentKind, StoryProject

from ..models import DefinitionSelection


class ProjectBrowser(QWidget):
    """Read-only project tree with typed selection identity."""

    selection_changed = Signal(object)

    _SELECTION_ROLE = Qt.ItemDataRole.UserRole

    _CATEGORIES: tuple[tuple[ContentKind, str], ...] = (
        (ContentKind.MANIFEST, "Manifest"),
        (ContentKind.PLAYER, "Player"),
        (ContentKind.SCENE, "Scenes"),
        (ContentKind.ITEM, "Items"),
        (ContentKind.BATTLE, "Battles"),
        (ContentKind.MOVE, "Combat Moves"),
        (ContentKind.EVENT_POOL, "Event Pools"),
        (ContentKind.ANIMATION, "Animations"),
        (ContentKind.AUDIO, "Audio Configuration"),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Project", "Source"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.tree)

        self._project: StoryProject | None = None

    @property
    def project(self) -> StoryProject | None:
        return self._project

    def clear_project(self) -> None:
        self._project = None
        self.tree.clear()

    def set_project(self, project: StoryProject | None) -> None:
        self._project = project
        self.tree.clear()
        if project is None:
            return

        root = QTreeWidgetItem([project.manifest.title or project.manifest.id, ""])
        root.setToolTip(0, str(project.story_root))
        self.tree.addTopLevelItem(root)
        assert project.index is not None

        for kind, label in self._CATEGORIES:
            entries = self._entries_for(project, kind)
            if not entries:
                continue
            category = QTreeWidgetItem([label, ""])
            category.setData(0, self._SELECTION_ROLE, None)
            root.addChild(category)
            for entry in entries:
                source = entry.source
                relative_source = self._relative_source(project.story_root, source)
                item = QTreeWidgetItem([entry.id, relative_source])
                item.setData(
                    0,
                    self._SELECTION_ROLE,
                    DefinitionSelection(kind, entry.id, source),
                )
                item.setToolTip(0, str(entry.reference))
                item.setToolTip(1, str(source) if source is not None else "")
                category.addChild(item)
            category.setExpanded(True)
        root.setExpanded(True)

    def select(self, selection: DefinitionSelection | None) -> bool:
        """Select a matching tree item, including duplicate-source entries."""

        if selection is None:
            self.tree.clearSelection()
            return True
        for item in self._items():
            if item.data(0, self._SELECTION_ROLE) == selection:
                blocker = QSignalBlocker(self.tree)
                self.tree.setCurrentItem(item)
                del blocker
                return True
        return False

    def _items(self) -> Iterable[QTreeWidgetItem]:
        def walk(item: QTreeWidgetItem) -> Iterable[QTreeWidgetItem]:
            yield item
            for index in range(item.childCount()):
                yield from walk(item.child(index))

        for index in range(self.tree.topLevelItemCount()):
            yield from walk(self.tree.topLevelItem(index))

    def _entries_for(self, project: StoryProject, kind: ContentKind) -> Iterable[object]:
        if kind is ContentKind.AUDIO:
            if "audio.yaml" in project.source_documents or project.audio_config:
                source = project.story_root / "audio.yaml"
                return (_BrowserEntry(kind, "audio", source),)
            return ()
        assert project.index is not None
        return project.index.entries(kind)

    @staticmethod
    def _relative_source(root: Path, source: Path | None) -> str:
        if source is None:
            return ""
        try:
            return source.relative_to(root).as_posix()
        except ValueError:
            return str(source)

    def _on_selection_changed(self) -> None:
        item = self.tree.currentItem()
        selection = item.data(0, self._SELECTION_ROLE) if item is not None else None
        self.selection_changed.emit(selection)


class _BrowserEntry:
    """Small index-entry-shaped object for the non-indexed audio document."""

    def __init__(self, kind: ContentKind, identifier: str, source: Path) -> None:
        self.kind = kind
        self.id = identifier
        self.source = source
        self.reference = f"{kind.value}:{identifier}"
