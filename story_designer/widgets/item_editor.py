"""Focused authoring widgets for the Story Designer Items tool.

The widgets in this module deliberately sit on top of the existing Core
normalization and Designer property-command layers.  They present the item
definition in a player-facing way without changing inventory runtime rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from engine.core.inventory import InventoryService, _INFERRED_EQUIPMENT_TYPES
from engine.story_core import ContentKind, Diagnostics, StoryProject
from engine.story_core.schema import MISSING

from ..models import DefinitionSelection, ProjectSession
from ..models.editing import EditValidationError, SetPropertyCommand
from .project_browser import ProjectBrowser
from .property_editors import AssetPathEditor, PropertyEditorFactory


class ItemNavigator(ProjectBrowser):
    """Project browser narrowed to Item definitions, with an empty-state action."""

    new_item_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            allowed_kinds={ContentKind.ITEM},
            title="Items",
            search_placeholder="Search items...",
        )
        self.empty_state = QLabel("No items yet.")
        self.empty_state.setStyleSheet("color: palette(mid);")
        self.empty_state.hide()
        self.new_item_button = QPushButton("New Item")
        self.new_item_button.clicked.connect(self.new_item_requested)
        self.layout().addWidget(self.empty_state)
        self.layout().addWidget(self.new_item_button)

    def clear_project(self) -> None:
        super().clear_project()
        self.empty_state.hide()
        self.new_item_button.setEnabled(False)

    def set_project(self, project: StoryProject | None) -> None:
        super().set_project(project)
        has_items = bool(project is not None and project.items)
        self.empty_state.setVisible(project is not None and not has_items)
        self.new_item_button.setEnabled(project is not None)


class SquarePreviewWidget(QWidget):
    """Layout-owned square viewport with a contained, crisp image label."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_pixmap = QPixmap()
        self.sprite_label = QLabel(self)
        self.sprite_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(160, 160)
        self.setMaximumSize(320, 320)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setStyleSheet("border: 1px solid palette(mid);")

    def setPixmap(self, pixmap: QPixmap) -> None:  # noqa: N802 - Qt API name
        """Keep the source image so every label resize is scaled crisply."""

        self._source_pixmap = QPixmap(pixmap)
        self._refresh_pixmap()

    def pixmap(self) -> QPixmap:  # noqa: N802 - Qt API name
        """Expose the displayed pixmap for callers and geometry tests."""

        return self.sprite_label.pixmap() or QPixmap()

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API name
        self.sprite_label.setText(text)

    def text(self) -> str:
        return self.sprite_label.text()

    def setAlignment(self, alignment: Qt.AlignmentFlag) -> None:  # noqa: N802 - Qt API name
        self.sprite_label.setAlignment(alignment)

    def alignment(self) -> Qt.AlignmentFlag:
        return self.sprite_label.alignment()

    def _refresh_pixmap(self) -> None:
        if self._source_pixmap.isNull():
            self.sprite_label.setPixmap(QPixmap())
            return
        target = self.sprite_label.contentsRect().size()
        if not target.isValid() or target.width() <= 0 or target.height() <= 0:
            return
        self.sprite_label.setPixmap(
            self._source_pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API name
        super().resizeEvent(event)
        # The viewport owns this child; the label can never paint outside the
        # rectangle that the viewport itself owns.
        self.sprite_label.setGeometry(self.contentsRect())
        self._refresh_pixmap()

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API name
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API name
        return max(self.minimumHeight(), min(self.maximumHeight(), width))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API name
        return QSize(320, 320)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API name
        return QSize(160, 160)


class ItemPreviewWidget(QWidget):
    """Player-facing preview backed by the current session working mapping."""

    def __init__(self, session: ProjectSession | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(320)
        self.session = session
        self.project: StoryProject | None = None
        self.selection: DefinitionSelection | None = None
        self._mapping: Mapping[str, Any] = {}

        self.title = QLabel("Item Preview")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet("font-weight: bold;")
        self.sprite_viewport = SquarePreviewWidget()
        self.sprite_viewport.setObjectName("itemSpriteViewport")
        self.sprite = self.sprite_viewport
        self.sprite_label = self.sprite_viewport.sprite_label
        # Compatibility alias for existing integrations; this is now the
        # actual square viewport rather than a second geometry owner.
        self.sprite_frame = self.sprite_viewport
        self.sprite.setText("No item selected")
        self.name_value = QLabel("—")
        self.name_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.type_value = QLabel("—")
        self.type_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.description_value = QLabel()
        self.description_value.setWordWrap(True)
        self.description_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stats_value = QLabel()
        self.stats_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.capabilities_value = QLabel()
        self.capabilities_value.setWordWrap(True)
        self.capabilities_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.moves_value = QLabel()
        self.moves_value.setWordWrap(True)
        self.moves_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.runtime_note = QLabel()
        self.runtime_note.setWordWrap(True)
        self.runtime_note.setStyleSheet("color: palette(mid);")

        base_font = QApplication.font()
        self._set_font(self.title, base_font, minimum=11.0, point_delta=1.0, bold=True)
        self._set_font(self.name_value, base_font, minimum=24.0, point_delta=10.0, bold=True)
        self._set_font(self.type_value, base_font, minimum=17.0, point_delta=5.0, italic=True)
        for label in (self.description_value, self.stats_value, self.capabilities_value, self.moves_value):
            self._set_font(label, base_font, minimum=15.0, point_delta=3.0)
        self._set_font(self.runtime_note, base_font, minimum=12.0, point_delta=1.0)

        self.preview_card = QWidget()
        self.preview_card.setObjectName("itemPreviewCard")
        self.preview_card.setMinimumWidth(220)
        self.preview_card.setMaximumWidth(600)
        self.preview_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.card_layout = QVBoxLayout(self.preview_card)
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setSpacing(16)
        self.card_layout.addWidget(self.title)
        self.card_layout.addWidget(self.sprite_viewport, 0, Qt.AlignmentFlag.AlignHCenter)

        self.text_container = QWidget()
        self.text_container.setObjectName("itemPreviewTextContainer")
        self.text_stack = self.text_container
        self.text_layout = QVBoxLayout(self.text_container)
        self.text_layout.setContentsMargins(0, 0, 0, 0)
        self.text_layout.setSpacing(8)
        for label in (
            self.name_value,
            self.type_value,
            self.description_value,
            self.stats_value,
            self.capabilities_value,
            self.moves_value,
            self.runtime_note,
        ):
            self.text_layout.addWidget(label)
        self.card_layout.addWidget(self.text_container)

        self.preview_scroll = QScrollArea(self)
        self.preview_scroll.setObjectName("itemPreviewScroll")
        self.preview_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.preview_scroll.setWidget(self.preview_card)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(0)
        layout.addWidget(self.preview_scroll)
        self._sync_preview_minimum_height()

    @staticmethod
    def _set_font(
        widget: QLabel,
        base: QFont,
        *,
        minimum: float,
        point_delta: float = 0,
        bold: bool = False,
        italic: bool = False,
    ) -> None:
        font = QFont(base)
        base_size = base.pointSizeF()
        if base_size <= 0:
            base_size = 10.0
        point_size = max(minimum, base_size + point_delta)
        font.setPointSizeF(point_size)
        font.setBold(bold)
        font.setItalic(italic)
        widget.setFont(font)
        # A global QLabel stylesheet can replace a directly assigned QFont.
        # Keep QFont as the source of truth and add a widget-local declaration
        # so the requested hierarchy wins the stylesheet cascade as well.
        existing_style = widget.styleSheet().strip()
        weight = "bold" if bold else "normal"
        style = "italic" if italic else "normal"
        widget.setStyleSheet(
            f"{existing_style}\nQLabel {{ font-size: {point_size:g}pt; font-weight: {weight}; font-style: {style}; }}"
        )
        widget.ensurePolished()
        widget.setFont(font)

    def _sync_preview_minimum_height(self) -> None:
        """Keep the scroll content at least as tall as its live layout hint."""

        self.text_layout.activate()
        self.card_layout.activate()
        required_height = self.card_layout.sizeHint().height()
        if self.preview_card.minimumHeight() != required_height:
            self.preview_card.setMinimumHeight(required_height)
            self.preview_card.updateGeometry()

    def clear(self) -> None:
        self.project = None
        self.selection = None
        self._mapping = {}
        self.title.setText("Item Preview")
        self.sprite.setPixmap(QPixmap())
        self.sprite.setText("Select an item to preview it.")
        for widget in (self.name_value, self.type_value, self.description_value, self.stats_value, self.capabilities_value, self.moves_value, self.runtime_note):
            widget.clear()
            widget.hide()
        self._sync_preview_minimum_height()

    def set_state(
        self,
        project: StoryProject | None,
        selection: DefinitionSelection | None,
        mapping: Mapping[str, Any] | None,
    ) -> None:
        self.project = project
        self.selection = selection
        self.update_from_mapping(mapping or {}) if selection is not None else self.clear()

    def update_from_mapping(self, mapping: Mapping[str, Any]) -> None:
        """Update only preview labels/image; never writes inferred values."""

        self._mapping = dict(mapping)
        if self.selection is None:
            self.clear()
            return
        raw = self._mapping
        item_id = self.selection.id
        name = raw.get("name") if isinstance(raw.get("name"), str) and raw.get("name") else item_id
        item_type = raw.get("type") if isinstance(raw.get("type"), str) and raw.get("type") else "item"
        self.title.setText(f"Item Preview · {item_id}")
        self.name_value.setText(str(name))
        self.name_value.show()
        self.type_value.setText(str(item_type).replace("_", " ").title())
        self.type_value.show()
        description = str(raw.get("description", "") or "")
        self.description_value.setText(description)
        self.description_value.setVisible(bool(description))

        normalized = InventoryService({item_id: raw}).definition(item_id)
        stat_labels = (("hp", "HP"), ("attack", "ATK"), ("defense", "DEF"))
        stat_lines = [
            f"{label} {int(normalized.stats.get(key, 0)):+d}"
            for key, label in stat_labels
            if int(normalized.stats.get(key, 0)) != 0
        ]
        self.stats_value.setText("    ".join(stat_lines))
        self.stats_value.setVisible(bool(stat_lines))

        capabilities: list[str] = []
        for action, label in (("use", "Usable"), ("equip", "Equippable"), ("toss", "Tossable")):
            if action in normalized.actions:
                if action == "equip" and self._equippable_is_inferred(raw, normalized.item_type):
                    continue
                capabilities.append(label)
        if normalized.use_actions:
            capabilities.append("Consumable")
        combat = raw.get("combat")
        if isinstance(combat, Mapping) and combat.get("effects"):
            capabilities.append("Combat Effect")
        self.capabilities_value.setText("  ·  ".join(capabilities))
        self.capabilities_value.setVisible(bool(capabilities))

        move_ids = self._move_grants(raw)
        move_names = [self._move_display_name(move_id) for move_id in move_ids]
        if move_names:
            visible_moves = move_names[:3]
            remaining = len(move_names) - len(visible_moves)
            if remaining:
                visible_moves.append(f"+{remaining}")
            self.moves_value.setText(f"Moves: {' · '.join(visible_moves)}")
        else:
            self.moves_value.clear()
        self.moves_value.setVisible(bool(move_names))

        effective: list[str] = []
        if normalized.equipment_slot:
            effective.append(f"Effective slot: {normalized.equipment_slot}")
        if normalized.legacy:
            effective.append("Effective values include legacy/default inference; authored YAML is unchanged.")
        self.runtime_note.setText("  ".join(effective))
        self.runtime_note.setVisible(bool(effective))
        self._set_sprite(normalized.icon)
        self._sync_preview_minimum_height()

    @staticmethod
    def _equippable_is_inferred(raw: Mapping[str, Any], item_type: str) -> bool:
        """Match InventoryService's type-to-slot inference, not UI guesses."""

        equipment = raw.get("equipment")
        has_explicit_slot = "equipment_slot" in raw or (isinstance(equipment, Mapping) and "slot" in equipment)
        return item_type in _INFERRED_EQUIPMENT_TYPES and not has_explicit_slot and "actions" not in raw

    @staticmethod
    def _move_grants(raw: Mapping[str, Any]) -> list[str]:
        combat = raw.get("combat")
        grants = combat.get("move_grants") if isinstance(combat, Mapping) else ()
        if not isinstance(grants, (list, tuple)):
            return []
        return [str(grant) for grant in grants if isinstance(grant, (str, int)) and str(grant)]

    def _move_display_name(self, move_id: str) -> str:
        if self.project is not None:
            move = self.project.moves.get(move_id)
            display_name = getattr(move, "name", "") if move is not None else ""
            if isinstance(display_name, str) and display_name:
                return display_name
        return move_id

    def _set_sprite(self, reference: str | None) -> None:
        self.sprite.setPixmap(QPixmap())
        if not reference or self.project is None:
            self.sprite.setText("No sprite assigned")
            return
        try:
            path = self.project.source.resolve_asset_reference(reference, "items")
        except Exception:
            self.sprite.setText(f"Missing item image\n{reference}")
            return
        image = QImage(str(path))
        if image.isNull():
            self.sprite.setText(f"Missing item image\n{reference}")
            return
        pixmap = QPixmap.fromImage(image)
        self.sprite.setPixmap(pixmap)
        self.sprite.setText("")

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API name
        super().resizeEvent(event)
        # The scroll area owns the card geometry.  Recompute the card's live
        # minimum after width-dependent wrapping changes; no child is moved or
        # resized manually here.
        self._sync_preview_minimum_height()


class ItemPropertiesWidget(QWidget):
    """Schema-backed Item context editor with explicit legacy/runtime sections."""

    state_changed = Signal()
    open_move_requested = Signal(str)

    def __init__(self, session: ProjectSession | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.project: StoryProject | None = None
        self.selection: DefinitionSelection | None = None
        self._definition: Any | None = None
        self._diagnostics = Diagnostics()
        self.factory = PropertyEditorFactory()
        self._editors: dict[tuple[str | int, ...], QWidget] = {}
        self._schema_signature: tuple[tuple[Any, ...], ...] | None = None
        self._action_checks: dict[str, QCheckBox] = {}
        self._stat_spins: dict[str, QSpinBox] = {}
        self._equipment_bonus_spins: dict[str, QSpinBox] = {}
        self.move_grants = QListWidget()
        self.move_grants_combo = QComboBox()
        self.move_grants_add = QPushButton("Add Move Grant")
        self.move_grants_remove = QPushButton("Remove Selected")
        self.move_grants_add.clicked.connect(self._add_move_grant)
        self.move_grants_remove.clicked.connect(self._remove_move_grant)

        self.header = QLabel("Item Properties")
        self.header.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.id_value = QLabel("—")
        self.source_value = QLabel("—")
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #b45309;")
        self.validation = QLabel(self)
        self.validation.setWordWrap(True)
        self.validation.setStyleSheet("color: #b00020;")
        self.revert_button = QPushButton("Revert Selected Item")
        self.revert_button.clicked.connect(self._revert)

        content = QWidget()
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        metadata = QFormLayout()
        metadata.addRow("ID", self.id_value)
        metadata.addRow("Source", self.source_value)
        layout.addLayout(metadata)
        layout.addWidget(self.revert_button)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.validation)
        self._create_stable_sections()
        self.clear()

    def clear(self) -> None:
        self.project = None
        self.selection = None
        self._definition = None
        self.header.setText("Item Properties")
        self.id_value.setText("—")
        self.source_value.setText("—")
        self.status.setText("No item selected.")
        self.validation.clear()
        self.revert_button.setEnabled(False)
        self._clear_dynamic_schema_sections()

    def set_state(
        self,
        project: StoryProject | None,
        selection: DefinitionSelection | None,
        definition: Any | None,
        diagnostics: Diagnostics,
    ) -> None:
        self.project, self.selection, self._definition, self._diagnostics = project, selection, definition, diagnostics
        if project is None or selection is None or definition is None:
            self.clear()
            return
        self.header.setText(f"Item Properties: {selection.id}")
        self.id_value.setText(selection.id)
        source = getattr(definition, "source", selection.source)
        self.source_value.setText(_relative_source(project.story_root, source))
        self.status.clear()
        self.revert_button.setEnabled(bool(self.session and self.session.is_definition_dirty(selection)))
        schema_signature = self._schema_signature_for(selection)
        if schema_signature != self._schema_signature:
            self._build_form()
        self._refresh_values()
        relevant = [item for item in diagnostics if item.source == source]
        self.validation.setText("\n".join(item.format() for item in relevant if item.is_error or item.is_warning))

    def _clear_dynamic_schema_sections(self) -> None:
        """Delete only schema-driven sections; the editor shell stays alive."""

        while self.schema_layout.count():
            item = self.schema_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._editors.clear()
        self._schema_signature = None

    def _schema_signature_for(self, selection: DefinitionSelection) -> tuple[tuple[Any, ...], ...]:
        if self.session is None:
            return ()
        model = self.session.property_model(selection)
        if model is None:
            return ()
        signature: list[tuple[Any, ...]] = []
        for path in (("name",), ("type",), ("icon",), ("description",), ("equipment_slot",)):
            try:
                descriptor = model.descriptor(path)
            except KeyError:
                continue
            type_spec = descriptor.type_spec
            signature.append((path, getattr(type_spec, "kind", None), getattr(type_spec, "asset_kind", None)))
        return tuple(signature)

    def _build_form(self) -> None:
        self._clear_dynamic_schema_sections()
        assert self.project is not None and self.selection is not None
        for title, paths in (
            ("Identity", (("name",), ("type",))),
            ("Appearance", (("icon",),)),
            ("Description", (("description",),)),
            ("Equipment", (("equipment_slot",),)),
        ):
            group = QGroupBox(title)
            form = QFormLayout(group)
            for path in paths:
                self._add_schema_editor(form, path)
            self.schema_layout.addWidget(group)

        self._schema_signature = self._schema_signature_for(self.selection)

    def _create_stable_sections(self) -> None:
        """Create controls whose Python attributes are reused across selections."""

        self.schema_container = QWidget()
        self.schema_layout = QVBoxLayout(self.schema_container)
        self.schema_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.addWidget(self.schema_container)

        stats_group = QGroupBox("Stats")
        stats_form = QFormLayout(stats_group)
        for stat in ("hp", "attack", "defense"):
            spin = QSpinBox()
            spin.setRange(-2_147_483_648, 2_147_483_647)
            spin.valueChanged.connect(lambda value, key=stat: self._set_stat(key, int(value)))
            self._stat_spins[stat] = spin
            stats_form.addRow(stat.replace("_", " ").title(), spin)
        self.content_layout.addWidget(stats_group)

        equipment_group = QGroupBox("Equipment")
        equipment_form = QFormLayout(equipment_group)
        self.equipment_bonus_note = QLabel("Effective HP/attack/defense bonuses are shown below; legacy equipment.bonuses remains preserved.")
        self.equipment_bonus_note.setWordWrap(True)
        equipment_form.addRow(self.equipment_bonus_note)
        for stat in ("hp", "attack", "defense"):
            spin = QSpinBox()
            spin.setRange(-2_147_483_648, 2_147_483_647)
            spin.valueChanged.connect(lambda value, key=stat: self._set_equipment_bonus(key, int(value)))
            self._equipment_bonus_spins[stat] = spin
            equipment_form.addRow(f"{stat.replace('_', ' ').title()} Bonus", spin)
        self.content_layout.addWidget(equipment_group)

        use_group = QGroupBox("Use / Combat")
        use_layout = QVBoxLayout(use_group)
        for action in ("use", "equip", "unequip", "toss"):
            check = QCheckBox(action.title())
            check.toggled.connect(lambda value, key=action: self._set_action(key, bool(value)))
            self._action_checks[action] = check
            use_layout.addWidget(check)
        self.use_summary = QLabel()
        self.use_summary.setWordWrap(True)
        use_layout.addWidget(self.use_summary)
        self.content_layout.addWidget(use_group)

        move_group = QGroupBox("Move Grants")
        move_layout = QVBoxLayout(move_group)
        move_layout.addWidget(self.move_grants)
        row = QHBoxLayout()
        row.addWidget(self.move_grants_combo, 1)
        row.addWidget(self.move_grants_add)
        row.addWidget(self.move_grants_remove)
        move_layout.addLayout(row)
        self.open_move_button = QPushButton("Open Move Definition")
        self.open_move_button.clicked.connect(self._open_selected_move)
        move_layout.addWidget(self.open_move_button)
        self.content_layout.addWidget(move_group)

        self.advanced = QGroupBox("Advanced / Legacy")
        self.advanced_text = QLabel()
        self.advanced_text.setWordWrap(True)
        self.advanced_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        advanced_layout = QVBoxLayout(self.advanced)
        advanced_layout.addWidget(QLabel("Unknown and runtime-compatibility fields are preserved."))
        advanced_layout.addWidget(self.advanced_text)
        self.content_layout.addWidget(self.advanced)
        self.content_layout.addStretch(1)

    def _add_schema_editor(self, form: QFormLayout, path: tuple[str | int, ...]) -> None:
        assert self.session is not None and self.project is not None and self.selection is not None
        model = self.session.property_model(self.selection)
        if model is None:
            return
        try:
            descriptor = model.descriptor(path)
        except KeyError:
            return
        editor = self.factory.create(
            descriptor,
            story_root=self.project.story_root,
            project=self.project,
            parent=self.schema_container,
        )
        self._editors[path] = editor
        if hasattr(editor, "value_edited"):
            editor.value_edited.connect(lambda value, p=path, w=editor: self._set_property(p, value, w))
        form.addRow(descriptor.display_name, editor)

    def _refresh_values(self) -> None:
        if self.session is None or self.selection is None or self.project is None:
            return
        mapping = self.session.working_mapping(self.selection) or {}
        model = self.session.property_model(self.selection)
        if model is not None:
            for path, editor in self._editors.items():
                try:
                    descriptor = model.descriptor(path)
                except KeyError:
                    continue
                blocker = QSignalBlocker(editor)
                try:
                    self._set_editor_value(editor, descriptor)
                finally:
                    del blocker
        normalized = InventoryService({self.selection.id: mapping}).definition(self.selection.id)
        for stat, spin in self._stat_spins.items():
            blocker = QSignalBlocker(spin)
            spin.setValue(int(normalized.stats.get(stat, 0)))
            del blocker
        for stat, spin in self._equipment_bonus_spins.items():
            blocker = QSignalBlocker(spin)
            spin.setValue(int(normalized.stats.get(stat, 0)))
            del blocker
        authored_actions = mapping.get("actions")
        effective_actions = set(normalized.actions)
        for action, check in self._action_checks.items():
            blocker = QSignalBlocker(check)
            check.setChecked(action in effective_actions)
            del blocker
        use = mapping.get("use")
        combat = mapping.get("combat")
        self.use_summary.setText(f"Effective use actions: {repr(normalized.use_actions) if normalized.use_actions else 'none'}")
        grants = combat.get("move_grants", []) if isinstance(combat, Mapping) else []
        self.move_grants.clear()
        for grant in grants if isinstance(grants, (list, tuple)) else []:
            self.move_grants.addItem(str(grant))
        self.move_grants_combo.clear()
        move_ids = []
        if self.project.index is not None:
            move_ids = list(dict.fromkeys(reference.identifier for reference in self.project.index.references(ContentKind.MOVE)))
        self.move_grants_combo.addItems(move_ids)
        unknown = {key: value for key, value in mapping.items() if key not in {"name", "description", "type", "icon", "stats", "equipment_slot", "actions", "use", "equipment", "combat"}}
        self.advanced_text.setText(repr({"unknown": unknown, "equipment": mapping.get("equipment", {}), "combat": mapping.get("combat", {})}))
        self.revert_button.setEnabled(bool(self.session.is_definition_dirty(self.selection)))

    def _set_editor_value(self, editor: QWidget, descriptor: Any) -> None:
        value = descriptor.effective_value
        kind = descriptor.type_spec.kind if descriptor.type_spec is not None else None
        if hasattr(editor, "set_initializing"):
            editor.set_initializing(True)
        try:
            if kind == "multiline_string" and hasattr(editor, "setPlainText"):
                editor.setPlainText("" if value is MISSING else str(value))
            elif kind in {"string", "asset"} and hasattr(editor, "setText"):
                editor.setText("" if value is MISSING else str(value))
            elif kind == "integer" and hasattr(editor, "setValue"):
                editor.setValue(0 if value is MISSING else int(value))
        finally:
            if hasattr(editor, "set_initializing"):
                editor.set_initializing(False)

    def _set_property(self, path: tuple[str | int, ...], value: Any, editor: QWidget) -> None:
        if self.session is None or self.selection is None:
            return
        model = self.session.property_model(self.selection)
        if model is None:
            return
        descriptor = model.descriptor(path)
        command: Any = SetPropertyCommand(self.selection, path, value)
        try:
            self.session.apply_command(command)
        except EditValidationError as exc:
            self.status.setText(exc.message)
            self._set_editor_value(editor, descriptor)
            return
        self.status.clear()
        self._refresh_values()
        self.state_changed.emit()

    def _set_stat(self, stat: str, value: int) -> None:
        if self.session is None or self.selection is None:
            return
        self._set_property(("stats", stat), value, self._stat_spins[stat])

    def _set_equipment_bonus(self, stat: str, value: int) -> None:
        if self.session is None or self.selection is None:
            return
        mapping = self.session.working_mapping(self.selection) or {}
        equipment = mapping.get("equipment")
        if isinstance(equipment, Mapping) and isinstance(equipment.get("bonuses"), Mapping) and "stats" not in mapping:
            updated_equipment = dict(equipment)
            bonuses = dict(updated_equipment.get("bonuses", {}))
            bonuses[stat] = value
            updated_equipment["bonuses"] = bonuses
            self._set_property(("equipment",), updated_equipment, self._equipment_bonus_spins[stat])
            return
        self._set_property(("stats", stat), value, self._equipment_bonus_spins[stat])

    def _set_action(self, action: str, enabled: bool) -> None:
        if self.session is None or self.selection is None:
            return
        mapping = self.session.working_mapping(self.selection) or {}
        actions = list(mapping.get("actions", [])) if isinstance(mapping.get("actions"), (list, tuple)) else []
        actions = [value for value in actions if value in {"use", "equip", "unequip", "toss"} and value != action]
        if enabled:
            actions.append(action)
        self._set_property(("actions",), actions, self._action_checks[action])

    def _set_combat(self, grants: list[str]) -> None:
        if self.session is None or self.selection is None:
            return
        mapping = self.session.working_mapping(self.selection) or {}
        combat = dict(mapping.get("combat", {})) if isinstance(mapping.get("combat"), Mapping) else {}
        combat["move_grants"] = grants
        self._set_property(("combat",), combat, self.move_grants)

    def _add_move_grant(self) -> None:
        current = [self.move_grants.item(i).text() for i in range(self.move_grants.count())]
        value = self.move_grants_combo.currentData() or self.move_grants_combo.currentText()
        if value and value not in current:
            current.append(str(value))
            self._set_combat(current)

    def _remove_move_grant(self) -> None:
        row = self.move_grants.currentRow()
        if row < 0:
            return
        current = [self.move_grants.item(i).text() for i in range(self.move_grants.count())]
        current.pop(row)
        self._set_combat(current)

    def _open_selected_move(self) -> None:
        item = self.move_grants.currentItem()
        if item is not None:
            self.open_move_requested.emit(item.text())

    def _revert(self) -> None:
        if self.session is None or self.selection is None:
            return
        if self.session.revert_definition(self.selection):
            self._refresh_values()
            self.state_changed.emit()


def _relative_source(root: Any, source: Any) -> str:
    if source is None:
        return "—"
    try:
        return str(source.relative_to(root)).replace("\\", "/")
    except (AttributeError, ValueError):
        return str(source)


__all__ = ["ItemNavigator", "ItemPreviewWidget", "ItemPropertiesWidget", "SquarePreviewWidget"]
