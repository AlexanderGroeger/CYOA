# Story Designer Foundation - Step 1 Architecture Audit

Audit date: 2026-08-13  
Scope: read-only architecture audit of the current Python / pygame-ce engine, followed by this documentation artifact. No gameplay, story, save, renderer, audio, or runtime behavior was changed.

The repository contains two shipped story projects and 58 production YAML files. The engine is already data-driven and has several useful pure-Python seams, but it does not yet have one project-level model, validator, or schema authority. The safe migration is therefore to add a shared, compatibility-first project layer beside the runtime, characterize existing behavior, and move consumers incrementally.

## 1. Current Architecture

The entry point is <code>main.py</code>, which creates <code>GameEngine</code>. <code>GameEngine.__init__</code> creates an <code>AssetLoader</code>, separately loads the manifest, audio preferences, player profile, items, and moves, constructs mutable <code>GameState</code>, then creates the pygame renderer and audio system. See <code>engine/core/game_engine.py:57-84</code>.

Major modules are:

| Area | Current responsibility |
| --- | --- |
| <code>engine/core/asset_loader.py</code> | Story-relative file lookup, story/shared asset resolution, YAML cache, and named raw-data loaders. |
| <code>engine/core/story_interpreter.py</code> | Legacy scene entry, actions, conditional choices, transitions, and battle rewards. |
| <code>engine/core/exploration.py</code> | Pure opt-in exploration vocabulary: structured conditions, object visibility, look targets, event actions, and scene-local dialogue resolution. |
| <code>engine/core/game_state.py</code> | The saved mutable player state and its initial construction from manifest/profile data. |
| <code>engine/core/inventory.py</code> | Partial item normalization, inventory/equipment actions, and derived stats. |
| <code>engine/battle/config.py</code> | Battle normalization and validation, including legacy battle adaptation. |
| <code>engine/battle/move_progression.py</code> | Pure combat-move deep merge, difficulty resolution, and saved skill state handling. |
| <code>engine/battle/controller.py</code>, <code>qte.py</code>, <code>defense.py</code> | Active battle state and execution of QTE and bullet-hell definition data. |
| <code>engine/render/renderer.py</code>, <code>display.py</code> | pygame rendering and display/UI defaults; the renderer directly reads scene, animation, and UI fields. |
| <code>engine/audio/audio_system.py</code> | pygame mixer wrapper and category-based playback lookup. |
| <code>engine/events/random_events.py</code> | Weighted event-pool selection. |
| <code>engine/save/save_system.py</code> | JSON save/load and save metadata. |

The generic YAML parse call is centralized: <code>AssetLoader.load_yaml()</code> uses <code>yaml.safe_load()</code> at <code>engine/core/asset_loader.py:83-93</code>. The shared-animation fallback at <code>:315-327</code> is the only other production YAML parse site. This is a good starting point, but the parsed values are cached mutable dictionaries/lists and then interpreted independently by many runtime modules.

The closest existing foundations for a shared layer are:

- <code>ItemDefinition</code> and <code>InventoryService</code> in <code>engine/core/inventory.py</code>;
- <code>BattleConfig</code>, <code>EnemyDefinition</code>, and game-over dataclasses in <code>engine/battle/config.py</code>;
- <code>SkillProgressionConfig</code> and move normalization in <code>engine/battle/move_progression.py</code>;
- <code>DialogueSequence</code>, <code>LookTarget</code>, and <code>EventSignal</code> in <code>engine/core/exploration.py</code>;
- <code>DisplayConfig</code> in <code>engine/render/display.py</code>.

They are useful seeds, but no one object represents an entire loaded story project.

## 2. Story Content Inventory

There is no standalone dialogue YAML type. Narrative dialogue is embedded in ordinary scenes, exploration scene tables, and battle definitions.

| Content/configuration type | Location and file shape | Loader and main consumers | Major fields, defaults, and references |
| --- | --- | --- | --- |
| Story manifest | <code>stories/&lt;story&gt;/story.yaml</code>, one mapping | <code>load_manifest()</code> → <code>GameEngine</code>, <code>GameState</code>, display parser, renderer | Observed: <code>id</code>, <code>title</code>, <code>version</code>, <code>start_scene</code>, required <code>display.width/height</code>, starting flags/variables, legacy starting player fields, <code>render</code>, <code>navigation</code>, <code>debug</code>, and default background. <code>id</code> defaults to directory name and <code>version</code> to <code>0.0</code> in <code>GameEngine</code>. |
| Initial player profile | Optional <code>player.yaml</code>, one mapping | <code>load_player()</code> → <code>GameState.new_from_manifest()</code>, inventory layout, move bootstrap | <code>stats</code>, <code>inventory</code>, <code>equipment</code>, <code>known_moves</code>, <code>move_skill_levels</code>, <code>inventory_ui</code>. Profile values override legacy manifest starting values. |
| Audio preferences | Optional <code>audio.yaml</code>, one mapping | <code>load_audio_config()</code> → <code>AudioSystem</code> | <code>master_volume</code>, <code>music_volume</code>, <code>effects_volume</code>; startup defaults are 0.8, 1.0, and 1.0. |
| Legacy/standard scene | Recursive <code>scenes/**/*.yaml</code>; file stem is the scene ID | <code>load_scene()</code> → <code>StoryInterpreter</code>, <code>GameEngine</code>, <code>Renderer</code> | <code>id</code>, <code>text</code>, art/audio fields, <code>actions</code>, <code>choices</code>, endings, checkpoints, animation, and text/menu settings. Choice references target a scene, battle, or event pool. |
| Exploration scene | A scene with <code>exploration: true</code> or a mapping; some root aliases are accepted | <code>load_scene()</code> + <code>exploration.py</code> + <code>GameEngine</code> + renderer | <code>dialog</code>, <code>dialogue_sequences</code>, <code>navigation</code>, <code>objects</code>, <code>look_regions</code>, <code>look_events</code>, cursors, cursor speed/start, and visit flags. It has its own structured condition and typed event vocabularies. |
| Item registry | Optional <code>items/items.yaml</code>, a mapping keyed by item ID | <code>load_items()</code> → <code>InventoryService</code>, battle config/controller | Canonical fields include name, description, type, icon, stats, equipment slot, actions, and <code>use.actions</code>. It also accepts legacy <code>equipment.bonuses</code>, <code>combat.usable</code>, effects, and move grants. |
| Global combat moves | Every <code>moves/**/*.yaml</code>; root may be a <code>moves:</code> list, a single move mapping, or a list | <code>load_combat_move_config()</code> → battle config, progression, QTE factory | Move IDs, names, common fields, numeric difficulty levels, QTE fields, availability, scoring, and optional one-file <code>skill_progression</code>. Common and selected difficulty data are deep-merged. |
| Battle | <code>battles/&lt;filename&gt;.yaml</code>, one mapping per file | <code>load_battle()</code> → <code>load_battle_config()</code> only when entered → <code>BattleController</code> | Modern battle fields include enemy, arena, patterns/sequences, enemy moves, dialogue, phases, escape, rewards, on-lose behavior, media, and test settings. Legacy <code>enemy.moves</code> is adapted to a modern configuration. |
| Random event pool | <code>events/&lt;pool-id&gt;.yaml</code>, one mapping | <code>load_event_pool()</code> → <code>maybe_trigger()</code> | <code>chance</code> and <code>events: [{id, weight}]</code>; each event ID is intended to be a scene ID. |
| Animation definition | <code>assets/animations/&lt;name&gt;/anim.yaml</code>, story-local or shared | <code>load_animation()</code> → renderer | <code>frames</code>, <code>frame_delay_ms</code>, <code>loop</code>. Frame files are read lazily by the renderer. |

The documentation example <code>docs/examples/defense_sequences.yaml</code> is not a story content file; it is read only by a test.

Current file discovery rules are type-local:

- Scene IDs are recursive filename stems. A declared scene <code>id</code>, if present, must match the stem; duplicate stems are rejected.
- Battle and event-pool IDs are filename-based for lookup, but their declared YAML <code>id</code> is not checked against the filename.
- Item IDs are mapping keys.
- Combat move, enemy move, enemy pattern, phase, event, object, and dialogue sequence IDs are local to their owning definition.
- IDs are not globally unique across types, and the current engine does not need them to be.

## 3. Data Flow

The main paths are:

~~~
main.py
  → GameEngine
      → AssetLoader
          → yaml.safe_load() / cached raw mappings
      → manifest + player → GameState
      → items → InventoryService
      → moves → battle configuration/progression

runtime scene path:
  scene YAML → StoryInterpreter → Transition
      → scene YAML
      → event-pool YAML → selected scene YAML
      → battle YAML → BattleConfig → BattleController

exploration path:
  scene YAML → exploration_config / conditions / EventRunner
             → renderer view model + GameState mutations
~~~

More specifically:

1. <code>GameEngine</code> loads manifest/audio/player/items/moves independently. It calls <code>validate_exploration_content()</code>, then creates mutable player state from manifest/profile precedence.
2. On scene entry, <code>StoryInterpreter.enter_scene()</code> obtains the raw scene mapping, writes <code>current_scene</code>, executes raw scene actions, and returns requested SFX.
3. Legacy choices are filtered by the string condition evaluator and resolved into a <code>Transition</code>. The runtime then loads another scene, an event pool, or a battle.
4. An exploration scene instead creates a transient <code>SceneRuntime</code> and repeatedly resolves raw config plus <code>GameState</code> into visible objects, look targets, and a simple renderer view.
5. A battle is loaded only after the player initiates it. <code>load_battle_config()</code> deep-copies/normalizes portions of the raw battle and returns a partially typed <code>BattleConfig</code>; the controller, QTE factory, and defense system then consume its nested dictionaries.

Reference/dependency map:

| Source | Target | Resolution timing/current behavior |
| --- | --- | --- |
| Manifest <code>start_scene</code> | Scene stem | Loaded when the game loop first enters the scene; not globally validated. |
| Legacy scene choice <code>goto</code> | Scene | Loaded after selection; late failure. |
| Legacy scene choice <code>battle</code> | Battle filename | Loaded and validated after selection; late failure. |
| Legacy scene choice <code>random_event</code> | Event pool filename | Loaded after selection; selected event scene is later loaded. |
| Battle/exploration outcomes | Scene | Exploration navigation validates these during its full preflight; legacy choices do not. |
| Event-pool event ID | Scene | Not validated before random selection. |
| Profile inventory/equipment | Item registry | Not eagerly checked. Unknown saved IDs are intentionally tolerated. |
| Profile known moves / item move grants | Global move | Move grants are checked when a battle is configured; profile known-move IDs are not eagerly checked. |
| Battle enemy move | Enemy pattern/defense sequence | Validated at battle configuration time. |
| Battle phases | Moves, patterns, items, phases | Many references are validated at battle configuration time. |
| Exploration navigation / event action | Scenes, battles, items, local dialogue, objects, events, animations | Strongest current cross-reference validation; checked at startup for opted-in exploration scenes. |
| Scene/item/battle media field | Story or shared asset | Path lookup is consumer-specific. Exploration/item icon preflight is stronger than ordinary scene/battle media checking. |

There is no general reference index. Each system resolves its own IDs with filenames, mappings, or local lookup tables.

## 4. Coupling and Technical Debt

### Direct schema coupling

The raw YAML shape is currently understood in several places:

| Priority | Finding | Evidence and impact |
| --- | --- | --- |
| High | Generic scene semantics are split between interpreter, game coordinator, and renderer. | <code>StoryInterpreter</code> owns actions/choices; <code>GameEngine</code> owns checkpoints, music, default backgrounds, font fallback, endings, saves, and transitions; <code>Renderer</code> owns background/sprite/animation/text fields. A scene editor would otherwise need knowledge from all three. |
| High | Battle configuration is only shallowly modeled. | <code>BattleConfig</code> is a dataclass, but moves, patterns, dialogue, phases, rewards, and defeat data remain <code>dict[str, Any]</code> / lists. <code>BattleController</code>, <code>qte.py</code>, and <code>defense.py</code> separately interpret their keys and defaults. |
| High | There is no full-project loader/validator. | Startup preflights exploration only. Legacy scenes, event pools, profile references, most assets, and all battles until entered can fail during play. |
| High | Conditions/actions have multiple incompatible authoring forms. | Legacy scenes use one-key action mappings and string conditions; exploration and item use use typed actions and structured conditions; battles add separate phase/dialogue/fight-flag forms. |
| Medium | Item semantics are normalized twice. | <code>InventoryService</code> produces <code>ItemDefinition</code>, while <code>BattleController</code> directly reads legacy raw <code>combat</code>/<code>equipment</code> mappings. |
| Medium | Defaults are distributed. | Defaults live in <code>GameState</code>, interpreter, inventory normalizer, move progression, battle config, defense/QTE classes, renderer, random events, and animation rendering. This is the main editor/runtime divergence risk. |
| Medium | Cached YAML is mutable shared state. | <code>AssetLoader.load_yaml()</code> returns the same cached object. Current code mostly copies before mutation, but ownership is implicit and unsafe for a future editor/live-preview boundary. |
| Medium | Unknown-field handling is inconsistent. | Exploration, item actions, and portions of battle/QTE validation reject selected unknowns; most roots and legacy scene fields silently ignore unknown keys. |
| Low | <code>engine/battle/battle_system.py</code> is a raw-dict legacy API used only by its dedicated tests, not the active runtime. | It should be kept stable or explicitly retired later, but it is not a first migration target. |
| Low | <code>text_fg</code> and <code>text_bg</code> appear in <code>stories/demo_story/scenes/intro.yaml</code> but have no runtime reader. | This is useful evidence that a future schema should distinguish supported, deprecated, and unknown fields rather than silently delete data. |

### Defaults and normalization

Important defaults are currently scattered:

- Missing audio/player/items files become empty mappings; a missing moves directory becomes an empty move configuration.
- Empty/missing legacy scene conditions are true; an absent/empty choices collection means an ending.
- Item defaults include <code>name = id</code>, empty description, type <code>item</code>, zero stats, inferred equipment slot/actions, and legacy field adaptation.
- Modern moves deep-merge <code>common</code> with a selected difficulty level and flatten three QTE parameter sections. Legacy move forms remain supported.
- Battle defaults include enemy attack/defense, arena geometry, initial move sets, dialogue/phases, escape behavior, on-lose presentation, progression, and test HP restoration.
- Exploration accepts aliases, supplies condition/look/event defaults, and supports both old and typed event actions.
- Renderer independently supplies UI/layout/font/animation defaults.

A shared core must not merely expose defaults as editor hints; it must own the same normalization path that runtime consumers use. Otherwise an omitted field can display one value in PySide6 and execute another in pygame.

### Validation and error timing

| Validation class | Current behavior |
| --- | --- |
| YAML syntax | Native <code>yaml.YAMLError</code> is not wrapped with an engine diagnostic or field/source context. |
| Structural | Items are substantially checked on load. Opt-in exploration is structurally checked at startup. Display is checked at startup. Moves/battles are checked mostly when a battle is entered. Profile/event/ordinary scene roots have little or no schema validation. |
| Semantic | Item restrictions, move progression, QTE, and defense rules have substantial validators. Exploration validates interaction/event semantics. Many legacy scene/action fields still surface as raw attribute/key/type errors later. |
| Cross-reference | Exploration has a startup pass for many scene/battle/item/local references. Battle internals validate at battle entry. Ordinary scene graph, event-pool scene IDs, player profile IDs, rewards, and much media remain late-bound. |
| Runtime-only | Pygame rendering, mixer playback, type coercions, current state-dependent availability, battle phase decisions, and active QTE/defense execution remain runtime concerns. |

Notable late/error-prone behavior to preserve or characterize before changing it:

- A legacy choice with more than one transition key is not rejected; resolution prioritizes <code>battle</code>, then <code>random_event</code>, then <code>goto</code>.
- A malformed standard scene action or target generally fails when that action/choice is reached.
- Raw string conditions in exploration are type-checked but not parsed during exploration preflight.
- Asset validation differs by consumer. In particular, exploration preflight accepts story-relative audio references through <code>resolve_asset_reference()</code>, while <code>AudioSystem</code> later resolves only category filenames through <code>resolve_asset_path()</code>. A story-relative exploration audio path can therefore validate and still fail at playback.
- <code>main.py</code> catches <code>EngineError</code>, not raw YAML parse errors, <code>KeyError</code>, <code>AttributeError</code>, or some numeric conversion errors.

## 5. Flags and Conditional Content

Persistent flags are an unconstrained <code>dict[str, bool]</code> on <code>GameState</code>. Missing flags read as false and writes coerce values with <code>bool()</code>. Variables are similarly unconstrained values; inventory quantities and IDs are also state-driven.

| Context | Condition/action form | State scope |
| --- | --- | --- |
| Legacy scene choices and battle move availability | Safe string expression: <code>flags.name</code>, <code>vars.name</code>, <code>has_item(...)</code> | Persistent <code>GameState</code> |
| Exploration dialog/navigation/objects/regions/look states | String expression or structured <code>all</code>/<code>any</code>/<code>not</code>/<code>flag</code>/<code>variable</code>/<code>has_item</code> mapping | Persistent state, then transient presentation resolution |
| Legacy scene actions | One-key actions such as <code>{set_flag: {x: true}}</code> | Persistent state |
| Exploration and inventory use actions | Typed actions such as <code>{type: set_flag, flag: x, value: true}</code> | Persistent state |
| Battle phases/dialogue | Battle-specific <code>when</code> mappings for HP, turn, used moves/items, phases, and fight flags | Active battle plus some persistent player state |
| Battle effects/phases | <code>set_fight_flag</code> | Transient <code>BattleController.fight_flags</code>, never saved |

Risks for the Story Designer:

- The string evaluator allows only Python-attribute-like flag/variable names and rejects underscore-prefixed names. Structured exploration conditions accept any non-empty name. A single flag picker cannot assume the two dialects are identical.
- Conditions have compatibility-sensitive truth behavior: absent strings and <code>{}</code> are true; an empty <code>all</code> is true and an empty <code>any</code> is false.
- Structured conditions allow combinations of <code>equals</code>, <code>not_equals</code>, and <code>exists</code> without enforcing exclusivity; evaluation gives them a specific precedence.
- Flags have no declaration registry, type metadata, typo detection, or reference validation. Initial flags are manifest-only; player profiles do not define flags/variables.
- Generated exploration once-flags are persisted as <code>_exploration_dialog_&lt;scene-id&gt;_&lt;dialog-index&gt;_seen</code>. Renaming a scene or reordering dialog entries can orphan/replay content in existing saves.

Step 2 should introduce one shared condition parser/validator and a symbol index, but it should initially accept both legacy forms and emit advisory diagnostics for undeclared names. It must keep persistent flags distinct from per-battle fight flags.

## 6. Definition vs Runtime State

The project has several good separation points:

- Story YAML is static source data; the loader caches it.
- <code>GameState</code> holds persistent mutable player state.
- <code>SceneRuntime</code> intentionally holds non-persistent exploration presentation changes: hidden/shown objects, sprite overrides, and animations. It is recreated on scene entry.
- <code>BattleController</code> creates transient enemy HP, turns, phase IDs, cooldowns, active effects, QTE/defense state, dialogue timing, and fight flags.
- Battle effective moves/patterns are copied before phase augmentation, so authored battle config is usually not mutated.

The boundary is nevertheless blurred in important ways:

1. <code>GameState.new_from_manifest()</code> embeds static profile/manifest precedence and normalization. Static definition interpretation happens while creating runtime state.
2. <code>GameEngine</code> reads static scene/manifest fields directly to apply defaults, checkpointing, music, UI/font behavior, history behavior, and endings.
3. The renderer reads static scene/art/UI/animation fields directly instead of receiving a definition/view API.
4. <code>BattleConfig</code> combines static definition data with raw nested payloads; <code>BattleController</code> knows their schema to create effective runtime copies.
5. The raw loader cache has no immutable definition boundary.

The target split should be:

| Static Story/Core | Mutable runtime/player state |
| --- | --- |
| Project layout, source paths, asset references, definitions, aliases, defaults, schema metadata, diagnostics, reference index, definition serialization | Current scene, flags, variables, inventory quantities, stats, equipment choices, learned moves, history, ending marker |
| Scene/battle/item/move/event/animation structure | Active exploration cursor/event/dialogue/presentation state |
| Condition/action syntax and validation | Active battle enemy HP, phases, fight flags, effects, QTE/defense timing |

The Story/Core layer should model static definitions and the initial player profile, but it should not execute pygame, audio playback, battle turns, rendering, or UI state.

## 7. Save Compatibility

Saves are JSON at <code>&lt;story&gt;/saves/&lt;slot&gt;.json</code>. The current payload is:

~~~
{
  "save_format_version": 1,
  "story_id": "...",
  "story_version": "...",
  "timestamp": ...,
  "state": {
    "current_scene": "...",
    "flags": {},
    "variables": {},
    "inventory": {},
    "stats": {},
    "equipment": {},
    "known_moves": [],
    "known_combat_moves": {},
    "history": [],
    "ending_reached": null
  }
}
~~~

The loader checks <code>story_id</code> and <code>save_format_version</code>, but stores and does not validate <code>story_version</code>. <code>GameState.from_dict()</code> supplies empty containers for fields absent from old sparse v1 saves.

Compatibility constraints:

- Saves reference static definitions by IDs; they do not embed scene/item/move/battle copies.
- Missing item definitions are shown as inert placeholders and missing equipped items contribute zero bonuses. This is deliberate tolerance for definition changes.
- A saved missing scene fails only when the engine tries to enter it.
- Old saves with no learned moves are supplemented from the current profile. This conflates a missing old field with intentionally empty learned moves and must remain characterized before changing.
- Load is not an exact runtime resume. It replaces <code>GameState</code> and re-enters the saved scene, replaying normal scene entry actions and appending scene history. Battle, QTE, cursor, event-runner, dialogue, selection, and exploration transient state are not saved.
- The persisted <code>GameState.history</code> is diagnostic; the actual Back-navigation stack is a separate <code>GameEngine._scene_history</code> reset on load.
- Equipment need not be present in inventory. The shipped demo starts with <code>dev_wand</code> equipped but not in its inventory, so a future validator must not make ownership mandatory.
- Duplicate profile move IDs are currently collapsed by <code>GameState.learn_move()</code>; the shipped demo repeats <code>rapid_slash</code>.

Step 2 must not bump the save format, rename JSON fields, tighten unknown-ID behavior, or alter load/re-enter behavior. It should introduce an explicit save compatibility adapter/test suite before any save model is relocated.

## 8. Recommended Story/Core Architecture

Use an engine-owned, pure-Python package such as <code>engine/story_core</code>. Keeping it inside <code>engine</code> fits the current import layout while making its no-pygame/no-Qt contract clear.

~~~
engine/story_core/
  __init__.py
  diagnostics.py        # source path, field path, code, severity, message
  source.py             # YAML source/cache and portable asset-path resolution
  project.py            # StoryProject and project loading orchestration
  index.py              # typed, type-local ID/reference index
  conditions.py         # legacy + structured condition parsing/normalization
  actions.py            # canonical static action representations + legacy adapters
  schema.py             # FieldSpec, TypeSpec, SchemaRegistry
  serialization.py      # semantic YAML mapping/export; no save-format change
  models/
    manifest.py
    player.py
    scene.py
    item.py
    move.py
    battle.py
    event_pool.py
    animation.py
  compat/
    legacy_views.py     # legacy raw mapping/dict views for pygame transition
    save_adapter.py     # read/characterize v1 state; initially non-invasive
~~~

Suggested public APIs:

~~~
project = load_story_project(story_root, shared_assets_root)
diagnostics = project.validate()
scene = project.scene("forest_clearing")
target = project.resolve(Reference.scene("cave_entrance"))
schema = project.schema_for("scene")
mapping = serialize_project(project)       # semantic output only
legacy = LegacyProjectView(project)        # temporary pygame adapter
~~~

Core responsibilities:

- discover all content files once and retain source-path provenance;
- parse YAML and resolve portable asset paths without importing pygame or Qt;
- normalize documented aliases/defaults through one canonical path;
- build a typed project index and return source-qualified diagnostics;
- expose immutable definitions or defensive copies;
- serialize definitions semantically and provide schema metadata for tooling;
- retain explicit legacy adapters until runtime consumers are migrated.

Runtime responsibilities that should remain outside Story/Core:

- <code>GameEngine</code>: pygame loop, input dispatch, scene/battle session coordination, checkpoints, and current UI state;
- <code>Renderer</code>/<code>AudioSystem</code>: pygame rendering/mixer setup and playback;
- <code>BattleController</code>/<code>qte.py</code>/<code>defense.py</code>: active gameplay execution and transient battle state;
- <code>GameState</code>/<code>save_system.py</code>: player snapshot behavior until a compatibility adapter is proven.

Existing modules to move, wrap, or retain:

| Treatment | Existing modules |
| --- | --- |
| Extract/reuse first | Low-level YAML/path/cache behavior from <code>AssetLoader</code>; item normalization; move deep-merge; condition parsing; existing dataclasses. They are pure and already tested. |
| Wrap first | <code>AssetLoader</code>, <code>StoryInterpreter</code>, and <code>load_battle_config()</code> should keep their public/runtime contracts while accepting core-backed legacy views. |
| Retain unchanged in Step 2 | <code>GameEngine</code>, renderer/display, audio, battle controller/QTE/defense, event execution, save JSON writer/reader. |
| Migrate in Step 3 | Runtime consumers should progressively replace raw mapping access with project definitions/view models, beginning at loaders/interpreter and ending at renderer/battle execution. |

## 9. Schema Strategy

The existing code contains field knowledge, validators, aliases, registries, and defaults, but no editor-consumable metadata. Do not duplicate that knowledge in a PySide6 form.

Introduce declarative metadata owned by Story/Core. A representative field descriptor is:

~~~
FieldSpec(
  key="damage",
  display_name="Damage",
  type=TypeSpec.integer(),
  required=False,
  default=0,
  minimum=0,
  description="Damage dealt by this effect.",
  ui_hint="spinbox"
)

FieldSpec(
  key="dialogue",
  display_name="Dialogue",
  type=TypeSpec.reference(target="dialogue_sequence"),
  required=False,
  asset_kind=None,
  description="A scene-local dialogue sequence."
)
~~~

Each field specification should support:

- serialized key and display/help text;
- scalar/object/list/map/discriminated-union/reference/asset/condition types;
- required/optional/default/aliases/deprecation state;
- bounds, allowed values, element type, and object schema;
- reference target type and optionality;
- asset category/path rules;
- editor hints such as multiline text, color, rectangle, collection editor, and picker;
- conditional field availability where genuinely required.

The registry should drive three consumers from one source:

1. parser/normalizer defaults and aliases;
2. structural validation/diagnostics;
3. Story Designer controls and reference pickers.

Do not force every current field into one giant schema immediately. Use discriminated schemas for QTE and defense pattern types, with a registry entry next to each runtime type. The existing QTE registry and defense pattern registry are natural future registration points. In Step 2, expose high-level battle/move metadata and bridge the current specialized validators; do not reimplement every QTE/defense parameter rule a second time.

Alias compatibility must be first-class metadata, not hidden parser conditionals. Examples include legacy battle <code>enemy_patterns</code> versus <code>defense_sequences</code>, enemy move <code>pattern</code> versus <code>defense_sequence</code>, legacy QTE names, legacy item combat fields, and root exploration aliases.

There is currently no YAML definition serializer. <code>yaml.safe_load()</code> loses comments and formatting, so Step 2 should define semantic round-trip equivalence, not textual fidelity. Preserve YAML order where practical, but do not claim comment/layout preservation. Before the Designer writes source files, decide explicitly whether comment-preserving round-trip YAML support is a product requirement and, if so, introduce a suitable parser deliberately rather than accidentally changing the loader.

## 10. Step 2 Implementation Plan

### Preconditions and characterization tests

1. Add a headless <code>load_and_validate_project()</code> test for both shipped stories. It should discover every production YAML file, load every scene/battle/event/move/animation, resolve all static references/assets, and assert source-qualified diagnostics.
2. Add fixture tests for every supported legacy/current form: scene actions/transitions, player profile fallback/duplicates/unowned equipment, item schemas, move root shapes/difficulty merges, legacy/modern battles, defense aliases, event pools, audio, and animations.
3. Add condition parity tests for string and structured conditions, including syntax errors, empty semantics, invalid identifiers, and all runtime contexts.
4. Add save characterization tests using the shipped save files plus synthetic sparse v1 saves. Explicitly cover load→re-enter, unknown/removed IDs, current profile move fallback, generated once flags, and mid-battle/exploration save behavior.
5. Add an asset-resolution contract test across ordinary scenes, exploration, item icons, battle art/audio, and animations. Resolve the current exploration audio-path mismatch as a documented compatibility decision before centralizing paths.

### Foundation implementation order

1. Create <code>engine/story_core/diagnostics.py</code>, <code>source.py</code>, <code>index.py</code>, and <code>schema.py</code>. Diagnostics must carry a file path, field path, error code, severity, and message. Start with path/field diagnostics; line/column marks can be a later parser enhancement.
2. Extract or compose the safe low-level YAML cache and portable asset resolver from <code>AssetLoader</code> into <code>source.py</code>, preserving story-local-first/shared fallback, cache behavior, and current public <code>AssetLoader</code> methods. Do not change runtime callers yet.
3. Add immutable/static definition models for manifest, player profile, item, scene, move, battle envelope, event pool, and animation. For deeply variant battle/QTE/defense payloads, use typed envelopes plus canonical payload/schema bridges first instead of a large risky rewrite.
4. Implement <code>StoryProject</code> discovery and a type-local <code>ProjectIndex</code>. It should map file-origin IDs to definitions, preserve nested scene filename lookup, detect ambiguities, and centralize reference lookup without requiring global ID uniqueness.
5. Implement canonical legacy adapters in <code>conditions.py</code> and <code>actions.py</code>. Read legacy one-key scene actions, typed exploration/item actions, and battle-only fight actions into explicitly scoped forms. Preserve legacy output/views for runtime compatibility.
6. Reuse the existing item normalizer and move deep-merge as the first detailed normalization migrations. Re-export old APIs from their present modules during the transition so tests/imports remain stable.
7. Add a pure <code>project.validate()</code> pass that runs syntax, structural, cross-reference, and asset checks. Initially distinguish errors from compatibility warnings; do not make currently tolerated legacy/unknown data fatal without a characterization test.
8. Implement definition-to-mapping serialization with stable semantic output. Keep it unused by normal pygame gameplay in Step 2. Add semantic load→serialize→reload tests.
9. Add a non-invasive <code>AssetLoader.load_project()</code> or equivalent bridge and compare core results with existing loader results in tests. Runtime code should still receive its legacy mapping contract until parity is demonstrated.

### Likely files

New files are expected under <code>engine/story_core/</code> as outlined in Section 8, plus focused tests such as:

~~~
tests/test_story_project_loading.py
tests/test_story_project_validation.py
tests/test_story_project_references.py
tests/test_story_core_schema.py
tests/test_story_core_serialization.py
tests/test_story_core_legacy_compat.py
tests/fixtures/story_core/
~~~

Likely changed files are:

- <code>engine/core/asset_loader.py</code>, limited initially to delegation/re-export while preserving method signatures and raw legacy views;
- <code>engine/core/inventory.py</code> and <code>engine/battle/move_progression.py</code>, limited to extracting/re-exporting pure normalizers after parity tests;
- package exports and tests.

<code>GameEngine</code>, renderer, audio, battle controller, QTE/defense runtime, scene interpreter behavior, YAML story files, and save JSON should remain behaviorally unchanged in Step 2.

### Explicit Step 2 non-goals

- Do not build PySide6 UI, generic inspectors, scene editor, preview, or live reload.
- Do not migrate pygame runtime consumers wholesale.
- Do not change save format/version or redesign save timing.
- Do not rewrite shipped YAML into a preferred canonical style.
- Do not remove legacy aliases/forms or make unknown fields fatal by default.
- Do not duplicate detailed QTE/defense validation in a second independent schema implementation.
- Do not introduce pygame or PySide6 imports into <code>engine/story_core</code>.

## 11. Risks / Open Questions

1. **Legacy compatibility policy:** Decide whether the future Designer should preserve every current alias and permissive form on save, or offer an explicit migration command. Step 2 must load all current forms regardless.
2. **Unknown fields:** Current content is mostly permissive, and <code>text_fg</code>/<code>text_bg</code> may be historical leftovers. Define warnings/deprecation metadata before strict rejection.
3. **Flag declarations:** Flags/variables are dynamic and generated in places. A declaration registry should be advisory first; mandatory declarations would change existing authoring semantics.
4. **Condition unification:** Preserve both current condition languages before proposing a canonical saved form. Their identifier rules and empty-condition behavior differ.
5. **Action unification:** Persistent state mutation, exploration asynchronous presentation, and battle-local effects have different scopes/lifetimes. A shared action model must not accidentally make fight flags persistent or make UI signals part of static state.
6. **Asset contract:** Normalize story-relative versus category-relative asset references across renderer, audio, battle, and validation. The current mismatch can produce a false-positive preflight.
7. **Battle schema scale:** The defense registry is intentionally extensible and permissive. Full typed modeling is valuable but should follow registry-backed schemas and parity tests, not a monolithic conversion.
8. **Reference graph semantics:** Scene cycles are valid and must not be reported as errors. A graph validator should report missing/unreachable nodes as configurable diagnostics, not reject circular navigation.
9. **Save semantics:** Existing loads replay scene entry actions, and static content changes affect old saves. Treat this as an explicit compatibility contract until the product chooses otherwise.
10. **YAML fidelity:** PyYAML supplies semantic values, not comments/format/source marks. Decide comment preservation before editor write support, and retain source file/field paths in the meantime.

## 12. Changes Made During This Audit

- Added this audit report only: <code>docs/story_designer_foundation_step1_audit.md</code>.
- No existing runtime behavior, story YAML, assets, saves, tests, or engine code was modified.
- Verification: <code>pytest tests -q -p no:cacheprovider --basetemp .pytest_tmp_story_audit</code> completed with <code>291 passed, 1 skipped</code>. The temporary test directory was removed after the run.
