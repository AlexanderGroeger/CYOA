"""Graphical, non-mutating Scene Editor workspace."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from engine.story_core import ContentKind, StoryProject

from ..models import (
    LookRegionPresentation,
    DefinitionSelection,
    DuplicateSceneElementCommand,
    EditValidationError,
    GeometryChange,
    InsertSceneElementCommand,
    ProjectSession,
    RemoveSceneElementCommand,
    SceneElementSelection,
    SceneObjectPresentation,
    ScenePresentation,
    SetGeometryCommand,
    build_scene_presentation,
    scene_collection_path,
    scene_geometry_target,
)
from .navigation_panel import NavigationPanel


class SceneCanvasView(QGraphicsView):
    """Graphics view that reports logical cursor coordinates."""

    cursor_moved = Signal(object)
    drag_position_changed = Signal(object)
    mouse_released = Signal(object)
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#252733"))
        self._drag_mode_before_resize: QGraphicsView.DragMode | None = None

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API name
        # ScrollHandDrag can begin a viewport grab before the graphics child
        # receives the press.  Suppress it for editor handles so the handle's
        # release cannot be turned into a canvas pan when its rect previews.
        scene_pos = self.mapToScene(event.position().toPoint())
        if self._resize_handle_at(scene_pos):
            self._drag_mode_before_resize = self.dragMode()
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        super().mousePressEvent(event)

    def viewportEvent(self, event) -> bool:  # noqa: N802 - Qt API name
        if event.type() == QEvent.Type.MouseButtonPress:
            position = event.position().toPoint()
            scene_pos = self.mapToScene(position)
            if self._resize_handle_at(scene_pos):
                self._drag_mode_before_resize = self.dragMode()
                self.setDragMode(QGraphicsView.DragMode.NoDrag)
        return super().viewportEvent(event)

    def _resize_handle_at(self, scene_pos: QPointF) -> bool:
        if self.scene() is None:
            return False
        # The scene boundary is intentionally above editor affordances and
        # ignores mouse buttons, but it still wins a plain itemAt() query.
        return any(hasattr(item, "_gesture_started") for item in self.scene().items(scene_pos))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API name
        point = self.mapToScene(event.position().toPoint())
        self.cursor_moved.emit(point)
        super().mouseMoveEvent(event)
        self.drag_position_changed.emit(point)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API name
        point = self.mapToScene(event.position().toPoint())
        super().mouseReleaseEvent(event)
        self.mouse_released.emit(point)
        self.restore_drag_mode()

    def restore_drag_mode(self) -> None:
        mode = self._drag_mode_before_resize
        self._drag_mode_before_resize = None
        if mode is not None:
            self.setDragMode(mode)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self.cursor_moved.emit(None)
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class SceneGraphicsItem(QGraphicsRectItem):
    """A selectable logical-space rectangle for artwork or an overlay."""

    def __init__(
        self,
        rect: tuple[int, int, int, int],
        *,
        ref: SceneElementSelection | None = None,
        pixmap: QPixmap | None = None,
        label: str | None = None,
        region: bool = False,
        missing: str | None = None,
        editable: bool = False,
        gesture_finished: Callable[["SceneGraphicsItem", QPointF, QPointF], None] | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        _x, _y, width, height = rect
        super().__init__(0, 0, max(1, width), max(1, height), parent)
        self.ref = ref
        self.pixmap = pixmap
        self.label = label
        self.region = region
        self.missing = missing
        self.editable = editable
        self._gesture_finished = gesture_finished
        self._gesture_start_pos: QPointF | None = None
        if ref is not None:
            self.setData(0, ref)
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
            if editable:
                self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        else:
            self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setPos(_x, _y)
        self.setToolTip(self._tooltip())

    def _tooltip(self) -> str:
        if self.ref is None:
            return self.label or self.missing or "Scene artwork"
        value = f"{self.ref.kind}: {self.ref.id}"
        if self.missing:
            value += f"\n{self.missing}"
        if self.label:
            value += f"\n{self.label}"
        return value

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            bounds = self.rect()
            if self.region:
                painter.fillRect(bounds, QColor(70, 180, 220, 45))
                pen = QPen(QColor("#56c7e8"), 2)
                pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawRect(bounds)
            elif self.pixmap is not None and not self.pixmap.isNull():
                painter.drawPixmap(bounds, self.pixmap, QRectF(self.pixmap.rect()))
            else:
                painter.fillRect(bounds, QColor(190, 70, 70, 120) if self.missing else QColor(110, 110, 125, 110))
                pen = QPen(QColor("#ff8b8b") if self.missing else QColor("#c3c4cf"), 1)
                pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawRect(bounds)
                painter.setFont(QFont("sans-serif", 7))
                painter.drawText(bounds.adjusted(3, 3, -3, -3), Qt.AlignmentFlag.AlignCenter,
                                 self.missing or self.label or "unsupported")

            if self.label and self.region:
                painter.setPen(QPen(QColor("#f5f6fa"), 1))
                painter.setBrush(QBrush(QColor(20, 25, 40, 190)))
                painter.setFont(QFont("sans-serif", 8, QFont.Weight.Bold))
                label_rect = bounds.adjusted(2, 2, -2, -max(2, bounds.height() - 16))
                painter.drawRect(label_rect)
                painter.drawText(label_rect.adjusted(3, 0, -3, 0), Qt.AlignmentFlag.AlignVCenter, self.label)

            if option.state & QStyle.StateFlag.State_Selected:
                selection_pen = QPen(QColor("#ffd166"), 3)
                selection_pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
                painter.setPen(selection_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(bounds)
        finally:
            painter.restore()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if self.editable and self.ref is not None:
            if self.scene() is not None and self.scene().views():
                self.scene().views()[0].setFocus()
            if not self.isSelected() and self.scene() is not None:
                self.scene().clearSelection()
                self.setSelected(True)
            self._gesture_start_pos = QPointF(self.pos())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().mouseReleaseEvent(event)
        start = self._gesture_start_pos
        self._gesture_start_pos = None
        if start is not None and self._gesture_finished is not None:
            self._gesture_finished(self, start, QPointF(self.pos()))


class ResizeHandleItem(QGraphicsRectItem):
    """Editor-only corner handle owned by one rectangular region item."""

    def __init__(
        self,
        owner: SceneGraphicsItem,
        corner: str,
        gesture_started: Callable[[SceneGraphicsItem, str, QPointF], None],
        gesture_moved: Callable[[SceneGraphicsItem, str, QPointF], None],
        gesture_finished: Callable[[SceneGraphicsItem, str, QPointF], None],
    ) -> None:
        super().__init__(parent=owner)
        self.owner = owner
        self.corner = corner
        self._gesture_started = gesture_started
        self._gesture_moved = gesture_moved
        self._gesture_finished = gesture_finished
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setCursor(_resize_cursor(corner))
        self.setZValue(1000)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setPen(QPen(QColor("#1f2633"), 1))
        painter.setBrush(QBrush(QColor("#ffd166")))
        painter.drawRect(self.rect())

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if self.scene() is not None and self.scene().views():
            self.scene().views()[0].setFocus()
        self._gesture_started(self.owner, self.corner, event.scenePos())
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self._gesture_moved(self.owner, self.corner, event.scenePos())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API name
        # The view owns the release boundary.  A handle can move away from
        # the pointer during preview, so committing here is unreliable when
        # Qt routes the release through the graphics scene with a refreshed
        # scene position.  SceneCanvasView.mouse_released supplies the stable
        # viewport position to the same finish callback.
        event.accept()


class SceneEditorWidget(QWidget):
    """Logical-coordinate scene canvas with editor-only selection overlays."""

    element_selected = Signal(object)
    geometry_committed = Signal(object)
    geometry_error = Signal(str)
    structure_changed = Signal(object)
    navigation_changed = Signal(object)
    open_destination_scene = Signal(str)
    open_dialogue_sequence = Signal(str)

    def __init__(self, session: ProjectSession | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.presentation: ScenePresentation | None = None
        self.selected_element: SceneElementSelection | None = None
        self.object_items: dict[str, SceneGraphicsItem] = {}
        self.look_region_items: dict[str, SceneGraphicsItem] = {}
        self.resize_handles: dict[str, ResizeHandleItem] = {}
        self._resize_gesture: tuple[SceneGraphicsItem, str, tuple[float, float, float, float]] | None = None
        self._resize_view_drag_mode: QGraphicsView.DragMode | None = None
        self.scene = QGraphicsScene(self)
        self.navigation_panel = NavigationPanel(session, self)
        self.navigation_panel.navigation_changed.connect(self.navigation_changed)
        self.navigation_panel.open_destination_scene.connect(self.open_destination_scene)
        self.view = SceneCanvasView(self)
        self.view.setScene(self.scene)
        self.view.cursor_moved.connect(self._update_cursor)
        self.view.drag_position_changed.connect(self._preview_active_resize)
        self.view.mouse_released.connect(self._finish_active_resize)
        self.view.cancel_requested.connect(self.cancel_gesture)
        self.scene.selectionChanged.connect(self._selection_changed)

        self.scene_title = QLabel("Scene Editor")
        self.scene_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.coordinate_value = QLabel("x: —   y: —")
        self.fit_button = QPushButton("Fit")
        self.actual_button = QPushButton("100%")
        self.zoom_out_button = QPushButton("Zoom Out")
        self.zoom_in_button = QPushButton("Zoom In")
        self.fit_button.clicked.connect(self.fit_scene)
        self.actual_button.clicked.connect(lambda: self.set_zoom(1.0))
        self.zoom_out_button.clicked.connect(lambda: self.set_zoom(self._zoom / 1.25))
        self.zoom_in_button.clicked.connect(lambda: self.set_zoom(self._zoom * 1.25))
        self.add_object_action = QAction("Add Object", self)
        self.add_object_action.triggered.connect(self.add_object)
        self.add_look_region_action = QAction("Add Look Region", self)
        self.add_look_region_action.triggered.connect(self.add_look_region)
        self.duplicate_action = QAction("Duplicate", self)
        self.duplicate_action.setShortcut(QKeySequence("Ctrl+D"))
        self.duplicate_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.duplicate_action.triggered.connect(self.duplicate_selected)
        self.delete_action = QAction("Delete", self)
        self.delete_action.setShortcut(QKeySequence("Delete"))
        self.delete_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.delete_action.triggered.connect(self.delete_selected)
        for action in (self.add_object_action, self.add_look_region_action, self.duplicate_action, self.delete_action):
            self.addAction(action)
        self.add_object_button = QPushButton("Add Object")
        self.add_object_button.clicked.connect(self.add_object)
        self.add_region_button = QPushButton("Add Look Region")
        self.add_region_button.clicked.connect(self.add_look_region)
        self.duplicate_button = QPushButton("Duplicate")
        self.duplicate_button.clicked.connect(self.duplicate_selected)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self.delete_selected)
        self.open_dialogue_button = QPushButton("Open Dialogue")
        self.open_dialogue_button.clicked.connect(self.open_selected_dialogue)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.scene_title)
        toolbar.addStretch(1)
        toolbar.addWidget(self.coordinate_value)
        toolbar.addWidget(self.add_object_button)
        toolbar.addWidget(self.add_region_button)
        toolbar.addWidget(self.duplicate_button)
        toolbar.addWidget(self.delete_button)
        toolbar.addWidget(self.open_dialogue_button)
        toolbar.addWidget(self.fit_button)
        toolbar.addWidget(self.actual_button)
        toolbar.addWidget(self.zoom_out_button)
        toolbar.addWidget(self.zoom_in_button)

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        self.navigation_summary = QLabel("Move Destinations: -")
        self.navigation_summary.setStyleSheet("color: #64748b;")
        layout.addWidget(self.navigation_summary)
        content = QHBoxLayout()
        content.addWidget(self.view, 1)
        content.addWidget(self.navigation_panel)
        layout.addLayout(content, 1)
        self._zoom = 1.0
        self._fit_mode = True
        self.clear()

    def clear(self) -> None:
        self.cancel_gesture()
        self.presentation = None
        self.selected_element = None
        self.object_items.clear()
        self.look_region_items.clear()
        self.resize_handles.clear()
        self.scene.clear()
        self.scene.setSceneRect(0, 0, 1, 1)
        self._update_structural_actions()
        self.scene_title.setText("Scene Editor")
        self.navigation_summary.setText("Move Destinations: -")
        self.navigation_panel.clear()
        self.coordinate_value.setText("x: —   y: —")

    def set_scene(
        self,
        project: StoryProject | None,
        scene_id: str | None,
        working_mapping: Mapping[str, Any] | None,
    ) -> None:
        if project is None or scene_id is None or working_mapping is None:
            self.clear()
            return
        self.cancel_gesture()
        previous = self.selected_element
        previous_presentation = self.presentation
        previous_transform = self.view.transform()
        previous_fit_mode = self._fit_mode
        presentation = build_scene_presentation(project, scene_id, working_mapping)
        self.presentation = presentation
        self.navigation_panel.set_scene(project, scene_id, working_mapping)
        destinations = [item.destination or "[invalid target]" for item in presentation.navigation]
        self.navigation_summary.setText(
            "Move Destinations: " + ("  ".join(f"→ {value}" for value in destinations) if destinations else "-")
        )
        self.object_items.clear()
        self.look_region_items.clear()
        self.resize_handles.clear()
        self.scene.clear()
        self.selected_element = previous if previous is not None and previous.scene_id == scene_id else None
        width, height = presentation.logical_size
        self.scene.setSceneRect(0, 0, width, height)
        self.scene_title.setText(f"Scene Editor — {scene_id}  ({width} × {height})")

        self._add_background(presentation)
        for obj in sorted(presentation.objects, key=lambda item: item.z):
            item = self._add_object(presentation.scene_id, obj)
            self.object_items[obj.id] = item
        if presentation.legacy_sprite is not None:
            self._add_legacy_sprite(presentation)
        for region in sorted(presentation.look_regions, key=lambda item: item.z):
            item = self._add_region(presentation.scene_id, region)
            self.look_region_items[region.id] = item
        if presentation.unsupported_animation:
            animation_item = SceneGraphicsItem(
                (6, 6, min(width - 12, 220), 22),
                label=f"Animation preview unavailable: {presentation.unsupported_animation}",
            )
            animation_item.setZValue(30000)
            self.scene.addItem(animation_item)

        boundary = QGraphicsRectItem(0, 0, width, height)
        boundary.setPen(QPen(QColor("#f0f2f5"), 2))
        boundary.setBrush(Qt.BrushStyle.NoBrush)
        boundary.setZValue(40000)
        boundary.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.scene.addItem(boundary)
        if self.selected_element is not None and self._item_for_ref(self.selected_element) is None:
            self.selected_element = None
        if self.selected_element is not None:
            self.select_element(self.selected_element, emit=False)
        same_scene = previous_presentation is not None and previous_presentation.scene_id == presentation.scene_id
        if same_scene:
            self.view.setTransform(previous_transform)
            self._fit_mode = previous_fit_mode
        else:
            self.fit_scene()
        self._update_structural_actions()

    def _add_background(self, presentation: ScenePresentation) -> None:
        width, height = presentation.logical_size
        pixmap = _load_pixmap(presentation.background_path, (width, height))
        missing = None
        if presentation.background and (presentation.background_path is None or pixmap is None):
            missing = presentation.background_error or f"Unable to load background: {presentation.background}"
        item = SceneGraphicsItem((0, 0, width, height), pixmap=pixmap, missing=missing,
                                 label="Scene background")
        item.setBrush(QBrush(QColor("#303341")))
        item.setZValue(-10000)
        self.scene.addItem(item)

    def _add_object(self, scene_id: str, obj: SceneObjectPresentation) -> SceneGraphicsItem:
        width, height = obj.size or _asset_size(obj.sprite_path, default=(48, 36))
        pixmap = _load_pixmap(obj.sprite_path, (width, height) if obj.size else None)
        missing = obj.asset_error if obj.sprite else "Object has no sprite asset"
        if obj.sprite and pixmap is None:
            missing = missing or f"Unable to load sprite: {obj.sprite}"
        elif obj.sprite_path is not None and pixmap is not None:
            missing = None
        label = obj.id
        if obj.conditional:
            label += "  ◇ conditional"
        if isinstance(obj.authored.get("animation"), str):
            label += f"  [animated: {obj.authored['animation']}]"
        item = SceneGraphicsItem(
            (obj.position[0], obj.position[1], width, height),
            ref=SceneElementSelection(scene_id, "object", obj.id),
            pixmap=pixmap,
            label=label,
            missing=missing,
            editable=True,
            gesture_finished=self._finish_item_drag,
        )
        item.setZValue(100 + obj.z)
        self.scene.addItem(item)
        return item

    def _add_legacy_sprite(self, presentation: ScenePresentation) -> None:
        width, height = _asset_size(presentation.legacy_sprite_path, default=(64, 48))
        pixmap = _load_pixmap(presentation.legacy_sprite_path, None)
        missing = presentation.legacy_sprite_error
        if presentation.legacy_sprite and pixmap is None:
            missing = missing or f"Unable to load sprite: {presentation.legacy_sprite}"
        elif presentation.legacy_sprite_path is not None and pixmap is not None:
            missing = None
        item = SceneGraphicsItem(
            (presentation.legacy_sprite_position[0], presentation.legacy_sprite_position[1], width, height),
            ref=SceneElementSelection(presentation.scene_id, "sprite", "scene_sprite"),
            pixmap=pixmap,
            label="scene sprite",
            missing=missing or "Scene has no loadable sprite",
            editable=False,
        )
        item.setZValue(1000)
        self.object_items["scene_sprite"] = item
        self.scene.addItem(item)

    def _add_region(self, scene_id: str, region: LookRegionPresentation) -> SceneGraphicsItem:
        label = region.id + ("  ◇ conditional" if region.conditional else "")
        item = SceneGraphicsItem(
            region.rect,
            ref=SceneElementSelection(scene_id, "look_region", region.id),
            label=label,
            region=True,
            editable=True,
            gesture_finished=self._finish_item_drag,
        )
        item.setZValue(20000 + region.z)
        self.scene.addItem(item)
        return item

    def select_element(self, ref: SceneElementSelection | None, *, emit: bool = True) -> None:
        self.scene.clearSelection()
        self.selected_element = ref
        if ref is not None:
            item = self._item_for_ref(ref)
            if item is not None:
                item.setSelected(True)
        self._sync_resize_handles()
        self._update_structural_actions()
        if emit:
            self.element_selected.emit(ref)

    def _item_for_ref(self, ref: SceneElementSelection) -> SceneGraphicsItem | None:
        if ref.kind == "look_region":
            return self.look_region_items.get(ref.id)
        return self.object_items.get(ref.id)

    def _selection_changed(self) -> None:
        selected = [item for item in self.scene.selectedItems() if isinstance(item, SceneGraphicsItem) and item.ref]
        ref = selected[-1].ref if selected else None
        self.selected_element = ref
        self._sync_resize_handles()
        self._update_structural_actions()
        self.element_selected.emit(ref)

    def _update_structural_actions(self) -> None:
        has_scene = self.presentation is not None and self.session is not None and self.session.project is not None
        editable = has_scene and self.selected_element is not None and self.selected_element.kind in {"object", "look_region"}
        for action in (self.add_object_action, self.add_look_region_action):
            action.setEnabled(bool(has_scene))
        self.duplicate_action.setEnabled(bool(editable))
        self.delete_action.setEnabled(bool(editable))
        self.add_object_button.setEnabled(bool(has_scene))
        self.add_region_button.setEnabled(bool(has_scene))
        self.duplicate_button.setEnabled(bool(editable))
        self.delete_button.setEnabled(bool(editable))
        self.open_dialogue_button.setEnabled(self._dialogue_reference_for_selected() is not None)

    def open_selected_dialogue(self) -> bool:
        reference = self._dialogue_reference_for_selected()
        if reference is None:
            return False
        self.open_dialogue_sequence.emit(reference)
        return True

    def _dialogue_reference_for_selected(self) -> str | None:
        ref = self.selected_element
        if ref is None or ref.kind not in {"object", "look_region"} or self.session is None or self.presentation is None:
            return None
        selection = self._scene_definition_selection(self.presentation.scene_id)
        mapping = self.session.working_mapping(selection) or {}
        sources = [mapping]
        exploration = mapping.get("exploration")
        if isinstance(exploration, Mapping):
            sources.insert(0, exploration)
        event_id = None
        key = "objects" if ref.kind == "object" else "look_regions"
        for source in sources:
            collection = source.get(key)
            values = collection if isinstance(collection, list) else list(collection.values()) if isinstance(collection, Mapping) else []
            for value in values:
                if isinstance(value, Mapping) and value.get("id") == ref.id:
                    look = value.get("look") if ref.kind == "object" else value
                    if isinstance(look, Mapping) and isinstance(look.get("event"), str):
                        event_id = look["event"]
                        break
            if event_id is not None:
                break
        if event_id is None:
            return None
        events = next((source.get("look_events") for source in sources if isinstance(source.get("look_events"), Mapping)), None)
        event = events.get(event_id) if isinstance(events, Mapping) else None
        actions = event.get("actions") if isinstance(event, Mapping) else None
        if not isinstance(actions, list):
            return None
        for action in actions:
            if not isinstance(action, Mapping):
                continue
            if action.get("type") == "dialog":
                value = action.get("dialog", action.get("sequence"))
                return value if isinstance(value, str) else None
            if "dialog" in action and isinstance(action.get("dialog"), str):
                return action["dialog"]
        return None

    def _structural_context(self) -> tuple[DefinitionSelection, dict[str, Any]] | None:
        if self.session is None or self.session.project is None or self.presentation is None:
            return None
        selection = self._scene_definition_selection(self.presentation.scene_id)
        mapping = self.session.working_mapping(selection)
        return (selection, mapping) if mapping is not None else None

    @staticmethod
    def _next_id(mapping: Mapping[str, Any], base: str) -> str:
        used: set[str] = set()
        sources = [mapping]
        exploration = mapping.get("exploration")
        if isinstance(exploration, Mapping):
            sources.append(exploration)
        for source in sources:
            for key in ("objects", "look_regions"):
                collection = source.get(key)
                if isinstance(collection, list):
                    used.update(
                        value.get("id") for value in collection
                        if isinstance(value, Mapping) and isinstance(value.get("id"), str)
                    )
                elif isinstance(collection, Mapping):
                    used.update(str(value) for value in collection)
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    def add_object(self) -> bool:
        context = self._structural_context()
        if context is None:
            return False
        selection, mapping = context
        width, height = self.presentation.logical_size
        identifier = self._next_id(mapping, "object")
        command = InsertSceneElementCommand(
            selection,
            scene_collection_path(mapping, "objects"),
            {"id": identifier, "position": [width // 2, height // 2]},
        )
        return self._apply_structural(command, SceneElementSelection(selection.id, "object", identifier))

    def add_look_region(self) -> bool:
        context = self._structural_context()
        if context is None:
            return False
        selection, mapping = context
        width, height = self.presentation.logical_size
        region_width = min(120, max(24, width // 4))
        region_height = min(80, max(16, height // 5))
        identifier = self._next_id(mapping, "look_region")
        element = {
            "id": identifier,
            "rect": [(width - region_width) // 2, (height - region_height) // 2, region_width, region_height],
            "interaction": "inspect",
        }
        command = InsertSceneElementCommand(selection, scene_collection_path(mapping, "look_regions"), element)
        return self._apply_structural(command, SceneElementSelection(selection.id, "look_region", identifier))

    def duplicate_selected(self) -> bool:
        ref = self.selected_element
        context = self._structural_context()
        if ref is None or ref.kind not in {"object", "look_region"} or context is None:
            return False
        selection, mapping = context
        key = "objects" if ref.kind == "object" else "look_regions"
        identifier = self._next_id(mapping, f"{ref.id}_copy")
        command = DuplicateSceneElementCommand(
            selection,
            scene_collection_path(mapping, key),
            ref.id,
            identifier,
        )
        return self._apply_structural(command, SceneElementSelection(ref.scene_id, ref.kind, identifier))

    def delete_selected(self) -> bool:
        ref = self.selected_element
        context = self._structural_context()
        if ref is None or ref.kind not in {"object", "look_region"} or context is None:
            return False
        selection, mapping = context
        key = "objects" if ref.kind == "object" else "look_regions"
        command = RemoveSceneElementCommand(selection, scene_collection_path(mapping, key), ref.id)
        return self._apply_structural(command, None)

    def _apply_structural(self, command: Any, selected: SceneElementSelection | None) -> bool:
        try:
            self.session.apply_command(command)
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._report_geometry_error(str(exc))
            return False
        self._refresh_authoritative(selected)
        self.structure_changed.emit(selected)
        return True

    def _sync_resize_handles(self) -> None:
        for handle in self.resize_handles.values():
            handle.setVisible(False)
        ref = self.selected_element
        item = self._item_for_ref(ref) if ref is not None else None
        if ref is None or ref.kind != "look_region" or item is None or not item.editable:
            return
        for corner in ("top_left", "top_right", "bottom_left", "bottom_right"):
            handle = self.resize_handles.get(corner)
            if handle is None:
                handle = ResizeHandleItem(
                    item,
                    corner,
                    self._begin_resize,
                    self._preview_resize,
                    self._finish_resize,
                )
                self.resize_handles[corner] = handle
            handle.setVisible(True)
            self._position_handle(item, handle, corner)

    def _position_handle(self, owner: SceneGraphicsItem, handle: ResizeHandleItem, corner: str) -> None:
        size = max(5.0, 10.0 / max(0.1, self._current_zoom()))
        half = size / 2.0
        width, height = owner.rect().width(), owner.rect().height()
        x = 0.0 if "left" in corner else width
        y = 0.0 if "top" in corner else height
        handle.setRect(-half, -half, size, size)
        handle.setPos(x, y)

    def _current_zoom(self) -> float:
        transform = self.view.transform()
        return max(0.1, abs(transform.m11()) or 1.0)

    def _begin_resize(self, owner: SceneGraphicsItem, corner: str, _scene_pos: QPointF) -> None:
        if owner.ref is None:
            return
        target = self._geometry_target(owner.ref)
        if target is None or target.shape != "rect":
            return
        x, y, width, height = target.value
        # Handles are only visible for the selected region.  Avoid clearing
        # and rebuilding selection while Qt is dispatching the press event;
        # that used to make the active child item lose its gesture in some
        # view/style combinations.
        if self.selected_element != owner.ref:
            self.select_element(owner.ref, emit=False)
        self._resize_view_drag_mode = self.view.dragMode()
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._resize_gesture = (owner, corner, (float(x), float(y), float(width), float(height)))

    def _preview_resize(self, owner: SceneGraphicsItem, corner: str, scene_pos: QPointF) -> None:
        gesture = self._resize_gesture
        if gesture is None or gesture[0] is not owner or gesture[1] != corner:
            return
        x, y, width, height = _resized_rect(gesture[2], corner, scene_pos.x(), scene_pos.y())
        owner.setPos(x, y)
        owner.setRect(0, 0, width, height)
        # Keep every affordance attached to the preview rectangle.  Updating
        # only the active handle leaves the opposite corners at stale scene
        # coordinates during a live drag.
        for handle_corner, handle in self.resize_handles.items():
            if handle.owner is owner:
                self._position_handle(owner, handle, handle_corner)
        owner.update()

    def _finish_resize(self, owner: SceneGraphicsItem, corner: str, scene_pos: QPointF) -> None:
        gesture = self._resize_gesture
        if gesture is None or gesture[0] is not owner or gesture[1] != corner or owner.ref is None:
            return
        self._preview_resize(owner, corner, scene_pos)
        self._resize_gesture = None
        self._restore_resize_view_mode()
        x, y = owner.scenePos().x(), owner.scenePos().y()
        geometry = (
            _logical_int(x),
            _logical_int(y),
            max(1, _logical_int(owner.rect().width())),
            max(1, _logical_int(owner.rect().height())),
        )
        if not self.commit_geometry(owner.ref, geometry):
            self._restore_rect(owner, gesture[2])

    def _preview_active_resize(self, scene_pos: QPointF) -> None:
        """Keep the gesture alive even if a moving handle loses child grab."""

        gesture = self._resize_gesture
        if gesture is not None:
            self._preview_resize(gesture[0], gesture[1], scene_pos)

    def _finish_active_resize(self, scene_pos: QPointF) -> None:
        """Commit a handle gesture at the canvas boundary on mouse release."""

        gesture = self._resize_gesture
        if gesture is not None:
            self._finish_resize(gesture[0], gesture[1], scene_pos)

    def _restore_resize_view_mode(self) -> None:
        mode = self._resize_view_drag_mode
        self._resize_view_drag_mode = None
        if mode is not None:
            self.view.setDragMode(mode)

    def _finish_item_drag(self, item: SceneGraphicsItem, start: QPointF, _end: QPointF) -> None:
        if item.ref is None:
            return
        if item.ref.kind == "object":
            geometry: tuple[int, ...] = (_logical_int(item.scenePos().x()), _logical_int(item.scenePos().y()))
        elif item.ref.kind == "look_region":
            geometry = (
                _logical_int(item.scenePos().x()),
                _logical_int(item.scenePos().y()),
                max(1, _logical_int(item.rect().width())),
                max(1, _logical_int(item.rect().height())),
            )
        else:
            item.setPos(start)
            return
        if not self.commit_geometry(item.ref, geometry):
            item.setPos(start)

    def commit_geometry(self, ref: SceneElementSelection, geometry: tuple[int, ...]) -> bool:
        """Commit one already-completed geometry edit through ProjectSession."""

        if self.session is None or self.session.project is None:
            return False
        selection = self._scene_definition_selection(ref.scene_id)
        mapping = self.session.working_mapping(selection)
        target = scene_geometry_target(mapping or {}, ref)
        if target is None:
            self._report_geometry_error("This scene element has no editable rectangular geometry.")
            return False
        new_value = tuple(int(value) for value in geometry)
        if new_value == target.value:
            item = self._item_for_ref(ref)
            if item is not None:
                if target.shape == "point":
                    item.setPos(new_value[0], new_value[1])
                else:
                    self._restore_rect(item, tuple(float(value) for value in new_value))
            return False
        command = SetGeometryCommand(
            selection,
            ref,
            (GeometryChange(target.path, target.shape, target.value, new_value),),
        )
        try:
            self.session.apply_command(command)
        except (EditValidationError, KeyError, TypeError, ValueError) as exc:
            self._report_geometry_error(str(exc))
            return False
        self._refresh_authoritative(ref)
        return True

    def _geometry_target(self, ref: SceneElementSelection):
        if self.session is None:
            return None
        selection = self._scene_definition_selection(ref.scene_id)
        return scene_geometry_target(self.session.working_mapping(selection) or {}, ref)

    def _scene_definition_selection(self, scene_id: str) -> DefinitionSelection:
        if self.session is not None and self.session.selection is not None:
            current = self.session.selection
            if current.kind is ContentKind.SCENE and current.id == scene_id:
                return current
        source = None
        if self.session is not None and self.session.project is not None and self.session.project.index is not None:
            entry = self.session.project.index.entry(ContentKind.SCENE, scene_id)
            source = entry.source if entry is not None else None
        return DefinitionSelection(ContentKind.SCENE, scene_id, source)

    def focus_navigation_path(self, path: tuple[str | int, ...]) -> bool:
        """Focus one existing exploration navigation entry in the side panel."""
        return self.navigation_panel.focus_path(tuple(path))

    def _refresh_authoritative(self, ref: SceneElementSelection | None) -> None:
        if self.session is None or self.session.project is None:
            return
        scene_id = ref.scene_id if ref is not None else self.presentation.scene_id if self.presentation is not None else None
        if scene_id is None:
            return
        selection = self._scene_definition_selection(scene_id)
        self.selected_element = ref
        self.set_scene(self.session.project, scene_id, self.session.working_mapping(selection))
        self.select_element(ref, emit=False)
        self.element_selected.emit(ref)
        self.geometry_committed.emit(ref)

    def _restore_rect(self, item: SceneGraphicsItem, geometry: tuple[float, float, float, float]) -> None:
        x, y, width, height = geometry
        item.setPos(x, y)
        item.setRect(0, 0, max(1.0, width), max(1.0, height))
        self._sync_resize_handles()

    def cancel_gesture(self) -> None:
        gesture = self._resize_gesture
        self._resize_gesture = None
        if gesture is not None:
            self._restore_rect(gesture[0], gesture[2])
            self._restore_resize_view_mode()
        self.view.restore_drag_mode()
        for item in [*self.object_items.values(), *self.look_region_items.values()]:
            start = getattr(item, "_gesture_start_pos", None)
            if start is not None:
                item.setPos(start)
                item._gesture_start_pos = None
        self._sync_resize_handles()

    def _report_geometry_error(self, message: str) -> None:
        self.geometry_error.emit(message)
        self.coordinate_value.setText(f"Geometry error: {message}")

    def _update_cursor(self, point: QPointF | None) -> None:
        if point is None:
            self.coordinate_value.setText("x: —   y: —")
            return
        self.coordinate_value.setText(f"x: {round(point.x())}   y: {round(point.y())}")

    def fit_scene(self) -> None:
        if self.presentation is None:
            return
        self._fit_mode = True
        self.view.resetTransform()
        self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._sync_resize_handles()

    def set_zoom(self, zoom: float) -> None:
        if self.presentation is None:
            return
        self._zoom = max(0.1, min(8.0, float(zoom)))
        self._fit_mode = False
        self.view.resetTransform()
        self.view.scale(self._zoom, self._zoom)
        self._sync_resize_handles()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit_scene()


def _load_pixmap(path: Path | None, size: tuple[int, int] | None) -> QPixmap | None:
    if path is None or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".gif"}:
        return None
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return None
    if size is not None:
        pixmap = pixmap.scaled(max(1, size[0]), max(1, size[1]), Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
    return pixmap


def _asset_size(path: Path | None, *, default: tuple[int, int]) -> tuple[int, int]:
    if path is not None and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".gif"}:
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            return max(1, pixmap.width()), max(1, pixmap.height())
    return default


def _logical_int(value: float) -> int:
    """Convert Qt's floating scene coordinate to the authored integer schema."""

    return int(round(float(value)))


def _resized_rect(
    original: tuple[float, float, float, float],
    corner: str,
    pointer_x: float,
    pointer_y: float,
) -> tuple[float, float, float, float]:
    left, top, width, height = original
    right, bottom = left + width, top + height
    if "left" in corner:
        left = min(pointer_x, right - 1.0)
    else:
        right = max(pointer_x, left + 1.0)
    if "top" in corner:
        top = min(pointer_y, bottom - 1.0)
    else:
        bottom = max(pointer_y, top + 1.0)
    return left, top, right - left, bottom - top


def _resize_cursor(corner: str):
    if corner == "top_left" or corner == "bottom_right":
        return Qt.CursorShape.SizeFDiagCursor
    return Qt.CursorShape.SizeBDiagCursor


__all__ = ["SceneEditorWidget", "SceneCanvasView", "SceneGraphicsItem", "ResizeHandleItem"]
