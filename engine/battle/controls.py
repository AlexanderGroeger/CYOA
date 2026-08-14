"""Battle-specific input helpers.

The main frontend still maps KEYDOWN events to the engine's conventional
actions.  This module adds the held directional state needed by defense and
positioning patterns without changing unrelated story input.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BattleInput:
    move_x: float = 0.0
    move_y: float = 0.0
    attack_held: bool = False


def held_battle_input(pygame: Any, controller_input: Any | None = None) -> BattleInput:
    """Translate keyboard and controller held state into battle movement."""
    held = pygame.key.get_pressed()
    controller_pressed = controller_input.is_pressed if controller_input is not None else lambda _control: False
    move_x = (
        bool(held[pygame.K_d] or held[pygame.K_RIGHT] or controller_pressed("DPAD_RIGHT"))
        - bool(held[pygame.K_a] or held[pygame.K_LEFT] or controller_pressed("DPAD_LEFT"))
    )
    move_y = (
        bool(held[pygame.K_s] or held[pygame.K_DOWN] or controller_pressed("DPAD_DOWN"))
        - bool(held[pygame.K_w] or held[pygame.K_UP] or controller_pressed("DPAD_UP"))
    )
    return BattleInput(
        float(move_x),
        float(move_y),
        bool(held[pygame.K_RETURN] or held[pygame.K_KP_ENTER] or controller_pressed("A")),
    )
