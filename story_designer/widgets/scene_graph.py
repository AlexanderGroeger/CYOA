"""Native Qt view for the read-only project-wide scene graph."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from engine.story_core import ContentKind, Diagnostics, StoryProject

from ..models import DefinitionSelection, ProjectSession, SceneGraphEdge, SceneGraphModel, SceneGraphNode


class SceneGraphCanvas(QGraphicsView):
    """Pannable graph canvas with mouse-wheel zoom."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#202532"))
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API name
        delta = event.angleDelta().y()
        if delta:
            self.scale(1.15 if delta > 0 else 1 / 1.15, 1.15 if delta > 0 else 1 / 1.15)
            event.accept()
            return
        super().wheelEvent(event)


class SceneGraphNodeItem(QGraphicsRectItem):
    WIDTH = 200.0
    HEIGHT = 82.0

    def __init__(self, node: SceneGraphNode, double_click) -> None:
        super().__init__(QRectF(0, 0, self.WIDTH, self.HEIGHT))
        self.node = node
        self._double_click = double_click
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setData(0, "node")
        self.setData(1, node.scene_id)
        self.setToolTip(_node_tooltip(node))

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self._double_click(self.node.scene_id)
        event.accept()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if self.node.is_missing:
            fill, border = QColor("#4c2630"), QColor("#ff8585")
        elif self.node.is_start:
            fill, border = QColor("#244d58"), QColor("#6ee7d0")
        elif not self.node.static_reachable:
            fill, border = QColor("#3d3c46"), QColor("#b0a8c5")
        else:
            fill, border = QColor("#303b55"), QColor("#8ca9e8")
        if self.node.validation_status == "error":
            border = QColor("#ff6b6b")
        elif self.node.validation_status == "warning":
            border = QColor("#f6c453")
        if option.state & QStyleStateSelected:
            border = QColor("#ffd166")
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(border, 3 if option.state & QStyleStateSelected else 1.5))
        painter.drawRoundedRect(self.rect(), 8, 8)
        painter.setPen(QColor("#f4f6fb"))
        painter.setFont(QFont("sans-serif", 10, QFont.Weight.Bold))
        painter.drawText(self.rect().adjusted(10, 8, -10, -48), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                         self.node.scene_id)
        painter.setFont(QFont("sans-serif", 8))
        lines = [self.node.scene_type.title()]
        if self.node.is_start:
            lines.append("Start")
        if self.node.is_ending:
            lines.append("Ending")
        if not self.node.static_reachable and not self.node.is_missing:
            lines.append("Not statically reachable")
        if self.node.dirty:
            lines.append("Modified")
        if self.node.incoming_count or self.node.outgoing_count:
            lines.append(f"In {self.node.incoming_count}  ·  Out {self.node.outgoing_count}")
        painter.setPen(QColor("#cbd5e1"))
        painter.drawText(self.rect().adjusted(10, 34, -10, -8), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                         "\n".join(lines))


class SceneGraphEdgeItem(QGraphicsPathItem):
    def __init__(self, edge: SceneGraphEdge, start: QPointF, end: QPointF) -> None:
        self.edge = edge
        path = QPainterPath(start)
        if abs(start.x() - end.x()) < 1 and abs(start.y() - end.y()) < 1:
            path.cubicTo(start + QPointF(80, -100), start + QPointF(140, 100), start + QPointF(4, 4))
        else:
            bend = (start.x() + end.x()) / 2
            path.cubicTo(QPointF(bend, start.y()), QPointF(bend, end.y()), end)
        super().__init__(path)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setData(0, "edge")
        self.setData(1, edge.edge_id)
        self.setZValue(-1)
        self.setToolTip(_edge_tooltip(edge))

    def paint(self, painter: QPainter, option, widget=None) -> None:
        selected = bool(option.state & QStyleStateSelected)
        colors = {
            "navigation": "#62d5c5",
            "legacy_goto": "#8ca9e8",
            "event_transition": "#d2a8ff",
            "battle_outcome": "#f6b26b",
        }
        pen = QPen(QColor("#ffd166" if selected else colors.get(self.edge.kind, "#aeb7c7")), 3 if selected else 2)
        if self.edge.conditional:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())
        end = self.path().pointAtPercent(1.0)
        before = self.path().pointAtPercent(0.97)
        direction = end - before
        if direction.manhattanLength() < 0.1:
            direction = QPointF(1, 0)
        length = max(1.0, (direction.x() ** 2 + direction.y() ** 2) ** 0.5)
        unit = QPointF(direction.x() / length, direction.y() / length)
        side = QPointF(-unit.y(), unit.x())
        arrow = QPolygonF([end, end - unit * 10 + side * 4, end - unit * 10 - side * 4])
        painter.setBrush(pen.color())
        painter.drawPolygon(arrow)


QStyleStateSelected = QStyleState = 0
try:  # Avoid depending on a private Qt enum in the custom item paint code.
    from PySide6.QtWidgets import QStyle
    QStyleStateSelected = QStyle.StateFlag.State_Selected
except ImportError:  # pragma: no cover
    pass


class SceneGraphWidget(QWidget):
    """Read-only graph workspace backed by :class:`SceneGraphModel`."""

    scene_selected = Signal(object)
    scene_open_requested = Signal(str)
    open_navigation_entry = Signal(object)

    def __init__(self, session: ProjectSession | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.project: StoryProject | None = None
        self.model = SceneGraphModel()
        self._node_items: dict[str, SceneGraphNodeItem] = {}
        self._edge_items: dict[str, SceneGraphEdgeItem] = {}
        self._has_rendered = False
        self._syncing_selection = False

        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Find Scene…")
        self.find_edit.returnPressed.connect(self.focus_search_result)
        find_button = QPushButton("Find")
        find_button.clicked.connect(self.focus_search_result)
        self.fit_button = QPushButton("Fit Graph")
        self.fit_button.clicked.connect(self.fit_graph)
        self.actual_button = QPushButton("100%")
        self.actual_button.clicked.connect(self.actual_size)
        self.zoom_out_button = QPushButton("Zoom Out")
        self.zoom_out_button.clicked.connect(lambda: self.canvas.scale(1 / 1.2, 1 / 1.2))
        self.zoom_in_button = QPushButton("Zoom In")
        self.zoom_in_button.clicked.connect(lambda: self.canvas.scale(1.2, 1.2))
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Scene Graph"))
        toolbar.addStretch(1)
        toolbar.addWidget(self.find_edit)
        toolbar.addWidget(find_button)
        toolbar.addWidget(self.fit_button)
        toolbar.addWidget(self.actual_button)
        toolbar.addWidget(self.zoom_out_button)
        toolbar.addWidget(self.zoom_in_button)

        self.canvas = SceneGraphCanvas()
        self.scene = QGraphicsScene(self)
        self.canvas.setScene(self.scene)
        self.scene.selectionChanged.connect(self._selection_changed)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumWidth(260)
        self.open_navigation_button = QPushButton("Open Navigation Entry")
        self.open_navigation_button.clicked.connect(self._open_navigation)
        self.open_navigation_button.setEnabled(False)
        side = QVBoxLayout()
        side.addWidget(QLabel("Graph Details"))
        side.addWidget(self.details, 1)
        side.addWidget(self.open_navigation_button)
        content = QHBoxLayout()
        content.addWidget(self.canvas, 1)
        side_widget = QWidget()
        side_widget.setLayout(side)
        content.addWidget(side_widget)
        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addLayout(content, 1)
        self.clear()

    def clear(self) -> None:
        self.project = None
        self.model = SceneGraphModel()
        self._node_items.clear()
        self._edge_items.clear()
        self.scene.clear()
        self.scene.setSceneRect(0, 0, 1, 1)
        self.details.setPlainText("Open a story project to inspect its scene graph.")
        self.open_navigation_button.setEnabled(False)
        self._has_rendered = False

    def set_state(
        self,
        project: StoryProject | None,
        selection: DefinitionSelection | None = None,
        definition: Any | None = None,
        diagnostics: Diagnostics | None = None,
    ) -> None:
        # ``definition`` is accepted for parity with the other workspace
        # widgets; the graph always rebuilds from the complete session.
        if diagnostics is None and isinstance(definition, Diagnostics):
            diagnostics = definition
        if project is None:
            self.clear()
            return
        center = self.canvas.mapToScene(self.canvas.viewport().rect().center()) if self._has_rendered else None
        scale = self.canvas.transform().m11() if self._has_rendered else 1.0
        self.project = project
        self.model = SceneGraphModel.from_session(self.session) if self.session is not None else SceneGraphModel.from_project(project)
        self._render()
        if center is not None:
            self.canvas.resetTransform()
            self.canvas.scale(scale, scale)
            self.canvas.centerOn(center)
        else:
            self.fit_graph()
        if selection is not None and selection.kind is ContentKind.SCENE:
            self.select_scene(selection.id, emit=False)
        elif selection is None:
            self.scene.clearSelection()
        self._has_rendered = True

    def _render(self) -> None:
        self.scene.clear()
        self._node_items.clear()
        self._edge_items.clear()
        positions = dict(self.model.positions)
        max_x = max((point[0] for point in positions.values()), default=0.0)
        for index, node in enumerate(self.model.missing_nodes):
            positions[node.scene_id] = (max_x + 260.0, float(index * 130))
        # Edges are created first so nodes remain visually on top.
        for edge in self.model.edges:
            start = _center(positions.get(edge.source_scene_id, (0, 0)))
            end = _center(positions.get(edge.target_scene_id, (max_x + 260, 0)))
            item = SceneGraphEdgeItem(edge, start, end)
            self.scene.addItem(item)
            self._edge_items[edge.edge_id] = item
        for node in self.model.all_nodes:
            item = SceneGraphNodeItem(node, self.scene_open_requested.emit)
            item.setPos(*positions.get(node.scene_id, (0.0, 0.0)))
            self.scene.addItem(item)
            self._node_items[node.scene_id] = item
        width = max_x + 520
        height = max((point[1] for point in positions.values()), default=0.0) + 180
        self.scene.setSceneRect(-40, -40, max(600, width), max(400, height))

    def select_scene(self, scene_id: str, *, emit: bool = True) -> bool:
        item = self._node_items.get(str(scene_id))
        if item is None:
            return False
        self._syncing_selection = True
        self.scene.clearSelection()
        item.setSelected(True)
        self._syncing_selection = False
        self.canvas.centerOn(item)
        self._present_item(item, emit=emit)
        return True

    def focus_search_result(self) -> bool:
        query = self.find_edit.text().strip().lower()
        if not query:
            return False
        match = next((node.scene_id for node in self.model.nodes if query in node.scene_id.lower()), None)
        return self.select_scene(match) if match is not None else False

    def fit_graph(self) -> None:
        if self.scene.items():
            self.canvas.fitInView(self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40), Qt.AspectRatioMode.KeepAspectRatio)

    def actual_size(self) -> None:
        center = self.canvas.mapToScene(self.canvas.viewport().rect().center())
        self.canvas.resetTransform()
        self.canvas.centerOn(center)

    def _selection_changed(self) -> None:
        if self._syncing_selection:
            return
        selected = self.scene.selectedItems()
        if not selected:
            self.open_navigation_button.setEnabled(False)
            self.details.setPlainText("Select a scene or connection to inspect it.")
            return
        self._present_item(selected[0], emit=True)

    def _present_item(self, item: QGraphicsItem, *, emit: bool) -> None:
        if isinstance(item, SceneGraphNodeItem):
            self.open_navigation_button.setEnabled(False)
            self.details.setPlainText(_node_details(self.model, item.node))
            if emit and not item.node.is_missing:
                self.scene_selected.emit(self._selection_for_scene(item.node.scene_id))
        elif isinstance(item, SceneGraphEdgeItem):
            edge = item.edge
            self.open_navigation_button.setEnabled(_is_navigation_entry(edge))
            self.details.setPlainText(_edge_details(self.model, edge))

    def _selection_for_scene(self, scene_id: str) -> DefinitionSelection:
        source = None
        if self.project is not None and self.project.index is not None:
            entry = self.project.index.entry(ContentKind.SCENE, scene_id)
            source = entry.source if entry is not None else None
        return DefinitionSelection(ContentKind.SCENE, scene_id, source)

    def _open_navigation(self) -> None:
        selected = self.scene.selectedItems()
        if selected and isinstance(selected[0], SceneGraphEdgeItem):
            self.open_navigation_entry.emit(selected[0].edge)


def _center(position: tuple[float, float]) -> QPointF:
    return QPointF(position[0] + SceneGraphNodeItem.WIDTH / 2, position[1] + SceneGraphNodeItem.HEIGHT / 2)


def _node_tooltip(node: SceneGraphNode) -> str:
    if node.is_missing:
        return f"Missing scene reference: {node.scene_id}"
    reachability = "statically reachable" if node.static_reachable else "not statically reachable"
    return f"{node.scene_id}\n{reachability}\nIncoming: {node.incoming_count}\nOutgoing: {node.outgoing_count}"


def _edge_tooltip(edge: SceneGraphEdge) -> str:
    value = f"{edge.label} · {edge.source_scene_id} → {edge.target_scene_id}"
    if edge.conditional:
        value += "\n◇ conditional"
    if edge.unresolved:
        value += "\nUnresolved destination"
    if edge.condition_summary:
        value += f"\nCondition: {edge.condition_summary}"
    return value


def _node_details(model: SceneGraphModel, node: SceneGraphNode) -> str:
    if node.is_missing:
        return f"Missing destination\n\nScene reference: {node.scene_id}"
    incoming = "\n".join(f"  {edge.source_scene_id}  ({edge.label})" for edge in model.incoming(node.scene_id)) or "  (none)"
    outgoing = "\n".join(f"  {edge.target_scene_id}  ({edge.label})" for edge in model.outgoing(node.scene_id)) or "  (none)"
    return (
        f"Scene: {node.scene_id}\nType: {node.scene_type}\n"
        f"Reachability: {'statically reachable' if node.static_reachable else 'not statically reachable'}\n"
        f"Source: {node.source or '—'}\n\nIncoming\n{incoming}\n\nOutgoing\n{outgoing}"
    )


def _edge_details(model: SceneGraphModel, edge: SceneGraphEdge) -> str:
    condition = edge.condition_summary or "(none)"
    status = "unresolved" if edge.unresolved else "resolved"
    return (
        f"Source: {edge.source_scene_id}\nDestination: {edge.target_scene_id}\n"
        f"Type: {edge.kind}\nLabel: {edge.label}\nCondition: {condition}\n"
        f"Source path: {'.'.join(str(part) for part in edge.source_path) or '—'}\n"
        f"Source file: {edge.source_file or '—'}\nStatus: {status}"
    )


def _is_navigation_entry(edge: SceneGraphEdge) -> bool:
    return edge.kind == "navigation" and (
        len(edge.source_path) >= 4 and edge.source_path[:2] == ("exploration", "navigation")
        or len(edge.source_path) >= 3 and edge.source_path[0] == "navigation"
    )


__all__ = ["SceneGraphCanvas", "SceneGraphEdgeItem", "SceneGraphNodeItem", "SceneGraphWidget"]
