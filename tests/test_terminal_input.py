import pytest

pygame = pytest.importorskip("pygame")

from engine.render.terminal_input import (
    BACK, DOWN, LEFT, QUIT, RIGHT, SELECT, SELECT_RELEASE, UNKNOWN, UP,
    action_from_event, move_selection,
)


def _controller_event(event_type, button):
    return pygame.event.Event(event_type, button=button, instance_id=1)


def test_pygame_keydown_events_map_to_navigation_actions():
    assert action_from_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_w, mod=0)) == UP
    assert action_from_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_s, mod=0)) == DOWN
    assert action_from_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, mod=0)) == LEFT
    assert action_from_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_d, mod=0)) == RIGHT
    assert action_from_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_UP, mod=0)) == UP
    assert action_from_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DOWN, mod=0)) == DOWN
    assert action_from_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LEFT, mod=0)) == LEFT
    assert action_from_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT, mod=0)) == RIGHT
    assert action_from_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0)) == SELECT
    assert action_from_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_BACKSPACE, mod=0)) == BACK
    assert action_from_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_RETURN, mod=0)) == SELECT_RELEASE
    assert action_from_event(pygame.event.Event(pygame.QUIT)) == QUIT


def test_controller_events_map_to_the_same_navigation_actions():
    assert action_from_event(_controller_event(pygame.CONTROLLERBUTTONDOWN, pygame.CONTROLLER_BUTTON_A)) == SELECT
    assert action_from_event(_controller_event(pygame.CONTROLLERBUTTONUP, pygame.CONTROLLER_BUTTON_A)) == SELECT_RELEASE
    assert action_from_event(_controller_event(pygame.CONTROLLERBUTTONDOWN, pygame.CONTROLLER_BUTTON_B)) == BACK
    assert action_from_event(_controller_event(pygame.CONTROLLERBUTTONDOWN, pygame.CONTROLLER_BUTTON_DPAD_UP)) == UNKNOWN
    assert action_from_event(pygame.event.Event(pygame.JOYAXISMOTION, axis=1, value=-1.0)) == UNKNOWN
    assert action_from_event(_controller_event(pygame.CONTROLLERBUTTONUP, pygame.CONTROLLER_BUTTON_DPAD_UP)) == UNKNOWN


def test_unassigned_controller_bindings_leave_room_for_future_actions():
    for button in (pygame.CONTROLLER_BUTTON_X, pygame.CONTROLLER_BUTTON_Y,
                   pygame.CONTROLLER_BUTTON_BACK, pygame.CONTROLLER_BUTTON_START,
                   pygame.CONTROLLER_BUTTON_LEFTSHOULDER, pygame.CONTROLLER_BUTTON_RIGHTSHOULDER):
        assert action_from_event(_controller_event(pygame.CONTROLLERBUTTONDOWN, button)) == UNKNOWN


def test_selection_movement_is_bounded():
    assert move_selection(0, UP, 3) == 0
    assert move_selection(2, DOWN, 3) == 2
    assert move_selection(1, UP, 3) == 0
    assert move_selection(1, DOWN, 3) == 2
