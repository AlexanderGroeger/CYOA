# Enemy defense patterns

Enemy defense patterns are the dodge/bullet-hell half of a battle: the enemy
creates hazards while the player moves inside the defense arena. They are
separate from player attack QTEs. A QTE scores the player's selected move and
damages the enemy; a defense sequence runs on an enemy turn and routes hazard
hits through the battle controller's existing player-health and invulnerability
handling.

The library at [examples/defense_sequences.yaml](examples/defense_sequences.yaml)
contains standalone copy-and-modify sequences. Each YAML document in that file
is one sequence, so it can be pasted into a battle file or loaded as a
multi-document library.

## Runtime model

The framework deliberately keeps content, simulation, and rendering separate:

~~~text
BattleController
  -> chooses an enemy move and its defense sequence
  -> DefenseSequence scheduler
       -> starts every due Pattern instance
       -> updates all active patterns concurrently
       -> owns the arena, player position, shared hazards, and hit gate
            -> reusable projectile / beam / zone / constraint hazards
  -> renderer draws hazard presentation data (native shapes or cached sprites)
~~~

A pattern is a short-lived producer of hazards. Its common lifecycle is
start(context), update(dt), finish(), and is_finished. Patterns do not own the
pygame event loop, battle state, player HP, or asset loading. The sequence
updates by elapsed seconds and slices at scheduled start boundaries, so a
pattern that starts part-way through a frame only simulates its actual
remaining time.

Hazards use simple geometry for collision. The sequence applies its one
configurable hit gate before calling the battle controller, which prevents a
cluster of overlapping hazards from draining HP once per frame. Native circles,
rectangles, lines, rings, telegraph outlines, and beams need no art. Sprite
metadata remains declarative until the renderer loads and caches it.

## Adding a sequence to a battle

Use the clear defense names in new battle YAML:

~~~yaml
defense_sequences:
  - id: ember_drill
    duration: 5.0
    arena: {width: 220, height: 110, player_speed: 120}
    patterns:
      - type: aimed_stream
        start: 0.0
        duration: 5.0
        origin: {x: 0.5, y: 0.0, normalized: true}
        fire_interval: 0.40
        projectile: {speed: 72, radius: 3, damage: 3}

enemy_moves:
  - id: ember_drill_move
    name: Ember Drill
    weight: 1
    defense_sequence: ember_drill
~~~

The existing battle keys remain accepted as compatibility aliases:

| Preferred new key | Compatibility alias | Meaning |
| --- | --- | --- |
| defense_sequences | enemy_patterns | The battle-local sequence collection |
| enemy_moves[].defense_sequence | enemy_moves[].pattern | The selected sequence id |
| type: aimed_stream | legacy timeline actions | New generic patterns replace the old primitive timeline for new content |

Do not declare both spellings for the same collection or move. Migrate a
battle one sequence at a time and retain its existing player_moves, QTE data,
and enemy move weights unchanged.

The normal enemy turn remains:

~~~text
player QTE -> enemy selects move -> telegraph/opening -> defense sequence
-> closing -> enemy resolution -> next battle state
~~~

Enemy moves can still be weighted, put in an enemy_sequence, enabled by
phases, or augmented using the existing battle rules. A defense sequence is
only the enemy's dodge phase; it is not a replacement for a player move.

## Sequence schema

Every standalone sequence is a mapping with the following fields.

| Field | Required | Default | Purpose |
| --- | --- | --- | --- |
| id | yes | — | Unique, non-empty sequence id |
| duration | yes | — | Total active sequence time in seconds |
| arena | yes for standalone content | battle arena | Local defense dimensions and player movement settings |
| pattern_groups | no | {} | Named reusable lists of timed pattern entries |
| patterns | yes | [] | Pattern entries and group references |
| seed | no | nondeterministic | Fixed random seed for reproducible testing/debugging |
| hit_invulnerability | no | battle default | Seconds after a successful hazard hit before another can apply |

All time values are seconds; all rates and speeds are per second. A standalone
library should always provide arena.width, arena.height, and arena.player_speed
so that it can be previewed independently. Battle-local placement fields such
as arena.x and arena.y remain presentation concerns; hazards use arena-local
coordinates.

~~~yaml
id: small_example
duration: 4.0
seed: 17
arena:
  width: 220
  height: 110
  player_speed: 120
  # Optional local safety inset used by arena constraints.
  padding: 0
patterns:
  - type: aimed_stream
    start: 0.0
    duration: 4.0
    origin: {x: 0.5, y: 0.0, normalized: true}
    fire_interval: 0.40
    projectile: {speed: 72, radius: 3, damage: 2}
~~~

### Coordinates, angles, and shared defaults

Positions are local to the defense arena. A coordinate without normalized:
true is measured in local pixels. With normalized: true, x and y use 0.0 at
the left/top and 1.0 at the right/bottom. This works equally for origin,
center, position, and other point fields.

Angles are in degrees: 0 points right, 90 points down, 180 points left, and
-90 points up, matching the screen coordinate system. Positive angular speed
turns clockwise unless clockwise: false is explicitly supplied.

Sensible defaults keep short content short:

| Omitted field | Default |
| --- | --- |
| pattern.start | 0.0 |
| pattern.repeat | one occurrence |
| telegraph | no warning/preview |
| projectile.damage | 1 |
| projectile.radius | 3 local pixels |
| projectile.color | the renderer's default hostile color |
| projectile.lifetime | until it leaves the arena, unless bouncing/persistent |
| projectile.destroy_outside_arena | true |
| sprite | native geometric rendering |
| hit_invulnerability | the battle/controller default |

Pattern-specific fields still have meaningful defaults, but authors should
write speed, interval, count, gap size, and warning length whenever they are
important to the intended difficulty. Validation identifies errors with a
sequence id and YAML path, such as
defense_sequences.fire_spiral.patterns[1].projectile.speed.

## Common pattern entry

Each direct pattern entry has type, start, and duration. A duration limits how
long an emitter or constraint creates hazards; hazards that have their own
lifetime may remain briefly after the emitter finishes.

~~~yaml
- type: aimed_stream
  start: 1.0
  duration: 2.5
  repeat:
    count: 3
    interval: 3.0
  origin: {x: 0.5, y: 0.0, normalized: true}
  fire_interval: 0.30
  projectile:
    speed: 86
    radius: 3
    damage: 3
~~~

repeat.count is the total number of starts, including the first one.
repeat.interval is measured start-to-start. Optional repeat.delay waits before
the first repeated start; use it when a group needs a lead-in. Repeated
instances get their own timers and may overlap if interval is shorter than
duration.

## Composition and reusable groups

Patterns are concurrent by design. Entries with overlapping start/duration
windows all update and render together. This is the ordinary way to build
crossfire, herding, pressure plus telegraphs, and boss phases.

pattern_groups is a mapping from a name to relative-time entries. A group
reference is a composition entry rather than a concrete type: its start shifts
every child entry and its repeat repeats the whole group.

~~~yaml
pattern_groups:
  pressure_pair:
    - type: aimed_stream
      start: 0.0
      duration: 2.2
      origin: {x: 0.5, y: 0.0, normalized: true}
      fire_interval: 0.38
      projectile: {speed: 68, damage: 2}
    - type: falling_rain
      start: 0.6
      duration: 1.6
      spawn_interval: 0.28
      projectile: {speed: 58, damage: 2}

patterns:
  - group: pressure_pair
    start: 1.0
    repeat: {count: 2, interval: 3.0}
~~~

Keep group child starts relative to the group. A group reference must name an
existing group; recursive groups and malformed repeat ranges are rejected
during validation.

## Random values and deterministic debugging

Anywhere a numeric value is accepted, a literal may be replaced by a range or
choice list:

~~~yaml
projectile:
  speed: {min: 65, max: 82}
  radius: {choices: [2, 3, 3, 4]}
angle: {choices: [-20, 0, 20]}
gap_position: random
~~~

A range samples uniformly once at the point the property is resolved. Repeated
shots therefore may vary without making an already-spawned projectile change
speed. choices may deliberately repeat an item to weight it. Use random for
pattern fields that support a random position or lane, and add a sequence
seed while designing, testing, or reporting a bug. Omit seed for normal
unpredictable play.

Avoid randomizing every pressure source at once. One predictable element plus
one limited random element gives players enough information to make a fair
decision.

## Difficulty overrides

Difficulty is data, not a second set of Python pattern classes. Put a
difficulty mapping on the concrete pattern whose values should vary; the
selected level is deep-merged over that pattern before it starts. Level keys
may be named (easy, normal, hard) or numbered (1, 2, 3) when the battle uses
numbered difficulty.

Select a sequence-wide level with `difficulty_level: hard`, or select it per
enemy move without duplicating the sequence:

~~~yaml
enemy_moves:
  - id: hard_ember_drill
    name: Ember Drill
    defense_sequence: ember_drill
    defense_difficulty: hard
~~~

~~~yaml
- type: moving_gap_wall
  start: 0.0
  duration: 4.5
  direction: top_to_bottom
  wall_speed: 46
  spacing: 10
  gap_width: 34
  gap_movement: oscillate
  gap_speed: 16
  projectile: {radius: 4, damage: 3}
  difficulty:
    easy:
      wall_speed: 36
      gap_width: 44
      gap_speed: 10
    hard:
      wall_speed: 60
      gap_width: 24
      gap_speed: 28
      projectile: {damage: 4}
~~~

Higher speed, more projectiles, shorter intervals, smaller gaps, wider beams,
shorter telegraphs, stronger homing, and longer persistence are generally
harder. Prefer changing one or two of those axes per difficulty level, so
the move keeps its character rather than becoming a different attack.

## Telegraphs, sprites, and hazard appearance

Most dangerous region patterns accept a telegraph mapping. The reusable
telegraph renderer can preview a beam, lane, circle, strip, or target marker.

~~~yaml
telegraph:
  duration: 0.60
  flash_rate: 8
  alpha: 100
  color: [255, 210, 80]
~~~

An omitted telegraph means the hazard is active immediately. Pattern-specific
warning_duration is an equivalent convenience field for lane attacks, mines,
and telegraph strikes; prefer one spelling consistently within a sequence.

Projectiles draw as native circles by default. Supply a sprite only where art
adds useful identity:

~~~yaml
projectile:
  speed: 78
  damage: 4
  collision_radius: 4
  sprite: sprites/battle/fireball.png
  scale: 1.0
  rotation_mode: velocity  # none | velocity | fixed
~~~

collision_radius remains simple geometry and does not inspect transparent
pixels. Sprite paths are resolved by the story asset loader and validated
before play. The renderer caches image loads, scales, and practical rotations;
do not expect runtime code to load files every frame.

## Pattern reference

The registry maps a YAML type string to a pattern factory. The initial public
type names are:

| Type | Main use | Key configuration |
| --- | --- | --- |
| aimed_stream | Aim repeated shots at current player position | origin, fire_interval, spread, projectile |
| predictive_stream | Lead the player's motion | prediction_strength, player_velocity_weight, fire_interval |
| radial_burst | Pulse bullets outward, optionally in spirals | projectile_count, burst_interval, repetitions, initial_rotation_angle, orbital_speed, bursts |
| spiral | Rotating/pinwheel emitter | arms, angular_speed, fire_interval, clockwise |
| gap_wall | Moving wall with a safe opening | direction, wall_speed, spacing, gap_width, gap_position |
| moving_gap_wall | Gap wall whose opening moves | gap_movement, gap_speed, gap_bounds |
| sweeping_beam | Rotating or linear telegraphed laser | start_angle, end_angle, sweep_duration, width, telegraph |
| lane_attack | Make selected lanes dangerous | lane_count, active_lanes, warning_duration, active_duration |
| telegraph_strike | Warn then activate a shape | shape, region_count, position, warning_duration, active_duration |
| falling_rain | Repeated projectiles from an edge | direction, spawn_interval, spawn_distribution, projectile |
| crossfire | Multiple edge emitters | sides, fire_interval, stagger, aiming |
| chaser | Turn-limited pursuer | count, speed, turning_rate, lifetime |
| mine | Delayed/persistent hazard near player | placement_interval, warning_duration, activation_radius, persistence_duration |
| expanding_ring | Escape an outward-moving ring | center, starting_radius, expansion_speed, thickness, gaps |
| contracting_ring | Avoid an inward-moving ring | center, starting_radius, contraction_speed, thickness, gaps |
| bouncing_projectiles | Boundary-reflecting shots | spawn_interval, initial_angle, bounce_count, projectile |
| curving_projectiles | Arcing/corkscrew shots | initial_angle, motion.angular_velocity, motion.angular_acceleration |
| accelerating_stream | Shots whose speed changes over time | fire_interval, motion.initial_speed, motion.acceleration, motion.max_speed |
| wave_stream | Perpendicular sine-wave shots | forward_speed, wave_amplitude, wave_frequency, phase_offset |
| orbiting_hazards | Hazards circling a point | center, count, orbit_radius, angular_speed |
| shrinking_arena | Temporary movement constraint | start_bounds, end_bounds, shrink_duration, hold_duration, restore_duration |
| maze_corridor | Navigable walls/corridor | direction, segments, wall_thickness, gap_width, wall_speed |
| rhythm | Timed lane/zone beats | beats, warning_duration, active_duration |

### Shared projectile and motion fields

Projectile-producing patterns commonly use:

~~~yaml
projectile:
  speed: 80
  radius: 3
  collision_radius: 3
  damage: 2
  lifetime: 3.0
  color: [255, 100, 80]
  destroy_outside_arena: true
  motion:
    acceleration: 0
    max_speed: 140
    angular_velocity: 0
    angular_acceleration: 0
    wave_amplitude: 0
    wave_frequency: 0
    homing_strength: 0
    homing_duration: 0
~~~

Only specify modifiers the pattern needs. For example, curving_projectiles
uses angular velocity, accelerating_stream uses acceleration, wave_stream
uses the wave fields, and chaser uses turn-limited homing. Combining several
strong modifiers can make trajectories unreadable.

### Family notes

- aimed_stream and predictive_stream accept count and spread for a small
  volley. Predictive shots lead player velocity rather than perfectly tracking
  the player's current point.
- radial_burst may repeat with burst_interval. initial_rotation_angle rotates
  the entire attack in degrees, while angular_offset rotates each successive
  burst. orbital_speed (degrees per second) makes each projectile spiral around
  the burst center as it expands outward; negative values rotate the other way.
  bursts is an ordered sequence of per-burst overrides. repetitions repeats
  that whole sequence, so the number of emitted bursts is
  `len(bursts) * repetitions`; burst_interval must be positive when more than
  one burst will be emitted. The pattern automatically remains active long
  enough to emit the complete sequence.
  Use low counts for spoke-like patterns or high counts for rings. Its optional
  bursts list provides ordered per-burst overrides for initial_rotation_angle,
  orbital_speed, and projectile (including nested fields such as motion). A
  numeric field written as [minimum, maximum] is sampled once for that burst.

  ~~~yaml
  - type: radial_burst
    projectile_count: 8
    burst_interval: 0.6
    repetitions: 2
    initial_rotation_angle: 0
    orbital_speed: 0
    projectile: {speed: 64, radius: 3, damage: 2}
    bursts:
      - {initial_rotation_angle: 15, orbital_speed: 45,
         projectile: {speed: [55, 70], motion: {angular_velocity: [10, 25]}}}
      - {initial_rotation_angle: 65, orbital_speed: -35,
         projectile: {speed: [75, 90]}}
  ~~~

  The first bursts entry affects the first emission, the second affects the
  second, and repetitions restarts at the first entry after the last one.
  Ranges do not change the usual list forms for vector/color fields such as
  size, velocity, or color.
- gap walls travel in direction. gap_position may be a local coordinate,
  normalized point, random, or a resolver value. moving gaps use a movement
  style such as oscillate, linear, or random.
- sweeping_beam uses width and length as collision geometry. Give it a
  telegraph unless the sweep is deliberately an advanced reaction test.
- lane_attack uses lane indices from 0 through lane_count - 1. Its sequence
  form makes authored safe-lane rhythms clearer than a fully random layout.
- telegraph_strike supports circle, vertical_strip, horizontal_strip,
  rectangle, and line. Random placement obeys the arena bounds.
- falling_rain supports top_to_bottom, bottom_to_top, left_to_right, and
  right_to_left. crossfire's sides are left, right, top, and bottom.
- chaser turning_rate deliberately limits steering so the player can
  outmaneuver it. Mines warn first, then become active zones that can persist.
- ring gaps are angles or angular spans; use them to turn a ring from a pure
  timing check into a positioning decision.
- shrinking_arena only changes the active sequence's legal player bounds. It
  restores on completion, interruption, victory, and defeat.
- maze_corridor takes explicit segments rather than generating an opaque maze.
  Keep a fair route visible through telegraphs or moving openings.
- rhythm beats use time relative to the pattern start. A beat may choose lanes
  or named strike regions and can overlap the warning for the next beat.

## Writing a new YAML-only attack

1. Copy the closest document from the example library.
2. Give it a new id, sequence duration, and arena values.
3. Start with one pattern and explicit speed, damage, interval, and telegraph
   values.
4. Add a second pattern only after the first produces a clear movement task.
5. Use start and duration to overlap them deliberately; do not rely on update
   order.
6. Add a seed while tuning random attacks and remove it only if varied play is
   desired.
7. Test easy and hard overrides, including their smallest gap and shortest
   warning.
8. Add the id to defense_sequences and reference it from an enemy move.

Keep damage modest when hazards overlap. A sequence that forces a hit can
still feel fair when it gives a telegraph, a viable route, and enough
invulnerability time to prevent accidental multi-hit bursts.

## Adding a new Python pattern type

New mechanics should extend the registry rather than add a controller branch:

1. Implement the shared defense-pattern lifecycle and use the context to spawn
   reusable hazards.
2. Validate its YAML mapping, including useful path-aware errors and sensible
   defaults.
3. Register the factory under one new stable YAML type in
   PATTERN_TYPES / DEFENSE_PATTERN_TYPES (and only add an alias when it genuinely improves
   migration).
4. Return declarative hazard presentation data so native rendering and sprite
   overrides remain renderer-owned.
5. Add headless tests for timing, expiration, collision behavior, random seed
   behavior, malformed configuration, and a representative sequence.
6. Add a compact example to the library and document its parameters above.

Avoid embedding battle HP arithmetic, pygame surfaces, filesystem reads, or a
private frame loop in a pattern. Reusing the sequence scheduler and hazard
primitives is what lets new types compose with every existing type.
