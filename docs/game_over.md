# Game-over battle sequence

A final loss is configured in battle YAML, rather than by routing a story
choice to a `game_over` scene:

```yaml
on_lose:
  type: game_over
  music: game_over.ogg
  text: "Game over"
```

The heart holds through its shake, plays `break1.wav` when it splits, waits
one second before starting `music`, and shows the title and `Get up` / `Die`
menu one second later. The title always types on character by character with
`dialog_blip.wav`, and does not inherit a story's `instant_scene_text` setting.

`Get up` keeps the split heart visible for one second, then plays `heal.wav`
while the restored heart uses the same brief shake as the split transition.
It returns to the most recently saved authored checkpoint, not a game-over
screen or the player's regular manual save. Mark a scene as a checkpoint with:

```yaml
checkpoint: true
```

The snapshot is taken after that scene's entry actions run. A story should
mark its starting scene when it needs a recovery point before any later
checkpoint is reached.

For a `determined_revival`, a later non-revivable loss can use the same final
sequence by adding a nested `game_over` block:

```yaml
on_lose:
  type: determined_revival
  next_phase: revived_phase
  revived_hp: 1
  game_over:
    music: game_over.ogg
    text: "Game over"
```
