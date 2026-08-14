import random

from engine.core.game_over import GameOverPresentation, GameOverStage


def test_game_over_get_up_waits_then_shakes_the_restored_heart_before_loading():
    game_over = GameOverPresentation(320, 180, random.Random(4))
    assert game_over.heart_sprite == "heart.png"

    changed, fade_music = game_over.update(1.0)
    assert changed and not fade_music
    assert game_over.stage == GameOverStage.MENU
    assert game_over.heart_sprite == "heart_break.png"
    game_over.update(game_over.TEXT_CHARACTER_DELAY)
    assert game_over.visible_text == "G"
    assert game_over.consume_audio_events() == []
    game_over.update(game_over.TEXT_CHARACTER_DELAY)
    assert game_over.consume_audio_events() == ["dialog_blip.wav"]
    assert not game_over.show_menu
    assert not game_over.choose_get_up()
    game_over.update((len(game_over.text) - 2) * game_over.TEXT_CHARACTER_DELAY + game_over.MENU_AFTER_TEXT_DELAY)
    assert game_over.consume_audio_events() == ["dialog_blip.wav", "dialog_blip.wav", "dialog_blip.wav"]
    assert game_over.show_menu

    assert game_over.choose_get_up()
    changed, fade_music = game_over.update(1.0)
    assert changed and not fade_music
    assert game_over.stage == GameOverStage.GET_UP_RESTORED
    assert game_over.heart_sprite == "heart.png"
    assert game_over.heart_shaking
    assert game_over.consume_audio_events() == ["heal.wav"]

    changed, fade_music = game_over.update(.25)
    assert changed and fade_music
    assert game_over.stage == GameOverStage.GET_UP_FADE
    assert game_over.heart_alpha == 255

    changed, fade_music = game_over.update(1.0)
    assert changed and not fade_music
    assert game_over.stage == GameOverStage.LOAD_SAVE
    assert not game_over.show_heart

    game_over.update(.99)
    assert not game_over.load_ready
    game_over.update(.01)
    assert game_over.load_ready


def test_game_over_die_waits_then_reuses_the_five_second_shard_presentation():
    game_over = GameOverPresentation(320, 180, random.Random(4))
    game_over.update(1.0)
    game_over.update(len(game_over.text) * game_over.TEXT_CHARACTER_DELAY + game_over.MENU_AFTER_TEXT_DELAY)
    assert game_over.consume_audio_events() == ["dialog_blip.wav"] * 4
    assert game_over.choose_die()

    game_over.update(.24)
    assert game_over.death_animation is None
    game_over.update(.01)
    assert game_over.stage == GameOverStage.DIE_SHATTER
    assert game_over.death_animation is not None
    assert game_over.death_animation.phase == "shards"
    assert game_over.consume_audio_events() == ["break2.wav"]

    game_over.update(4.99)
    assert not game_over.finished
    game_over.update(.01)
    assert game_over.finished
