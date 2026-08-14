import pytest

pygame = pytest.importorskip("pygame")

from engine.battle.controls import held_battle_input
from engine.render.controller_input import ControllerInput, CONTROLLER_BINDINGS, controller_binding, controller_control_from_event


def test_named_bindings_use_pygames_standard_controller_constants():
    expected = {
        "A": pygame.CONTROLLER_BUTTON_A,
        "B": pygame.CONTROLLER_BUTTON_B,
        "X": pygame.CONTROLLER_BUTTON_X,
        "Y": pygame.CONTROLLER_BUTTON_Y,
        "DPAD_UP": pygame.CONTROLLER_BUTTON_DPAD_UP,
        "DPAD_DOWN": pygame.CONTROLLER_BUTTON_DPAD_DOWN,
        "DPAD_LEFT": pygame.CONTROLLER_BUTTON_DPAD_LEFT,
        "DPAD_RIGHT": pygame.CONTROLLER_BUTTON_DPAD_RIGHT,
        "SELECT": pygame.CONTROLLER_BUTTON_BACK,
        "START": pygame.CONTROLLER_BUTTON_START,
        "LEFT_SHOULDER": pygame.CONTROLLER_BUTTON_LEFTSHOULDER,
        "RIGHT_SHOULDER": pygame.CONTROLLER_BUTTON_RIGHTSHOULDER,
        "LEFT_TRIGGER": pygame.CONTROLLER_AXIS_TRIGGERLEFT,
        "RIGHT_TRIGGER": pygame.CONTROLLER_AXIS_TRIGGERRIGHT,
    }
    assert set(CONTROLLER_BINDINGS) == set(expected)
    assert {control: controller_binding(pygame, control) for control in expected} == expected


def test_controller_button_event_resolves_to_its_named_binding():
    event = pygame.event.Event(pygame.CONTROLLERBUTTONDOWN, button=pygame.CONTROLLER_BUTTON_DPAD_LEFT)
    assert controller_control_from_event(event, pygame) == "DPAD_LEFT"


def test_raw_axis_and_hat_dpad_events_have_directional_fallbacks():
    assert controller_control_from_event(
        pygame.event.Event(pygame.JOYAXISMOTION, axis=0, value=-1.0), pygame
    ) == "DPAD_LEFT"
    assert controller_control_from_event(
        pygame.event.Event(pygame.JOYAXISMOTION, axis=1, value=1.0), pygame
    ) == "DPAD_DOWN"
    assert controller_control_from_event(
        pygame.event.Event(pygame.JOYHATMOTION, hat=0, value=(0, 1)), pygame
    ) == "DPAD_UP"


def _navigation_input():
    controller_input = ControllerInput.__new__(ControllerInput)
    controller_input.pygame = pygame
    controller_input._navigation_sources = {}
    return controller_input


def test_dpad_sources_merge_into_one_navigation_action():
    controller_input = _navigation_input()
    events = [
        pygame.event.Event(pygame.CONTROLLERBUTTONDOWN, button=pygame.CONTROLLER_BUTTON_DPAD_UP, instance_id=1),
        pygame.event.Event(pygame.CONTROLLERAXISMOTION, axis=1, value=-1.0, instance_id=1),
        pygame.event.Event(pygame.JOYAXISMOTION, axis=1, value=-1.0, instance_id=1),
    ]
    assert controller_input.navigation_actions(events) == ["UP"]
    assert controller_input.navigation_actions([
        pygame.event.Event(pygame.JOYAXISMOTION, axis=1, value=-1.0, instance_id=1)
    ]) == []


def test_dpad_axis_release_and_controller_axis_noise_do_not_navigate():
    controller_input = _navigation_input()
    assert controller_input.navigation_actions([
        pygame.event.Event(pygame.JOYAXISMOTION, axis=1, value=1.0, instance_id=1)
    ]) == ["DOWN"]
    assert controller_input.navigation_actions([
        pygame.event.Event(pygame.CONTROLLERAXISMOTION, axis=1, value=-1, instance_id=1),
        pygame.event.Event(pygame.JOYAXISMOTION, axis=1, value=0.0, instance_id=1),
    ]) == []


def test_hat_release_does_not_navigate_and_new_direction_moves_once():
    controller_input = _navigation_input()
    assert controller_input.navigation_actions([
        pygame.event.Event(pygame.JOYHATMOTION, hat=0, value=(-1, 0), instance_id=1)
    ]) == ["LEFT"]
    assert controller_input.navigation_actions([
        pygame.event.Event(pygame.JOYHATMOTION, hat=0, value=(0, 0), instance_id=1)
    ]) == []
    assert controller_input.navigation_actions([
        pygame.event.Event(pygame.JOYHATMOTION, hat=0, value=(1, 0), instance_id=1)
    ]) == ["RIGHT"]


class _PressedKeys:
    def __getitem__(self, _key):
        return False


class _Keyboard:
    def get_pressed(self):
        return _PressedKeys()


class _Pygame:
    K_d = K_RIGHT = K_a = K_LEFT = K_s = K_DOWN = K_w = K_UP = K_RETURN = K_KP_ENTER = 0
    key = _Keyboard()


class _ControllerInput:
    def __init__(self, *pressed):
        self.pressed = set(pressed)

    def is_pressed(self, control):
        return control in self.pressed


def test_held_battle_input_includes_dpad_and_a_button():
    input_state = held_battle_input(_Pygame(), _ControllerInput("DPAD_RIGHT", "DPAD_UP", "A"))
    assert input_state.move_x == 1
    assert input_state.move_y == -1
    assert input_state.attack_held is True
