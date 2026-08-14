"""Headless coverage for the reusable player attack QTE framework."""

from __future__ import annotations

import math
import random

import pytest

from engine.battle.config import load_battle_config
from engine.battle.controls import BattleInput
from engine.battle.qte import (
    ChargeReleaseQTE,
    DirectionalComboQTE,
    MovingWeakPointQTE,
    PrecisionBarQTE,
    QuickSlashQTE,
    QTE_REGISTRY,
    RhythmComboQTE,
    ShrinkingRingQTE,
    StabilityQTE,
    RotatingStrikeQTE,
    create_attack_qte,
    result_for_score,
    tier_for_score,
)
from engine.errors import BattleConfigError


def test_shared_score_threshold_boundaries_are_inclusive():
    thresholds = {"weak": 0.25, "strong": 0.70, "critical": 0.95}
    assert tier_for_score(.249999, thresholds) == "miss"
    assert tier_for_score(.25, thresholds) == "weak"
    assert tier_for_score(.70, thresholds) == "strong"
    assert tier_for_score(.95, thresholds) == "critical"
    result = result_for_score(.70, thresholds, {"miss": 0, "weak": .65, "strong": 1, "critical": 1.35})
    assert (result.tier, result.score, result.multiplier) == ("strong", .70, 1)


@pytest.mark.parametrize("qte", [
    PrecisionBarQTE(duration=.1), ShrinkingRingQTE(duration=.1),
    RotatingStrikeQTE(duration=.1), DirectionalComboQTE(duration=.1, initial_speed=1), RhythmComboQTE(duration=.1),
    MovingWeakPointQTE(duration=.1), StabilityQTE(duration=.1),
])
def test_all_qtes_timeout_to_a_structured_miss(qte):
    for _ in range(10):  # Collapse/fade states finish after their visual hold.
        qte.update(.1)
        if qte.done:
            break
    assert qte.done
    assert qte.result.tier == "miss"
    assert qte.result.score == 0
    assert qte.result.metrics["timeout"] is True


def _tap_charge_release(qte: ChargeReleaseQTE, presses: int = 1) -> None:
    for _ in range(presses):
        assert qte.handle_action("SELECT")
        assert qte.handle_action("SELECT_RELEASE")


def _confirm_charge_release_strike(qte: ChargeReleaseQTE) -> None:
    """Advance into the small release arc and land its required Enter press."""
    assert qte.state == qte.RELEASING
    strike_angle = (qte.release_strike_arc_start_degrees + qte.release_strike_arc_end_degrees) / 2
    progress = (1 - strike_angle / qte.release_start_angle) ** (1 / 3)
    qte.update(qte.swing_duration_seconds * progress - qte.release_elapsed)
    assert qte.release_strike_arc_start_degrees <= qte.mallet_angle <= qte.release_strike_arc_end_degrees
    assert qte.handle_action("SELECT")


def test_charge_release_uses_discrete_taps_and_adjacent_angle_bands():
    qte = ChargeReleaseQTE(charge_step_degrees=5, minimum_charge_step_degrees=5,
                            arc_start_min_degrees=120, arc_start_max_degrees=120)
    assert qte.presentation()["scoring_arcs"] == {
        "weak": (120, 150), "strong": (150, 160), "critical": (160, 165),
    }

    assert qte.handle_action("SELECT")
    assert qte.handle_action("SELECT")  # Repeated key-down while held is ignored.
    assert qte.accepted_press_count == 1
    assert qte.target_charge_angle == 5
    assert qte.handle_action("SELECT_RELEASE")
    _tap_charge_release(qte, 29)  # 30 accepted presses means exactly 150 degrees.

    qte.update(.333)
    assert qte.state == qte.RELEASING
    assert qte.released_tier == "strong"  # weak's upper edge belongs to strong
    assert qte.presentation()["mallet_angle"] == pytest.approx(150)
    _confirm_charge_release_strike(qte)
    qte.update(.5)
    assert qte.done
    assert qte.result.tier == "strong"
    assert qte.result.metrics["release_strike_confirmed"] is True
    assert qte.result.metrics["charged_angle_degrees"] == 150


def test_charge_release_tweens_toward_new_targets_and_accelerates_with_more_taps():
    qte = ChargeReleaseQTE(charge_step_degrees=5, minimum_charge_step_degrees=5,
                            release_delay_seconds=1.0)
    _tap_charge_release(qte)
    assert qte.target_charge_angle == 5
    assert qte.presentation()["mallet_angle"] == 0

    qte.update(.25)
    assert qte.presentation()["mallet_angle"] == pytest.approx(2.5)
    _tap_charge_release(qte)
    assert qte.target_charge_angle == 10

    qte.update(.25)
    # The first tween moved 2.5 degrees in a quarter second. The new target
    # is 7.5 degrees away, so its fresh half-second tween moves 3.75 degrees
    # over the same interval.
    assert qte.presentation()["mallet_angle"] == pytest.approx(6.25)
    qte.update(.25)
    assert qte.presentation()["mallet_angle"] == pytest.approx(10)


def test_charge_release_charge_tween_duration_is_configurable():
    qte = ChargeReleaseQTE(charge_tween_duration_seconds=.25, release_delay_seconds=1.0)
    _tap_charge_release(qte)
    qte.update(.25)
    assert qte.presentation()["mallet_angle"] == pytest.approx(15)
    assert qte.presentation()["charge_tween_duration_seconds"] == .25


def test_charge_release_critical_endpoint_is_inclusive_and_miss_breaks_the_mallet():
    critical = ChargeReleaseQTE(charge_step_degrees=5, minimum_charge_step_degrees=5,
                                arc_start_min_degrees=120, arc_start_max_degrees=120)
    _tap_charge_release(critical, 33)  # 165 degrees, the final critical endpoint.
    critical.update(.333)
    assert critical.released_tier == "critical"
    _confirm_charge_release_strike(critical)
    critical.update(.5)
    assert critical.done and critical.result.tier == "critical"

    missed = ChargeReleaseQTE(charge_step_degrees=5, minimum_charge_step_degrees=5,
                              arc_start_min_degrees=120, arc_start_max_degrees=120)
    _tap_charge_release(missed)
    missed.update(.333)
    snapshot = missed.presentation()
    assert missed.state == missed.FAILED_RELEASE
    assert snapshot["head_detached"] is True
    assert snapshot["detached_head"]["angle"] == 5
    missed.update(.25)
    assert missed.done and missed.result.tier == "miss"
    assert missed.result.metrics["released"] is True
    assert missed.result.metrics["release_strike_confirmed"] is False


def test_charge_release_consumes_a_long_frame_using_the_release_deadline():
    qte = ChargeReleaseQTE()
    _tap_charge_release(qte)
    qte.update(2)
    assert qte.done
    assert qte.result.tier == "miss"
    assert qte.elapsed == pytest.approx(2)


def test_charge_release_missed_strike_flies_the_head_up_left_for_a_quarter_second():
    qte = ChargeReleaseQTE(charge_step_degrees=15, minimum_charge_step_degrees=15)
    _tap_charge_release(qte)
    qte.update(.333)
    qte.update(qte.release_strike_deadline_elapsed)
    assert qte.state == qte.FAILED_RELEASE
    assert qte.presentation()["detached_head"]["offset_x"] == 0
    assert qte.presentation()["detached_head"]["offset_y"] == 0

    qte.update(.125)
    head = qte.presentation()["detached_head"]
    assert head["offset_x"] < 0 and head["offset_y"] < 0
    assert not qte.done
    qte.update(.125)
    assert qte.done and qte.result.tier == "miss"


def test_charge_release_steps_start_high_then_fall_to_a_minimum():
    qte = ChargeReleaseQTE()
    expected_angles = (15, 29, 42, 54)
    for expected_angle in expected_angles:
        _tap_charge_release(qte)
        assert qte.target_charge_angle == expected_angle
    assert qte.last_charge_step_degrees == 12
    assert qte.presentation()["next_charge_step_degrees"] == 11

    _tap_charge_release(qte, 9)
    assert qte.last_charge_step_degrees == 3
    assert qte.presentation()["next_charge_step_degrees"] == 3


def test_charge_release_waits_for_its_first_tap_before_starting_the_inactivity_timer():
    qte = ChargeReleaseQTE()
    qte.update(10)
    assert qte.state == qte.CHARGING
    assert not qte.done

    _tap_charge_release(qte)
    qte.update(.332)
    assert qte.state == qte.CHARGING
    qte.update(.001)
    assert qte.state == qte.RELEASING


def test_charge_release_uses_the_seeded_battle_rng_for_one_arc_placement():
    move = {"qte": {"type": "charge_release", "parameters": {
        "arc_start_min_degrees": 100, "arc_start_max_degrees": 130,
    }}}
    first = create_attack_qte(move, random.Random(17))
    second = create_attack_qte(move, random.Random(17))

    assert isinstance(first, ChargeReleaseQTE)
    assert first.arc_start_degrees == second.arc_start_degrees
    assert 100 <= first.arc_start_degrees <= 130


class _DirectionSequence:
    """Small seeded-choice stand-in for exact outbound-path assertions."""

    def __init__(self, directions):
        self.directions = iter(directions)
        self.options: list[tuple[str, ...]] = []

    def choice(self, choices):
        self.options.append(tuple(choices))
        direction = next(self.directions)
        assert direction in choices
        return direction


def _direction_input(direction: str, attack_held: bool = False) -> BattleInput:
    return {
        "UP": BattleInput(move_y=-1, attack_held=attack_held),
        "DOWN": BattleInput(move_y=1, attack_held=attack_held),
        "LEFT": BattleInput(move_x=-1, attack_held=attack_held),
        "RIGHT": BattleInput(move_x=1, attack_held=attack_held),
    }[direction]


def _place_target_in_region(qte: DirectionalComboQTE, direction: str) -> None:
    left, top, width, height = qte.region_rect(direction)
    qte.target_x, qte.target_y = left + width / 2, top + height / 2
    qte.outbound_direction = direction
    qte.phase = "outbound"


def test_directional_combo_starts_centered_and_every_outbound_path_crosses_its_region():
    qte = DirectionalComboQTE(rng=random.Random(4))

    assert qte.presentation()["target"] == (.5, .5)
    for direction in qte.DIRECTION_ORDER:
        left, top, width, height = qte.region_rect(direction)
        center_x, center_y = left + width / 2, top + height / 2
        assert qte.target_overlaps_region(direction) is False
        if direction in {"UP", "DOWN"}:
            assert center_x == pytest.approx(.5)
        else:
            assert center_y == pytest.approx(.5)
        qte.target_x, qte.target_y = center_x, center_y
        assert qte.target_overlaps_region(direction)
        qte.target_x, qte.target_y = .5, .5


def test_directional_combo_target_motion_is_delta_time_independent():
    one_step = DirectionalComboQTE(initial_speed=.4, rng=_DirectionSequence(["LEFT"]))
    many_steps = DirectionalComboQTE(initial_speed=.4, rng=_DirectionSequence(["LEFT"]))

    one_step.update(.3)
    for _ in range(30):
        many_steps.update(.01)

    assert one_step.presentation()["target"] == pytest.approx(many_steps.presentation()["target"])


def test_directional_combo_direction_press_strikes_once_and_return_to_center():
    directions = _DirectionSequence(["UP", "RIGHT"])
    qte = DirectionalComboQTE(required_hits=3, initial_speed=.4, speed_increase=.1, rng=directions)
    _place_target_in_region(qte, "UP")
    qte.update(0, _direction_input("UP"))

    data = qte.presentation()
    assert {region["direction"] for region in data["regions"] if region["held"]} == {"UP"}
    assert not qte.handle_action("SELECT")
    assert qte.handle_action("UP")
    assert qte.hits == 1
    assert qte.phase == "returning"
    assert qte.presentation()["regions"][0]["flashing"]
    assert qte.handle_action("UP")  # One held direction cannot hit twice.
    assert qte.hits == 1

    qte.update(0, BattleInput())  # Direction released.
    assert not any(region["held"] for region in qte.presentation()["regions"])
    return_time = math.hypot(qte.target_x - .5, qte.target_y - .5) / qte.current_speed
    qte.update(return_time, BattleInput())
    assert qte.presentation()["target"] == pytest.approx((.5, .5))
    assert qte.phase == "outbound"
    assert qte.outbound_direction == "RIGHT"
    assert directions.options[1] == ("DOWN", "LEFT", "RIGHT")


def test_directional_combo_only_hits_matching_illuminated_region_and_caps_speed():
    qte = DirectionalComboQTE(required_hits=5, initial_speed=.1, speed_increase=.1,
                              max_speed_multiplier=3, rng=random.Random(2))
    _place_target_in_region(qte, "UP")
    qte.update(0, _direction_input("LEFT"))
    assert qte.handle_action("LEFT")
    assert qte.hits == 0
    assert qte.presentation()["target_tier"] == "miss"

    _place_target_in_region(qte, "UP")
    qte.update(0, BattleInput(move_x=1, move_y=-1))
    assert qte.held_directions == ("UP", "RIGHT")
    assert qte.handle_action("UP")
    assert qte.hits == 1  # A diagonal hold still evaluates only one target region.
    qte.update(0, BattleInput())

    for _ in range(4):
        _place_target_in_region(qte, "UP")
        qte.update(0, _direction_input("UP"))
        assert qte.handle_action("UP")
        qte.update(0, BattleInput())
    assert qte.hits == 5
    assert qte.current_speed == pytest.approx(.3)
    assert qte.current_speed == qte.maximum_speed


def test_directional_combo_escape_ratings_and_final_critical_pause():
    tiers = {0: "miss", 1: "weak", 2: "strong"}
    for hits, expected_tier in tiers.items():
        qte = DirectionalComboQTE(required_hits=3, strong_threshold_ratio=.67, initial_speed=1, rng=random.Random(1))
        qte.hits = hits
        qte.update(1, BattleInput())
        assert qte.done and qte.result.tier == expected_tier
        assert qte.result.metrics["escaped"] is True
        assert qte.result.metrics["completion"] == pytest.approx(hits / 3)

    qte = DirectionalComboQTE(required_hits=1, final_critical_pause=.2, rng=random.Random(1))
    _place_target_in_region(qte, "UP")
    qte.update(0, _direction_input("UP"))
    assert qte.handle_action("UP")
    assert qte.hits == 1
    assert qte.presentation()["target_tier"] == "critical"
    assert qte.input_locked and not qte.done
    assert any(region["flashing"] for region in qte.presentation()["regions"])
    frozen_target = qte.presentation()["target"]
    assert not qte.handle_action("UP")
    qte.update(.19, BattleInput())
    assert not qte.done and qte.presentation()["target"] == frozen_target
    qte.update(.01, BattleInput())
    assert qte.done and qte.result.tier == "critical"


def test_rhythm_penalties_and_moving_weak_point_use_new_distinct_rules():
    rhythm = RhythmComboQTE(duration=1.5, beats=[.5, 1.0], tolerance=.15)
    assert rhythm.presentation()["striking_x"] == .16
    assert {bar["lane"] for bar in rhythm.presentation()["bars"]} == {0}
    assert {bar["y"] for bar in rhythm.presentation()["bars"]} == {.5}
    assert rhythm.presentation()["penalty_markers"] == [False, False, False]
    rhythm.update(.5)
    rhythm.handle_action("SELECT")
    cleared_position = rhythm.presentation()["bars"][0]["position"]
    rhythm.update(.1)
    cleared_bar = rhythm.presentation()["bars"][0]
    assert cleared_bar["position"] == cleared_position  # Cleared bars freeze in place as they grow vertically.
    assert cleared_bar["vertical_scale"] > 1
    rhythm.update(.4)
    rhythm.handle_action("SELECT")
    rhythm.update(.25)  # Let the cleared bars grow/fade before resolving.
    assert rhythm.done and rhythm.result.tier == "critical"
    assert rhythm.result.metrics["penalties"] == 0

    target = MovingWeakPointQTE(duration=1.5, target_y=.4)
    target.update(.5)
    # With the launcher centered at the bottom, aim left toward the target
    # after it has begun its left-to-right pass.
    target.aim_angle = -110
    target.handle_action("SELECT")
    for _ in range(100):
        target.update(.01)
        if target.done:
            break
    assert target.result.tier in {"weak", "strong", "critical"}
    assert target.result.metrics["hit"] is True

    penalized = RhythmComboQTE(duration=1.2, beats=[.3, .6], tolerance=.1)
    penalized.handle_action("SELECT")  # Empty striking box: one penalty.
    penalized.update(.3)
    penalized.handle_action("SELECT")
    penalized.update(.3)
    penalized.handle_action("SELECT")
    penalized.update(.25)
    assert penalized.result.tier == "strong"
    assert penalized.result.metrics["penalties"] == 1


def test_rhythm_combo_ends_in_a_miss_after_three_penalties():
    rhythm = RhythmComboQTE(duration=2, beats=[1.0], tolerance=.1)

    for expected_markers in ([True, False, False], [True, True, False], [True, True, True]):
        assert rhythm.handle_action("SELECT")
        assert rhythm.presentation()["penalty_markers"] == expected_markers

    assert rhythm.done
    assert rhythm.result.tier == "miss"
    assert rhythm.result.metrics["penalties"] == 3


def test_rhythm_combo_expired_bars_do_not_spend_a_player_penalty_marker():
    rhythm = RhythmComboQTE(duration=2, beats=[.1, 1.5], tolerance=.14)
    rhythm.update(.3)  # The first bar has passed, while the second remains active.

    assert rhythm.presentation()["penalty_markers"] == [False, False, False]
    assert rhythm.handle_action("SELECT")
    assert rhythm.presentation()["penalty_markers"] == [True, False, False]


def test_rhythm_combo_scores_partial_bar_clearance_from_the_strong_threshold():
    one_hit = RhythmComboQTE(duration=1.2, beats=[.2, .4, .6, .8], tolerance=.14)
    one_hit.update(.2)
    assert one_hit.handle_action("SELECT")
    one_hit.update(.8)

    assert one_hit.done
    assert one_hit.result.tier == "weak"
    assert one_hit.result.metrics["hit_ratio"] == pytest.approx(.25)

    strong = RhythmComboQTE(duration=1.2, beats=[.2, .4, .6, .8], tolerance=.14)
    for _ in range(3):
        strong.update(.2)
        assert strong.handle_action("SELECT")
    strong.update(.4)

    assert strong.done
    assert strong.result.tier == "strong"


def test_rhythm_combo_critical_requires_every_bar_and_no_penalties():
    rhythm = RhythmComboQTE(duration=1, beats=[.1, .2, .3], tolerance=.14,
                             thresholds={"strong": .5, "critical": .6})
    rhythm.update(.1)
    assert rhythm.handle_action("SELECT")
    rhythm.update(.1)
    assert rhythm.handle_action("SELECT")
    rhythm.update(.3)

    # Two of three clears exceed the configured critical score threshold,
    # but an unhit bar still prevents a critical rhythm result.
    assert rhythm.done
    assert rhythm.result.tier == "strong"


def test_rhythm_combo_only_accepts_input_while_bars_overlap():
    rhythm = RhythmComboQTE(duration=2, beats=[1.0], tolerance=.14)

    # At half speed, the configured timing tolerance is still narrowed to
    # the span where the two .02-wide bars overlap.
    rhythm.update(.92)
    assert rhythm.handle_action("SELECT")
    assert rhythm.penalties == 1

    rhythm = RhythmComboQTE(duration=2, beats=[1.0], tolerance=.14)
    rhythm.update(.94)
    assert rhythm.handle_action("SELECT")
    assert rhythm.cleared == 1
    assert rhythm.presentation()["last_activation_hit"] is True
    assert rhythm.presentation()["activation_flash"] > 0

    missed = RhythmComboQTE(duration=2, beats=[1.0], tolerance=.14)
    assert missed.handle_action("SELECT")
    assert missed.presentation()["last_activation_hit"] is False
    assert missed.presentation()["activation_flash"] > 0


def test_rhythm_combo_randomizes_the_five_centered_bar_blocks():
    rhythm = RhythmComboQTE(duration=5, rng=random.Random(7))

    assert sorted(rhythm.block_counts) == [1, 1, 2, 2, 3]
    assert len(rhythm.beats) == 9
    block_duration = (rhythm.duration - rhythm.lead_in) / len(rhythm.block_counts)
    half_bar_duration = rhythm.TIMING_BAR_WIDTH / rhythm.approach_speed / 2
    block_margin = (1 - rhythm.GROUP_TIME_FRACTION) / 2
    offset = 0
    for block_index, count in enumerate(rhythm.block_counts):
        group = rhythm.beats[offset:offset + count]
        offset += count
        block_start = rhythm.lead_in + block_index * block_duration
        if count == 1:
            assert group == pytest.approx([block_start + block_duration / 2])
        else:
            if count == 3:
                assert group[0] - half_bar_duration == pytest.approx(block_start + block_duration * block_margin)
                assert group[-1] + half_bar_duration == pytest.approx(block_start + block_duration * (1 - block_margin))
        assert sum(group) / count == pytest.approx(block_start + block_duration / 2)
        if count > 1:
            spacings = [right - left for left, right in zip(group, group[1:])]
            assert spacings == pytest.approx([spacings[0]] * len(spacings))

    grouped_spacings = {
        count: rhythm.beats[sum(rhythm.block_counts[:index]) + 1] - rhythm.beats[sum(rhythm.block_counts[:index])]
        for index, count in enumerate(rhythm.block_counts) if count in {2, 3}
    }
    assert grouped_spacings[2] == pytest.approx(grouped_spacings[3])


def test_rhythm_combo_block_order_uses_the_seeded_battle_rng():
    move = {"qte": {"type": "rhythm_combo", "duration": 2.4}}
    first = create_attack_qte(move, random.Random(11))
    second = create_attack_qte(move, random.Random(11))

    assert isinstance(first, RhythmComboQTE)
    assert first.block_counts == second.block_counts
    assert first.beats == second.beats


def test_rhythm_combo_hit_pitch_escalates_only_within_its_block():
    rhythm = RhythmComboQTE(duration=4.8, rng=random.Random(3))
    triple_block = next(index for index, count in enumerate(rhythm.block_counts) if count == 3)
    triple_bars = [bar for bar in rhythm.bars if bar["block"] == triple_block]

    pitches = []
    for bar in triple_bars:
        rhythm.elapsed = bar["beat"]
        assert rhythm.handle_action("SELECT")
        pitches.append(rhythm.last_hit_pitch)

    assert pitches == pytest.approx([1.0, 1.059463, 1.059463 ** 2])

    single_block = next(index for index, count in enumerate(rhythm.block_counts) if count == 1)
    single_bar = next(bar for bar in rhythm.bars if bar["block"] == single_block)
    rhythm.elapsed = single_bar["beat"]
    assert rhythm.handle_action("SELECT")
    assert rhythm.last_hit_pitch == 1.0


def test_delta_time_updates_are_consistent_for_precision_and_aim_motion():
    one_step = PrecisionBarQTE(duration=2, target_position=.5)
    many_steps = PrecisionBarQTE(duration=2, target_position=.5)
    one_step.update(.75)
    for _ in range(75):
        many_steps.update(.01)
    assert one_step.indicator_position == pytest.approx(many_steps.indicator_position)

    input_state = BattleInput(move_x=1)
    one_step_target = MovingWeakPointQTE(duration=2)
    many_steps_target = MovingWeakPointQTE(duration=2)
    one_step_target.update(.5, input_state)
    for _ in range(50):
        many_steps_target.update(.01, input_state)
    assert one_step_target.aim_angle == pytest.approx(many_steps_target.aim_angle)


def test_precision_bar_renders_one_exact_critical_strip_center_frame_when_it_would_skip_it():
    qte = PrecisionBarQTE(duration=1, target_position=.75)
    qte.update(.80)

    assert qte.elapsed > qte.target_cross_time
    assert qte.presentation()["indicator"] == pytest.approx(qte.target_position)

    qte.update(.01)
    assert qte.presentation()["indicator"] != pytest.approx(qte.target_position)


def test_precision_bar_adjusts_a_slow_speed_to_reach_the_critical_strip_before_timeout():
    qte = PrecisionBarQTE(duration=2, target_position=.75, speed_multiplier=.1)

    assert qte.speed_multiplier == pytest.approx(1)
    assert qte.target_cross_time == pytest.approx(1.5)
    qte.update(qte.target_cross_time + .01)

    assert not qte.done
    assert qte.presentation()["indicator"] == pytest.approx(qte.target_position)


def test_moving_weak_point_uses_a_centered_upward_launcher_and_slower_crossing():
    qte = MovingWeakPointQTE(duration=2)
    assert qte.presentation()["launch"] == (.5, .86)
    assert qte.aim_angle == -90

    qte.update(qte.duration)
    # The target is still within the field because its default speed is a
    # slightly reduced multiplier of the former full-duration crossing.
    assert qte.target_x < 1


def test_moving_weak_point_randomizes_target_depth_and_launcher_offset():
    move = {"qte": {"type": "moving_weak_point", "duration": 1, "parameters": {
        "target_y": .24, "target_y_variance": .07, "launch_x_variance": .13,
    }}}
    first = create_attack_qte(move, random.Random(2))
    second = create_attack_qte(move, random.Random(3))

    assert .17 <= first.target_y <= .31
    assert .37 <= first.launch_x <= .63
    assert (first.target_y, first.launch_x) != (second.target_y, second.launch_x)


def test_moving_weak_point_checks_the_target_line_and_freezes_its_arrow_tip():
    qte = MovingWeakPointQTE(duration=10, target_y=.30, speed=1)
    qte.elapsed = 5  # Place the left-to-right target over the centered launcher.
    qte.handle_action("SELECT")
    for _ in range(100):
        qte.update(.01)
        if qte.impact_remaining is not None:
            break

    assert qte.impact_remaining is not None
    assert qte.presentation()["impact_distance"] is not None
    # The projectile coordinate is the arrow tip, which freezes on the target
    # line instead of snapping to the bullseye center.
    assert qte.projectile_y == pytest.approx(qte.target_y)
    assert (qte.projectile_x, qte.projectile_y) != (qte.target_x, qte.target_y)


def test_moving_weak_point_misses_after_the_arrow_passes_the_target_line():
    qte = MovingWeakPointQTE(duration=10, target_y=.30, speed=1, aim_angle=-45)
    qte.elapsed = 5
    qte.handle_action("SELECT")
    for _ in range(100):
        qte.update(.01)
        if qte.passed_target_at is not None:
            break

    assert qte.passed_target_at is not None
    assert not qte.done
    qte.update(.24)
    assert not qte.done
    qte.update(.01)
    assert qte.done and qte.result.metrics["passed_target"] is True


def test_shrinking_ring_collapses_after_player_steers_it_to_target():
    qte = ShrinkingRingQTE(duration=1, ring_x=.5, ring_y=.5, target_x=.5, target_y=.5)
    qte.update(1)
    assert not qte.done and qte.presentation()["collapsing"]
    qte.update(.18)
    assert qte.done and qte.result.tier == "critical"


def test_shrinking_ring_randomizes_its_target_and_starts_far_away():
    move = {"qte": {"type": "shrinking_ring", "duration": 1, "parameters": {
        "target_x_variance": .16, "target_y_variance": .13, "ring_min_distance": .45,
    }}}
    first = create_attack_qte(move, random.Random(2))
    second = create_attack_qte(move, random.Random(3))

    assert .34 <= first.target_x <= .66
    assert .37 <= first.target_y <= .63
    assert math.hypot(first.ring_x - first.target_x, first.ring_y - first.target_y) >= .45
    assert (first.target_x, first.target_y, first.ring_x, first.ring_y) != (
        second.target_x, second.target_y, second.ring_x, second.ring_y,
    )


def test_rotating_strike_requires_successive_colored_arcs():
    qte = RotatingStrikeQTE(duration=3, target_angle=0, rotations=3)
    for expected_next_tier in ("strong", "critical"):
        qte.elapsed = qte.target_cross_time
        assert qte.handle_action("SELECT")
        assert not qte.done and qte.current_tier == expected_next_tier
    qte.elapsed = qte.target_cross_time
    assert qte.handle_action("SELECT")
    assert qte.done and qte.result.tier == "critical"


def test_rotating_strike_stops_at_the_arc_center_on_the_final_strike():
    qte = RotatingStrikeQTE(duration=3, target_angle=0, rotations=3, critical_window=10)
    for _ in range(2):
        qte.elapsed = qte.target_cross_time
        assert qte.handle_action("SELECT")

    # A valid final hit is intentionally a little past the target.  The final
    # visual should still lock to the arc center instead of remaining past it.
    qte.elapsed = qte.target_cross_time + .01
    struck_angle = qte.angle
    assert qte.handle_action("SELECT")
    qte.elapsed += .5

    assert qte.done
    assert struck_angle != pytest.approx(qte.target_angle)
    assert qte.presentation()["angle"] == pytest.approx(qte.target_angle)


def test_rotating_strike_renders_one_exact_arc_center_frame_when_it_would_skip_it():
    qte = RotatingStrikeQTE(duration=3, target_angle=35, rotations=3)
    qte.update(qte.target_cross_time + .1)

    assert qte.elapsed > qte.target_cross_time
    assert qte.presentation()["angle"] == pytest.approx(qte.target_angle)

    qte.update(.01)
    assert qte.presentation()["angle"] != pytest.approx(qte.target_angle)


def test_rotating_strike_compounds_speed_after_each_successful_strike():
    qte = RotatingStrikeQTE(duration=3, target_angle=0, rotations=3)
    first_approach = qte.target_cross_time

    qte.elapsed = qte.target_cross_time
    assert qte.handle_action("SELECT")
    second_approach = qte.target_cross_time - qte.stage_started_at

    qte.elapsed = qte.target_cross_time
    assert qte.handle_action("SELECT")
    third_approach = qte.target_cross_time - qte.stage_started_at

    assert second_approach == pytest.approx((qte.duration / qte.rotations) / 1.25)
    assert third_approach == pytest.approx((qte.duration / qte.rotations) / 1.25 ** 2)
    assert first_approach == pytest.approx(.75 * qte.duration / qte.rotations)
    assert third_approach < second_approach


def test_rotating_strike_keeps_a_successful_first_arc_on_timeout():
    qte = RotatingStrikeQTE(duration=4.85, target_angle=35, rotations=3)
    hit_time = .75 * qte.duration / qte.rotations
    qte.elapsed = hit_time

    assert qte.handle_action("SELECT")
    assert qte.last_hit_tier == "weak"
    assert qte.success_flash > 0

    qte.update(qte.duration - qte.elapsed)
    assert qte.done and qte.result.tier == "weak"


def test_rotating_strike_pointer_vector_uses_the_same_angle_direction_as_pygame_arcs():
    qte = RotatingStrikeQTE(duration=3, target_angle=0, rotations=3)
    qte.elapsed = .75 * qte.duration / qte.rotations  # model angle 0: right
    assert qte.pointer_vector == pytest.approx((1, 0))

    qte.elapsed = .5 * qte.duration / qte.rotations  # model angle 90: up
    assert qte.pointer_vector == pytest.approx((0, -1))


def test_rotating_strike_misses_when_the_pointer_passes_the_active_arc():
    qte = RotatingStrikeQTE(duration=3, target_angle=0, rotations=3, weak_window=30)
    qte.elapsed = (.75 + 31 / 360) * qte.duration / qte.rotations
    qte.update(0)
    assert not qte.done
    qte.update(.251)
    assert qte.done and qte.result.tier == "miss"


def test_rotating_strike_late_enter_within_grace_saves_the_arc():
    qte = RotatingStrikeQTE(duration=3, target_angle=0, rotations=3, weak_window=30)
    qte.elapsed = qte.arc_pass_time + .2
    assert qte.handle_action("SELECT")
    assert qte.achieved_stage == 0 and not qte.done


@pytest.mark.parametrize("qte_type", sorted(QTE_REGISTRY))
def test_registry_creates_every_supported_qte(qte_type):
    move = {"qte": {"type": qte_type, "duration": 1}}
    qte = create_attack_qte(move, random.Random(2))
    assert qte.qte_type == qte_type
    assert qte.presentation()["kind"] == qte_type


def _minimal_battle_with_qte(qte: dict):
    return {
        "id": "qte_validation", "enemy": {"name": "Dummy", "hp": 5},
        "player_moves": [{"id": "test", "name": "Test", "base_power": 1, "qte": qte}],
        "enemy_patterns": [{"id": "wait", "duration": 0, "timeline": []}],
        "enemy_moves": [{"id": "wait", "name": "Wait", "pattern": "wait"}],
    }


def test_modern_qte_yaml_parsing_and_validation_errors_are_specific():
    config = load_battle_config(_minimal_battle_with_qte({
        "type": "shrinking_ring", "duration": 1.5, "difficulty": "hard",
        "thresholds": {"weak": .2, "strong": .7, "critical": .96},
        "damage_multipliers": {"miss": 0, "weak": .65, "strong": 1, "critical": 1.35},
        "parameters": {"starting_radius": .48, "target_radius": .16, "contraction_curve": "linear"},
        "label": "Arcane focus", "sound": "slash.wav", "animation": "rune_align",
    }), source="qte.yaml")
    assert config.player_moves["test"]["qte"]["type"] == "shrinking_ring"

    with pytest.raises(BattleConfigError, match=r"thresholds must satisfy weak < strong < critical"):
        load_battle_config(_minimal_battle_with_qte({"type": "precision_bar", "thresholds": {"weak": .8, "strong": .7}}), source="bad-qte.yaml")
    with pytest.raises(BattleConfigError, match=r"prompts must be a non-empty list"):
        load_battle_config(_minimal_battle_with_qte({"type": "directional_combo", "parameters": {"prompts": ["JUMP"]}}), source="bad-qte.yaml")


def test_charge_release_configuration_validates_its_meter_geometry():
    config = load_battle_config(_minimal_battle_with_qte({
        "type": "charge_release", "parameters": {
            "charge_step_degrees": 15, "charge_step_decrement_degrees": 1,
            "minimum_charge_step_degrees": 3, "charge_tween_duration_seconds": .25,
            "release_delay_seconds": .333, "release_strike_arc_start_degrees": 5,
            "release_strike_arc_end_degrees": 20,
            "swing_duration_seconds": .5, "arc_start_min_degrees": 100,
            "arc_start_max_degrees": 130, "weak_arc_width_degrees": 30,
            "strong_arc_width_degrees": 10, "critical_arc_width_degrees": 5,
        },
    }), source="mallet.yaml")
    assert config.player_moves["test"]["qte"]["type"] == "charge_release"

    with pytest.raises(BattleConfigError, match=r"must not exceed"):
        load_battle_config(_minimal_battle_with_qte({"type": "charge_release", "parameters": {
            "arc_start_min_degrees": 130, "arc_start_max_degrees": 100,
        }}), source="bad-mallet.yaml")
    with pytest.raises(BattleConfigError, match=r"must fit within the 180-degree meter"):
        load_battle_config(_minimal_battle_with_qte({"type": "charge_release", "parameters": {
            "arc_start_max_degrees": 140,
        }}), source="bad-mallet.yaml")
    with pytest.raises(BattleConfigError, match=r"minimum_charge_step_degrees must not exceed"):
        load_battle_config(_minimal_battle_with_qte({"type": "charge_release", "parameters": {
            "charge_step_degrees": 3, "minimum_charge_step_degrees": 4,
        }}), source="bad-mallet.yaml")
    with pytest.raises(BattleConfigError, match=r"release_strike_arc_start_degrees must not exceed"):
        load_battle_config(_minimal_battle_with_qte({"type": "charge_release", "parameters": {
            "release_strike_arc_start_degrees": 20, "release_strike_arc_end_degrees": 5,
        }}), source="bad-mallet.yaml")


def test_directional_combo_configuration_validates_gameplay_geometry_and_thresholds():
    config = load_battle_config(_minimal_battle_with_qte({
        "type": "directional_combo", "duration": 4.8, "parameters": {
            "required_hits": 3, "initial_speed": .4, "speed_increase": .08,
            "max_speed_multiplier": 3, "strong_threshold_ratio": .67,
            "striking_region_size": .18, "striking_region_inset": .07,
            "strike_flash_duration": .14, "final_critical_pause": .35, "target_radius": .025,
        },
    }), source="directional.yaml")
    assert config.player_moves["test"]["qte"]["parameters"]["required_hits"] == 3

    with pytest.raises(BattleConfigError, match=r"required_hits must be a positive integer"):
        load_battle_config(_minimal_battle_with_qte({"type": "directional_combo", "parameters": {"required_hits": 0}}), source="bad-qte.yaml")
    with pytest.raises(BattleConfigError, match=r"strong_threshold_ratio must be between"):
        load_battle_config(_minimal_battle_with_qte({"type": "directional_combo", "parameters": {"strong_threshold_ratio": 1.1}}), source="bad-qte.yaml")
    with pytest.raises(BattleConfigError, match=r"does not fit"):
        load_battle_config(_minimal_battle_with_qte({"type": "directional_combo", "parameters": {"striking_region_size": .4}}), source="bad-qte.yaml")


def _place_rapid_slash_blocks_in_region(qte: QuickSlashQTE, count: int | None = None) -> None:
    top = qte.slash_region_vertical_position - qte.block_height / 2
    for block in qte.blocks[:count]:
        block["top"] = top


def test_rapid_slash_requires_alternating_directions_and_cuts_each_block_once():
    qte = QuickSlashQTE(block_count=3, strong_threshold=2)
    _place_rapid_slash_blocks_in_region(qte)

    assert qte.presentation()["available_directions"] == ("LEFT", "RIGHT")
    assert qte.handle_action("LEFT")
    assert qte.hits == 1
    assert qte.blocks[0]["cut"] is True
    assert qte.next_direction == "RIGHT"
    assert qte.last_hit_pitch == 1.0
    assert not qte.handle_action("LEFT")
    assert qte.hits == 1

    assert qte.handle_action("RIGHT")
    assert qte.hits == 2
    assert qte.blocks[1]["cut"] is True
    assert qte.next_direction == "LEFT"
    assert qte.last_hit_pitch == pytest.approx(1.059463)
    assert qte.performance_tier == "strong"


def test_rapid_slash_valid_miss_flips_the_blade_without_creating_a_cut():
    qte = QuickSlashQTE()
    assert not qte._overlapping_uncut_blocks()

    assert qte.handle_action("RIGHT")
    assert qte.next_direction == "LEFT"
    assert qte.hits == 0
    assert qte.penalties == 1
    assert qte.presentation()["penalty_markers"] == [True, False, False, False, False]
    assert not any(block["cut"] for block in qte.blocks)
    assert qte.presentation()["slash_active"]


def test_rapid_slash_ends_with_its_current_performance_after_five_accepted_misses():
    qte = QuickSlashQTE()
    for action in ("LEFT", "RIGHT", "LEFT", "RIGHT", "LEFT"):
        assert qte.handle_action(action)

    assert qte.done
    assert qte.result.tier == "miss"
    assert qte.result.metrics["penalty_limit"] is True
    assert qte.presentation()["penalty_markers"] == [True] * 5


def test_rapid_slash_penalty_limit_preserves_a_strong_performance():
    qte = QuickSlashQTE(block_count=3, strong_threshold=2)
    _place_rapid_slash_blocks_in_region(qte, 2)
    assert qte.handle_action("LEFT")
    assert qte.handle_action("RIGHT")

    for action in ("LEFT", "RIGHT", "LEFT", "RIGHT", "LEFT"):
        assert qte.handle_action(action)

    assert qte.done
    assert qte.result.tier == "strong"
    assert qte.result.metrics["penalty_limit"] is True


def test_rapid_slash_blocks_are_seeded_staggered_and_split_while_falling():
    first = QuickSlashQTE(block_count=4, block_height=.1, block_spacing=[1, 2], rng=random.Random(7))
    second = QuickSlashQTE(block_count=4, block_height=.1, block_spacing=[1, 2], rng=random.Random(7))
    assert first.blocks == second.blocks
    assert all(first.blocks[index]["top"] > first.blocks[index + 1]["top"]
               for index in range(len(first.blocks) - 1))
    gaps = [float(first.blocks[index]["top"]) - float(first.blocks[index + 1]["top"]) - first.block_height
            for index in range(len(first.blocks) - 1)]
    assert all(any(gap == pytest.approx(multiplier * first.block_height) for multiplier in (1, 2))
               for gap in gaps)

    fixed_spacing = QuickSlashQTE(block_count=3, block_height=.1, block_spacing=1, rng=random.Random(7))
    fixed_gaps = [float(fixed_spacing.blocks[index]["top"]) - float(fixed_spacing.blocks[index + 1]["top"])
                  - fixed_spacing.block_height for index in range(len(fixed_spacing.blocks) - 1)]
    assert fixed_gaps == pytest.approx([fixed_spacing.block_height] * 2)

    _place_rapid_slash_blocks_in_region(first, 1)
    top_before = first.blocks[0]["top"]
    x_before = first.blocks[0]["x"]
    assert first.handle_action("LEFT")
    assert first.blocks[0]["cut"] is True
    assert first.blocks[0]["vertical_velocity"] == 0
    assert first.blocks[0]["horizontal_velocity"] == pytest.approx(-first.cut_horizontal_speed)
    first.update(.25)
    assert first.blocks[0]["top"] > top_before
    assert first.blocks[0]["vertical_velocity"] == pytest.approx(first.cut_gravity * .25)
    assert first.blocks[0]["x"] < x_before
    assert first.blocks[0]["separation"] == pytest.approx(first.half_separation_speed * .25)


def test_rapid_slash_cut_height_uses_the_strike_overlap_and_keeps_both_halves_large_enough():
    qte = QuickSlashQTE(block_count=1, block_height=.14, minimum_half_height=.04,
                         slash_region_height=.024, slash_region_vertical_position=.72)
    block = qte.blocks[0]
    block["top"] = .69  # The narrow region grazes the upper part of this block.

    assert qte.handle_action("LEFT")
    assert block["cut"] is True
    assert block["cut_offset"] == pytest.approx(.04)
    assert block["cut_offset"] >= qte.minimum_half_height
    assert qte.block_height - block["cut_offset"] >= qte.minimum_half_height
    assert block["cut_offset"] != pytest.approx(qte.block_height / 2)


def test_rapid_slash_scores_from_blocks_cut_and_finishes_after_the_sequence_exits():
    qte = QuickSlashQTE(block_count=3, strong_threshold=2)
    _place_rapid_slash_blocks_in_region(qte)

    assert qte.handle_action("LEFT")
    assert qte.performance_tier == "weak"
    assert qte.handle_action("RIGHT")
    assert qte.performance_tier == "strong"
    assert qte.handle_action("LEFT")
    assert qte.performance_tier == "critical"
    assert not qte.done
    for block in qte.blocks:
        block["top"] = .99
    qte.update(.1)
    assert qte.done
    assert qte.result.tier == "critical"
    assert qte.result.metrics["hits"] == 3


def test_rapid_slash_all_blocks_with_a_penalty_is_strong_not_critical():
    qte = QuickSlashQTE(block_count=3, strong_threshold=2)
    _place_rapid_slash_blocks_in_region(qte)

    assert qte.handle_action("LEFT")
    # Move the remaining blocks out of the strike region for one accepted
    # empty slash, then restore them to complete the sequence.
    for block in qte.blocks:
        if not block["cut"]:
            block["top"] = -1
    assert qte.handle_action("RIGHT")
    assert qte.penalties == 1
    _place_rapid_slash_blocks_in_region(qte)
    assert qte.handle_action("LEFT")
    assert qte.handle_action("RIGHT")
    assert qte.hits == qte.block_count
    assert qte.performance_tier == "strong"


def test_rapid_slash_exits_at_the_bottom_with_its_accumulated_performance():
    qte = QuickSlashQTE(block_count=4, strong_threshold=2)
    qte.hits = 2
    for block in qte.blocks:
        block["top"] = .99
    qte.update(.1)

    assert qte.done
    assert qte.result.tier == "strong"
    assert qte.result.metrics["block_count"] == 4


def test_rapid_slash_configuration_validates_all_tunable_gameplay_fields():
    config = load_battle_config(_minimal_battle_with_qte({
        "type": "rapid_slash", "parameters": {
            "block_count": 10, "block_fall_speed": .82, "block_height": .14, "block_width": .16,
            "block_spacing": [1, 2], "block_horizontal_offset": .28,
            "half_separation_speed": .09, "cut_gravity": 1.6, "cut_horizontal_speed": .12,
            "slash_animation_duration": .05, "slash_region_height": .14,
            "slash_region_vertical_position": .72, "minimum_half_height": .03, "strong_threshold": 7,
            "hit_sound_pitch_progression": False,
        },
    }), source="quick-slash.yaml")
    assert config.player_moves["test"]["qte"]["type"] == "rapid_slash"

    with pytest.raises(BattleConfigError, match=r"block_spacing must be a non-empty"):
        load_battle_config(_minimal_battle_with_qte({"type": "rapid_slash", "parameters": {"block_spacing": []}}),
                           source="bad-quick-slash.yaml")

    with pytest.raises(BattleConfigError, match=r"strong_threshold must not exceed block_count"):
        load_battle_config(_minimal_battle_with_qte({"type": "rapid_slash", "parameters": {"block_count": 8, "strong_threshold": 12}}),
                           source="bad-quick-slash.yaml")
    with pytest.raises(BattleConfigError, match=r"minimum_half_height must fit twice within block_height"):
        load_battle_config(_minimal_battle_with_qte({"type": "rapid_slash", "parameters": {"block_height": .1, "minimum_half_height": .06}}),
                           source="bad-quick-slash.yaml")
