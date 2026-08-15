"""Qt-independent static scene graph construction and analysis.

The graph intentionally models authored *potential* scene flow.  It does not
evaluate conditions or attempt to simulate inventory, flags, variables, or
battle state.  Keeping this layer independent from Qt makes the graph useful
to diagnostics, tests, and future non-visual tooling as well as the editor.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable

from engine.core.exploration import exploration_config
from engine.story_core import ContentKind, StoryProject

from .project_session import DefinitionSelection, ProjectSession
from .scene_presentation import navigation_collection_path


GraphEdgeKind = str


@dataclass(frozen=True)
class SceneGraphNode:
    """Small graph-facing projection of one authored scene."""

    scene_id: str
    source: Path | None = None
    scene_type: str = "legacy"
    title: str | None = None
    incoming_count: int = 0
    outgoing_count: int = 0
    static_reachable: bool = False
    is_start: bool = False
    is_ending: bool = False
    dirty: bool = False
    validation_status: str = "ok"
    is_missing: bool = False

    @property
    def node_id(self) -> str:
        return self.scene_id


@dataclass(frozen=True)
class SceneGraphEdge:
    """One static authored scene-to-scene connection."""

    edge_id: str
    source_scene_id: str
    target_scene_id: str
    kind: GraphEdgeKind
    label: str
    conditional: bool = False
    condition: Any = None
    source_path: tuple[str | int, ...] = ()
    source_file: Path | None = None
    unresolved: bool = False
    event_pool_id: str | None = None

    @property
    def source(self) -> str:
        return self.source_scene_id

    @property
    def target(self) -> str:
        return self.target_scene_id

    @property
    def condition_summary(self) -> str:
        return summarize_condition(self.condition)


class SceneGraphModel:
    """A deterministic, read-only projection of a session's working state."""

    def __init__(
        self,
        nodes: Iterable[SceneGraphNode] = (),
        edges: Iterable[SceneGraphEdge] = (),
        *,
        start_scene_id: str | None = None,
        positions: Mapping[str, tuple[float, float]] | None = None,
    ) -> None:
        self.nodes = tuple(nodes)
        self.edges = tuple(edges)
        self.start_scene_id = start_scene_id
        self._node_by_id = {node.scene_id: node for node in self.nodes}
        self._missing_ids = tuple(sorted({edge.target_scene_id for edge in self.edges if edge.unresolved}))
        self.missing_nodes = tuple(
            SceneGraphNode(
                scene_id=identifier,
                scene_type="missing",
                title=f"Missing: {identifier}",
                validation_status="error",
                is_missing=True,
            )
            for identifier in self._missing_ids
        )
        self._node_by_id.update({node.scene_id: node for node in self.missing_nodes})
        self._outgoing = {node.scene_id: [] for node in self.nodes}
        self._incoming = {node.scene_id: [] for node in self.nodes}
        for edge in self.edges:
            self._outgoing.setdefault(edge.source_scene_id, []).append(edge)
            if not edge.unresolved:
                self._incoming.setdefault(edge.target_scene_id, []).append(edge)
        self.positions = dict(positions or {})
        max_x = max((point[0] for point in self.positions.values()), default=0.0)
        for index, node in enumerate(self.missing_nodes):
            self.positions.setdefault(node.scene_id, (max_x + 260.0, float(index * 130)))

    @property
    def all_nodes(self) -> tuple[SceneGraphNode, ...]:
        return (*self.nodes, *self.missing_nodes)

    @property
    def reachable_scene_ids(self) -> frozenset[str]:
        return frozenset(node.scene_id for node in self.nodes if node.static_reachable)

    @property
    def unreachable_scene_ids(self) -> frozenset[str]:
        return frozenset(node.scene_id for node in self.nodes if not node.static_reachable)

    def node(self, scene_id: str) -> SceneGraphNode | None:
        return self._node_by_id.get(str(scene_id))

    def incoming(self, scene_id: str) -> tuple[SceneGraphEdge, ...]:
        return tuple(self._incoming.get(str(scene_id), ()))

    def outgoing(self, scene_id: str) -> tuple[SceneGraphEdge, ...]:
        return tuple(self._outgoing.get(str(scene_id), ()))

    @classmethod
    def from_session(cls, session: ProjectSession) -> "SceneGraphModel":
        project = session.project
        if project is None:
            return cls()
        return cls.from_project(project, session=session)

    @classmethod
    def from_project(
        cls,
        project: StoryProject,
        *,
        session: ProjectSession | None = None,
    ) -> "SceneGraphModel":
        scene_ids = tuple(sorted(str(identifier) for identifier in project.scenes))
        raw_scenes = {
            scene_id: _working_definition_mapping(project, ContentKind.SCENE, scene_id, session)
            for scene_id in scene_ids
        }
        edges: list[SceneGraphEdge] = []
        for scene_id in scene_ids:
            mapping = raw_scenes[scene_id]
            source = getattr(project.scenes[scene_id], "source", None)
            edges.extend(_scene_edges(project, scene_id, mapping, source, session))

        incoming_counts = {scene_id: 0 for scene_id in scene_ids}
        outgoing_counts = {scene_id: 0 for scene_id in scene_ids}
        for edge in edges:
            outgoing_counts[edge.source_scene_id] += 1
            if not edge.unresolved and edge.target_scene_id in incoming_counts:
                incoming_counts[edge.target_scene_id] += 1

        start = project.manifest.start_scene
        reachable = _reachable(scene_ids, edges, start)
        diagnostics = session.diagnostics if session is not None else project.validate()
        nodes: list[SceneGraphNode] = []
        for scene_id in scene_ids:
            definition = project.scenes[scene_id]
            mapping = raw_scenes[scene_id]
            source = getattr(definition, "source", None)
            status = _scene_validation_status(diagnostics, source)
            scene_type = "exploration" if _is_exploration_mapping(mapping) else "legacy"
            title = mapping.get("title") if isinstance(mapping.get("title"), str) else None
            nodes.append(SceneGraphNode(
                scene_id=scene_id,
                source=source,
                scene_type=scene_type,
                title=title,
                incoming_count=incoming_counts[scene_id],
                outgoing_count=outgoing_counts[scene_id],
                static_reachable=scene_id in reachable,
                is_start=scene_id == start,
                is_ending=_is_ending(mapping),
                dirty=_is_dirty(session, ContentKind.SCENE, scene_id, source),
                validation_status=status,
            ))
        positions = _layout(scene_ids, edges, reachable, start)
        return cls(nodes, edges, start_scene_id=start, positions=positions)


def summarize_condition(value: Any, *, limit: int = 96) -> str:
    """Return a compact display summary without evaluating a condition."""
    if value is None:
        return ""
    if isinstance(value, str):
        result = value
    else:
        try:
            result = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError):
            result = str(value)
    return result if len(result) <= limit else result[: limit - 1] + "…"


def _working_definition_mapping(
    project: StoryProject,
    kind: ContentKind,
    identifier: str,
    session: ProjectSession | None,
) -> dict[str, Any]:
    definition = project.index.entry(kind, identifier).definition  # type: ignore[union-attr]
    source = getattr(definition, "source", None)
    selection = DefinitionSelection(kind, identifier, source)
    if session is not None:
        pending = session.working_definitions.get(selection)
        if pending is not None:
            return pending
    return dict(definition.to_mapping()) if hasattr(definition, "to_mapping") else {}


def _is_dirty(session: ProjectSession | None, kind: ContentKind, identifier: str, source: Path | None) -> bool:
    if session is None:
        return False
    return session.is_definition_dirty(DefinitionSelection(kind, identifier, source))


def _scene_edges(
    project: StoryProject,
    source_id: str,
    mapping: Mapping[str, Any],
    source_file: Path | None,
    session: ProjectSession | None,
) -> list[SceneGraphEdge]:
    result: list[SceneGraphEdge] = []
    ordinal = 0

    def add(
        target: Any,
        kind: str,
        label: str,
        path: tuple[str | int, ...],
        *,
        condition: Any = None,
        conditional: bool = False,
        event_pool_id: str | None = None,
    ) -> None:
        nonlocal ordinal
        if not isinstance(target, str) or not target:
            return
        result.append(SceneGraphEdge(
            edge_id=f"{source_id}:{ordinal}",
            source_scene_id=source_id,
            target_scene_id=target,
            kind=kind,
            label=label,
            conditional=conditional,
            condition=condition,
            source_path=path,
            source_file=source_file,
            unresolved=target not in project.scenes,
            event_pool_id=event_pool_id,
        ))
        ordinal += 1

    choices = mapping.get("choices", [])
    if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes, Mapping)):
        for index, choice in enumerate(choices):
            if not isinstance(choice, Mapping):
                continue
            condition, conditional = _condition(choice)
            prefix = ("choices", index)
            if "goto" in choice:
                add(choice.get("goto"), "legacy_goto", "Goto", prefix + ("goto",), condition=condition, conditional=conditional)
            elif "battle" in choice:
                add(choice.get("on_win"), "battle_outcome", "Victory", prefix + ("on_win",), condition=condition, conditional=conditional)
                add(choice.get("on_lose"), "battle_outcome", "Defeat", prefix + ("on_lose",), condition=condition, conditional=conditional)
            elif "random_event" in choice:
                pool_id = choice.get("random_event")
                for event_index, target in _event_pool_targets(project, pool_id, session):
                    add(target, "event_transition", "Event", prefix + ("random_event",), condition=condition,
                        conditional=conditional, event_pool_id=pool_id if isinstance(pool_id, str) else None)

    config = _exploration_config(mapping)
    if config is not None:
        navigation_path = navigation_collection_path(mapping)
        navigation = config.get("navigation", [])
        if isinstance(navigation, Sequence) and not isinstance(navigation, (str, bytes, Mapping)):
            for index, entry in enumerate(navigation):
                if not isinstance(entry, Mapping):
                    continue
                condition, conditional = _condition(entry)
                prefix = (*navigation_path, index)
                if "scene" in entry:
                    add(entry.get("scene"), "navigation", str(entry.get("label") or "Move"), prefix + ("scene",),
                        condition=condition, conditional=conditional)
                elif "battle" in entry:
                    add(entry.get("on_win"), "battle_outcome", "Victory", prefix + ("on_win",),
                        condition=condition, conditional=conditional)
                    add(entry.get("on_lose"), "battle_outcome", "Defeat", prefix + ("on_lose",),
                        condition=condition, conditional=conditional)
        for path, action, inherited_condition in _exploration_actions(config):
            action_type, target = _action_transition(action)
            if target is not None:
                add(target, "navigation", "Transition", path, condition=inherited_condition,
                    conditional=inherited_condition is not None)

    return result


def _event_pool_targets(
    project: StoryProject,
    pool_id: Any,
    session: ProjectSession | None,
) -> Iterable[tuple[int, str]]:
    if not isinstance(pool_id, str):
        return ()
    definition = project.event_pools.get(pool_id)
    if definition is None:
        return ()
    mapping = _working_definition_mapping(project, ContentKind.EVENT_POOL, pool_id, session)
    events = mapping.get("events", [])
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes, Mapping)):
        return ()
    return tuple((index, event["id"]) for index, event in enumerate(events)
                 if isinstance(event, Mapping) and isinstance(event.get("id"), str) and event["id"])


def _exploration_config(mapping: Mapping[str, Any]) -> Mapping[str, Any] | None:
    try:
        return exploration_config(mapping)
    except Exception:
        raw = mapping.get("exploration")
        return raw if isinstance(raw, Mapping) else None


def _exploration_actions(config: Mapping[str, Any]):
    """Yield known exploration action lists with authored paths."""
    def walk(actions: Any, path: tuple[str | int, ...], condition: Any = None):
        if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes, Mapping)):
            return
        for index, action in enumerate(actions):
            action_path = path + (index,)
            if not isinstance(action, Mapping):
                continue
            current_condition = action.get("condition", condition)
            yield action_path, action, current_condition
            nested = action.get("actions")
            if nested is not None:
                yield from walk(nested, action_path + ("actions",), current_condition)

    dialog = config.get("dialog", [])
    yield from walk(dialog, ("exploration", "dialog"))
    sequences = config.get("dialogue_sequences", {})
    if isinstance(sequences, Mapping):
        for sequence_id, sequence in sequences.items():
            if isinstance(sequence, Mapping):
                yield from walk(sequence.get("actions"), ("exploration", "dialogue_sequences", sequence_id, "actions"))
    events = config.get("look_events", {})
    if isinstance(events, Mapping):
        for event_id, event in events.items():
            if isinstance(event, Mapping):
                yield from walk(event.get("actions"), ("exploration", "look_events", event_id, "actions"))


def _action_transition(action: Mapping[str, Any]) -> tuple[str | None, str | None]:
    action_type = action.get("type")
    if action_type in {"scene_transition", "goto"}:
        target = action.get("scene", action.get("goto"))
        return str(action_type), target if isinstance(target, str) and target else None
    if len(action) == 1 and "goto" in action:
        target = action["goto"]
        return "goto", target if isinstance(target, str) and target else None
    return None, None


def _condition(mapping: Mapping[str, Any]) -> tuple[Any, bool]:
    if "conditions" in mapping:
        return mapping.get("conditions"), True
    if "condition" in mapping:
        return mapping.get("condition"), True
    return None, False


def _is_exploration_mapping(mapping: Mapping[str, Any]) -> bool:
    return mapping.get("exploration") is True or isinstance(mapping.get("exploration"), Mapping)


def _is_ending(mapping: Mapping[str, Any]) -> bool:
    if bool(mapping.get("ending", False)):
        return True
    choices = mapping.get("choices", [])
    config = _exploration_config(mapping)
    navigation = config.get("navigation", []) if config is not None else []
    return not isinstance(choices, Sequence) or isinstance(choices, (str, bytes, Mapping)) or not choices and not navigation


def _scene_validation_status(diagnostics: Any, source: Path | None) -> str:
    matches = [item for item in diagnostics if source is not None and item.source == source]
    if any(item.is_error for item in matches):
        return "error"
    if any(item.is_warning for item in matches):
        return "warning"
    return "ok"


def _reachable(scene_ids: Sequence[str], edges: Sequence[SceneGraphEdge], start: str | None) -> set[str]:
    if not isinstance(start, str) or start not in scene_ids:
        return set()
    outgoing: dict[str, list[str]] = {scene_id: [] for scene_id in scene_ids}
    for edge in edges:
        if not edge.unresolved:
            outgoing.setdefault(edge.source_scene_id, []).append(edge.target_scene_id)
    result: set[str] = set()
    pending = deque([start])
    while pending:
        current = pending.popleft()
        if current in result:
            continue
        result.add(current)
        pending.extend(target for target in outgoing.get(current, ()) if target not in result)
    return result


def _layout(
    scene_ids: Sequence[str],
    edges: Sequence[SceneGraphEdge],
    reachable: set[str],
    start: str | None,
) -> dict[str, tuple[float, float]]:
    """Stable BFS columns with cycles and disconnected areas handled."""
    outgoing: dict[str, list[str]] = {scene_id: [] for scene_id in scene_ids}
    for edge in edges:
        if not edge.unresolved and edge.target_scene_id in outgoing:
            outgoing[edge.source_scene_id].append(edge.target_scene_id)
    levels: dict[str, int] = {}
    if start in reachable:
        queue = deque([(start, 0)])
        while queue:
            current, level = queue.popleft()
            if current in levels and levels[current] <= level:
                continue
            levels[current] = level
            for target in sorted(set(outgoing.get(current, ()) )):
                queue.append((target, level + 1))
    unreachable = sorted(set(scene_ids) - set(levels))
    if unreachable:
        unreachable_column = (max(levels.values(), default=-1) + 2)
        for index, scene_id in enumerate(unreachable):
            levels[scene_id] = unreachable_column
    columns: dict[int, list[str]] = {}
    for scene_id, level in levels.items():
        columns.setdefault(level, []).append(scene_id)
    positions: dict[str, tuple[float, float]] = {}
    for level, identifiers in columns.items():
        for row, scene_id in enumerate(sorted(identifiers)):
            positions[scene_id] = (float(level * 260), float(row * 130))
    return positions


__all__ = [
    "GraphEdgeKind",
    "SceneGraphEdge",
    "SceneGraphModel",
    "SceneGraphNode",
    "summarize_condition",
]
