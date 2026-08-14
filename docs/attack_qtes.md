# Attack QTEs

Player attacks use a reusable QTE runtime in `engine/battle/qte.py`. A QTE is
created by the registry, receives discrete engine actions plus delta-time
updates, and returns `QTEResult` when it completes:

```python
QTEResult(tier="strong", score=0.82, multiplier=1.0, metrics={...})
```

`score` is normalized from `0.0` to `1.0`; `tier` is always `miss`, `weak`,
`strong`, or `critical`. `metrics` holds mechanic-specific debugging/UI data
such as timing error, hit accuracy, completion, distance, or stability.
The battle controller alone applies `multiplier` to its centralized damage
formula after the QTE is complete.

## Move and player configuration

Move declarations live in one or more YAML files beneath a story's `moves/`
folder; battle YAML does not declare player moves. `player.yaml` owns the
starting learned move list and other player state. A move appears during a
fight only when it is learned and the equipped weapon grants its id through
`combat.move_grants`. Legacy `pattern`/`pattern_config` entries stay supported
(`timing_bar` maps to `precision_bar`; `position_target` maps to
`moving_weak_point`). All durations and beat times are seconds.

```yaml
# player.yaml
known_moves: [hammer_crush]
equipment: {weapon: iron_hammer}
```

```yaml
# moves/heavy_weapons.yaml
- id: hammer_crush
  name: Hammer Crush
  base_power: 11
  qte:
    type: charge_release
    difficulty: normal            # easy | normal | hard
    sound: slash.wav               # optional result SFX override
    animation: hammer_arc          # optional presentation identifier
    allowed_inputs: [SELECT]
    thresholds: {weak: 0.25, strong: 0.70, critical: 0.95}
    damage_multipliers: {miss: 0.0, weak: 0.65, strong: 1.0, critical: 1.35}
    parameters:
      charge_step_degrees: 5
      release_delay_seconds: 0.333
      swing_duration_seconds: 0.5
      arc_start_min_degrees: 100
      arc_start_max_degrees: 130
      weak_arc_width_degrees: 30
      strong_arc_width_degrees: 10
      critical_arc_width_degrees: 5
```

Thresholds must be normalized and ordered `weak < strong < critical`.
Validation runs when the battle loads and identifies the affected battle,
move, and field. Difficulty adjusts timing/aiming windows, motion speed, and
stability force predictably; authored values remain the source of truth.

## Available QTE types

| Type | Input and purpose | Common `parameters` |
| --- | --- | --- |
| `precision_bar` | Press A/Enter as a ping-pong marker crosses a target; sword strikes | `target_position`, `critical_window`, `strong_window`, `weak_window`, `speed_multiplier` |
| `charge_release` | Tap A/Enter rapidly to draw back a mallet, then let its rhythm release the strike | charge step, release/swing timing, scoring-arc placement and widths |
| `shrinking_ring` | Steer a shrinking ring to a bullseye; spell/gun focus | `starting_radius`, target/ring position, `target_x_variance` / `target_y_variance`, `ring_min_distance`, tolerances, `movement_speed` |
| `rotating_strike` | Clear weak, strong, then critical rotating arcs; sword/axe arc | `target_angle`, `rotations` (minimum 3), angle windows |
| `directional_combo` | Press the matching direction as a target crosses it; martial art/sword forms | `required_hits`, target speed fields, region geometry, `strong_threshold_ratio` |
| `rapid_slash` | Alternate Left/Right slashes through a staggered sequence of falling blocks; sword combo | block-count, fall, spacing, offset, split, and slash-region fields |
| `rhythm_combo` | Clear incoming bars inside a left-side striking box; volley/flurry | `beats`, `beat_count`, `tolerance`, `fade_duration` |
| `moving_weak_point` | Aim a slightly offset, upward-facing launcher left/right and fire one arrow at a crossing target; bow/firearm | `target_y`, `target_y_variance`, `target_radius`, `speed`, `launch_x_variance`, `aim_angle`, `aim_speed`, `arrow_speed` |
| `stability` | Correct a drifting marker with Left/Right; recoil/beam focus | `force`, `correction_speed`, `center_width` |

The renderer requests each QTE's `presentation()` snapshot and uses the
existing logical canvas, fonts, and palette. QTE classes do not import pygame,
enemies, or damage formulas. Their `tutorial_instruction` is retained for a
future guided/tutorial attack flow; live attacks use the full visual canvas
instead of displaying that text.

### Mallet charge-release configuration

`charge_release` begins with a mallet pointing right at `0` degrees. Each new
A/Enter key-down adds `charge_step_degrees` (15 by default), then reduces the
next step by `charge_step_decrement_degrees` (1 by default) down to
`minimum_charge_step_degrees` (3 by default). Key-up ends that physical press
so operating-system key-repeat cannot add free charge. The QTE
waits indefinitely for that first accepted press; after it, if no new accepted
press arrives for `release_delay_seconds` (0.333 by default), the stored
charged angle is scored and the mallet returns to zero over exactly
`swing_duration_seconds` (0.5 by default).

During that return swing, A/Enter must be pressed while the mallet crosses the
small release-strike arc. Its defaults are 5 through 20 degrees and can be
changed with `release_strike_arc_start_degrees` and
`release_strike_arc_end_degrees`. A press in that arc earns the previously
charged performance tier. Missing it detaches the head at the arc exit, sends
it up and left for 0.25 seconds, and resolves the QTE as a miss.

The displayed mallet uses a 0.5-second tween toward its discrete charged
target. Each additional tap retargets from its current rendered angle; because
the new target is farther away, the hammer visibly moves faster while the
authoritative scoring angle remains the exact tap-derived value.

Set `charge_tween_duration_seconds` to tune that visual response. It defaults
to `0.5`; the demo hammer uses `0.2` for a quicker pull-back.

The upper semicircle is grey except for one randomized consecutive scoring
band. The yellow weak arc, green strong arc, and red critical arc have default
widths 30, 10, and 5 degrees. Their yellow start is sampled once per QTE from
`arc_start_min_degrees` through `arc_start_max_degrees` (100 through 130 by
default). Boundaries are half-open for weak and strong, while the final
critical endpoint is inclusive: weak `[start, end)`, strong `[start, end)`,
critical `[start, end]`; all other angles miss. Missing the release-strike
window detaches the mallet head and sends it up-left before the miss resolves.

### Directional striking configuration

`directional_combo` starts its target in the canvas centre, then sends it
through one randomly selected cardinal striking region. Press that direction
(the controller D-pad, WASD, or arrow keys) while the target overlaps the illuminated
region. A hit returns the target visibly to centre, increases its speed, and
starts a new random outbound path. Escaping the canvas completes the QTE with
the hits already earned.

```yaml
qte:
  type: directional_combo
  duration: 4.8                 # retained for shared QTE presentation
  parameters:
    required_hits: 4
    initial_speed: 0.44          # normalized canvas lengths per second
    speed_increase: 0.08         # added after each successful hit
    max_speed_multiplier: 3.0
    strong_threshold_ratio: 0.70
    striking_region_size: 0.18
    striking_region_inset: 0.07
    strike_flash_duration: 0.14
    final_critical_pause: 0.35
    target_radius: 0.025
```

`strong_threshold_ratio` is rounded up, then clamped to the partial-hit range
`1..required_hits - 1` when there is more than one required hit. Zero hits are
always miss, all hits are always critical, and partial results below or at that
threshold are weak or strong respectively. The target uses the same provisional
result color while the QTE is active. `prompts`, `prompt_count`, and
`response_window` are accepted as legacy directional-combo fields but no
longer control gameplay.

### Quick-slash configuration

`rapid_slash` begins with either Left or Right available. Every accepted slash
changes the allowed direction; a slash that misses the region still changes
orientation, but only overlapping slashes cut one uncut block. The seeded
sequence uses random spacing and horizontal offsets. `block_spacing` is in
block heights: use one number for a constant gap, or a list to randomly choose
a gap multiplier for each block. Each cut block splits
across a horizontal line into upper and lower halves that continue to fall
while slowly separating. On impact, a block's downward velocity resets to
zero, then gravity resumes its fall while the halves drift in the slash direction.
The cut follows the block's overlap with the strike region, but
`minimum_half_height` keeps it far enough from either edge for both pieces to
remain visible.

```yaml
qte:
  type: rapid_slash
  parameters:
    block_count: 10
    block_fall_speed: 1.05           # normalized window heights / second
    block_height: 0.14
    block_width: 0.16
    block_spacing: [1, 2]             # gap is one or two block heights
    block_horizontal_offset: 0.16
    half_separation_speed: 0.09
    cut_gravity: 1.60
    cut_horizontal_speed: 0.12
    slash_animation_duration: 0.05
    slash_region_height: 0.024
    slash_region_vertical_position: 0.72
    minimum_half_height: 0.03
    strong_threshold: 7
    hit_sound_pitch_progression: true
```

Zero hits are Miss, 1 through `strong_threshold - 1` are Weak,
`strong_threshold` through `block_count - 1` are Strong, and cutting every
block with no penalties is Critical. A successful cut uses `slash.wav`; an accepted empty slash
uses `arrow.wav` at a 2.0 pitch/playback rate. Successful-cut pitch continues
to progress by `1.059463 ** successful_hits_before` unless
`hit_sound_pitch_progression` is disabled. Five accepted slashes that fail to
cut a block fill the bottom-centre penalty markers and immediately end the QTE
as a Miss.

## Adding a QTE

1. Add a small `AttackQTE` subclass in `engine/battle/qte.py` with
   `update(dt, input)`, `handle_action(action)`, and `presentation()`.
2. Score it with `result_for_score()` and include useful metrics.
3. Register it in `QTE_REGISTRY` and add its validation in `_validate_qte`.
4. Add a renderer branch for its presentation kind and headless tests.
5. Assign it to a move's `qte` block and optionally grant that move from a
   weapon's `combat.move_grants`.

See `stories/demo_story/moves/combat_moves.yaml` for sword, hammer, bow, gun,
magic, and martial-arts examples.
