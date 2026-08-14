import sys
from pathlib import Path

import pygame

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.render.controller_input import ControllerInput, controller_control_from_event
from engine.render.terminal_input import DOWN, LEFT, RIGHT, UP, action_from_event, move_selection

if __name__ != "__main__":
    import pytest

    pytest.skip("controller_test.py is an interactive diagnostic program", allow_module_level=True)

pygame.init()
pygame.joystick.init()

screen = pygame.display.set_mode((800, 500))
pygame.display.set_caption("Controller Input Mapper")
font = pygame.font.Font(None, 30)
clock = pygame.time.Clock()

if pygame.joystick.get_count() == 0:
    raise RuntimeError(
        "No controller detected. Make sure the 2.4 GHz receiver is connected "
        "before starting the program."
    )

controllers: dict[int, pygame.joystick.Joystick] = {}
controller_input = ControllerInput(pygame)

for index in range(pygame.joystick.get_count()):
    controller = pygame.joystick.Joystick(index)
    controller.init()
    controllers[controller.get_instance_id()] = controller

    print(f"Controller {index}: {controller.get_name()}")
    print(f"  GUID: {controller.get_guid()}")
    print(f"  Buttons: {controller.get_numbuttons()}")
    print(f"  Axes: {controller.get_numaxes()}")
    print(f"  Hats: {controller.get_numhats()}")

event_log: list[str] = []
selected = 2
frame_number = 0

def log(message: str) -> None:
    print(message)
    event_log.append(message)
    del event_log[:-14]


def _event_details(event: pygame.event.Event) -> str:
    """Format every field that can identify a duplicate input source."""
    fields = [f"type={pygame.event.event_name(event.type)}"]
    for name in ("instance_id", "which", "button", "axis", "value", "hat", "device_index"):
        if hasattr(event, name):
            fields.append(f"{name}={getattr(event, name)!r}")
    if event.type in (pygame.CONTROLLERBUTTONDOWN, pygame.JOYBUTTONDOWN):
        state = "press"
    elif event.type in (pygame.CONTROLLERBUTTONUP, pygame.JOYBUTTONUP):
        state = "release"
    elif event.type in (pygame.CONTROLLERAXISMOTION, pygame.JOYAXISMOTION):
        state = "neutral" if abs(float(event.value)) < .5 else "directional"
    elif event.type == pygame.JOYHATMOTION:
        state = "neutral" if event.value == (0, 0) else "directional"
    else:
        state = "other"
    return f"{' '.join(fields)} state={state}"


def _is_dpad_event(event: pygame.event.Event) -> bool:
    control = controller_control_from_event(event, pygame)
    return ((control is not None and control.startswith("DPAD_"))
            or (event.type in (pygame.CONTROLLERAXISMOTION, pygame.JOYAXISMOTION)
                and event.axis in (0, 1))
            or event.type == pygame.JOYHATMOTION)

running = True

while running:
    frame_number += 1
    raw_events = pygame.event.get()
    controller_input.handle_events(raw_events)
    navigation_actions = controller_input.navigation_actions(raw_events)

    for event in raw_events:
        action = action_from_event(event)
        if _is_dpad_event(event):
            log(
                f"frame={frame_number} RAW {_event_details(event)} "
                f"control={controller_control_from_event(event, pygame)} "
                f"direct_action={action} normalized_actions={navigation_actions}"
            )

    for event in raw_events:
        if event.type == pygame.QUIT:
            running = False

        elif event.type == pygame.JOYBUTTONDOWN:
            log(
                f"BUTTON DOWN: controller={event.instance_id}, "
                f"button={event.button}"
            )

        elif event.type == pygame.JOYBUTTONUP:
            log(
                f"BUTTON UP: controller={event.instance_id}, "
                f"button={event.button}"
            )

        elif event.type == pygame.JOYAXISMOTION:
            # Ignore minor analog-stick drift.
            if abs(event.value) >= 0.25:
                log(
                    f"AXIS: controller={event.instance_id}, "
                    f"axis={event.axis}, value={event.value:.3f}"
                )

        elif event.type == pygame.JOYHATMOTION:
            log(
                f"HAT: controller={event.instance_id}, "
                f"hat={event.hat}, value={event.value}"
            )

        elif event.type == pygame.JOYDEVICEADDED:
            controller = pygame.joystick.Joystick(event.device_index)
            controller.init()
            controllers[controller.get_instance_id()] = controller
            log(f"CONNECTED: {controller.get_name()}")

        elif event.type == pygame.JOYDEVICEREMOVED:
            controllers.pop(event.instance_id, None)
            log(f"DISCONNECTED: controller={event.instance_id}")

    for action in navigation_actions:
        if action in (UP, DOWN, LEFT, RIGHT):
            before = selected
            selected = move_selection(selected, action, 5)
            log(
                f"frame={frame_number} APPLY engine.render.terminal_input.move_selection "
                f"source=ControllerInput.navigation_actions action={action} selected={before}->{selected}"
            )

    screen.fill((25, 25, 25))

    title = font.render(
        "Press controller buttons, sticks, triggers, and D-pad",
        True,
        (240, 240, 240),
    )
    screen.blit(title, (20, 20))

    y = 65
    for line in event_log:
        rendered = font.render(line, True, (210, 210, 210))
        screen.blit(rendered, (20, y))
        y += 29

    pygame.display.flip()
    clock.tick(60)

pygame.quit()
