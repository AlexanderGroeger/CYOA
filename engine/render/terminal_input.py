"""Pygame event-to-action mapping.

The module name remains for import compatibility with older integrations;
there is deliberately no terminal or stdin fallback in the pygame frontend.
"""

from __future__ import annotations

from typing import Any

from engine.render.controller_input import controller_control_from_event

UP = "UP"
DOWN = "DOWN"
LEFT = "LEFT"
RIGHT = "RIGHT"
SELECT = "SELECT"
SELECT_RELEASE = "SELECT_RELEASE"
BACK = "BACK"
QUIT = "QUIT"
SAVE = "SAVE"
LOAD = "LOAD"
UNKNOWN = "UNKNOWN"


def action_from_event(event: Any) -> str:
    """Map one pygame event to a discrete game action.

    Keyboard and standardized controller button presses both become the same
    engine actions, preventing per-frame key-repeat from moving a menu
    selection uncontrollably.
    """
    import pygame

    if event.type == pygame.QUIT:
        return QUIT
    controller_control = controller_control_from_event(event, pygame)
    if event.type == pygame.CONTROLLERBUTTONUP:
        return SELECT_RELEASE if controller_control == "A" else UNKNOWN
    if controller_control == "A":
        return SELECT
    controller_mapping = {
        "B": BACK,
    }
    if controller_control is not None:
        return controller_mapping.get(controller_control, UNKNOWN)
    if event.type == pygame.KEYUP:
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            return SELECT_RELEASE
        return UNKNOWN
    if event.type != pygame.KEYDOWN:
        return UNKNOWN
    mapping = {
        pygame.K_w: UP, pygame.K_UP: UP,
        pygame.K_s: DOWN, pygame.K_DOWN: DOWN,
        pygame.K_a: LEFT, pygame.K_LEFT: LEFT,
        pygame.K_d: RIGHT, pygame.K_RIGHT: RIGHT,
        pygame.K_RETURN: SELECT,
        pygame.K_KP_ENTER: SELECT,
        pygame.K_BACKSPACE: BACK,
        pygame.K_ESCAPE: QUIT,
    }
    if event.key == pygame.K_s and event.mod & pygame.KMOD_CTRL:
        return SAVE
    if event.key == pygame.K_l and event.mod & pygame.KMOD_CTRL:
        return LOAD
    return mapping.get(event.key, UNKNOWN)


def move_selection(selected: int, action: str, item_count: int) -> int:
    """Clamp vertical selection movement to the available option range."""
    if item_count <= 0:
        return 0
    if action in (UP, LEFT):
        return max(0, selected - 1)
    if action in (DOWN, RIGHT):
        return min(item_count - 1, selected + 1)
    return min(max(selected, 0), item_count - 1)
