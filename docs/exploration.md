# Exploration scenes

Exploration is opt-in. A scene with an exploration mapping enters the
non-combat loop after its applicable dialogue completes:

~~~text
scene dialogue -> Move | Look | Bag
                      |      |     |
                 destinations cursor inventory grid
~~~

Scenes without this mapping retain the original dialogue-and-choices flow.
The isolated reference network lives in
stories/demo_story/scenes/exploration_demo/. It is deliberately not linked
from demo_story's start path, so it can be copied into a story or selected as
that story's start_scene during author testing without changing the shipped
demo narrative.

## Scene shape

~~~yaml
id: study
background: study.png

exploration:
  cursor_speed: 210

  # The first matching entry is used. Conditions are optional.
  dialog:
    - conditions:
        all:
          - flag: key_found
            equals: false
      sequence: study_before_key
    - sequence: study_after_key

  dialogue_sequences:
    study_before_key:
      - "A dusty study waits for inspection."
    study_after_key: "The desk is empty now."

  navigation:
    - scene: hallway
      label: Hallway
    - scene: archive
      label: Hidden Archive
      conditions:
        all:
          - flag: archive_open
            equals: true
    - battle: wolf_fight
      label: Confront the wolf
      on_win: cave_entrance
      on_lose: forest_clearing
~~~

Each navigation entry has exactly one target: `scene` or `battle`. Battle
targets use the normal battle flow; `on_win` and `on_lose` optionally name the
scene to enter after that outcome. Without `on_win`, victory returns to the
exploration scene that started the battle.

dialogue_sequences accepts a string, a {text: ...} mapping, or a list of
strings. conditions supports all and any groups of {flag, equals} checks. Put
a catch-all dialogue entry last.

Set `once: true` on a dialog entry to persistently skip that entry after it
has started once; a later matching entry then becomes the repeat dialogue.
For an author-named first-visit flag, set `visit_flag: visited_study` in the
exploration mapping. The flag is set after dialogue resolution, so its first
entry is still available to that scene's conditions.

Look cursors use the shared cursor sprites automatically. The default is
`cursor/default.png`; inspect regions alternate between `inspect1.png` and
`inspect2.png`, and action regions alternate between `activate1.png` and
`activate2.png` every half second. Holding A or Enter uses `click.png`.

## Objects, regions, and events

Visible objects are drawn in ascending z order. An object may be gated with
visible_when; hidden objects are neither drawn nor interactive. A look target
can be an object hitbox or an invisible background region. Every target uses
`interaction: inspect` or `interaction: action`. A target activates only when
A/Enter is pressed and then released while the cursor remains over that same
target.

~~~yaml
objects:
  - id: desk_key
    sprite: key.png
    position: [312, 150]
    z: 12
    visible_when:
      all:
        - flag: key_found
          equals: false
    look:
      interaction: action
      rect: [312, 150, 24, 24]
      event: take_key

look_regions:
  - id: bookcase
    rect: [70, 54, 140, 126]
    interaction: inspect
    event: inspect_books
    priority: 2
  - id: hidden_switch
    rect: [106, 76, 38, 36]
    interaction: action
    event: open_archive
    priority: 20

look_events:
  take_key:
    actions:
      - type: dialog
        dialog: take_key_text
      - type: sound
        file: got_item.wav
      - type: give_item
        item: silver_key
        quantity: 1
      - type: set_flag
        flag: key_found
        value: true
~~~

The event actions run in list order. The reference content uses:

| Type | Required fields |
| --- | --- |
| dialog | dialog sequence id |
| sound | file SFX basename |
| animation | target object id, animation animation id |
| set_flag | flag, value |
| give_item | item, optional positive quantity |

When regions overlap, give the intended winner a higher priority. The
reference study's lamp_switch overlaps bookcase and has priority 20 versus 2,
making the special interaction unambiguous.

## Inventory content

Inventory grid dimensions are part of the player profile, separate from its
owned item list so legacy profiles remain valid:

~~~yaml
# player.yaml
inventory_ui: {columns: 4, rows: 3}
inventory: [field_ration]
~~~

New item definitions can declare an icon, details, action menu, and derived
equipment bonuses:

~~~yaml
restorative_tea:
  name: Restorative Tea
  icon: tea.png
  type: consumable
  description: Restores 10 HP.
  stats: {hp: 10, attack: 0, defense: 0}
  actions: [use, toss]
  use:
    actions:
      - type: heal
        amount: 10

archivist_blade:
  name: Archivist's Blade
  icon: blade.png
  type: weapon
  description: A balanced light weapon.
  equipment_slot: weapon
  stats: {hp: 0, attack: 2, defense: 0}
  actions: [equip, toss]

ember_seal:
  name: Ember Seal
  type: key
  description: A non-discardable archive key.
  actions: []
~~~

Icons are basenames from assets/items/; omitted icons use the inventory
fallback rather than failing legacy content. A declared icon must exist. The
context menu exposes only authored actions; equip becomes unequip when that
item occupies its slot. Items with no toss action never show Toss. Equipment
bonuses are derived from the current equipped items, rather than being
permanently applied to base stats.

## Authoring checklist

- Keep scene and battle ids unique and make every navigation target and battle
  outcome scene exist.
- Define every look.event in the scene's look_events mapping.
- Use unique object/region ids and positive [x, y, width, height] rects.
- Reference existing SFX, animations, item ids, and icon assets.
- Guard one-time rewards with a flag, then use that same flag in
  visible_when or dialogue conditions.
- Use distinct priorities for meaningful overlaps.

exploration_study.yaml, exploration_hall.yaml, and exploration_archive.yaml
collectively demonstrate these patterns, including flag-gated Move entries,
cursor variants, overlapping regions, conditional object visibility, a
sound-and-animation event, one-time item pickup, and Use / Equip / Toss /
non-tossable-key inventory content.
