# Determined revival battle sequence

A battle can replace its first normal loss with the `determined_revival`
cutscene.  This is configured in the battle YAML itself, beside `phases` and
regular battle dialogue:

```yaml
on_lose:
  type: determined_revival
  dialogue:
    - speaker: narrator
      text: "But the player refused to die."
  enemy_message:
    speaker: enemy
    text: "You are still standing?"
  # Optional; defaults to dialog_loud.wav. Played as the cutscene text typewrites.
  dialog_sound: dialog_loud.wav
  next_phase: revived_phase
  revived_hp: 1
  # Optional; false by default.  When false, a later loss uses normal death.
  repeatable: false

phases:
  - id: revived_phase
    name: True Resolve
    # This condition is bypassed by on_lose; it prevents automatic entry.
    when: {fight_flag: never_auto_activate}
    actions:
      - set_fight_flag: {revived: true}
      - add_enemy_move: final_assault
      - set_background: forest_angry.png
      - set_enemy_sprite: enemy_angry.png
```

`dialogue` and `enemy_message` use the existing battle-dialogue `text`
format. `speaker` is validated authoring metadata; the current modal battle
dialogue panel is text-only. Both dialogue fields are optional. `next_phase`
must name a phase in the same battle and `revived_hp` must be a positive
integer. The revival applies that phase's normal actions once before returning
the battle to the command menu.

`dialog_sound` selects the SFX used for the cutscene's typewriter dialogue,
including `enemy_message`; it defaults to `dialog_loud.wav`.

Any phase can use `set_background` and `set_enemy_sprite` with a non-empty
asset filename. The controller immediately exposes those assets to the normal
battle renderer; they remain active for the rest of that battle.

The sequence holds the authored `heart_break.png` split pose, starts
`refused_to_die.ogg` with a 0.5-second fade-in, waits one second, then
typewrites the revival dialogue. After the hero music begins, the restored
heart fades out, the screen holds black for one second, and then the new
battle background fades in.
After the dialogue, it fades the music over one second, switches to and briefly
shakes the restored `heart.png` with `heal.wav`, then plays `true_hero_intro.ogg`
once and queues
`true_hero_loop.ogg` as the gapless, indefinitely looping battle music for the
revived phase.
