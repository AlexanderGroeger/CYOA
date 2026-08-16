"""Tree browser backed by ``StoryProject.index``."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from engine.story_core import ContentKind, StoryProject

from ..models import DefinitionSelection


def definition_display_name(definition: object | None, kind: ContentKind | str, definition_id: str) -> str:
    """Return the authored navigation label for a definition."""

    content_kind = ContentKind.coerce(kind)
    fields = {
        ContentKind.MANIFEST: ("title", "name", "display_name"),
        ContentKind.PLAYER: ("name", "title", "display_name"),
        ContentKind.SCENE: ("name", "title", "display_name"),
        ContentKind.ITEM: ("name", "title", "display_name"),
        ContentKind.BATTLE: ("name", "title", "display_name"),
        ContentKind.MOVE: ("name", "title", "display_name"),
        ContentKind.EVENT_POOL: ("name", "title", "display_name"),
        ContentKind.ANIMATION: ("name", "title", "display_name"),
        ContentKind.AUDIO: ("name", "title", "display_name"),
    }[content_kind]

    for field_name in fields:
        value = getattr(definition, field_name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()

    authored = getattr(definition, "authored", None)
    if not isinstance(authored, Mapping):
        authored = getattr(definition, "raw", None)
    if isinstance(authored, Mapping):
        for field_name in fields:
            value = authored.get(field_name)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return str(definition_id)


class ProjectBrowser(QWidget):
    """Read-only project tree with typed selection identity."""

    selection_changed = Signal(object)

    _SELECTION_ROLE = Qt.ItemDataRole.UserRole
    _NODE_TYPE_ROLE = Qt.ItemDataRole.UserRole + 1
    _NODE_PATH_ROLE = Qt.ItemDataRole.UserRole + 2
    _SOURCE_ROLE = Qt.ItemDataRole.UserRole + 3
    _ID_ROLE = Qt.ItemDataRole.UserRole + 4

    _ROOT_NODE = "root"
    _CATEGORY_NODE = "category"
    _FOLDER_NODE = "folder"
    _DEFINITION_NODE = "definition"

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

    # These categories are discovered as individual source files. Registry
    # categories intentionally do not get synthetic folders from one file.
    _PATH_BACKED_ROOTS: dict[ContentKind, str] = {
        ContentKind.SCENE: "scenes",
        ContentKind.MOVE: "moves",
        ContentKind.ANIMATION: "assets/animations",
    }

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        allowed_kinds: Iterable[ContentKind | str] | None = None,
        title: str = "Project",
        search_placeholder: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.allowed_kinds = (
            {ContentKind.coerce(kind) for kind in allowed_kinds}
            if allowed_kinds is not None
            else None
        )
        self._title = str(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText(search_placeholder or f"Search {self._title.casefold()}...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search_changed)
        self.search_edit = self.search

        self.tree = QTreeWidget()
        self.tree.setColumnCount(1)
        self.tree.setHeaderLabels([self._title])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(self.search)
        layout.addWidget(self.tree)

        self._project: StoryProject | None = None

    def set_allowed_kinds(self, kinds: Iterable[ContentKind | str] | None) -> None:
        """Limit this browser to the resource kinds owned by one tool."""

        self.allowed_kinds = None if kinds is None else {ContentKind.coerce(kind) for kind in kinds}
        self.set_project(self._project)

    @property
    def project(self) -> StoryProject | None:
        return self._project

    def clear_project(self) -> None:
        self._project = None
        blocker = QSignalBlocker(self.tree)
        self.tree.clear()
        del blocker

    def set_project(self, project: StoryProject | None) -> None:
        expanded = self._expanded_nodes()
        had_tree = self._project is not None and self.tree.topLevelItemCount() > 0
        self._project = project
        blocker = QSignalBlocker(self.tree)
        self.tree.clear()
        if project is None:
            del blocker
            return

        root = QTreeWidgetItem([project.manifest.title or project.manifest.id])
        self._set_node_data(root, self._ROOT_NODE, ())
        root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        root.setToolTip(0, str(project.story_root))
        self.tree.addTopLevelItem(root)
        assert project.index is not None

        for kind, label in self._CATEGORIES:
            if self.allowed_kinds is not None and kind not in self.allowed_kinds:
                continue
            entries = tuple(self._entries_for(project, kind))
            if not entries:
                continue
            category = QTreeWidgetItem([label])
            self._set_node_data(category, self._CATEGORY_NODE, (kind.value,))
            self._set_bold(category, True)
            category.setFlags(category.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            root.addChild(category)
            self._populate_category(category, project, kind, entries)

        if had_tree:
            self._restore_expanded_nodes(expanded)
        else:
            root.setExpanded(True)
            self._expand_all_folders(root)
        self._apply_filter()
        del blocker

    def select(self, selection: DefinitionSelection | None) -> bool:
        """Select a matching tree item, including duplicate-source entries."""

        if selection is None:
            blocker = QSignalBlocker(self.tree)
            self.tree.clearSelection()
            del blocker
            return True
        for item in self._items():
            if item.data(0, self._SELECTION_ROLE) == selection:
                blocker = QSignalBlocker(self.tree)
                self.tree.setCurrentItem(item)
                del blocker
                return True
        return False

    def _populate_category(
        self,
        category: QTreeWidgetItem,
        project: StoryProject,
        kind: ContentKind,
        entries: tuple[object, ...],
    ) -> None:
        entries_by_folder: dict[tuple[str, ...], list[object]] = {}
        folders: set[tuple[str, ...]] = set()
        for entry in entries:
            folder = self._source_folder_parts(project, kind, getattr(entry, "source", None))
            entries_by_folder.setdefault(folder, []).append(entry)
            folders.update(tuple(folder[:index]) for index in range(1, len(folder) + 1))

        def add_children(parent: QTreeWidgetItem, folder: tuple[str, ...]) -> None:
            direct_folders = sorted(
                (candidate for candidate in folders if len(candidate) == len(folder) + 1 and candidate[:-1] == folder),
                key=lambda candidate: candidate[-1].casefold(),
            )
            for child_folder in direct_folders:
                folder_item = QTreeWidgetItem([child_folder[-1]])
                self._set_node_data(folder_item, self._FOLDER_NODE, (kind.value, child_folder))
                folder_item.setFlags(folder_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self._set_bold(folder_item, False)
                parent.addChild(folder_item)
                add_children(folder_item, child_folder)

            definitions = sorted(
                entries_by_folder.get(folder, ()),
                key=lambda entry: (
                    definition_display_name(getattr(entry, "definition", None), kind, entry.id).casefold(),
                    entry.id.casefold(),
                    str(getattr(entry, "source", "")).casefold(),
                ),
            )
            for entry in definitions:
                self._add_definition(parent, project, kind, entry)

        add_children(category, ())

    def _add_definition(self, parent: QTreeWidgetItem, project: StoryProject, kind: ContentKind, entry: object) -> None:
        display_name = definition_display_name(getattr(entry, "definition", None), kind, entry.id)
        source = getattr(entry, "source", None)
        item = QTreeWidgetItem([display_name])
        selection = DefinitionSelection(kind, entry.id, source)
        self._set_node_data(item, self._DEFINITION_NODE, (), selection=selection, source=source, identifier=entry.id)
        relative_source = self._relative_source(project.story_root, source)
        tooltip = f"{display_name}\nID: {entry.id}"
        if relative_source:
            tooltip += f"\nSource: {relative_source}"
        item.setToolTip(0, tooltip)
        parent.addChild(item)

    def _set_node_data(
        self,
        item: QTreeWidgetItem,
        node_type: str,
        path: tuple[object, ...],
        *,
        selection: DefinitionSelection | None = None,
        source: Path | None = None,
        identifier: str | None = None,
    ) -> None:
        item.setData(0, self._NODE_TYPE_ROLE, node_type)
        item.setData(0, self._NODE_PATH_ROLE, path)
        item.setData(0, self._SELECTION_ROLE, selection)
        if source is not None:
            item.setData(0, self._SOURCE_ROLE, str(source))
        if identifier is not None:
            item.setData(0, self._ID_ROLE, identifier)

    @staticmethod
    def _set_bold(item: QTreeWidgetItem, bold: bool) -> None:
        font: QFont = item.font(0)
        font.setBold(bold)
        item.setFont(0, font)

    @staticmethod
    def _expand_all_folders(root: QTreeWidgetItem) -> None:
        for index in range(root.childCount()):
            child = root.child(index)
            child.setExpanded(True)
            ProjectBrowser._expand_all_folders(child)

    def _expanded_nodes(self) -> set[tuple[object, ...]]:
        return {
            tuple(item.data(0, self._NODE_PATH_ROLE))
            for item in self._items()
            if item.isExpanded() and item.data(0, self._NODE_TYPE_ROLE) != self._DEFINITION_NODE
        }

    def _restore_expanded_nodes(self, expanded: set[tuple[object, ...]]) -> None:
        for item in self._items():
            if item.data(0, self._NODE_TYPE_ROLE) != self._DEFINITION_NODE:
                item.setExpanded(tuple(item.data(0, self._NODE_PATH_ROLE)) in expanded)

    def _apply_filter(self) -> None:
        query = self.search.text().strip().casefold()
        blocker = QSignalBlocker(self.tree)

        def visit(item: QTreeWidgetItem, force_visible: bool = False) -> bool:
            if not query:
                item.setHidden(False)
                for index in range(item.childCount()):
                    visit(item.child(index))
                return True
            own_match = query in item.text(0).casefold()
            if force_visible or own_match:
                item.setHidden(False)
                for index in range(item.childCount()):
                    visit(item.child(index), True)
                return True
            descendant_match = False
            for index in range(item.childCount()):
                descendant_match = visit(item.child(index)) or descendant_match
            item.setHidden(not descendant_match)
            return own_match or descendant_match

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))
        del blocker

    def _on_search_changed(self, _text: str) -> None:
        self._apply_filter()

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
                return (_BrowserEntry(kind, "audio", source, None),)
            return ()
        assert project.index is not None
        return project.index.entries(kind)

    def _source_folder_parts(self, project: StoryProject, kind: ContentKind, source: Path | None) -> tuple[str, ...]:
        root_name = self._PATH_BACKED_ROOTS.get(kind)
        if root_name is None or source is None:
            return ()
        roots = [project.story_root / root_name]
        shared_root = getattr(project.source, "shared_assets_root", None)
        if kind is ContentKind.ANIMATION and shared_root is not None:
            roots.append(Path(shared_root) / "animations")
        for root in roots:
            try:
                relative = Path(source).relative_to(root)
            except ValueError:
                continue
            return tuple(part for part in relative.parent.parts if part not in ("", "."))
        return ()

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
        if item is None:
            self.selection_changed.emit(None)
            return
        selection = item.data(0, self._SELECTION_ROLE)
        if isinstance(selection, DefinitionSelection):
            self.selection_changed.emit(selection)


class _BrowserEntry:
    """Small index-entry-shaped object for the non-indexed audio document."""

    def __init__(self, kind: ContentKind, identifier: str, source: Path, definition: object | None) -> None:
        self.kind = kind
        self.id = identifier
        self.source = source
        self.definition = definition
        self.reference = f"{kind.value}:{identifier}"
