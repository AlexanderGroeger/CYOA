# CYOA Engine

A data-driven, fullscreen `pygame-ce` choose-your-own-adventure engine.
Stories remain YAML-driven; rendering and player input are handled entirely
inside a pygame window.

## Run a game story

```bash
pip install -r requirements.txt
python main.py --story stories/demo_story
```

## Launch Story Designer

```bash
python -m story_designer
```

The Designer is a standalone authoring application; it does not require a
story argument or initialize the pygame runtime. From its welcome screen use
`File → New Story...` to create a valid blank project, or `File → Open Story...`
to edit an existing one. The game runtime remains the separate `main.py`
entry point shown above.

## Mechanics lab

Run the standalone developer test story with:

```bash
python main.py --story stories/mechanics_lab
```

It starts at a feature menu for player QTEs, enemy defense patterns,
inventory/equipment, and dialogue/presentation. Selecting an attack or defense
pattern starts that sequence immediately. Player attacks return directly to
their difficulty menu; an attack victory can be replayed with A/Enter or
returned from with Backspace.
Defense reports show damage taken and offer a replay of the current difficulty
or levels 1–3. The lab has no story-local media; it uses shared assets only.

The game opens fullscreen at the desktop resolution. WASD or the controller
D-pad moves the selection, Enter or controller A confirms, and Backspace or
controller B cancels/exits. Escape quits, and Ctrl+S or Ctrl+L save and load.
The terminal is not a game interface. X, Y, triggers, shoulders, Select, and
Start are recognized named controller bindings reserved for future actions.

## Display configuration

Every `story.yaml` must define a logical canvas:

```yaml
display:
  width: 320
  height: 180
```

The logical surface is drawn once per changed game state and then scaled with
nearest-neighbor interpolation by the largest fitting integer. It is centered
in the fullscreen display; unused space is black letterboxing/pillarboxing.

## UI configuration

Story manifests may override the default UI constants under `render.ui`.
Positions and sizes are fractions of the logical canvas; colors are RGB.

```yaml
render:
  ui:
    text_box_alpha: 0.75
    dialogue_position: [0.5, 0.84]
    dialogue_size: [0.92, 0.18]
    options_position: [0.5, 0.50]
    highlight_option_color: [255, 214, 102]
    selected_option_color: [18, 18, 35]
```

## Audio configuration

An optional story-root `audio.yaml` defines default mixer preferences. The
effective playback volume is `master_volume` multiplied by the relevant
category volume (and by an individual playback volume, when supplied).

```yaml
master_volume: 0.8  # scales every audio output
music_volume: 1.0   # scales tracks played as music
effects_volume: 1.0 # scales sound effects
```

## Layout

```text
engine/core/       story loading, state, conditions, transitions
engine/render/     pygame display setup, rendering, event mapping
engine/battle/     pure battle-resolution rules
engine/audio/      pygame mixer wrapper
stories/           story manifests, scenes, assets, and saves
tests/             headless logic and configuration tests
```

Image backgrounds and sprites (`.png`, `.jpg`, `.jpeg`, `.bmp`, `.gif`) are
supported. Images and fonts are loaded and cached after pygame initializes.

## Test

```bash
pytest tests/ -q
```

## Interactive battles

Battles are now state-driven and remain deliberately data-first. The pygame
frontend renders them, but the controller, damage calculation, pattern update,
inventory filtering, progression, and validation are all pure Python and can
be tested without a window.

Player attacks use a registry-backed QTE system with normalized results,
weapon-specific presentations, predictable difficulty modifiers, and
validated YAML configuration. See [Attack QTEs](docs/attack_qtes.md) for the
interface, all eight QTE types, scoring, examples, and extension steps.

Enemy turns can instead run a configurable dodge/bullet-hell defense sequence:
multiple registry-backed hazards may overlap while the player moves in the
arena. These sequences are separate from player QTEs and are authored in
YAML through `defense_sequences` and `enemy_moves[].defense_sequence` (the
older `enemy_patterns` / `pattern` names remain valid). See [Enemy defense
patterns](docs/defense_patterns.md) and its copyable example library for the
schema, all built-in pattern types, difficulty overrides, groups, repetition,
telegraphs, sprites, and extension points.

The default command menu is `Fight` and `Inventory`; a battle gets `Escape`
only when its YAML contains `escape: {enabled: true}`. WASD or the controller
D-pad navigate menus and move the marker/positioning cursor, A/Enter confirms,
and B/Backspace only backs out of safe menus (`Fight` moves, Inventory, items,
and gear).

### Battle YAML shape

`stories/demo_story/battles/wolf_fight.yaml` is a complete commented example.
The main top-level fields are:

```yaml
id: crystal_guard
enemy: {name: "Crystal Guard", hp: 45, attack: 4, defense: 2, sprite: crystal_guard.png}
arena: {x: 210, y: 145, width: 220, height: 105, player_speed: 125}
enemy_patterns: []
enemy_moves: []
initial_enemy_moves: []        # defaults to every declared enemy move
dialogue: []
phases: []
victory: {rewards: {variables: {gold: 5}, items: [crystal]}}
```

Player moves are global story data, not encounter data. Put them in one or
more YAML files beneath `moves/`; each has an `id`, display `name`,
`base_power`, and either the legacy
`pattern`/`pattern_config` form or a modern `qte` block. Legacy patterns
remain valid; new content should use the QTE configuration documented in
[Attack QTEs](docs/attack_qtes.md).

```yaml
- id: precise_cut
  name: "Precise Cut"
  pattern: timing_bar            # legacy: precision bar
  base_power: 12
  scoring: {minimum_multiplier: 0.25, maximum_multiplier: 1.75, perfect_threshold: 0.92}
  pattern_config: {duration: 1.8, target_position: 0.72, perfect_window: 0.05, good_window: 0.16}
```

`position_target` is a supported legacy alias for `moving_weak_point`.
All QTE result scores are normalized to `0.0..1.0`; only the battle controller
uses the configured result multiplier in its centralized damage formula.

Enemy moves select a named pattern by weight and may set `cooldown`,
`no_immediate_repeat`, `telegraph`, `telegraph_duration`, and an
`availability` mapping (`condition`, `min_turn`, `max_turn`, `phases`, or
`requires_fight_flags`). A top-level `enemy_sequence: [move_a, move_b]` is
consumed before normal weighted selection, which is useful for authored
openings.

```yaml
enemy_moves:
  - id: shard_fall
    name: "Falling Shards"
    pattern: falling_shards
    weight: 3
    no_immediate_repeat: true
```

An enemy pattern has a duration, optional `attack_delay` before projectiles
can begin (default `0.25` seconds), optional arena/player override, and a
timeline. The defense arena opens over that delay and collapses over `0.25`
seconds after the pattern; projectiles are clipped to its animated height and
cannot damage the player during either transition. Supported actions are `spawn`, `spawn_repeated`, `spawn_radial`,
`spawn_sweep`, `spawn_rotating`, and `dialogue`. Projectiles support circle or
rectangle shapes, edge/origin/player targeting, straight/toward-player/sine
movement, acceleration, delayed activation, size, damage, and lifetimes.

```yaml
enemy_patterns:
  - id: falling_shards
    duration: 4.0
    attack_delay: 0.25
    player: {invulnerability_time: 0.55}
    timeline:
      - at: 0.25
        action: spawn_repeated
        repeat: {count: 9, interval: 0.33}
        projectile:
          shape: rectangle
          size: [7, 15]
          spawn: {edge: top, x: random}
          velocity: {x: 0, y: 90}
          damage: 3
          lifetime: 2.0
```

`spawn_rotating` uses the same `repeat` mapping plus `start_angle` and
`angular_speed`; `spawn_sweep` is a scheduled spawn group whose projectile
velocity defines the wall/line movement. Prefer composing these primitives in
YAML instead of adding encounter-specific Python.

### Phases, dialogue, weapons, and items

Phases are evaluated after resolved turns and run once. Their `when` mapping
supports `enemy_hp_below`, `enemy_hp_ratio_lte`, `player_hp_below`,
`turn_at_least`, `move_used`, `item_used`, `fight_flag`, and `previous_phase`.
Actions can add/remove moves, replace a player move, set enemy weights, set
fight flags/arena fields, or apply validated augmentations:

```yaml
phases:
  - id: desperate
    when: {enemy_hp_ratio_lte: 0.50}
    actions:
      - add_player_move: crescent_lunge
      - augment_player_move:
          move: poised_slash
          fields: {base_power_add: 2, timing_window_multiplier: 0.85}
      - augment_enemy_pattern:
          pattern: ring_pulse
          fields: {projectile_count_add: 4, projectile_speed_multiplier: 1.12}
```

Only documented augmentation keys are accepted. Player keys include power,
timing windows, multipliers, and QTE speed. Enemy keys include projectile
speed/count/size/damage, spawn interval, duration, player speed, and arena
size. Invalid references and unsupported keys fail while the battle loads.

Dialogue entries use a `trigger`, `text` (or a `pool`), optional `when`, and
optional `once: false`. Triggers include battle start, before/after actions,
before/after enemy patterns, phase changes, hits, low health, a move/item use,
victory, and defeat. The default `type: modal` waits for A/Enter. `type: remark`
is a non-blocking line that remains while the command menu is usable, until
the player chooses Fight. `type: environment` is a non-blocking, single-line
typewriter caption for the battle setting. It remains while the player chooses
an action and clears when their attack sequence begins. `type: opponent` uses one small, wrapped speech
window to the enemy's right. It types at the normal dialogue cadence with a
smaller font; after its final character appears, it waits for `pause` seconds
(1.25 by default), then resumes the battle automatically. Player input cannot
skip opponent speech.

Player state lives in `player.yaml`; it initializes stats, inventory,
equipment, and `known_moves`. Learned moves are serialized in every save.
At battle time, an equipped weapon permits only the learned moves listed in
its `combat.move_grants`. Weapons are the sole place to associate moves with
weapons.

```yaml
# player.yaml
stats: {hp: 20, max_hp: 20, attack: 4, defense: 2}
equipment: {weapon: iron_hammer}
known_moves: [hammer_crush]
```

```yaml
# items/items.yaml
iron_sword:
  type: weapon
  equipment: {bonuses: {attack: 2}}
  combat: {move_grants: [precise_cut]}
```

```yaml
# moves/weapon_moves.yaml
 - id: precise_cut
   name: Precise Cut
   base_power: 12
   qte: {type: precision_bar, duration: 1.8}
```

`move_grants` is what permits a weapon to use a move, and learning is the
final player-specific gate.

```yaml
field_ration:
  type: consumable
  combat:
    usable: true
    consume_turn: true
    effects:
      - heal: 8
```

Combat items are limited to owned, positive-quantity, non-equipment entries
whose `combat.usable` is true. Effects currently support `heal`,
`damage_enemy`, `set_fight_flag`, `apply_effect`, and `remove_effect`.

Set `debug: {battle: true}` in `story.yaml` to show the current battle state,
turn/phase, active enemy move, projectile count, invulnerability timer, and
attack score on the logical game surface.

### Legacy battles

The original `enemy.moves` battle shape remains supported. It is normalized at
load time into a timing-bar player move plus legacy enemy damage/effect moves.
`deer_fight.yaml` intentionally remains in that format as a compatibility
example; new content should use the modern schema above.
