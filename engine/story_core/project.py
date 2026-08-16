"""Project-level static Story/Core representation and validation.

``StoryProject`` is deliberately a definition layer, not a game session.  It
loads all supported story content into isolated immutable envelopes, retains
source provenance and raw authored mappings, and collects diagnostics without
initializing pygame or changing existing runtime loader contracts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .actions import ActionError, ActionReferences, ActionScope, actions_references, parse_actions
from .conditions import ConditionDialect, ConditionError, ConditionSymbols, parse_condition
from .diagnostics import Diagnostic, DiagnosticSeverity, Diagnostics, StoryCoreError
from .index import ContentKind, ProjectIndex, Reference
from .models import (
    AnimationDefinition,
    BattleDefinition,
    EventPoolDefinition,
    ItemDefinition,
    MoveDefinition,
    PlayerProfile,
    SceneDefinition,
    StoryManifest,
    freeze_value,
    thaw_value,
)
from .schema import SchemaRegistry, default_schema_registry
from .source import StorySource, StorySourceError


_UNPARSED_DOCUMENT = object()


@dataclass(frozen=True)
class ProjectSymbols:
    """Static symbols found across authored definitions.

    Flags and variables remain dynamic runtime values.  The index therefore
    distinguishes initial/action-produced declarations from mere condition
    references so validation can offer advice without turning existing stories
    into fatal errors.
    """

    declared_flags: frozenset[str] = frozenset()
    declared_variables: frozenset[str] = frozenset()
    referenced_flags: frozenset[str] = frozenset()
    referenced_variables: frozenset[str] = frozenset()
    referenced_items: frozenset[str] = frozenset()
    fight_flags: frozenset[str] = frozenset()

    @property
    def undeclared_flags(self) -> frozenset[str]:
        return self.referenced_flags - self.declared_flags

    @property
    def undeclared_variables(self) -> frozenset[str]:
        return self.referenced_variables - self.declared_variables


class StoryProjectLoadError(StoryCoreError):
    """A required project source could not be loaded into a project."""

    def __init__(self, message: str, diagnostics: Iterable[Diagnostic] = ()) -> None:
        super().__init__(message)
        self.diagnostics = Diagnostics(diagnostics)


@dataclass
class StoryProject:
    """A coherent, headless view of one story directory.

    Definition collections are type-local mappings: a scene and battle may
    safely share an ID.  ``source_documents`` preserves full semantic YAML
    roots (including unknown fields) for serialization and legacy views.
    """

    source: StorySource
    manifest: StoryManifest
    player_profile: PlayerProfile
    audio_config: Mapping[str, Any]
    scenes: Mapping[str, SceneDefinition]
    items: Mapping[str, ItemDefinition]
    moves: Mapping[str, MoveDefinition]
    battles: Mapping[str, BattleDefinition]
    event_pools: Mapping[str, EventPoolDefinition]
    animations: Mapping[str, AnimationDefinition]
    move_skill_progression: Mapping[str, Any] = field(default_factory=dict)
    source_documents: Mapping[str, Any] = field(default_factory=dict)
    load_diagnostics: Diagnostics = field(default_factory=Diagnostics)
    schema_registry: SchemaRegistry | None = None
    index: ProjectIndex | None = None
    symbols: ProjectSymbols = field(default_factory=ProjectSymbols)

    def __post_init__(self) -> None:
        self.audio_config = _frozen_mapping(self.audio_config)
        self.scenes = _frozen_mapping(self.scenes)
        self.items = _frozen_mapping(self.items)
        self.moves = _frozen_mapping(self.moves)
        self.battles = _frozen_mapping(self.battles)
        self.event_pools = _frozen_mapping(self.event_pools)
        self.animations = _frozen_mapping(self.animations)
        self.move_skill_progression = _frozen_mapping(self.move_skill_progression)
        self.source_documents = MappingProxyType({
            str(path).replace("\\", "/"): freeze_value(data)
            for path, data in self.source_documents.items()
        })
        self.load_diagnostics = self.load_diagnostics.copy()
        if self.schema_registry is None:
            self.schema_registry = default_schema_registry()
        if self.index is None:
            self.index = _build_index(self)

    @property
    def story_root(self) -> Path:
        return self.source.story_root

    @property
    def shared_assets_root(self) -> Path:
        return self.source.shared_assets_root

    @property
    def player(self) -> PlayerProfile:
        """Short compatibility-oriented alias for ``player_profile``."""

        return self.player_profile

    @property
    def audio(self) -> Mapping[str, Any]:
        return self.audio_config

    @property
    def combat_move_config(self) -> dict[str, Any]:
        """Legacy-shaped global move configuration built from raw envelopes."""

        return {
            "moves": [definition.to_mapping() for definition in self.moves.values()],
            "skill_progression": thaw_value(self.move_skill_progression),
        }

    def scene(self, identifier: str) -> SceneDefinition:
        return self._resolve_kind(ContentKind.SCENE, identifier)

    def item(self, identifier: str) -> ItemDefinition:
        return self._resolve_kind(ContentKind.ITEM, identifier)

    def move(self, identifier: str) -> MoveDefinition:
        return self._resolve_kind(ContentKind.MOVE, identifier)

    def battle(self, identifier: str) -> BattleDefinition:
        return self._resolve_kind(ContentKind.BATTLE, identifier)

    def event_pool(self, identifier: str) -> EventPoolDefinition:
        return self._resolve_kind(ContentKind.EVENT_POOL, identifier)

    event = event_pool

    def animation(self, identifier: str) -> AnimationDefinition:
        return self._resolve_kind(ContentKind.ANIMATION, identifier)

    def resolve(self, reference: Reference | str, *, default_kind: ContentKind | str | None = None) -> Any:
        """Resolve a type-qualified reference through the project index."""

        parsed = Reference.parse(reference, default_kind=default_kind)
        assert self.index is not None
        return self.index.resolve(parsed)

    def schema_for(self, content_type: ContentKind | str) -> Any:
        """Return shared authoring metadata for one content category."""

        assert self.schema_registry is not None
        return self.schema_registry.get(content_type)

    def definition_for_source(self, source_path: str | Path) -> Any | None:
        """Return the first definition whose provenance matches ``source_path``."""

        target = Path(source_path)
        if self.index is not None:
            for entry in self.index.entries():
                if entry.source == target:
                    return entry.definition
        for entry in self.iter_definitions():
            if entry.source == target:
                return entry
        return None

    def iter_definitions(self) -> Iterable[Any]:
        yield self.manifest
        yield self.player_profile
        yield from self.scenes.values()
        yield from self.items.values()
        yield from self.moves.values()
        yield from self.battles.values()
        yield from self.event_pools.values()
        yield from self.animations.values()

    def document_mapping(self, relative_path: str | Path) -> Any:
        """Return a fresh YAML-compatible mapping/list for one source file."""

        key = str(relative_path).replace("\\", "/")
        if key not in self.source_documents:
            raise KeyError(f"No project source document named {key!r}")
        return thaw_value(self.source_documents[key])

    def serialize(self) -> dict[str, Any]:
        """Return semantic source-file mappings; see :mod:`serialization`."""

        from .serialization import serialize_project

        return serialize_project(self)

    def legacy_view(self) -> Any:
        """Create the temporary raw-mapping compatibility adapter."""

        from .compat.legacy_views import LegacyProjectView

        return LegacyProjectView(self)

    def validate(self) -> Diagnostics:
        """Collect static diagnostics without executing gameplay or pygame."""

        diagnostics = self.load_diagnostics.copy()
        _validate_manifest(self, diagnostics)
        _validate_audio_config(self, diagnostics)
        _validate_index(self, diagnostics)
        _validate_items(self, diagnostics)
        _validate_moves(self, diagnostics)
        _validate_scenes(self, diagnostics)
        _validate_event_pools(self, diagnostics)
        _validate_animations(self, diagnostics)
        _validate_battles(self, diagnostics)
        _validate_profile(self, diagnostics)
        _validate_symbol_advisories(self, diagnostics)
        return diagnostics

    def _resolve_kind(self, kind: ContentKind, identifier: str) -> Any:
        assert self.index is not None
        return self.index.resolve(Reference(kind, identifier))


def load_story_project(
    story_root: str | Path,
    shared_assets_root: str | Path = "shared_assets",
    *,
    source: StorySource | None = None,
    schema_registry: SchemaRegistry | None = None,
) -> StoryProject:
    """Load all supported static content from a story root without pygame.

    The manifest is required.  Independent optional/malformed documents are
    represented as diagnostics where a useful partial project can still be
    constructed; malformed ``story.yaml`` is intentionally catastrophic.
    """

    story_source = source or StorySource(story_root, shared_assets_root)
    diagnostics = Diagnostics()
    documents: dict[str, Any] = {}

    manifest_document = _required_mapping_document(story_source, "story.yaml", diagnostics)
    if manifest_document is None:
        raise StoryProjectLoadError("Could not load required story.yaml", diagnostics)
    manifest_data, manifest_path = manifest_document
    try:
        manifest = StoryManifest.from_mapping(manifest_data, manifest_path, identifier=story_source.story_root.name)
    except (TypeError, ValueError) as exc:
        diagnostics.error("invalid_manifest", str(exc), source=manifest_path)
        raise StoryProjectLoadError("Could not construct story manifest", diagnostics) from exc
    documents["story.yaml"] = manifest_data

    player_data, player_path, player_document = _optional_mapping_document(story_source, "player.yaml", diagnostics)
    if player_path is not None and player_document is not _UNPARSED_DOCUMENT:
        # Preserve a parseable but malformed root for semantic serialization;
        # the fallback model below is only a safe inspection view.
        documents["player.yaml"] = player_document
    try:
        player = PlayerProfile.from_mapping(player_data, player_path or story_source.story_path("player.yaml"))
    except (TypeError, ValueError) as exc:
        diagnostics.error("invalid_player_profile", str(exc), source=player_path)
        player = PlayerProfile.from_mapping({}, player_path or story_source.story_path("player.yaml"))

    audio_data, audio_path, audio_document = _optional_mapping_document(story_source, "audio.yaml", diagnostics)
    if audio_path is not None and audio_document is not _UNPARSED_DOCUMENT:
        documents["audio.yaml"] = audio_document

    project_index = ProjectIndex()
    project_index.register(ContentKind.MANIFEST, manifest.id, manifest)
    project_index.register(ContentKind.PLAYER, player.id, player)

    item_models = _load_items(story_source, diagnostics, documents, index=project_index)
    scene_models = _load_scenes(story_source, diagnostics, documents, index=project_index)
    move_models, skill_progression = _load_moves(story_source, diagnostics, documents, index=project_index)
    battle_models = _load_battles(story_source, diagnostics, documents, index=project_index)
    event_models = _load_event_pools(story_source, diagnostics, documents, index=project_index)
    animation_models = _load_animations(story_source, diagnostics, documents, index=project_index)

    project = StoryProject(
        source=story_source,
        manifest=manifest,
        player_profile=player,
        audio_config=audio_data,
        scenes=scene_models,
        items=item_models,
        moves=move_models,
        battles=battle_models,
        event_pools=event_models,
        animations=animation_models,
        move_skill_progression=skill_progression,
        source_documents=documents,
        load_diagnostics=diagnostics,
        schema_registry=schema_registry,
        index=project_index,
    )
    project.symbols = _collect_project_symbols(project)
    return project


def _required_mapping_document(
    source: StorySource, relative_path: str, diagnostics: Diagnostics,
) -> tuple[dict[str, Any], Path] | None:
    document, error = source.try_load_document(relative_path)
    if error is not None:
        diagnostics.error(error.code, str(error), source=error.path)
        return None
    assert document is not None
    if not isinstance(document.data, Mapping):
        diagnostics.error("invalid_document_root", f"{relative_path} must contain a mapping", source=document.path)
        return None
    return dict(document.data), document.path


def _optional_mapping_document(
    source: StorySource, relative_path: str, diagnostics: Diagnostics,
) -> tuple[dict[str, Any], Path | None, Any]:
    if not source.has_story_file(relative_path):
        return {}, None, _UNPARSED_DOCUMENT
    document, error = source.try_load_document(relative_path)
    if error is not None:
        diagnostics.error(error.code, str(error), source=error.path)
        return {}, error.path, _UNPARSED_DOCUMENT
    assert document is not None
    if not isinstance(document.data, Mapping):
        diagnostics.error("invalid_document_root", f"{relative_path} must contain a mapping", source=document.path)
        return {}, document.path, document.data
    return dict(document.data), document.path, document.data


def _load_items(
    source: StorySource,
    diagnostics: Diagnostics,
    documents: dict[str, Any],
    *,
    index: ProjectIndex | None = None,
) -> dict[str, ItemDefinition]:
    data, path, document = _optional_mapping_document(source, "items/items.yaml", diagnostics)
    if path is not None and document is not _UNPARSED_DOCUMENT:
        documents["items/items.yaml"] = document
    models: dict[str, ItemDefinition] = {}
    for item_id, raw in data.items():
        if not isinstance(item_id, str) or not item_id:
            diagnostics.error("invalid_item_id", "Item registry keys must be non-empty strings", source=path)
            continue
        if not isinstance(raw, Mapping):
            diagnostics.error("invalid_item_definition", f"Item {item_id!r} must be a mapping", source=path, path=(item_id,))
            continue
        try:
            definition = ItemDefinition.from_mapping(
                raw,
                path or source.story_path("items/items.yaml"),
                identifier=item_id,
                field_path=(item_id,),
            )
            models[item_id] = definition
            if index is not None:
                index.register(ContentKind.ITEM, item_id, definition)
        except (TypeError, ValueError) as exc:
            diagnostics.error("invalid_item_definition", str(exc), source=path, path=(item_id,))
    return models


def _load_scenes(
    source: StorySource,
    diagnostics: Diagnostics,
    documents: dict[str, Any],
    *,
    index: ProjectIndex | None = None,
) -> dict[str, SceneDefinition]:
    models: dict[str, SceneDefinition] = {}
    origins: dict[str, Path] = {}
    for path in source.discover_yaml("scenes", recursive=True):
        relative = source.relative_path(path)
        try:
            data = source.load_yaml_path(path)
        except StorySourceError as exc:
            diagnostics.error(exc.code, str(exc), source=exc.path or path)
            continue
        # Keep every parseable YAML root for semantic serialization, even if
        # it is not a valid scene mapping and therefore cannot make a model.
        documents[relative] = data
        if not isinstance(data, Mapping):
            diagnostics.error("invalid_scene_root", "Scene files must contain mappings", source=path)
            continue
        scene_id = path.stem
        declared = data.get("id")
        if declared and declared != scene_id:
            diagnostics.error(
                "scene_id_mismatch",
                f"Scene declares id {declared!r}, which does not match filename id {scene_id!r}",
                source=path,
                path=("id",),
            )
        try:
            definition = SceneDefinition.from_mapping(data, path, identifier=scene_id)
        except (TypeError, ValueError) as exc:
            diagnostics.error("invalid_scene_definition", str(exc), source=path)
            continue
        if index is not None:
            # Retain every candidate so a typed index can report ambiguity,
            # while ``project.scenes`` remains the first-wins compatibility
            # mapping used by existing raw loader views.
            index.register(ContentKind.SCENE, scene_id, definition)
        if scene_id in models:
            diagnostics.error(
                "duplicate_scene_id",
                f"Scene id {scene_id!r} is ambiguous; also defined by {origins[scene_id]}",
                source=path,
            )
            continue
        models[scene_id] = definition
        origins[scene_id] = path
    return models


def _load_moves(
    source: StorySource, diagnostics: Diagnostics, documents: dict[str, Any], *, index: ProjectIndex | None = None,
) -> tuple[dict[str, MoveDefinition], dict[str, Any]]:
    models: dict[str, MoveDefinition] = {}
    skill_progression: dict[str, Any] = {}
    progression_source: Path | None = None
    for path in source.discover_yaml("moves", recursive=True):
        relative = source.relative_path(path)
        try:
            data = source.load_yaml_path(path)
        except StorySourceError as exc:
            diagnostics.error(exc.code, str(exc), source=exc.path or path)
            continue
        documents[relative] = data
        if isinstance(data, Mapping) and "skill_progression" in data:
            value = data["skill_progression"]
            if not isinstance(value, Mapping):
                diagnostics.error("invalid_skill_progression", "skill_progression must be a mapping", source=path, path=("skill_progression",))
            elif progression_source is not None:
                diagnostics.error(
                    "duplicate_skill_progression", "Only one moves file may define skill_progression",
                    source=path, path=("skill_progression",),
                )
            else:
                skill_progression = dict(value)
                progression_source = path
        wrapped_moves = isinstance(data, Mapping) and "moves" in data
        wrapped_move_mapping = wrapped_moves and isinstance(data.get("moves"), Mapping)
        entries: Any = data.get("moves", []) if wrapped_moves else data
        if isinstance(entries, Mapping) and "id" in entries:
            entries = [entries]
        if not isinstance(entries, list):
            diagnostics.error("invalid_move_root", "Move file must contain a move mapping or moves list", source=path)
            continue
        for entry_index, raw in enumerate(entries):
            if not isinstance(raw, Mapping):
                diagnostics.error("invalid_move_definition", "Move entries must be mappings", source=path, path=("moves", entry_index))
                continue
            move_id = raw.get("id")
            if not isinstance(move_id, str) or not move_id:
                diagnostics.error("invalid_move_id", "Move entries require a non-empty string id", source=path, path=("moves", entry_index, "id"))
                continue
            try:
                if wrapped_move_mapping:
                    field_path = ("moves",)
                elif wrapped_moves:
                    field_path = ("moves", entry_index)
                elif isinstance(data, list):
                    field_path = (entry_index,)
                else:
                    # A single direct move mapping is the complete source
                    # document, not an entry below a synthetic ``moves`` key.
                    field_path = ()
                definition = MoveDefinition.from_mapping(raw, path, identifier=move_id, field_path=field_path)
            except (TypeError, ValueError) as exc:
                diagnostics.error("invalid_move_definition", str(exc), source=path, path=("moves", entry_index))
                continue
            if index is not None:
                index.register(ContentKind.MOVE, move_id, definition)
            if move_id in models:
                diagnostics.error("duplicate_move_id", f"Duplicate move id {move_id!r}", source=path, path=("moves", entry_index, "id"))
                continue
            models[move_id] = definition
    return models, skill_progression


def _load_battles(
    source: StorySource,
    diagnostics: Diagnostics,
    documents: dict[str, Any],
    *,
    index: ProjectIndex | None = None,
) -> dict[str, BattleDefinition]:
    models: dict[str, BattleDefinition] = {}
    for path in source.discover_yaml("battles", recursive=False):
        relative = source.relative_path(path)
        try:
            data = source.load_yaml_path(path)
        except StorySourceError as exc:
            diagnostics.error(exc.code, str(exc), source=exc.path or path)
            continue
        documents[relative] = data
        if not isinstance(data, Mapping):
            diagnostics.error("invalid_battle_root", "Battle files must contain mappings", source=path)
            continue
        battle_id = path.stem
        try:
            definition = BattleDefinition.from_mapping(data, path, identifier=battle_id)
        except (TypeError, ValueError) as exc:
            diagnostics.error("invalid_battle_definition", str(exc), source=path)
            continue
        if index is not None:
            index.register(ContentKind.BATTLE, battle_id, definition)
        if battle_id in models:
            diagnostics.error("duplicate_battle_id", f"Duplicate battle id {battle_id!r}", source=path)
            continue
        models[battle_id] = definition
    return models


def _load_event_pools(
    source: StorySource,
    diagnostics: Diagnostics,
    documents: dict[str, Any],
    *,
    index: ProjectIndex | None = None,
) -> dict[str, EventPoolDefinition]:
    models: dict[str, EventPoolDefinition] = {}
    for path in source.discover_yaml("events", recursive=False):
        relative = source.relative_path(path)
        try:
            data = source.load_yaml_path(path)
        except StorySourceError as exc:
            diagnostics.error(exc.code, str(exc), source=exc.path or path)
            continue
        documents[relative] = data
        if not isinstance(data, Mapping):
            diagnostics.error("invalid_event_pool_root", "Event pool files must contain mappings", source=path)
            continue
        pool_id = path.stem
        try:
            definition = EventPoolDefinition.from_mapping(data, path, identifier=pool_id)
        except (TypeError, ValueError) as exc:
            diagnostics.error("invalid_event_pool_definition", str(exc), source=path)
            continue
        if index is not None:
            index.register(ContentKind.EVENT_POOL, pool_id, definition)
        if pool_id in models:
            diagnostics.error("duplicate_event_pool_id", f"Duplicate event pool id {pool_id!r}", source=path)
            continue
        models[pool_id] = definition
    return models


def _load_animations(
    source: StorySource,
    diagnostics: Diagnostics,
    documents: dict[str, Any],
    *,
    index: ProjectIndex | None = None,
) -> dict[str, AnimationDefinition]:
    models: dict[str, AnimationDefinition] = {}
    animation_root = source.story_path("assets/animations")
    for path in source.discover_yaml("assets/animations", recursive=True):
        if path.name != "anim.yaml":
            continue
        relative = source.relative_path(path)
        try:
            data = source.load_yaml_path(path)
        except StorySourceError as exc:
            diagnostics.error(exc.code, str(exc), source=exc.path or path)
            continue
        documents[relative] = data
        if not isinstance(data, Mapping):
            diagnostics.error("invalid_animation_root", "Animation files must contain mappings", source=path)
            continue
        animation_id = path.parent.relative_to(animation_root).as_posix()
        try:
            definition = AnimationDefinition.from_mapping(data, path, identifier=animation_id)
        except (TypeError, ValueError) as exc:
            diagnostics.error("invalid_animation_definition", str(exc), source=path)
            continue
        if index is not None:
            index.register(ContentKind.ANIMATION, animation_id, definition)
        if animation_id in models:
            diagnostics.error("duplicate_animation_id", f"Duplicate animation id {animation_id!r}", source=path)
            continue
        models[animation_id] = definition

    # Shared animations are fallback definitions, not story documents.  They
    # are discoverable for tooling but never emitted by story serialization.
    shared_root = source.shared_assets_root / "animations"
    if shared_root.is_dir():
        for path in sorted(shared_root.rglob("anim.yaml")):
            animation_id = path.parent.relative_to(shared_root).as_posix()
            if animation_id in models:
                continue
            try:
                data = source.load_yaml_path(path)
            except StorySourceError as exc:
                diagnostics.error(exc.code, str(exc), source=exc.path or path)
                continue
            if not isinstance(data, Mapping):
                diagnostics.error("invalid_animation_root", "Animation files must contain mappings", source=path)
                continue
            try:
                definition = AnimationDefinition.from_mapping(data, path, identifier=animation_id)
            except (TypeError, ValueError) as exc:
                diagnostics.error("invalid_animation_definition", str(exc), source=path)
                continue
            models[animation_id] = definition
            if index is not None:
                index.register(ContentKind.ANIMATION, animation_id, definition)
    return models


def _build_index(project: StoryProject) -> ProjectIndex:
    index = ProjectIndex()
    index.register(ContentKind.MANIFEST, project.manifest.id, project.manifest)
    index.register(ContentKind.PLAYER, project.player_profile.id, project.player_profile)
    for kind, definitions in (
        (ContentKind.SCENE, project.scenes),
        (ContentKind.ITEM, project.items),
        (ContentKind.MOVE, project.moves),
        (ContentKind.BATTLE, project.battles),
        (ContentKind.EVENT_POOL, project.event_pools),
        (ContentKind.ANIMATION, project.animations),
    ):
        for identifier, definition in definitions.items():
            index.register(kind, identifier, definition)
    return index


def _collect_project_symbols(project: StoryProject) -> ProjectSymbols:
    declared_flags = _mapping_string_keys(project.manifest.starting_flags)
    declared_variables = _mapping_string_keys(project.manifest.starting_variables)
    referenced_flags: set[str] = set()
    referenced_variables: set[str] = set()
    referenced_items: set[str] = set()
    fight_flags: set[str] = set()

    def add_condition(raw: Any, *, dialect: ConditionDialect | str = "auto") -> None:
        nonlocal referenced_flags, referenced_variables, referenced_items
        try:
            symbols = parse_condition(raw, dialect=dialect).symbols
        except ConditionError:
            return
        referenced_flags.update(symbols.flags)
        referenced_variables.update(symbols.variables)
        referenced_items.update(symbols.items)

    def add_actions(raw: Any, scope: ActionScope) -> None:
        nonlocal declared_flags, declared_variables, referenced_items, fight_flags
        try:
            references = actions_references(parse_actions(raw, scope))
        except ActionError:
            return
        declared_flags.update(references.flags)
        declared_variables.update(references.variables)
        referenced_items.update(references.items)
        fight_flags.update(references.fight_flags)

    def add_availability_symbols(availability: Mapping[str, Any]) -> None:
        """Keep persistent and per-fight availability gates separate."""

        referenced_flags.update(_mapping_string_keys(availability.get("requires_flags")))
        fight_flags.update(_mapping_string_keys(availability.get("requires_fight_flags")))

    for scene in project.scenes.values():
        raw = scene.to_mapping()
        visit_flag = _exploration_visit_flag(raw)
        if visit_flag is not None:
            declared_flags.add(visit_flag)
        add_actions(raw.get("actions"), ActionScope.STORY)
        choices = raw.get("choices", [])
        if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes, Mapping)):
            for choice in choices:
                if isinstance(choice, Mapping):
                    # Ordinary scene choices are evaluated by the legacy
                    # string evaluator; structured mappings belong only to
                    # exploration content.
                    add_condition(choice.get("condition"), dialect=ConditionDialect.LEGACY)
                    add_actions(choice.get("actions"), ActionScope.STORY)
        _scan_exploration_symbols(raw, add_condition, add_actions)

    for move in project.moves.values():
        raw = move.to_mapping()
        for availability, _path in _move_availability_mappings(raw):
            add_availability_symbols(availability)
        for condition, _path in _move_availability_conditions(raw):
            add_condition(condition, dialect=ConditionDialect.LEGACY)

    for item in project.items.values():
        raw = item.to_mapping()
        use = raw.get("use")
        if isinstance(use, Mapping):
            add_actions(use.get("actions"), ActionScope.INVENTORY_USE)
        combat = raw.get("combat")
        if isinstance(combat, Mapping):
            effects = combat.get("effects", ())
            if isinstance(effects, Sequence) and not isinstance(effects, (str, bytes, Mapping)):
                for effect in effects:
                    if isinstance(effect, Mapping):
                        fight_flags.update(_mapping_string_keys(effect.get("set_fight_flag")))
    for battle in project.battles.values():
        raw = battle.to_mapping()
        for availability, _path in _battle_availability_mappings(raw):
            add_availability_symbols(availability)
        for condition, _path in _battle_availability_conditions(raw):
            add_condition(condition, dialect=ConditionDialect.LEGACY)
        phases = raw.get("phases", [])
        if isinstance(phases, Sequence) and not isinstance(phases, (str, bytes, Mapping)):
            for phase in phases:
                if isinstance(phase, Mapping):
                    add_actions(phase.get("actions"), ActionScope.BATTLE)
                    when = phase.get("when")
                    if isinstance(when, Mapping):
                        flag = when.get("fight_flag")
                        if isinstance(flag, str) and flag:
                            fight_flags.add(flag)
    return ProjectSymbols(
        frozenset(declared_flags), frozenset(declared_variables),
        frozenset(referenced_flags), frozenset(referenced_variables),
        frozenset(referenced_items), frozenset(fight_flags),
    )


def _scan_exploration_symbols(
    scene: Mapping[str, Any],
    add_condition: Any,
    add_actions: Any,
) -> None:
    for condition, _path in _exploration_condition_values(scene):
        add_condition(condition)
    raw = scene.get("exploration")
    if raw is None or raw is False:
        return
    if raw is not True and not isinstance(raw, Mapping):
        return
    config = raw if isinstance(raw, Mapping) else {}
    for _path, actions in _exploration_action_sets(config, scene=scene):
        add_actions(actions, ActionScope.EXPLORATION)


def _exploration_condition_values(
    scene: Mapping[str, Any],
) -> Iterable[tuple[Any, tuple[str | int, ...]]]:
    """Yield every runtime-evaluated exploration condition and source path."""

    raw = scene.get("exploration")
    if raw is None or raw is False:
        return
    if raw is not True and not isinstance(raw, Mapping):
        return
    config = raw if isinstance(raw, Mapping) else {}
    for key in ("dialog", "navigation", "objects", "look_regions"):
        entries, section_path = _exploration_section(config, key, scene=scene, default=[])
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, Mapping)):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                continue
            entry_path = (*section_path, index)
            for condition_key in ("conditions", "condition", "visible_when"):
                if condition_key in entry:
                    yield entry[condition_key], (*entry_path, condition_key)

            # Object looks are explicitly nested while a region may use its
            # own fields as the look specification.  ``_resolved_look_spec``
            # evaluates only ``conditions``/``condition`` on each state.
            if key not in {"objects", "look_regions"}:
                continue
            if key == "objects":
                look = entry.get("look")
                look_path = (*entry_path, "look")
            else:
                has_nested_look = isinstance(entry.get("look"), Mapping)
                look = entry["look"] if has_nested_look else entry
                look_path = (*entry_path, "look") if has_nested_look else entry_path
            if not isinstance(look, Mapping):
                continue
            states = look.get("states")
            if not isinstance(states, Sequence) or isinstance(states, (str, bytes, Mapping)):
                continue
            for state_index, state in enumerate(states):
                if not isinstance(state, Mapping):
                    continue
                for condition_key in ("conditions", "condition"):
                    if condition_key in state:
                        yield state[condition_key], (*look_path, "states", state_index, condition_key)


def _exploration_section(
    config: Mapping[str, Any],
    key: str,
    *,
    scene: Mapping[str, Any] | None = None,
    default: Any = None,
) -> tuple[Any, tuple[str | int, ...]]:
    """Return one runtime exploration section with its authored YAML path.

    ``exploration_config`` accepts root-level aliases when opt-in exploration
    is enabled.  Validation receives its merged mapping in a few call sites,
    so consult the original scene first to avoid reporting an alias as if it
    had been written under ``exploration``.
    """

    if scene is not None:
        raw = scene.get("exploration")
        if isinstance(raw, Mapping) and key in raw:
            return raw[key], ("exploration", key)
        if key in scene:
            return scene[key], (key,)
    return config.get(key, default), ("exploration", key)


def _exploration_visit_flag(scene: Mapping[str, Any]) -> str | None:
    """Return the persistent flag GameEngine sets on exploration entry."""

    raw = scene.get("exploration")
    if not isinstance(raw, Mapping):
        return None
    value = raw.get("visit_flag")
    return value if isinstance(value, str) and value else None


def _exploration_action_sets(
    config: Mapping[str, Any],
    *,
    scene: Mapping[str, Any] | None = None,
) -> Iterable[tuple[tuple[str | int, ...], Any]]:
    """Yield every exploration action list that can reach an EventRunner."""

    events, events_path = _exploration_section(config, "look_events", scene=scene, default={})
    if isinstance(events, Mapping):
        for event_id, event in events.items():
            if isinstance(event, Mapping) and "actions" in event:
                yield from _nested_exploration_action_sets(
                    (*events_path, str(event_id), "actions"), event["actions"],
                )
    for actions_path, actions in _exploration_dialogue_action_sets(config, scene=scene):
        yield from _nested_exploration_action_sets(actions_path, actions)


def _nested_exploration_action_sets(
    actions_path: tuple[str | int, ...],
    actions: Any,
    ancestors: frozenset[int] = frozenset(),
) -> Iterable[tuple[tuple[str | int, ...], Any]]:
    """Yield an action list plus inline-dialogue follow-up action lists."""

    if id(actions) in ancestors:
        return
    yield actions_path, actions
    if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes, Mapping)):
        return
    next_ancestors = ancestors | frozenset({id(actions)})
    for index, action in enumerate(actions):
        if not isinstance(action, Mapping):
            continue
        for nested_path, nested_actions in _embedded_dialogue_action_sets(action, (*actions_path, index)):
            yield from _nested_exploration_action_sets(nested_path, nested_actions, next_ancestors)


def _embedded_dialogue_action_sets(
    action: Mapping[str, Any],
    action_path: tuple[str | int, ...],
) -> Iterable[tuple[tuple[str | int, ...], Any]]:
    """Find actions embedded in a typed or legacy inline ``dialog`` action."""

    reference: Any = None
    reference_key: str | None = None
    if action.get("type") == "dialog":
        if "dialog" in action:
            reference, reference_key = action["dialog"], "dialog"
        elif "sequence" in action:
            reference, reference_key = action["sequence"], "sequence"
    elif len(action) == 1:
        key, value = next(iter(action.items()))
        if key == "dialog":
            reference, reference_key = value, "dialog"
    if isinstance(reference, Mapping) and "actions" in reference and reference_key is not None:
        yield (*action_path, reference_key, "actions"), reference["actions"]


def _exploration_dialogue_action_sets(
    config: Mapping[str, Any],
    *,
    scene: Mapping[str, Any] | None = None,
) -> Iterable[tuple[tuple[str | int, ...], Any]]:
    """Yield event-action lists that execute after exploration dialogue.

    The normal exploration preflight already establishes the outer dialogue
    shapes.  Keeping these paths explicit lets Core validate/symbol-index the
    deferred EventRunner actions without confusing them with legacy scene
    actions.
    """

    sequences, sequences_path = _exploration_section(config, "dialogue_sequences", scene=scene, default={})
    if isinstance(sequences, Mapping):
        for sequence_id, sequence in sequences.items():
            if isinstance(sequence, Mapping) and "actions" in sequence:
                yield (*sequences_path, str(sequence_id), "actions"), sequence["actions"]
    dialog, dialog_path = _exploration_section(config, "dialog", scene=scene, default=[])
    if isinstance(dialog, Sequence) and not isinstance(dialog, (str, bytes, Mapping)):
        for index, entry in enumerate(dialog):
            # resolve_dialogue gives sequence/dialog precedence over inline
            # text, so only this selected inline form can execute entry.actions.
            if (
                isinstance(entry, Mapping)
                and "text" in entry
                and "sequence" not in entry
                and "dialog" not in entry
                and "actions" in entry
            ):
                yield (*dialog_path, index, "actions"), entry["actions"]
            if not isinstance(entry, Mapping):
                continue
            # A dialog entry may itself contain an inline dialogue mapping.
            # Runtime resolves it through ``_dialogue_from_reference`` and
            # executes its actions after the text just like a named sequence.
            for reference_key in ("sequence", "dialog"):
                if reference_key in entry and isinstance(entry[reference_key], Mapping) and "actions" in entry[reference_key]:
                    yield (*dialog_path, index, reference_key, "actions"), entry[reference_key]["actions"]


def _move_availability_mappings(
    raw: Mapping[str, Any],
) -> Iterable[tuple[Mapping[str, Any], tuple[str | int, ...]]]:
    """Yield availability blocks across a global move and its overlays."""

    for move, prefix in ((raw, ()), (raw.get("common"), ("common",))):
        if isinstance(move, Mapping) and isinstance(move.get("availability"), Mapping):
            yield move["availability"], (*prefix, "availability")
    levels = raw.get("difficulty_levels")
    if isinstance(levels, Mapping):
        for level, entry in levels.items():
            if isinstance(entry, Mapping) and isinstance(entry.get("availability"), Mapping):
                yield entry["availability"], ("difficulty_levels", level, "availability")


def _move_availability_conditions(raw: Mapping[str, Any]) -> Iterable[tuple[Any, tuple[str | int, ...]]]:
    """Yield legacy condition fields across global move overlays."""

    for availability, prefix in _move_availability_mappings(raw):
        if "condition" in availability:
            yield availability["condition"], (*prefix, "condition")


def _battle_availability_mappings(
    raw: Mapping[str, Any],
) -> Iterable[tuple[Mapping[str, Any], tuple[str | int, ...]]]:
    """Yield availability blocks from modern and legacy battle move forms."""

    for collection_key in ("player_moves", "enemy_moves"):
        entries = raw.get(collection_key, [])
        if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes, Mapping)):
            for index, entry in enumerate(entries):
                if isinstance(entry, Mapping) and isinstance(entry.get("availability"), Mapping):
                    yield entry["availability"], (collection_key, index, "availability")
    enemy = raw.get("enemy")
    if isinstance(enemy, Mapping):
        entries = enemy.get("moves", [])
        if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes, Mapping)):
            for index, entry in enumerate(entries):
                if isinstance(entry, Mapping) and isinstance(entry.get("availability"), Mapping):
                    yield entry["availability"], ("enemy", "moves", index, "availability")


def _battle_availability_conditions(raw: Mapping[str, Any]) -> Iterable[tuple[Any, tuple[str | int, ...]]]:
    """Yield legacy availability condition fields from battle move forms."""

    for availability, prefix in _battle_availability_mappings(raw):
        if "condition" in availability:
            yield availability["condition"], (*prefix, "condition")


def _validate_availability_requirements(
    diagnostics: Diagnostics,
    availability: Mapping[str, Any],
    source: Path,
    prefix: tuple[str | int, ...],
) -> None:
    """Validate requirement maps read directly by BattleController.

    The controller deliberately boolean-coerces requirement values, so Core
    does the same by leaving values permissive.  It does require a mapping
    whose keys can serve as authored flag identifiers; a scalar would reach
    ``.items()`` at runtime and crash a battle.
    """

    for requirement in ("requires_flags", "requires_fight_flags"):
        if requirement not in availability:
            continue
        values = availability[requirement]
        field_path = (*prefix, requirement)
        if not isinstance(values, Mapping):
            diagnostics.error(
                "invalid_availability_requirement",
                f"availability.{requirement} must be a mapping",
                source=source,
                path=field_path,
            )
            continue
        for flag in values:
            if not isinstance(flag, str) or not flag:
                diagnostics.error(
                    "invalid_availability_requirement",
                    f"availability.{requirement} keys must be non-empty strings",
                    source=source,
                    path=field_path,
                )
                break


def _validate_manifest(project: StoryProject, diagnostics: Diagnostics) -> None:
    raw = project.manifest.to_mapping()
    start = project.manifest.start_scene
    if not isinstance(start, str) or not start:
        diagnostics.error("missing_start_scene", "Story manifest requires a start_scene", source=project.manifest.source, path=("start_scene",))
    elif start not in project.scenes:
        diagnostics.error("unknown_scene_reference", f"Unknown start scene {start!r}", source=project.manifest.source, path=("start_scene",))

    # Keep this tiny structural check pure instead of importing the renderer.
    # It mirrors ``render.display.parse_display_config`` closely enough to
    # diagnose bad authored canvas dimensions before pygame startup.
    display = raw.get("display")
    if not isinstance(display, Mapping):
        diagnostics.error(
            "invalid_display_config",
            "story.yaml requires display.width and display.height for the pygame logical canvas",
            source=project.manifest.source,
            path=("display",),
        )
        return
    for dimension in ("width", "height"):
        value = display.get(dimension)
        if isinstance(value, bool) or not isinstance(value, int):
            diagnostics.error(
                "invalid_display_config",
                "display.width and display.height must be positive integers",
                source=project.manifest.source,
                path=("display", dimension),
            )
        elif value <= 0:
            diagnostics.error(
                "invalid_display_config",
                "display.width and display.height must be greater than zero",
                source=project.manifest.source,
                path=("display", dimension),
            )


def _validate_audio_config(project: StoryProject, diagnostics: Diagnostics) -> None:
    """Validate numeric audio preferences without importing AudioSystem."""

    source = project.source.story_path("audio.yaml")
    for key in ("master_volume", "music_volume", "effects_volume"):
        value = project.audio_config.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            diagnostics.error(
                "invalid_audio_volume",
                f"audio.{key} must be a number",
                source=source,
                path=(key,),
            )
        elif value < 0 or value > 1:
            # AudioSystem intentionally clamps these values.  Keep that
            # compatibility while making the designer-visible coercion clear.
            diagnostics.warning(
                "audio_volume_clamped",
                f"audio.{key} is outside 0..1 and will be clamped by AudioSystem",
                source=source,
                path=(key,),
            )


def _validate_index(project: StoryProject, diagnostics: Diagnostics) -> None:
    assert project.index is not None
    for reference, entries in project.index.duplicates.items():
        diagnostics.error(
            "duplicate_definition",
            f"Duplicate {reference.kind.value} id {reference.identifier!r}",
            source=entries[-1].source,
        )


def _validate_items(project: StoryProject, diagnostics: Diagnostics) -> None:
    try:
        from engine.core.inventory import InventorySchemaError, normalize_item_definition
    except ImportError:  # pragma: no cover - source tree always has the seam
        return
    for item in project.items.values():
        raw = item.to_mapping()
        try:
            normalized = normalize_item_definition(item.id, raw)
        except InventorySchemaError as exc:
            diagnostics.error("invalid_item_definition", str(exc), source=item.source)
            continue
        if normalized.icon:
            _validate_asset(project, diagnostics, normalized.icon, "items", item.source, ("icon",))
        combat = raw.get("combat")
        if isinstance(combat, Mapping) and "move_grants" in combat:
            grants = combat["move_grants"]
            if not isinstance(grants, list):
                diagnostics.error("invalid_move_grants", "combat.move_grants must be a list", source=item.source, path=("combat", "move_grants"))
            else:
                for index, move_id in enumerate(grants):
                    if not isinstance(move_id, str) or not move_id:
                        diagnostics.error("invalid_move_reference", "Move grants must be non-empty string IDs", source=item.source, path=("combat", "move_grants", index))
                    elif move_id not in project.moves:
                        diagnostics.error("unknown_move_reference", f"Unknown move {move_id!r}", source=item.source, path=("combat", "move_grants", index))


def _validate_moves(project: StoryProject, diagnostics: Diagnostics) -> None:
    try:
        from engine.battle.move_progression import normal_difficulty_levels, resolve_combat_move
        from engine.errors import BattleConfigError
    except ImportError:  # pragma: no cover
        return
    for move in project.moves.values():
        raw = move.to_mapping()
        for availability, path in _move_availability_mappings(raw):
            _validate_availability_requirements(diagnostics, availability, move.source, path)
        for condition, path in _move_availability_conditions(raw):
            _validate_condition_value(
                diagnostics,
                condition,
                move.source,
                path,
                dialect=ConditionDialect.LEGACY,
            )
        try:
            levels = normal_difficulty_levels(raw)
            selected = set(levels)
            raw_levels = raw.get("difficulty_levels")
            if isinstance(raw_levels, Mapping) and 0 in raw_levels:
                selected.add(0)
            for level in sorted(selected):
                resolve_combat_move(raw, level)
        except BattleConfigError as exc:
            diagnostics.error("invalid_move_definition", str(exc), source=move.source)


def _validate_scenes(project: StoryProject, diagnostics: Diagnostics) -> None:
    for scene in project.scenes.values():
        raw = scene.to_mapping()
        _validate_scene_media(project, diagnostics, scene, raw)
        _validate_scene_choices(project, diagnostics, scene, raw)
        _validate_scene_actions(project, diagnostics, scene, raw)
        _validate_scene_conditions(diagnostics, scene, raw)
        # ``SceneDefinition.exploration`` is a typed projection and turns an
        # invalid shape into ``None``.  Inspect the preserved authored raw
        # field here so Core diagnoses the same malformed value that
        # AssetLoader's exploration preflight rejects.
        if "exploration" in raw and raw["exploration"] is not None:
            _validate_exploration(project, diagnostics, scene, raw)


def _validate_scene_media(project: StoryProject, diagnostics: Diagnostics, scene: SceneDefinition, raw: Mapping[str, Any]) -> None:
    # Standard-scene media has historically been late-bound and permissive;
    # preserving that contract means a missing legacy asset is useful warning
    # information rather than a project-fatal error.  Exploration media is
    # already preflighted by the current runtime and remains an error.
    media_severity = DiagnosticSeverity.ERROR if scene.is_exploration else DiagnosticSeverity.WARNING
    for key, category in (("background", "backgrounds"), ("sprite", "sprites")):
        value = raw.get(key)
        if isinstance(value, str) and value:
            _validate_asset(
                project,
                diagnostics,
                value,
                category,
                scene.source,
                (key,),
                severity=media_severity,
                # Exploration media uses the same image-only asset rules as
                # the renderer.
                category_only=not scene.is_exploration,
            )
    value = raw.get("music")
    if isinstance(value, str) and value:
        _validate_audio_asset(project, diagnostics, value, scene.source, ("music",), severity=media_severity)
    value = raw.get("animation")
    if isinstance(value, str) and value and value not in project.animations:
        diagnostics.error("unknown_animation_reference", f"Unknown animation {value!r}", source=scene.source, path=("animation",))


def _validate_scene_choices(project: StoryProject, diagnostics: Diagnostics, scene: SceneDefinition, raw: Mapping[str, Any]) -> None:
    choices = raw.get("choices", [])
    if choices is None:
        return
    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes, Mapping)):
        diagnostics.warning("invalid_choices_shape", "Scene choices are expected to be a list", source=scene.source, path=("choices",))
        return
    for index, choice in enumerate(choices):
        if not isinstance(choice, Mapping):
            diagnostics.warning("invalid_choice", "Scene choices are expected to be mappings", source=scene.source, path=("choices", index))
            continue
        # Match StoryInterpreter.resolve_choice exactly: battle wins over a
        # stale random_event/goto field, then random_event wins over goto.
        # Lower-priority legacy fields are intentionally ignored at runtime
        # and must not make an otherwise playable choice fatal in Core.
        if "battle" in choice:
            _validate_reference_value(
                project, diagnostics, choice.get("battle"), ContentKind.BATTLE,
                scene.source, ("choices", index, "battle"), required=True,
            )
            for key in ("on_win", "on_lose"):
                _validate_reference_value(project, diagnostics, choice.get(key), ContentKind.SCENE, scene.source, ("choices", index, key))
        elif "random_event" in choice:
            _validate_reference_value(
                project, diagnostics, choice.get("random_event"), ContentKind.EVENT_POOL,
                scene.source, ("choices", index, "random_event"), required=True,
            )
        elif "goto" in choice:
            _validate_reference_value(
                project, diagnostics, choice.get("goto"), ContentKind.SCENE,
                scene.source, ("choices", index, "goto"), required=True,
            )
        else:
            diagnostics.error(
                "missing_choice_transition",
                "Choice requires one of battle, random_event, or goto",
                source=scene.source,
                path=("choices", index),
            )


def _validate_scene_actions(project: StoryProject, diagnostics: Diagnostics, scene: SceneDefinition, raw: Mapping[str, Any]) -> None:
    for location, actions in _scene_action_sets(raw):
        # ``exploration_config`` supports root aliases when ``exploration:
        # true`` is authored.  Their source-qualified path deliberately
        # starts at the root (for truthful diagnostics), but their actions
        # still execute through EventRunner rather than StoryInterpreter.
        scope = (
            ActionScope.EXPLORATION
            if location and location[0] in {"exploration", "dialog", "dialogue_sequences", "look_events"}
            else ActionScope.STORY
        )
        try:
            references = actions_references(parse_actions(actions, scope))
        except ActionError as exc:
            if scope is ActionScope.STORY:
                diagnostics.error("invalid_story_action", str(exc), source=scene.source, path=location)
            else:
                diagnostics.warning("malformed_action", str(exc), source=scene.source, path=location)
            continue
        _validate_action_references(project, diagnostics, references, scene.source, location)


def _validate_scene_conditions(diagnostics: Diagnostics, scene: SceneDefinition, raw: Mapping[str, Any]) -> None:
    choices = raw.get("choices", [])
    if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes, Mapping)):
        for index, choice in enumerate(choices):
            if isinstance(choice, Mapping) and "condition" in choice:
                _validate_condition_value(
                    diagnostics,
                    choice["condition"],
                    scene.source,
                    ("choices", index, "condition"),
                    dialect=ConditionDialect.LEGACY,
                )
    _scan_scene_condition_values(raw, lambda value, path: _validate_condition_value(diagnostics, value, scene.source, path))


def _validate_exploration(project: StoryProject, diagnostics: Diagnostics, scene: SceneDefinition, raw: Mapping[str, Any]) -> None:
    try:
        from engine.core.exploration import (
            _validate_event_action,
            exploration_config,
            validate_exploration_scene,
        )
        from engine.errors import StoryValidationError
    except ImportError:  # pragma: no cover
        return
    try:
        validate_exploration_scene(
            raw, scene.id, known_scene_ids=set(project.scenes), known_battle_ids=set(project.battles), item_ids=set(project.items),
        )
    except StoryValidationError as exc:
        diagnostics.error("invalid_exploration_scene", str(exc), source=scene.source, path=("exploration",))
        return
    config = exploration_config(raw) or {}
    # Match the existing preflight asset behavior, including story-relative
    # references that AudioSystem cannot always play later.
    for name, filename in _exploration_asset_references(config, scene=raw):
        category, field_path = name
        if category in {"music", "sfx"}:
            _validate_audio_asset(project, diagnostics, filename, scene.source, field_path)
        elif category == "animation":
            if filename not in project.animations:
                diagnostics.error("unknown_animation_reference", f"Unknown animation {filename!r}", source=scene.source, path=field_path)
        else:
            _validate_asset(
                project,
                diagnostics,
                filename,
                category,
                scene.source,
                field_path,
            )
    events = config.get("look_events", {})
    sequences = config.get("dialogue_sequences", {})
    objects = config.get("objects", [])
    object_ids = {
        value["id"]
        for value in objects
        if isinstance(value, Mapping) and isinstance(value.get("id"), str)
    }
    event_ids = set(events) if isinstance(events, Mapping) else set()
    # Named dialogue, opening dialogue, look events, and inline dialog
    # payloads all eventually execute through EventRunner.  Validate each
    # list here rather than treating only look-event actions as executable.
    for actions_path, actions in _exploration_action_sets(config, scene=raw):
        if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes, Mapping)):
            continue
        for index, action in enumerate(actions):
            try:
                _validate_event_action(
                    action,
                    "exploration dialogue action",
                    sequences if isinstance(sequences, Mapping) else {},
                    object_ids,
                    event_ids,
                    set(project.items),
                    set(project.scenes),
                )
            except StoryValidationError as exc:
                diagnostics.error(
                    "invalid_exploration_action",
                    str(exc),
                    source=scene.source,
                    path=(*actions_path, index),
                )


def _validate_event_pools(project: StoryProject, diagnostics: Diagnostics) -> None:
    for pool in project.event_pools.values():
        raw = pool.to_mapping()
        events = raw.get("events", [])
        if not isinstance(events, Sequence) or isinstance(events, (str, bytes, Mapping)):
            diagnostics.error("invalid_event_pool", "Event pool events must be a list", source=pool.source, path=("events",))
            continue
        chance = raw.get("chance", 0.0)
        if isinstance(chance, bool) or not isinstance(chance, (int, float)):
            diagnostics.error(
                "invalid_event_chance",
                "Event pool chance must be a number",
                source=pool.source,
                path=("chance",),
            )
        for index, event in enumerate(events):
            if not isinstance(event, Mapping):
                diagnostics.error("invalid_event_entry", "Event entries must be mappings", source=pool.source, path=("events", index))
                continue
            _validate_reference_value(project, diagnostics, event.get("id"), ContentKind.SCENE, pool.source, ("events", index, "id"))
            weight = event.get("weight", 1)
            if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                diagnostics.error(
                    "invalid_event_weight",
                    "Event entry weight must be a number",
                    source=pool.source,
                    path=("events", index, "weight"),
                )


def _validate_animations(project: StoryProject, diagnostics: Diagnostics) -> None:
    for animation in project.animations.values():
        raw = animation.to_mapping()
        frames = raw.get("frames", [])
        if not isinstance(frames, list):
            diagnostics.error("invalid_animation_frames", "Animation frames must be a list", source=animation.source, path=("frames",))
            continue
        delay = raw.get("frame_delay_ms", 300)
        if isinstance(delay, bool) or not isinstance(delay, int):
            diagnostics.error(
                "invalid_animation_frame_delay",
                "Animation frame_delay_ms must be an integer",
                source=animation.source,
                path=("frame_delay_ms",),
            )
        for index, frame in enumerate(frames):
            if not isinstance(frame, str) or not frame:
                diagnostics.error("invalid_animation_frame", "Animation frames must be non-empty strings", source=animation.source, path=("frames", index))
                continue
            if not project.source.is_image_asset(frame):
                diagnostics.error(
                    "unsupported_animation_frame",
                    f"Animation frame {frame!r} is not a supported image asset",
                    source=animation.source,
                    path=("frames", index),
                )
                continue
            if not (animation.directory / frame).exists():
                diagnostics.error("missing_asset", f"Animation frame {frame!r} does not exist", source=animation.source, path=("frames", index))


def _validate_battles(project: StoryProject, diagnostics: Diagnostics) -> None:
    try:
        from engine.battle.config import load_battle_config
        from engine.errors import BattleConfigError
    except ImportError:  # pragma: no cover
        return
    item_data = {identifier: definition.to_mapping() for identifier, definition in project.items.items()}
    move_config = project.combat_move_config

    def sprite_exists(filename: str) -> bool:
        if not isinstance(filename, str) or not filename:
            return False
        try:
            candidate = filename.replace("\\", "/")
            if candidate.startswith("assets/sprites/") or candidate.startswith("sprites/"):
                project.source.resolve_asset_reference(candidate, "sprites")
            else:
                project.source.resolve_asset_path("sprites", candidate)
            return True
        except StorySourceError:
            return False

    for battle in project.battles.values():
        raw = battle.to_mapping()
        for availability, path in _battle_availability_mappings(raw):
            _validate_availability_requirements(diagnostics, availability, battle.source, path)
        for condition, path in _battle_availability_conditions(raw):
            _validate_condition_value(
                diagnostics,
                condition,
                battle.source,
                path,
                dialect=ConditionDialect.LEGACY,
            )
        try:
            load_battle_config(
                raw, item_data, source=str(battle.source), moves=move_config,
                skill_progression=thaw_value(project.move_skill_progression), sprite_exists=sprite_exists,
            )
        except BattleConfigError as exc:
            diagnostics.error("invalid_battle_definition", str(exc), source=battle.source)
        enemy = raw.get("enemy")
        if isinstance(enemy, Mapping) and isinstance(enemy.get("sprite"), str) and enemy["sprite"]:
            _validate_asset(
                project, diagnostics, enemy["sprite"], "sprites", battle.source,
                ("enemy", "sprite"), category_only=True,
            )
        for key, category in (("background", "backgrounds"),):
            if isinstance(raw.get(key), str) and raw[key]:
                _validate_asset(project, diagnostics, raw[key], category, battle.source, (key,), category_only=True)
        if isinstance(raw.get("music"), str) and raw["music"]:
            _validate_audio_asset(project, diagnostics, raw["music"], battle.source, ("music",), severity=DiagnosticSeverity.WARNING)


def _validate_profile(project: StoryProject, diagnostics: Diagnostics) -> None:
    profile = project.player_profile
    raw = profile.to_mapping()
    inventory = raw.get("inventory", [])
    if isinstance(inventory, Mapping) and ({"columns", "rows"} & set(inventory)):
        inventory = inventory.get("items", [])
    identifiers = inventory.keys() if isinstance(inventory, Mapping) else inventory if isinstance(inventory, Sequence) and not isinstance(inventory, (str, bytes)) else ()
    for index, item_id in enumerate(identifiers):
        if isinstance(item_id, str) and item_id and item_id not in project.items:
            diagnostics.warning("unknown_profile_item", f"Profile references unknown item {item_id!r}; runtime currently tolerates it", source=profile.source, path=("inventory", index))
    equipment = raw.get("equipment", {})
    if isinstance(equipment, Mapping):
        for slot, item_id in equipment.items():
            if isinstance(item_id, str) and item_id and item_id not in project.items:
                diagnostics.warning("unknown_profile_item", f"Profile equipment {slot!r} references unknown item {item_id!r}; runtime currently tolerates it", source=profile.source, path=("equipment", str(slot)))
    known_moves = raw.get("known_moves", [])
    if isinstance(known_moves, Sequence) and not isinstance(known_moves, (str, bytes, Mapping)):
        for index, entry in enumerate(known_moves):
            move_id = entry.get("id") if isinstance(entry, Mapping) else entry
            if isinstance(move_id, str) and move_id and move_id not in project.moves:
                diagnostics.warning("unknown_profile_move", f"Profile references unknown move {move_id!r}; runtime currently tolerates it", source=profile.source, path=("known_moves", index))


def _validate_symbol_advisories(project: StoryProject, diagnostics: Diagnostics) -> None:
    for flag in sorted(project.symbols.undeclared_flags):
        diagnostics.advisory("dynamic_flag_symbol", f"Flag {flag!r} is referenced but not initialized or set by a discovered persistent action")
    for variable in sorted(project.symbols.undeclared_variables):
        diagnostics.advisory("dynamic_variable_symbol", f"Variable {variable!r} is referenced but not initialized or set by a discovered persistent action")


def _validate_reference_value(
    project: StoryProject,
    diagnostics: Diagnostics,
    value: Any,
    kind: ContentKind,
    source: Path,
    path: tuple[str | int, ...],
    *,
    required: bool = False,
) -> None:
    if value is None:
        if required:
            diagnostics.error("invalid_reference", f"{kind.value} references must be non-empty strings", source=source, path=path)
        return
    if not isinstance(value, str) or not value:
        diagnostics.error("invalid_reference", f"{kind.value} references must be non-empty strings", source=source, path=path)
        return
    collections: dict[ContentKind, Mapping[str, Any]] = {
        ContentKind.SCENE: project.scenes,
        ContentKind.ITEM: project.items,
        ContentKind.MOVE: project.moves,
        ContentKind.BATTLE: project.battles,
        ContentKind.EVENT_POOL: project.event_pools,
        ContentKind.ANIMATION: project.animations,
    }
    if value not in collections[kind]:
        diagnostics.error(f"unknown_{kind.value}_reference", f"Unknown {kind.value.replace('_', ' ')} {value!r}", source=source, path=path)


def _validate_action_references(project: StoryProject, diagnostics: Diagnostics, refs: ActionReferences, source: Path, prefix: tuple[str | int, ...]) -> None:
    for kind, identifiers in (
        (ContentKind.SCENE, refs.scenes), (ContentKind.ITEM, refs.items), (ContentKind.MOVE, refs.moves),
        (ContentKind.BATTLE, refs.battles), (ContentKind.ANIMATION, refs.animations),
    ):
        for identifier in identifiers:
            _validate_reference_value(project, diagnostics, identifier, kind, source, prefix)


def _validate_condition_value(
    diagnostics: Diagnostics,
    value: Any,
    source: Path,
    path: tuple[str | int, ...],
    *,
    dialect: ConditionDialect | str = "auto",
) -> None:
    if value is None:
        return
    try:
        parse_condition(value, dialect=dialect)
    except ConditionError as exc:
        diagnostics.error("invalid_condition", str(exc), source=source, path=path)


def _scan_scene_condition_values(scene: Mapping[str, Any], callback: Any) -> None:
    for value, path in _exploration_condition_values(scene):
        callback(value, path)


def _scene_action_sets(scene: Mapping[str, Any]) -> Iterable[tuple[tuple[str | int, ...], Any]]:
    if "actions" in scene:
        yield ("actions",), scene["actions"]
    choices = scene.get("choices", [])
    if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes, Mapping)):
        for index, choice in enumerate(choices):
            if isinstance(choice, Mapping) and "actions" in choice:
                yield ("choices", index, "actions"), choice["actions"]
    exploration = scene.get("exploration")
    if exploration is None or exploration is False:
        return
    config = exploration if isinstance(exploration, Mapping) else {}
    yield from _exploration_action_sets(config, scene=scene)


def _exploration_asset_references(
    config: Mapping[str, Any],
    *,
    scene: Mapping[str, Any] | None = None,
) -> Iterable[tuple[tuple[str, tuple[str | int, ...]], str]]:
    # scene-level media is checked separately.  This generator mirrors only
    # assets the existing exploration preflight explicitly visits.
    cursors, cursors_path = _exploration_section(config, "cursors", scene=scene, default={})
    if isinstance(cursors, Mapping):
        for name, value in cursors.items():
            if isinstance(value, str) and value:
                yield ("sprites", (*cursors_path, str(name))), value
    objects, objects_path = _exploration_section(config, "objects", scene=scene, default=[])
    if isinstance(objects, Sequence) and not isinstance(objects, (str, bytes, Mapping)):
        for index, value in enumerate(objects):
            if isinstance(value, Mapping) and isinstance(value.get("sprite"), str) and value["sprite"]:
                yield ("sprites", (*objects_path, index, "sprite")), value["sprite"]
    for actions_path, actions in _exploration_action_sets(config, scene=scene):
        if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes, Mapping)):
            continue
        for index, action in enumerate(actions):
            if not isinstance(action, Mapping):
                continue
            try:
                adapted = parse_actions([action], ActionScope.EXPLORATION)[0]
            except ActionError:
                continue
            payload = adapted.payload if isinstance(adapted.payload, Mapping) else {}
            scalar_payload = adapted.payload if isinstance(adapted.payload, str) else None
            action_path = (*actions_path, index)
            if adapted.action_type == "sound":
                filename, field = _exploration_action_filename(payload, scalar_payload, "sound")
                if isinstance(filename, str) and filename:
                    yield ("sfx", _exploration_action_field_path(action, action_path, field)), filename
            elif adapted.action_type == "music":
                filename, field = _exploration_action_filename(payload, scalar_payload, "music")
                if isinstance(filename, str) and filename:
                    yield ("music", _exploration_action_field_path(action, action_path, field)), filename
            elif adapted.action_type == "animation":
                identifier = payload.get("animation")
                if isinstance(identifier, str) and identifier:
                    yield ("animation", _exploration_action_field_path(action, action_path, "animation")), identifier
            elif adapted.action_type == "change_sprite":
                sprite = payload.get("sprite")
                if isinstance(sprite, str) and sprite:
                    yield ("sprites", _exploration_action_field_path(action, action_path, "sprite")), sprite


def _exploration_action_filename(
    payload: Mapping[str, Any],
    scalar_payload: str | None,
    alias: str,
) -> tuple[Any, str]:
    """Return an exploration audio value and its authored key where known."""

    if "file" in payload:
        return payload["file"], "file"
    if alias in payload:
        return payload[alias], alias
    return scalar_payload, alias


def _exploration_action_field_path(
    action: Mapping[str, Any],
    action_path: tuple[str | int, ...],
    field: str,
) -> tuple[str | int, ...]:
    """Preserve the YAML location for typed and one-key action forms."""

    if "type" in action:
        return (*action_path, field)
    if len(action) == 1:
        action_key, value = next(iter(action.items()))
        if isinstance(value, Mapping) and field in value:
            return (*action_path, str(action_key), field)
        return (*action_path, str(action_key))
    return (*action_path, field)


def _validate_asset(
    project: StoryProject,
    diagnostics: Diagnostics,
    filename: str,
    category: str,
    source: Path,
    path: tuple[str | int, ...],
    *,
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
    category_only: bool = False,
) -> None:
    try:
        if category_only:
            project.source.resolve_asset_path(category, filename)
        else:
            project.source.resolve_asset_reference(filename, category)
    except StorySourceError as exc:
        diagnostics.emit(severity, "missing_asset", str(exc), source=source, path=path)
        return

def _validate_audio_asset(
    project: StoryProject,
    diagnostics: Diagnostics,
    filename: str,
    source: Path,
    path: tuple[str | int, ...],
    *,
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
) -> None:
    try:
        project.source.resolve_asset_reference(filename, "music")
    except StorySourceError:
        try:
            project.source.resolve_asset_reference(filename, "sfx")
        except StorySourceError as exc:
            diagnostics.emit(severity, "missing_asset", str(exc), source=source, path=path)
            return
    if "/" in filename.replace("\\", "/") or filename.replace("\\", "/").startswith("assets/"):
        diagnostics.advisory(
            "audio_reference_runtime_mismatch",
            "Story-relative audio paths validate in exploration but AudioSystem currently resolves category-relative filenames only.",
            source=source,
            path=path,
        )


def _mapping_string_keys(mapping: Any) -> set[str]:
    """Return authored mapping keys without treating scalar strings as maps."""

    if not isinstance(mapping, Mapping):
        return set()
    return {key for key in mapping if isinstance(key, str) and key}


def _frozen_mapping(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen = freeze_value(mapping)
    # ``freeze_value`` returns a mapping proxy for actual mappings.  The
    # fallback keeps the helper total for lightweight project-like callers.
    return frozen if isinstance(frozen, Mapping) else MappingProxyType({})


__all__ = [
    "ProjectSymbols",
    "StoryProject",
    "StoryProjectLoadError",
    "load_story_project",
]
