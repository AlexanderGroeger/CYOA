"""Standardized game-controller bindings and held-input tracking.

pygame's raw joystick values vary by driver. The SDL Game Controller API
normalizes recognized devices to the ``CONTROLLER_*`` constants below, so the
same named controls can drive the game on more than one controller model.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


NAV_UP = "UP"
NAV_DOWN = "DOWN"
NAV_LEFT = "LEFT"
NAV_RIGHT = "RIGHT"
_NAVIGATION_ORDER = (NAV_UP, NAV_DOWN, NAV_LEFT, NAV_RIGHT)
_DPAD_ACTIONS = {
    "DPAD_UP": NAV_UP,
    "DPAD_DOWN": NAV_DOWN,
    "DPAD_LEFT": NAV_LEFT,
    "DPAD_RIGHT": NAV_RIGHT,
}


@dataclass(frozen=True)
class ControllerBinding:
    """One named control's pygame constant and its input kind."""

    pygame_constant: str
    kind: str = "button"


# The Xbox 360 capture in ``tests/controller_test.py`` reported A/B/X/Y as
# buttons 0--3, its D-pad as raw axes 0/1, and its left/right triggers as raw
# axes 4/5. SDL normally maps those raw values to these controller-level
# constants. Keeping every named control here makes future action bindings a
# one-line addition without reintroducing device-specific button numbers.
CONTROLLER_BINDINGS = MappingProxyType({
    "A": ControllerBinding("CONTROLLER_BUTTON_A"),
    "B": ControllerBinding("CONTROLLER_BUTTON_B"),
    "X": ControllerBinding("CONTROLLER_BUTTON_X"),
    "Y": ControllerBinding("CONTROLLER_BUTTON_Y"),
    "DPAD_UP": ControllerBinding("CONTROLLER_BUTTON_DPAD_UP"),
    "DPAD_DOWN": ControllerBinding("CONTROLLER_BUTTON_DPAD_DOWN"),
    "DPAD_LEFT": ControllerBinding("CONTROLLER_BUTTON_DPAD_LEFT"),
    "DPAD_RIGHT": ControllerBinding("CONTROLLER_BUTTON_DPAD_RIGHT"),
    "SELECT": ControllerBinding("CONTROLLER_BUTTON_BACK"),
    "START": ControllerBinding("CONTROLLER_BUTTON_START"),
    "LEFT_SHOULDER": ControllerBinding("CONTROLLER_BUTTON_LEFTSHOULDER"),
    "RIGHT_SHOULDER": ControllerBinding("CONTROLLER_BUTTON_RIGHTSHOULDER"),
    "LEFT_TRIGGER": ControllerBinding("CONTROLLER_AXIS_TRIGGERLEFT", "axis"),
    "RIGHT_TRIGGER": ControllerBinding("CONTROLLER_AXIS_TRIGGERRIGHT", "axis"),
})


def controller_binding(pygame: Any, control: str) -> int:
    """Return pygame's standardized code for a named controller control."""
    return getattr(pygame, CONTROLLER_BINDINGS[control].pygame_constant)


def controller_control_from_event(event: Any, pygame: Any) -> str | None:
    """Return a named control from a standardized or raw direction event.

    SDL's controller database maps a D-pad to controller buttons when the
    driver exposes it correctly. Some Windows drivers instead emit it as the
    first two joystick axes (as in the supplied Xbox 360 capture), so those
    normalized joystick axes and hats are a directional fallback as well.
    """
    if event.type in (pygame.CONTROLLERBUTTONDOWN, pygame.CONTROLLERBUTTONUP):
        button = getattr(event, "button", None)
        for control, binding in CONTROLLER_BINDINGS.items():
            if binding.kind == "button" and button == controller_binding(pygame, control):
                return control
        return None
    if event.type == pygame.JOYAXISMOTION:
        value = float(getattr(event, "value", 0.0))
        if getattr(event, "axis", -1) == 0:
            return "DPAD_LEFT" if value <= -0.5 else "DPAD_RIGHT" if value >= 0.5 else None
        if getattr(event, "axis", -1) == 1:
            return "DPAD_UP" if value <= -0.5 else "DPAD_DOWN" if value >= 0.5 else None
    if event.type == pygame.JOYHATMOTION:
        x, y = getattr(event, "value", (0, 0))
        if y > 0:
            return "DPAD_UP"
        if y < 0:
            return "DPAD_DOWN"
        if x < 0:
            return "DPAD_LEFT"
        if x > 0:
            return "DPAD_RIGHT"
    return None


class ControllerInput:
    """Own pygame-ce controller objects and expose their held button state."""

    def __init__(self, pygame: Any):
        self.pygame = pygame
        self._backend: Any | None = None
        self._controllers: dict[int, Any] = {}
        self._navigation_sources: dict[tuple[int, str, int], frozenset[str]] = {}
        try:
            from pygame._sdl2 import controller as backend

            if not backend.get_init():
                backend.init()
            self._backend = backend
            self._connect_existing()
        except (ImportError, pygame.error):
            # Controllers are optional. Keyboard input remains fully usable
            # when SDL cannot initialize this subsystem.
            self._backend = None

    def handle_events(self, events: list[Any]) -> None:
        """Track controller hot-plugging while the renderer drains events."""
        if self._backend is None:
            return
        for event in events:
            if event.type == self.pygame.CONTROLLERDEVICEADDED:
                self._connect(getattr(event, "device_index", -1))
            elif event.type == self.pygame.CONTROLLERDEVICEREMOVED:
                self._disconnect(getattr(event, "instance_id", -1))

    def navigation_actions(self, events: list[Any]) -> list[str]:
        """Emit one action when a controller D-pad direction becomes active.

        ``CONTROLLERAXISMOTION`` is intentionally not a navigation source.
        The diagnostic capture proved it duplicates ``JOYAXISMOTION`` for this
        receiver and can emit ``-1`` while releasing Down. Controller buttons,
        normalized joystick axes, and hats are each merged into one per-device
        directional state before an action can be emitted.
        """
        actions: list[str] = []
        for event in events:
            source: tuple[int, str, int] | None = None
            directions: frozenset[str] | None = None
            instance_id = self._instance_id(event)
            if event.type in (self.pygame.CONTROLLERBUTTONDOWN, self.pygame.CONTROLLERBUTTONUP):
                control = controller_control_from_event(event, self.pygame)
                if control in _DPAD_ACTIONS:
                    source = (instance_id, "button", int(getattr(event, "button", -1)))
                    directions = frozenset({_DPAD_ACTIONS[control]}) if event.type == self.pygame.CONTROLLERBUTTONDOWN else frozenset()
            elif event.type == self.pygame.JOYAXISMOTION:
                axis = int(getattr(event, "axis", -1))
                if axis in (0, 1):
                    source = (instance_id, "axis", axis)
                    directions = self._axis_directions(axis, float(getattr(event, "value", 0.0)))
            elif event.type == self.pygame.JOYHATMOTION:
                source = (instance_id, "hat", int(getattr(event, "hat", 0)))
                directions = self._hat_directions(getattr(event, "value", (0, 0)))
            if source is not None and directions is not None:
                actions.extend(self._update_navigation_source(source, directions))
        return actions

    def is_pressed(self, control: str) -> bool:
        """Whether any connected controller currently holds ``control``."""
        binding = CONTROLLER_BINDINGS[control]
        if binding.kind != "button":
            raise ValueError(f"{control} is an axis, not a button")
        button = controller_binding(self.pygame, control)
        for instance_id, controller in tuple(self._controllers.items()):
            try:
                if not controller.attached():
                    self._controllers.pop(instance_id, None)
                    continue
                if controller.get_button(button):
                    return True
                if control.startswith("DPAD_") and self._raw_direction_pressed(controller, control):
                    return True
            except self.pygame.error:
                self._controllers.pop(instance_id, None)
        return False

    def _connect_existing(self) -> None:
        assert self._backend is not None
        for index in range(self._backend.get_count()):
            self._connect(index)

    def _connect(self, device_index: int) -> None:
        if self._backend is None or device_index < 0:
            return
        try:
            controller = self._backend.Controller(device_index)
        except self.pygame.error:
            return
        self._controllers[controller.id] = controller

    def _disconnect(self, instance_id: int) -> None:
        self._navigation_sources = {
            source: directions for source, directions in self._navigation_sources.items()
            if source[0] != instance_id
        }
        controller = self._controllers.pop(instance_id, None)
        if controller is not None:
            try:
                controller.quit()
            except self.pygame.error:
                pass

    def _raw_direction_pressed(self, controller: Any, control: str) -> bool:
        """Read raw hat/axis direction for drivers without D-pad buttons."""
        joystick = controller.as_joystick()
        try:
            hat_x, hat_y = joystick.get_hat(0)
            if ((control == "DPAD_UP" and hat_y > 0)
                    or (control == "DPAD_DOWN" and hat_y < 0)
                    or (control == "DPAD_LEFT" and hat_x < 0)
                    or (control == "DPAD_RIGHT" and hat_x > 0)):
                return True
            axis = joystick.get_axis(0 if control in {"DPAD_LEFT", "DPAD_RIGHT"} else 1)
            return ((control in {"DPAD_LEFT", "DPAD_UP"} and axis <= -0.5)
                    or (control in {"DPAD_RIGHT", "DPAD_DOWN"} and axis >= 0.5))
        except self.pygame.error:
            return False

    @staticmethod
    def _instance_id(event: Any) -> int:
        """Read pygame-ce's SDL2 instance ID, including older ``which`` events."""
        return int(getattr(event, "instance_id", getattr(event, "which", -1)))

    @staticmethod
    def _axis_directions(axis: int, value: float) -> frozenset[str]:
        if axis == 0:
            return frozenset({NAV_LEFT}) if value <= -0.5 else frozenset({NAV_RIGHT}) if value >= 0.5 else frozenset()
        return frozenset({NAV_UP}) if value <= -0.5 else frozenset({NAV_DOWN}) if value >= 0.5 else frozenset()

    @staticmethod
    def _hat_directions(value: Any) -> frozenset[str]:
        x, y = value
        directions = set()
        if x < 0:
            directions.add(NAV_LEFT)
        elif x > 0:
            directions.add(NAV_RIGHT)
        if y < 0:
            directions.add(NAV_DOWN)
        elif y > 0:
            directions.add(NAV_UP)
        return frozenset(directions)

    def _update_navigation_source(self, source: tuple[int, str, int], directions: frozenset[str]) -> list[str]:
        before = self._active_directions(source[0])
        if directions:
            self._navigation_sources[source] = directions
        else:
            self._navigation_sources.pop(source, None)
        after = self._active_directions(source[0])
        return [action for action in _NAVIGATION_ORDER if action in after and action not in before]

    def _active_directions(self, instance_id: int) -> frozenset[str]:
        return frozenset().union(
            *(directions for source, directions in self._navigation_sources.items() if source[0] == instance_id)
        )
