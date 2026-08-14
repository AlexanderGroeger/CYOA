"""pygame-ce renderer for the logical story canvas."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from engine.core.asset_loader import AssetLoader
from engine.errors import AssetNotFoundError
from engine.render.controller_input import ControllerInput
from engine.render.display import DisplayConfig, centered_rect, chunk_lines, integer_scale

# Normalized logical-canvas UI defaults. Stories may override these through
# story.yaml's ``ui`` mapping; see README for the schema.
TEXT_BOX_ALPHA = 0.75
DIALOGUE_POSITION = (0.5, 0.84)
DIALOGUE_SIZE = (0.92, 0.18)
OPTIONS_POSITION = (0.5, 0.50)
OPTION_HIGHLIGHT_COLOR = (255, 214, 102)
SELECTED_OPTION_COLOR = (18, 18, 35)
TEXT_BOX_COLOR = (20, 20, 48)
TEXT_BOX_BORDER_COLOR = (105, 105, 145)
BATTLE_TITLE_SIZE = 28
BATTLE_TEXT_SIZE = 20
REVIVAL_DIALOGUE_TEXT_SIZE = BATTLE_TEXT_SIZE + 8
BATTLE_SMALL_TEXT_SIZE = 16
# The enemy occupies the center upper field.  Keep its speech panel just to
# the right, with a deliberately generous 70px edge margin on a 640px canvas.
OPPONENT_DIALOGUE_RECT = (425, 108, 145, 86)
OPPONENT_DIALOGUE_TEXT_SIZE = 16
POST_DEFEND_REMARK_RECT = (48, 324, 544, 28)
# Leave the lower-left gutter clear for the battle menu-state label.
ENVIRONMENT_DIALOGUE_RECT = (120, 324, 472, 28)
PLAYER_HURT_FLICKER_SECONDS = 0.06

class Renderer:
    """Owns pygame setup, cached assets, logical drawing, and presentation."""

    def __init__(self, assets: AssetLoader, display_config: DisplayConfig, render_config: dict[str, Any] | None = None):
        import pygame

        self.pygame = pygame
        self.assets = assets
        self.config = display_config
        self.render_config = render_config or {}
        self.ui_config = self.render_config.get("ui", {})
        pygame.init()
        self.controller_input = ControllerInput(pygame)
        self._controller_navigation_actions: list[str] = []
        desktop = pygame.display.get_desktop_sizes()[0]
        self.window = pygame.display.set_mode(desktop, pygame.FULLSCREEN)
        self.desktop_size = self.window.get_size()
        self.scale = integer_scale(*self.desktop_size, self.config.width, self.config.height)
        self.destination = centered_rect(*self.desktop_size, self.config.width, self.config.height, self.scale)
        self.surface = pygame.Surface((self.config.width, self.config.height)).convert()
        self.clock = pygame.time.Clock()
        self._images: dict[Path, Any] = {}
        self._scaled_images: dict[tuple[Path, tuple[int, int]], Any] = {}
        self._rotated_images: dict[tuple[Path, tuple[int, int], int], Any] = {}
        self._fonts: dict[int, Any] = {}
        self._text: dict[tuple[str, int, tuple[int, int, int]], Any] = {}
        self._animations: dict[str, tuple[list[list[str]], int, bool]] = {}
        self._animation_indices: dict[str, int] = {}
        self.dirty = True
        pygame.display.set_caption("CYOA Engine")

    def shutdown(self) -> None:
        self.pygame.quit()

    def tick(self, fps: int = 60) -> int:
        """Cap the frame rate and return elapsed milliseconds for battle timers."""
        return self.clock.tick(fps)

    def events(self) -> list[Any]:
        events = self.pygame.event.get()
        self.controller_input.handle_events(events)
        self._controller_navigation_actions = self.controller_input.navigation_actions(events)
        return events

    def controller_navigation_actions(self) -> list[str]:
        """Return this event batch's deduplicated controller navigation."""
        return list(self._controller_navigation_actions)

    def _font(self, size: int):
        size = max(8, size)
        if size not in self._fonts:
            font_path = Path(__file__).resolve().parents[2] / "shared_assets" / "fonts" / "BlockBlueprint.ttf"
            self._fonts[size] = self.pygame.font.Font(str(font_path) if font_path.exists() else None, size)
        return self._fonts[size]

    def _text_surface(self, text: str, size: int, color: tuple[int, int, int]):
        key = (text, size, color)
        if key not in self._text:
            self._text[key] = self._font(size).render(text, False, color)
        return self._text[key]

    def _image(self, category: str, filename: str):
        path = self.assets.resolve_asset_path(category, filename)
        if path not in self._images:
            self._images[path] = self.pygame.image.load(str(path)).convert_alpha()
        return path, self._images[path]

    def _image_reference(self, default_category: str, filename: str):
        """Load a normal category asset or an explicit story-relative asset.

        Scene exploration uses independently layered objects, so authors may
        keep an object beside a scene rather than flattening every image into
        ``assets/sprites``.  ``AssetLoader`` owns the path rules; this method
        only shares the renderer's image cache with legacy asset loading.
        """
        path = self.assets.resolve_asset_reference(filename, default_category)
        if path not in self._images:
            self._images[path] = self.pygame.image.load(str(path)).convert_alpha()
        return path, self._images[path]

    def _fit_image(self, path: Path, image: Any, bounds: tuple[int, int]):
        scale = min(bounds[0] / image.get_width(), bounds[1] / image.get_height(), 1.0)
        size = (max(1, int(image.get_width() * scale)), max(1, int(image.get_height() * scale)))
        key = (path, size)
        if key not in self._scaled_images:
            self._scaled_images[key] = self.pygame.transform.scale(image, size)
        return self._scaled_images[key]

    def _scaled_image(self, path: Path, image: Any, size: tuple[int, int]):
        key = (path, size)
        if key not in self._scaled_images:
            self._scaled_images[key] = self.pygame.transform.scale(image, size)
        return self._scaled_images[key]

    def _ui_position(self, key: str, default: tuple[float, float]) -> tuple[int, int]:
        value = self.ui_config.get(key, default)
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            value = default
        return (int(float(value[0]) * self.config.width), int(float(value[1]) * self.config.height))

    def _ui_size(self, key: str, default: tuple[float, float]) -> tuple[int, int]:
        value = self.ui_config.get(key, default)
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            value = default
        return (max(1, int(float(value[0]) * self.config.width)), max(1, int(float(value[1]) * self.config.height)))

    def _ui_color(self, key: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
        value = self.ui_config.get(key, default)
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            return default
        return tuple(max(0, min(255, int(channel))) for channel in value)

    def _dialogue_rect(self):
        width, height = self._ui_size("dialogue_size", DIALOGUE_SIZE)
        x, y = self._ui_position("dialogue_position", DIALOGUE_POSITION)
        return self.pygame.Rect(x - width // 2, y - height // 2, width, height)

    def _draw_transparent_box(self, rect: Any) -> None:
        alpha = max(0.0, min(1.0, float(self.ui_config.get("text_box_alpha", TEXT_BOX_ALPHA))))
        box = self.pygame.Surface(rect.size, self.pygame.SRCALPHA)
        box.fill((*TEXT_BOX_COLOR, round(255 * alpha)))
        self.pygame.draw.rect(box, (*TEXT_BOX_BORDER_COLOR, round(255 * alpha)), box.get_rect(), 1)
        self.surface.blit(box, rect.topleft)

    def _wrapped_lines(self, text: str, width: int, size: int) -> list[str]:
        """Wrap paragraph-preserving text for the logical dialogue region."""
        font = self._font(size)
        lines: list[str] = []
        for paragraph in text.splitlines() or [""]:
            if not paragraph.strip():
                lines.append("")
                continue
            current = ""
            for word in paragraph.split():
                candidate = f"{current} {word}".strip()
                if current and font.size(candidate)[0] > width:
                    lines.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                lines.append(current)
        return lines or [""]

    def prepare_dialogue_text(self, text: str, rect: Any, size: int) -> str:
        """Freeze all line breaks before dialogue is drawn or animated."""
        return "\n".join(self._wrapped_lines(text, rect.width, size))

    def paginate_text(self, text: str, font_size: int | None = None) -> list[str]:
        """Split dialogue into pages that fit entirely in the text region."""
        size = max(8, int(font_size or self.render_config.get("font_size", 14)))
        panel = self._dialogue_rect()
        text_rect = self.pygame.Rect(panel.x + 6, panel.y + 5, panel.width - 12, panel.height - 10)
        lines = self.prepare_dialogue_text(text, text_rect, size).split("\n")
        capacity = max(1, text_rect.height // self._font(size).get_linesize())
        return ["\n".join(page) for page in chunk_lines(lines, capacity)]

    def prepare_battle_dialogue(self, battle: Any) -> None:
        """Prepare opponent speech before its first typewriter update."""
        if battle.state.name != "DIALOGUE" or battle.dialogue_type != "opponent" or not battle.dialogue_text:
            return
        panel = self.pygame.Rect(OPPONENT_DIALOGUE_RECT)
        text_rect = self.pygame.Rect(panel.x + 6, panel.y + 6, panel.width - 12, panel.height - 12)
        battle.prepare_opponent_dialogue(self.prepare_dialogue_text(battle.dialogue_text, text_rect, OPPONENT_DIALOGUE_TEXT_SIZE))

    def _draw_prepared_text(self, text: str, rect: Any, size: int, color: tuple[int, int, int]) -> int:
        """Draw pre-wrapped dialogue verbatim; no reflow occurs while typing."""
        font = self._font(size)
        y = rect.y
        for line in text.split("\n"):
            self.surface.blit(self._text_surface(line, size, color), (rect.x, y))
            y += font.get_linesize()
        return y

    def _draw_single_line(self, text: str, rect: Any, size: int, color: tuple[int, int, int]) -> None:
        """Draw one clipped line, abbreviating rather than wrapping it."""
        line = " ".join(text.split())
        font = self._font(size)
        if font.size(line)[0] > rect.width:
            shortened = line
            while shortened and font.size(shortened + "...")[0] > rect.width:
                shortened = shortened[:-1]
            line = shortened.rstrip() + "..."
        self.surface.blit(self._text_surface(line, size, color), (rect.x, rect.y + (rect.height - font.get_height()) // 2))

    def _draw_text_art(self, category: str, filename: str, bounds: Any, surface: Any | None = None) -> None:
        target = self.surface if surface is None else surface
        lines = self.assets.load_text_asset(category, filename).splitlines()
        if not lines:
            return
        font = self._font(10)
        line_h, width = font.get_linesize(), max(font.size(line)[0] for line in lines)
        x, y = bounds.x + (bounds.width - width) // 2, bounds.y + max(0, (bounds.height - line_h * len(lines)) // 2)
        for line in lines:
            target.blit(self._text_surface(line, font.get_height(), (230, 230, 230)), (x, y))
            y += line_h

    def _animation_frame(self, name: str) -> list[str]:
        """Return the current cached animation frame, loading its files once."""
        if name not in self._animations:
            data = self.assets.load_animation(name)
            directory = self.assets.animation_dir(name)
            frames: list[list[str]] = []
            for filename in data.get("frames", []):
                path = directory / filename
                if self.assets.is_image_asset(filename):
                    # Image animation frames use the normal image cache when
                    # drawn; text frames are cached here as immutable lines.
                    frames.append([str(path)])
                else:
                    frames.append(path.read_text(encoding="utf-8").splitlines())
            self._animations[name] = (frames, max(1, int(data.get("frame_delay_ms", 300))), bool(data.get("loop", True)))
        frames, delay, loop = self._animations[name]
        if not frames:
            return []
        index = self.pygame.time.get_ticks() // delay
        index = index % len(frames) if loop else min(index, len(frames) - 1)
        self._animation_indices[name] = index
        return frames[index]

    def animation_changed(self, scene: dict[str, Any]) -> bool:
        """Whether an animated scene needs another presentation this tick."""
        name = scene.get("animation")
        if not name:
            return False
        before = self._animation_indices.get(name)
        self._animation_frame(name)
        return before != self._animation_indices.get(name)

    def exploration_animation_changed(self, names: list[str] | tuple[str, ...] | set[str]) -> bool:
        """Check object-local exploration animations without a second loop."""
        changed = False
        for name in names:
            before = self._animation_indices.get(name)
            self._animation_frame(name)
            changed |= before != self._animation_indices.get(name)
        return changed

    def _draw_lines(self, lines: list[str], bounds: Any) -> None:
        if not lines:
            return
        font = self._font(10)
        line_h, width = font.get_linesize(), max(font.size(line)[0] for line in lines)
        x, y = bounds.x + (bounds.width - width) // 2, bounds.y + max(0, (bounds.height - line_h * len(lines)) // 2)
        for line in lines:
            self.surface.blit(self._text_surface(line, font.get_height(), (255, 195, 110)), (x, y))
            y += line_h

    def _draw_health_bar(self, rect: Any, ratio: float, color: tuple[int, int, int], label: str,
                         alpha: int = 255, text_color: tuple[int, int, int] = (245, 245, 255),
                         surface: Any | None = None) -> None:
        """Draw a clamped logical-surface health bar for the battle UI."""
        pg = self.pygame
        if alpha <= 0:
            return
        # Draw the bar and label into one transparent layer so both fade
        # together during the enemy vaporization sequence.
        layer = pg.Surface((rect.width, rect.height + 18), pg.SRCALPHA)
        local_rect = pg.Rect(0, 18, rect.width, rect.height)
        safe_ratio = max(0.0, min(1.0, ratio))
        pg.draw.rect(layer, (28, 28, 42), local_rect)
        fill_width = max(0, min(local_rect.width, round(local_rect.width * safe_ratio)))
        if fill_width:
            pg.draw.rect(layer, color, pg.Rect(local_rect.x, local_rect.y, fill_width, local_rect.height))
        pg.draw.rect(layer, (220, 220, 235), local_rect, 1)
        layer.blit(self._text_surface(label, BATTLE_SMALL_TEXT_SIZE, text_color), (0, 0))
        layer.set_alpha(alpha)
        (self.surface if surface is None else surface).blit(layer, (rect.x, rect.y - 18))

    def _draw_battle_menu(self, entries: list[str], selected: int, center: tuple[int, int]) -> None:
        if not entries:
            return
        pg = self.pygame
        font_size, padding, gap = BATTLE_TEXT_SIZE, 7, 3
        glyphs = [self._text_surface(entry, font_size, (255, 255, 255)) for entry in entries]
        width = min(self.config.width - 16, max(glyph.get_width() for glyph in glyphs) + padding * 2)
        height = sum(glyph.get_height() + gap for glyph in glyphs) + padding * 2 - gap
        rect = pg.Rect(center[0] - width // 2, center[1] - height // 2, width, height)
        self._draw_transparent_box(rect)
        y = rect.y + padding
        for index, entry in enumerate(entries):
            glyph = glyphs[index]
            row = pg.Rect(rect.x + 3, y, rect.width - 6, glyph.get_height())
            if index == selected:
                pg.draw.rect(self.surface, (255, 214, 102), row)
                self.surface.blit(self._text_surface(entry, font_size, (18, 18, 35)), (row.x + 4, row.y))
            else:
                self.surface.blit(glyph, (row.x + 4, row.y))
            y = row.bottom + gap

    def _draw_battle_enemy_sprite(self, battle: Any, alpha: int = 255, monochrome: bool = False,
                                  surface: Any | None = None) -> Any | None:
        """Draw the configured opponent centered in the battle's upper field."""
        sprite = battle.enemy_sprite
        if not sprite or alpha <= 0:
            return None
        pg = self.pygame
        bounds = pg.Rect(self.config.width // 2 - 90, 90, 180, 165)
        if self.assets.is_image_asset(sprite):
            path, image = self._image("sprites", sprite)
            fitted = self._fit_image(path, image, bounds.size)
            if monochrome:
                fitted = pg.transform.grayscale(fitted)
            if alpha < 255:
                fitted = fitted.copy()
                fitted.set_alpha(alpha)
            rect = fitted.get_rect(center=bounds.center)
            shakes = [animation for animation in battle.animations.active if animation.kind == "enemy_shake"]
            if shakes:
                shake = min(shakes, key=lambda animation: animation.elapsed)
                magnitude = max(1, round(min(rect.width, rect.height) * 0.05 * (1 - shake.progress)))
                offsets = ((1, 0), (-1, 1), (0, -1), (-1, -1), (1, 1), (0, 1))
                x_factor, y_factor = offsets[int(shake.elapsed / 0.025) % len(offsets)]
                rect.move_ip(x_factor * magnitude, y_factor * magnitude)
            (self.surface if surface is None else surface).blit(fitted, rect)
            return rect
        self._draw_text_art("sprites", sprite, bounds, surface)
        return bounds

    def _qte_point(self, canvas: Any, point: tuple[float, float]) -> tuple[int, int]:
        return canvas.x + round(canvas.width * point[0]), canvas.y + round(canvas.height * point[1])

    def _draw_qte_bullseye(self, canvas: Any, point: tuple[float, float], critical_radius: float,
                            strong_radius: float, weak_radius: float) -> None:
        """Shared explicit miss/weak/strong/critical target boundary artwork."""
        pg = self.pygame
        center = self._qte_point(canvas, point)
        scale = min(canvas.width, canvas.height)
        weak = max(4, round(weak_radius * scale))
        strong = max(3, round(strong_radius * scale))
        critical = max(2, round(critical_radius * scale))
        pg.draw.circle(self.surface, (255, 220, 70), center, weak, 2)       # weak edge; outside is miss
        pg.draw.circle(self.surface, (70, 205, 105), center, strong, 2)     # strong ring
        pg.draw.circle(self.surface, (225, 65, 65), center, critical)       # critical bullseye

    def _draw_qte_hit_region(self, canvas: Any, point: tuple[float, float], distance: float,
                             critical_radius: float, strong_radius: float, weak_radius: float) -> None:
        """Show only the scoring band struck by a completed moving-target hit."""
        pg = self.pygame
        center = self._qte_point(canvas, point)
        scale = min(canvas.width, canvas.height)
        critical = max(2, round(critical_radius * scale))
        strong = max(3, round(strong_radius * scale))
        weak = max(4, round(weak_radius * scale))
        background = (8, 10, 23)
        if distance <= critical_radius:
            pg.draw.circle(self.surface, (225, 65, 65), center, critical)
        elif distance <= strong_radius:
            pg.draw.circle(self.surface, (70, 205, 105), center, strong)
            pg.draw.circle(self.surface, background, center, critical)
        else:
            pg.draw.circle(self.surface, (255, 220, 70), center, weak)
            pg.draw.circle(self.surface, background, center, strong)

    def _draw_attack_qte(self, attack: Any, canvas: Any) -> None:
        """Draw a large attack canvas from a pure QTE presentation snapshot."""
        pg = self.pygame
        data = attack.presentation()
        kind = data["kind"]
        weak, strong, critical = (255, 220, 70), (70, 205, 105), (225, 65, 65)
        pg.draw.rect(self.surface, (8, 10, 23), canvas)
        pg.draw.rect(self.surface, (135, 145, 180), canvas, 2)

        if kind == "precision_bar":
            bar = pg.Rect(canvas.x + 14, canvas.centery - 12, canvas.width - 28, 24)
            pg.draw.rect(self.surface, (62, 66, 84), bar)
            target = float(data["target"])
            zones = ((data["weak_window"], weak), (data["strong_window"], strong), (data["critical_window"], critical))
            for window, color in zones:
                left = bar.x + round(bar.width * max(0.0, target - window))
                right = bar.x + round(bar.width * min(1.0, target + window))
                pg.draw.rect(self.surface, color, pg.Rect(left, bar.y, max(1, right - left), bar.height))
            marker = data["indicator"]
            pg.draw.rect(self.surface, (255, 245, 180), pg.Rect(bar.x + round(bar.width * max(0, min(1, marker))) - 3, bar.y - 8, 6, bar.height + 16))
            return

        if kind == "charge_release":
            # pygame's arc angles increase through the visual upper half of
            # the dial. This maps the QTE's 0..180-degree mallet sweep to
            # the same upper semicircle rather than its lower complement.
            pivot = (canvas.centerx, canvas.y + round(canvas.height * .68))
            radius = max(26, min(round(canvas.width * .33), round(canvas.height * .38)))
            dial = pg.Rect(pivot[0] - radius, pivot[1] - radius, radius * 2, radius * 2)
            arc_width = max(6, round(radius * .12))
            pg.draw.arc(self.surface, (92, 98, 112), dial, 0.0, math.pi, arc_width)
            for tier, color in (("weak", weak), ("strong", strong), ("critical", critical)):
                start, end = data["scoring_arcs"][tier]
                pg.draw.arc(self.surface, color, dial, math.radians(start), math.radians(end), arc_width)
            if data["state"] != "CHARGING":
                strike_start, strike_end = data["release_strike_arc"]
                strike_color = (105, 220, 255) if not data["strike_confirmed"] else (235, 250, 255)
                pg.draw.arc(self.surface, strike_color, dial, math.radians(strike_start), math.radians(strike_end),
                            max(4, arc_width - 3))

            def unit_for_angle(angle: float) -> tuple[float, float]:
                radians = math.radians(-angle)
                return math.cos(radians), math.sin(radians)

            handle_width = max(4, round(radius * .075))
            head_depth = max(8, round(radius * .14))
            head_length = max(18, round(radius * .34))
            head_distance = radius + max(12, round(radius * .20))

            def draw_head(center: tuple[float, float], angle: float) -> None:
                axis_x, axis_y = unit_for_angle(angle)
                perpendicular_x, perpendicular_y = -axis_y, axis_x
                half_depth, half_length = head_depth / 2, head_length / 2
                points = [
                    (round(center[0] - axis_x * half_depth + perpendicular_x * half_length),
                     round(center[1] - axis_y * half_depth + perpendicular_y * half_length)),
                    (round(center[0] + axis_x * half_depth + perpendicular_x * half_length),
                     round(center[1] + axis_y * half_depth + perpendicular_y * half_length)),
                    (round(center[0] + axis_x * half_depth - perpendicular_x * half_length),
                     round(center[1] + axis_y * half_depth - perpendicular_y * half_length)),
                    (round(center[0] - axis_x * half_depth - perpendicular_x * half_length),
                     round(center[1] - axis_y * half_depth - perpendicular_y * half_length)),
                ]
                pg.draw.polygon(self.surface, (182, 116, 63), points)
                pg.draw.polygon(self.surface, (238, 183, 102), points, 1)

            mallet_angle = float(data["mallet_angle"])
            axis_x, axis_y = unit_for_angle(mallet_angle)
            handle_end = (pivot[0] + axis_x * (head_distance - head_depth / 2),
                          pivot[1] + axis_y * (head_distance - head_depth / 2))
            pg.draw.line(self.surface, (211, 166, 91), pivot,
                         (round(handle_end[0]), round(handle_end[1])), handle_width)
            pg.draw.circle(self.surface, (240, 202, 120), pivot, max(3, handle_width // 2))

            detached = data["detached_head"]
            if detached is None:
                head_center = (pivot[0] + axis_x * head_distance, pivot[1] + axis_y * head_distance)
                draw_head(head_center, mallet_angle)
            else:
                detached_axis_x, detached_axis_y = unit_for_angle(float(detached["angle"]))
                head_center = (pivot[0] + detached_axis_x * head_distance,
                               pivot[1] + detached_axis_y * head_distance)
                head_center = (head_center[0] + float(detached.get("offset_x", 0.0)) * radius,
                               head_center[1] + (float(detached.get("offset_y", 0.0))
                                                 + float(detached.get("drop", 0.0))) * radius)
                draw_head(head_center, float(detached["angle"]) - float(detached["rotation"]))
            return

        if kind == "shrinking_ring":
            self._draw_qte_bullseye(canvas, data["target"], data["critical_radius"], data["strong_radius"], data["weak_radius"])
            center = self._qte_point(canvas, data["ring"])
            radius = max(1, round(data["moving_radius"] * min(canvas.width, canvas.height)))
            ring_color = (255, 245, 180) if not data["collapsing"] else (245, 245, 255)
            pg.draw.circle(self.surface, ring_color, center, radius, 3 if radius > 4 else 1)
            return

        if kind == "rotating_strike":
            center, radius = canvas.center, canvas.width // 2 - 20
            dial = pg.Rect(center[0] - radius, center[1] - radius, radius * 2, radius * 2)
            pg.draw.circle(self.surface, (105, 115, 150), center, radius, 2)
            arc_color = (weak, strong, critical)[data["stage"]]
            start = math.radians(data["target_angle"] - data["window"])
            end = math.radians(data["target_angle"] + data["window"])
            pg.draw.arc(self.surface, arc_color, dial, start, end, 11)
            vector_x, vector_y = data["pointer_vector"]
            # The endpoint reaches the inside edge of the thick target arc,
            # so visual contact and the angle-only hit test describe the same
            # moment.
            endpoint = (center[0] + round(vector_x * (radius - 5)), center[1] + round(vector_y * (radius - 5)))
            pg.draw.line(self.surface, (255, 245, 180), center, endpoint, 4)
            pg.draw.circle(self.surface, (255, 245, 180), center, 4)
            if data["success_flash"] > 0 and data["last_hit_tier"]:
                hit_color = {"weak": weak, "strong": strong, "critical": critical}[data["last_hit_tier"]]
                flash_radius = radius + 7
                pg.draw.circle(self.surface, hit_color, center, flash_radius, 3)
            for index, color in enumerate((weak, strong, critical)):
                marker = pg.Rect(canvas.x + 12 + index * 24, canvas.bottom - 19, 16, 8)
                pg.draw.rect(self.surface, color if index <= data["achieved_stage"] else (55, 60, 80), marker)
            return

        if kind == "rapid_slash":
            # The runtime supplies normalized block data only. Rendering the
            # split pieces here keeps collision and animation frame-rate
            # independent and leaves room for later visual effects.
            previous_clip = self.surface.get_clip()
            self.surface.set_clip(previous_clip.clip(canvas))
            try:
                region_center, region_height = data["slash_region"]
                block_width = max(6, round(canvas.width * data["block_width"]))
                block_height = max(4, round(canvas.height * data["block_height"]))

                # Sliced blocks are subdued background debris. Draw them
                # first so the strike region, intact blocks, and blade streak
                # all remain visually dominant.
                for block in data["blocks"]:
                    if not block["cut"]:
                        continue
                    center_x = canvas.x + round(canvas.width * block["x"])
                    top = canvas.y + round(canvas.height * block["top"])
                    separation = round(canvas.height * block["separation"])
                    top_height = min(block_height - 2, max(2, round(canvas.height * block["cut_offset"])))
                    bottom_height = block_height - top_height
                    top_half = pg.Rect(center_x - block_width // 2, top - separation,
                                       block_width, top_height)
                    bottom_half = pg.Rect(center_x - block_width // 2,
                                          top + top_height + separation,
                                          block_width, bottom_height)
                    for half in (top_half, bottom_half):
                        pg.draw.rect(self.surface, (55, 70, 82), half)
                        pg.draw.rect(self.surface, (85, 98, 108), half, 1)

                region = pg.Rect(canvas.x + 5,
                                 canvas.y + round(canvas.height * (region_center - region_height / 2)),
                                 canvas.width - 10, max(1, round(canvas.height * region_height)))
                region_fill = pg.Surface(region.size, pg.SRCALPHA)
                region_fill.fill((105, 110, 120, 80))
                self.surface.blit(region_fill, region)
                pg.draw.line(self.surface, (115, 120, 130), (region.left, region.top), (region.right, region.top), 1)
                pg.draw.line(self.surface, (115, 120, 130), (region.left, region.bottom), (region.right, region.bottom), 1)

                for block in data["blocks"]:
                    if block["cut"]:
                        continue
                    center_x = canvas.x + round(canvas.width * block["x"])
                    top = canvas.y + round(canvas.height * block["top"])
                    body = pg.Rect(center_x - block_width // 2, top, block_width, block_height)
                    pg.draw.rect(self.surface, (122, 180, 225), body)
                    pg.draw.rect(self.surface, (220, 242, 255), body, 2)

                if data["slash_active"] and data["slash_direction"]:
                    progress = data["slash_progress"]
                    slash_length = max(16, round(canvas.width * .58))
                    if data["slash_direction"] == "LEFT":
                        slash_center_x = canvas.right + slash_length // 2 - round((canvas.width + slash_length) * progress)
                    else:
                        slash_center_x = canvas.left - slash_length // 2 + round((canvas.width + slash_length) * progress)
                    slash_y = canvas.y + round(canvas.height * region_center)
                    start = (slash_center_x - slash_length // 2, slash_y)
                    end = (slash_center_x + slash_length // 2, slash_y)
                    slash_thickness = max(1, round(canvas.height * region_height))
                    direction = -1 if data["slash_direction"] == "LEFT" else 1
                    # Draw successive, dimmer horizontal streaks behind the
                    # white blade line. The tail follows the same motion path
                    # but stays offset opposite the direction of travel.
                    for index in range(5, 0, -1):
                        offset = direction * -round(slash_length * .16 * index)
                        intensity = 45 + (5 - index) * 32
                        trail_start = (start[0] + offset, slash_y)
                        trail_end = (end[0] + offset, slash_y)
                        pg.draw.line(self.surface, (intensity, intensity, intensity), trail_start, trail_end,
                                     max(1, slash_thickness - index // 2))
                    pg.draw.line(self.surface, (255, 255, 255), start, end, slash_thickness)

                markers = data["penalty_markers"]
                marker_width, marker_height, marker_gap = 10, 7, 6
                total_width = len(markers) * marker_width + max(0, len(markers) - 1) * marker_gap
                marker_x = canvas.centerx - total_width // 2
                marker_y = canvas.bottom - marker_height - 8
                for index, used in enumerate(markers):
                    marker = pg.Rect(marker_x + index * (marker_width + marker_gap), marker_y,
                                     marker_width, marker_height)
                    pg.draw.rect(self.surface, (145, 58, 68) if used else (44, 50, 62), marker)
                    pg.draw.rect(self.surface, (215, 100, 108) if used else (95, 105, 120), marker, 1)
            finally:
                self.surface.set_clip(previous_clip)

            return

        if kind == "directional_combo":
            region_colors = {
                "UP": (100, 155, 245), "DOWN": (135, 105, 245),
                "LEFT": (80, 185, 230), "RIGHT": (185, 105, 235),
            }
            performance_colors = {
                "miss": (155, 155, 155), "weak": (255, 230, 90),
                "strong": (90, 220, 120), "critical": (235, 75, 75),
            }
            for region in data["regions"]:
                left, top, width, height = region["rect"]
                rect = pg.Rect(canvas.x + round(canvas.width * left), canvas.y + round(canvas.height * top),
                               max(1, round(canvas.width * width)), max(1, round(canvas.height * height)))
                base_color = region_colors[region["direction"]]
                dim_color = tuple(max(18, component // 3) for component in base_color)
                pg.draw.rect(self.surface, dim_color, rect)
                if region["flashing"]:
                    outline_color = performance_colors[data["target_tier"]]
                    outline_width = 4
                elif region["held"]:
                    outline_color = base_color
                    outline_width = 3
                else:
                    outline_color = tuple(max(40, component // 2) for component in base_color)
                    outline_width = 2
                pg.draw.rect(self.surface, outline_color, rect, outline_width)

            center = self._qte_point(canvas, (.5, .5))
            target = self._qte_point(canvas, data["target"])
            target_color = performance_colors[data["target_tier"]]
            radius = max(3, round(data["target_radius"] * min(canvas.width, canvas.height)))
            pg.draw.circle(self.surface, (65, 70, 95), center, 3)
            pg.draw.circle(self.surface, target_color, target, radius)
            pg.draw.circle(self.surface, (245, 245, 255), target, radius, 1)
            hits = self._text_surface(f"{data['hits']}/{data['required_hits']}", BATTLE_TEXT_SIZE, (225, 230, 245))
            self.surface.blit(hits, hits.get_rect(midbottom=(canvas.centerx, canvas.bottom - 5)))
            return

        if kind == "rhythm_combo":
            # Rhythm is a single, compact horizontal track: incoming timing
            # bars and the player-controlled striking bar now have the same
            # baseline and height.
            track = pg.Rect(canvas.x + 42, canvas.centery - 16, canvas.width - 84, 32)
            pg.draw.rect(self.surface, (45, 50, 70), track)
            pg.draw.rect(self.surface, (120, 130, 165), track, 2)
            # Keep moving bars inside the horizontal track, while allowing a
            # hit flourish to grow vertically until the attack frame clips it.
            bar_viewport = canvas.clip(pg.Rect(track.left, canvas.top, track.width, canvas.height))
            previous_clip = self.surface.get_clip()
            self.surface.set_clip(previous_clip.clip(bar_viewport))
            try:
                for bar in data["bars"]:
                    alpha = max(0, min(255, round(255 * (1 - bar["fade"] / data["fade_duration"]))))
                    if bar["state"] == "missed":
                        color = (80, 82, 100)
                    elif bar["state"] == "cleared":
                        color = strong
                    else:
                        color = (125, 180, 245)
                    x = canvas.x + round(canvas.width * bar["position"])
                    height = max(1, round(track.height * bar["vertical_scale"]))
                    width = max(1, round(canvas.width * data["timing_bar_width"]))
                    rect = pg.Rect(x - width // 2, track.centery - height // 2, width, height)
                    if rect.right <= track.left or rect.left >= track.right:
                        continue
                    layer = pg.Surface(rect.size, pg.SRCALPHA)
                    layer.fill((*color, alpha))
                    self.surface.blit(layer, rect)
            finally:
                self.surface.set_clip(previous_clip)
            strike_width = max(1, round(canvas.width * data["rhythm_bar_width"]))
            strike = pg.Rect(canvas.x + round(canvas.width * data["striking_x"]) - strike_width // 2,
                             track.y, strike_width, track.height)
            dim_fill = pg.Surface(strike.size, pg.SRCALPHA)
            dim_fill.fill((10, 12, 20, 175))
            self.surface.blit(dim_fill, strike)
            if data["activation_flash"] > 0:
                strike_color = strong if data["last_activation_hit"] else (225, 85, 95)
                strike_outline = 3
            else:
                strike_color = (255, 220, 70)
                strike_outline = 2
            pg.draw.rect(self.surface, strike_color, strike, strike_outline)
            markers_y = track.bottom + 13
            marker_width, marker_gap = 12, 7
            markers_width = len(data["penalty_markers"]) * marker_width + (len(data["penalty_markers"]) - 1) * marker_gap
            start_x = strike.centerx - markers_width // 2
            for index, used in enumerate(data["penalty_markers"]):
                marker = pg.Rect(start_x + index * (marker_width + marker_gap), markers_y, marker_width, 7)
                pg.draw.rect(self.surface, (225, 85, 95) if used else (62, 66, 84), marker)
                pg.draw.rect(self.surface, (255, 190, 190) if used else (120, 130, 165), marker, 1)
            return

        if kind == "moving_weak_point":
            target_track_y = self._qte_point(canvas, data["target"])[1]
            pg.draw.line(self.surface, (70, 75, 100), (canvas.x + 10, target_track_y),
                         (canvas.right - 10, target_track_y), 1)
            if data["impact"]:
                self._draw_qte_hit_region(canvas, data["target"], data["impact_distance"],
                                          data["critical_radius"], data["strong_radius"], data["weak_radius"])
            else:
                self._draw_qte_bullseye(canvas, data["target"], data["critical_radius"], data["strong_radius"], data["weak_radius"])
            launch = self._qte_point(canvas, data["launch"])
            radians = math.radians(data["aim_angle"])
            direction = math.cos(radians), math.sin(radians)
            barrel_end = (launch[0] + round(direction[0] * 22), launch[1] + round(direction[1] * 22))
            if not data["fired"]:
                pg.draw.line(self.surface, (140, 150, 185), barrel_end,
                             (launch[0] + round(direction[0] * canvas.width * .62), launch[1] + round(direction[1] * canvas.height * .62)), 1)
            pg.draw.circle(self.surface, (80, 85, 115), launch, 8)
            pg.draw.line(self.surface, (180, 185, 215), launch, barrel_end, 5)
            pg.draw.circle(self.surface, (225, 230, 245), launch, 4)
            projectile = self._qte_point(canvas, data["projectile"])
            if data["fired"]:
                # Draw the arrow itself around its current position, rather
                # than connecting it back to the launcher like a rope.
                # ``projectile`` is the tip's actual collision point.
                tail = (projectile[0] - round(direction[0] * 24), projectile[1] - round(direction[1] * 24))
                tip = projectile
                pg.draw.line(self.surface, (245, 245, 210), tail, tip, 3)
                wing = (-direction[1], direction[0])
                left = (tip[0] - round(direction[0] * 6) + round(wing[0] * 4), tip[1] - round(direction[1] * 6) + round(wing[1] * 4))
                right = (tip[0] - round(direction[0] * 6) - round(wing[0] * 4), tip[1] - round(direction[1] * 6) - round(wing[1] * 4))
                pg.draw.polygon(self.surface, (255, 238, 145), [tip, left, right])
            return

        if kind == "stability":
            bar = pg.Rect(canvas.x + 14, canvas.centery - 12, canvas.width - 28, 24)
            pg.draw.rect(self.surface, (110, 115, 130), bar)
            for fraction, color in ((.72, weak), (.38, strong), (data["center_width"], critical)):
                half = round(bar.width * fraction / 2)
                pg.draw.rect(self.surface, color, pg.Rect(bar.centerx - half, bar.y, half * 2, bar.height))
            marker = bar.centerx + round(bar.width * data["position"] / 2)
            pg.draw.rect(self.surface, (255, 230, 115), pg.Rect(marker - 3, bar.y - 8, 6, bar.height + 16))

    def _draw_defense_hazard(self, arena_surface: Any, renderable: dict[str, Any], full: Any, viewport: Any) -> None:
        """Rasterize pure defense-hazard presentation data inside the arena.

        Simulation lives in ``engine.battle.defense`` and intentionally has no
        pygame dependency.  This renderer-side adapter preserves cached
        sprite loading while native shapes remain the default for every
        pattern type.
        """
        pg = self.pygame
        kind = renderable.get("kind")
        color = tuple(renderable.get("color", (255, 105, 105)))
        alpha = int(renderable.get("alpha", 255))
        color_with_alpha = (*color[:3], max(0, min(255, alpha)))

        def point(x: float, y: float) -> tuple[int, int]:
            return round(x - full.x), round(y - viewport.y)

        if kind == "projectile":
            x, y = point(float(renderable["x"]), float(renderable["y"]))
            width, height = max(1, round(float(renderable["width"]))), max(1, round(float(renderable["height"])))
            sprite = renderable.get("sprite")
            if sprite:
                name = str(sprite).replace("\\", "/")
                for prefix in ("assets/sprites/", "sprites/"):
                    if name.startswith(prefix):
                        name = name[len(prefix):]
                        break
                try:
                    path, image = self._image("sprites", name)
                    scale = max(.01, float(renderable.get("sprite_scale", 1.0)))
                    size = (max(1, round(image.get_width() * scale)), max(1, round(image.get_height() * scale)))
                    image = self._scaled_image(path, image, size)
                    rotation = float(renderable.get("rotation", 0.0))
                    if abs(rotation) > .01:
                        rotation_key = (path, size, int(round(rotation)) % 360)
                        if rotation_key not in self._rotated_images:
                            self._rotated_images[rotation_key] = pg.transform.rotate(image, -rotation_key[2])
                        image = self._rotated_images[rotation_key]
                    arena_surface.blit(image, image.get_rect(center=(x, y)))
                    return
                except AssetNotFoundError:
                    # A native fallback keeps an optional asset from making a
                    # whole fight unplayable; AssetLoader still gives a clear
                    # path in logs when a story intentionally preflights it.
                    pass
            rect = pg.Rect(x - width // 2, y - height // 2, width, height)
            if renderable.get("shape", "circle") == "circle":
                pg.draw.ellipse(arena_surface, color_with_alpha, rect)
                pg.draw.ellipse(arena_surface, (255, 235, 235, alpha), rect, 1)
            else:
                pg.draw.rect(arena_surface, color_with_alpha, rect)
            return
        if kind == "beam":
            start = point(float(renderable["x1"]), float(renderable["y1"]))
            end = point(float(renderable["x2"]), float(renderable["y2"]))
            pg.draw.line(arena_surface, color_with_alpha, start, end, max(1, round(float(renderable["width"]))))
            return
        if kind == "zone":
            shape = renderable.get("shape", "circle")
            x, y = point(float(renderable.get("x", 0)), float(renderable.get("y", 0)))
            if shape == "circle":
                pg.draw.circle(arena_surface, color_with_alpha, (x, y), max(1, round(float(renderable.get("radius", 1)))))
            elif shape in {"rectangle", "rect", "strip"}:
                width, height = max(1, round(float(renderable.get("width", 1)))), max(1, round(float(renderable.get("height", 1))))
                pg.draw.rect(arena_surface, color_with_alpha, pg.Rect(x - width // 2, y - height // 2, width, height))
            elif shape == "line":
                angle = math.radians(float(renderable.get("angle", 0)))
                length = float(renderable.get("length", 0))
                end = point(float(renderable.get("x", 0)) + math.cos(angle) * length,
                            float(renderable.get("y", 0)) + math.sin(angle) * length)
                pg.draw.line(arena_surface, color_with_alpha, (x, y), end, max(1, round(float(renderable.get("width", 1)))))
            elif shape == "polygon":
                points = [point(float(px), float(py)) for px, py in renderable.get("points", [])]
                if len(points) >= 3:
                    pg.draw.polygon(arena_surface, color_with_alpha, points)
            return
        if kind == "ring":
            x, y = point(float(renderable["x"]), float(renderable["y"]))
            radius = max(1, round(float(renderable.get("radius", 1))))
            thickness = max(1, round(float(renderable.get("thickness", 1))))
            gaps = renderable.get("gaps", [])
            if not gaps:
                pg.draw.circle(arena_surface, color_with_alpha, (x, y), radius, thickness)
                return
            blocked: list[tuple[float, float]] = []
            for start, end in gaps:
                start, end = float(start) % 360.0, float(end) % 360.0
                if start <= end:
                    blocked.append((start, end))
                else:
                    blocked.extend(((0.0, end), (start, 360.0)))
            blocked.sort()
            cursor = 0.0
            rect = pg.Rect(x - radius, y - radius, radius * 2, radius * 2)
            for start, end in blocked:
                if start > cursor:
                    pg.draw.arc(arena_surface, color_with_alpha, rect, math.radians(cursor), math.radians(start), thickness)
                cursor = max(cursor, end)
            if cursor < 360.0:
                pg.draw.arc(arena_surface, color_with_alpha, rect, math.radians(cursor), math.tau, thickness)
            return
        if kind == "orbit":
            x, y = point(float(renderable["x"]), float(renderable["y"]))
            pg.draw.circle(arena_surface, color_with_alpha, (x, y), max(1, round(float(renderable.get("radius", 1)))))
            return
        if kind == "moving_gap_wall":
            for piece_x, piece_y, piece_width, piece_height in renderable.get("pieces", []):
                x, y = point(float(piece_x), float(piece_y))
                pg.draw.rect(arena_surface, color_with_alpha, pg.Rect(
                    x, y, max(1, round(float(piece_width))), max(1, round(float(piece_height))),
                ))
            return
        if kind == "arena_constraint":
            x, y = point(float(renderable["x"]), float(renderable["y"]))
            rect = pg.Rect(x, y, max(1, round(float(renderable["width"]))), max(1, round(float(renderable["height"]))))
            pg.draw.rect(arena_surface, color_with_alpha, rect, 1)

    def _draw_defense_arena(self, defense: Any, vertical_scale: float) -> None:
        """Render into the current clipped arena viewport without scaling sprites."""
        pg = self.pygame
        full = pg.Rect(round(defense.x), round(defense.y), round(defense.width), round(defense.height))
        visible_height = max(1, round(full.height * max(0.0, min(1.0, vertical_scale))))
        crop_top = (full.height - visible_height) // 2
        viewport = pg.Rect(full.x, full.y + crop_top, full.width, visible_height)
        arena_surface = pg.Surface(viewport.size, pg.SRCALPHA)
        arena_surface.fill((8, 8, 15))
        pg.draw.rect(arena_surface, (235, 235, 250), arena_surface.get_rect(), 2)
        renderables = getattr(defense, "renderables", None)
        if renderables is None:
            # Kept for small third-party DefenseSequence-like objects.
            renderables = [
                {"kind": "projectile", "x": projectile.x, "y": projectile.y,
                 "width": projectile.width, "height": projectile.height,
                 "shape": projectile.shape, "damage": projectile.damage,
                 "active": projectile.active, "color": (255, 115, 115) if projectile.damage else (235, 205, 100)}
                for projectile in defense.projectiles
            ]
        for renderable in renderables:
            if renderable.get("active") or renderable.get("telegraph"):
                self._draw_defense_hazard(arena_surface, renderable, full, viewport)
        hurt_frame = defense.player_hurt_for > 0 and int(defense.player_hurt_for / PLAYER_HURT_FLICKER_SECONDS) % 2 == 0
        player_sprite = "heart_hurt.png" if hurt_frame else "heart.png"
        try:
            _, heart = self._image("sprites", player_sprite)
            heart_rect = heart.get_rect(center=(round(defense.player_x - full.x), round(defense.player_y - viewport.y)))
            arena_surface.blit(heart, heart_rect)
        except AssetNotFoundError:
            # Stories that do not provide the optional heart artwork retain
            # the original minimal player marker.
            player_color = (255, 245, 245) if defense.player_invulnerable_for <= 0 else (120, 120, 140)
            pg.draw.circle(arena_surface, player_color, (round(defense.player_x - full.x), round(defense.player_y - viewport.y)), 4)
        self.surface.blit(arena_surface, viewport.topleft)

    def _draw_player_death(self, death: Any) -> None:
        """Draw the intentionally UI-free heart-break loss animation."""
        pg = self.pygame
        self.surface.fill((0, 0, 0))
        if death.phase in {"heart", "broken_heart"}:
            sprite = "heart.png" if death.phase == "heart" else "heart_break.png"
            x, y = round(death.x), round(death.y)
            if death.heart_shaking:
                offsets = ((1, 0), (-1, 1), (0, -1), (-1, -1), (1, 1), (0, 1))
                offset_x, offset_y = offsets[int((death.elapsed - death.heart_shake_start) / 0.025) % len(offsets)]
                x += offset_x * 2
                y += offset_y * 2
            try:
                _, image = self._image("sprites", sprite)
                self.surface.blit(image, image.get_rect(center=(x, y)))
            except AssetNotFoundError:
                pg.draw.circle(self.surface, (255, 70, 80), (x, y), 5)
            return

        # All fragments travel with a constant velocity.  Their orientation
        # advances in discrete quarter-turns rather than frame-rate-dependent
        # continuous rotation, matching the requested 0.125-second cadence.
        rotation = (int(death.shard_elapsed / 0.125) % 4) * 90
        for shard in death.shards:
            x = round(death.x + shard.velocity_x * death.shard_elapsed)
            y = round(death.y + shard.velocity_y * death.shard_elapsed)
            try:
                _, image = self._image("sprites", shard.sprite)
                image = pg.transform.rotate(image, rotation)
                self.surface.blit(image, image.get_rect(center=(x, y)))
            except AssetNotFoundError:
                pg.draw.rect(self.surface, (255, 70, 80), pg.Rect(x - 2, y - 2, 4, 4))

    def render_game_over(self, presentation: Any, text: str, choices: list[dict[str, Any]], selected: int) -> None:
        """Draw the YAML game-over timeline with the existing heart assets."""
        pg = self.pygame
        if presentation.death_animation is not None:
            self._draw_player_death(presentation.death_animation)
            self._present()
            return

        self.surface.fill((0, 0, 0))
        if presentation.show_heart:
            x, y = round(presentation.x), round(presentation.y)
            if getattr(presentation, "heart_shaking", False):
                # Keep this exactly in step with the shake used just before
                # the heart splits during the loss sequence.
                offsets = ((1, 0), (-1, 1), (0, -1), (-1, -1), (1, 1), (0, 1))
                offset_x, offset_y = offsets[int(presentation.stage_elapsed / 0.025) % len(offsets)]
                x += offset_x * 2
                y += offset_y * 2
            try:
                _, heart = self._image("sprites", presentation.heart_sprite)
                if presentation.heart_alpha < 255:
                    heart = heart.copy()
                    heart.set_alpha(presentation.heart_alpha)
                self.surface.blit(heart, heart.get_rect(center=(x, y)))
            except AssetNotFoundError:
                pg.draw.circle(self.surface, (255, 70, 80), (x, y), 5)

        if presentation.stage.name == "MENU":
            title = self._text_surface(getattr(presentation, "visible_text", text), BATTLE_TITLE_SIZE, (255, 255, 255))
            self.surface.blit(title, title.get_rect(center=(self.config.width // 2, self.config.height // 4)))
        if presentation.show_menu:
            option_size = int(self.render_config.get("option_font_size", 16))
            rows = [self._text_surface(choice["text"], option_size, (255, 255, 255)) for choice in choices]
            width = min(self.config.width - 12, max((row.get_width() for row in rows), default=0) + 20)
            height = sum(row.get_height() + 4 for row in rows) + 10
            box = pg.Rect(self.config.width // 2 - width // 2, self.config.height // 2 - height // 2, width, height)
            self._draw_transparent_box(box)
            y = box.y + 5
            for index, row in enumerate(rows):
                row_rect = pg.Rect(box.x + 5, y, box.width - 10, row.get_height())
                if index == selected:
                    pg.draw.rect(self.surface, self._ui_color("highlight_option_color", OPTION_HIGHLIGHT_COLOR), row_rect)
                    row = self._text_surface(choices[index]["text"], option_size,
                                             self._ui_color("selected_option_color", SELECTED_OPTION_COLOR))
                self.surface.blit(row, (row_rect.x + 3, row_rect.y))
                y = row_rect.bottom + 4
        self._present()

    def _draw_determined_revival(self, battle: Any, cutscene: Any) -> None:
        """Draw the black, UI-free portion of a determined-revival sequence."""
        pg = self.pygame
        self.surface.fill((0, 0, 0))
        stage = cutscene.stage_name
        # Once the heart has finished fading, preserve a truly empty black
        # screen during the requested hold before the background reveal.
        if stage == "background_fade_delay":
            return
        x, y = round(cutscene.x), round(cutscene.y)
        split_stages = {"split_pause", "revival_dialogue_delay", "revival_dialogue", "music_fade"}
        mode = "intact"
        shake_elapsed: float | None = None
        if stage == "heart_split":
            mode = "intact" if cutscene.heart_elapsed < battle.DEATH_BREAK_1_AT else "broken"
            shake_elapsed = (cutscene.heart_elapsed - battle.DEATH_INITIAL_PAUSE
                             if battle.DEATH_INITIAL_PAUSE <= cutscene.heart_elapsed
                             < battle.DEATH_INITIAL_PAUSE + battle.DEATH_HEART_SHAKE_DURATION else None)
        elif stage in split_stages:
            mode = "broken"
        elif stage == "heart_recombine" and cutscene.stage_elapsed < battle.REVIVAL_RECOMBINE_SHAKE_DURATION:
            # The original, intact heart returns with the existing healing
            # cue.  Keep its brief shake presentation sprite-native rather
            # than assembling or modifying temporary heart fragments.
            shake_elapsed = cutscene.stage_elapsed
        if shake_elapsed is not None:
            offsets = ((1, 0), (-1, 1), (0, -1), (-1, -1), (1, 1), (0, 1))
            offset_x, offset_y = offsets[int(shake_elapsed / 0.025) % len(offsets)]
            x += offset_x * 2
            y += offset_y * 2
        try:
            _, heart = self._image("sprites", "heart.png")
            if mode == "intact":
                if stage == "heart_fade":
                    duration = max(0.001, float(battle.REVIVAL_HEART_FADE_DURATION))
                    alpha = max(0, min(255, round(255 * (1.0 - cutscene.stage_elapsed / duration))))
                    heart = heart.copy()
                    heart.set_alpha(alpha)
                self.surface.blit(heart, heart.get_rect(center=(x, y)))
                return
            if mode == "broken":
                _, broken_heart = self._image("sprites", "heart_break.png")
                self.surface.blit(broken_heart, broken_heart.get_rect(center=(x, y)))
                return
            self.surface.blit(heart, heart.get_rect(center=(x, y)))
        except AssetNotFoundError:
            pg.draw.circle(self.surface, (255, 70, 80), (x, y), 4)

    def _draw_game_over_cutscene(self, battle: Any, cutscene: Any) -> None:
        """Draw the heart portion of a controller-owned final-loss sequence."""
        pg = self.pygame
        self.surface.fill((0, 0, 0))
        x, y = round(cutscene.x), round(cutscene.y)
        broken = cutscene.stage_name != "heart_split" or cutscene.heart_elapsed >= battle.DEATH_BREAK_1_AT
        if not broken and battle.DEATH_INITIAL_PAUSE <= cutscene.heart_elapsed < (
                battle.DEATH_INITIAL_PAUSE + battle.DEATH_HEART_SHAKE_DURATION):
            offsets = ((1, 0), (-1, 1), (0, -1), (-1, -1), (1, 1), (0, 1))
            offset_x, offset_y = offsets[int((cutscene.heart_elapsed - battle.DEATH_INITIAL_PAUSE) / 0.025) % len(offsets)]
            x += offset_x * 2
            y += offset_y * 2
        try:
            _, heart = self._image("sprites", "heart_break.png" if broken else "heart.png")
            self.surface.blit(heart, heart.get_rect(center=(x, y)))
        except AssetNotFoundError:
            pg.draw.circle(self.surface, (255, 70, 80), (x, y), 4)

    def _draw_revival_dialogue(self, battle: Any) -> None:
        """Draw centered revival narration directly over the black cutscene."""
        if battle.state.name != "DIALOGUE" or not battle.dialogue_text:
            return
        canvas = self.pygame.Rect(0, 0, self.config.width, self.config.height)
        text_rect = canvas.inflate(-64, -32)
        prepared = self.prepare_dialogue_text(
            battle.visible_dialogue_text or "", text_rect, REVIVAL_DIALOGUE_TEXT_SIZE,
        )
        font = self._font(REVIVAL_DIALOGUE_TEXT_SIZE)
        lines = prepared.split("\n")
        y = canvas.top + canvas.height // 4 - (len(lines) * font.get_linesize()) // 2
        alpha = max(0, min(255, round(255 * battle.revival_dialogue_alpha)))
        for line in lines:
            glyph = self._text_surface(line, REVIVAL_DIALOGUE_TEXT_SIZE, (255, 255, 255)).copy()
            glyph.set_alpha(alpha)
            self.surface.blit(glyph, glyph.get_rect(centerx=canvas.centerx, y=y))
            y += font.get_linesize()

    def _present(self) -> None:
        """Scale the logical canvas and flip the display once."""
        self.window.fill((0, 0, 0))
        self.window.blit(self.pygame.transform.scale(self.surface, self.destination[2:]), self.destination[:2])
        self.pygame.display.flip()
        self.dirty = False

    def render_battle(self, battle: Any) -> None:
        """Render a :class:`BattleController` onto the existing game surface."""
        pg = self.pygame
        self.prepare_battle_dialogue(battle)
        if getattr(battle, "death_animation", None) is not None:
            self._draw_player_death(battle.death_animation)
            self._present()
            return
        game_over = getattr(battle, "game_over_cutscene", None)
        if game_over is not None:
            self._draw_game_over_cutscene(battle, game_over)
            self._present()
            return
        revival = getattr(battle, "revival_cutscene", None)
        if revival is not None and revival.stage_name not in {"background_fade", "enemy_dialogue"}:
            self._draw_determined_revival(battle, revival)
            self._draw_revival_dialogue(battle)
            self._present()
            return
        self.surface.fill((12, 12, 28))
        w, h = self.config.width, self.config.height
        if battle.background:
            if self.assets.is_image_asset(battle.background):
                path, image = self._image("backgrounds", battle.background)
                self.surface.blit(self._scaled_image(path, image, (w, h)), (0, 0))
            else:
                self._draw_text_art("backgrounds", battle.background, pg.Rect(0, 0, w, h))

        victory = getattr(battle, "victory_animation", None)
        enemy_ratio = battle.animations.displayed_health.get("enemy", battle.enemy.hp / battle.enemy.max_hp)
        player_ratio = battle.animations.displayed_health.get("player", battle.current_player_hp() / battle.maximum_player_hp())
        self._draw_health_bar(pg.Rect(28, 28, 235, 12), player_ratio, (80, 210, 120),
                              f"YOU HP {battle.current_player_hp()}/{battle.maximum_player_hp()}")
        enemy_bar = pg.Rect(w - 263, 28, 235, 12)
        if victory is None:
            self._draw_health_bar(enemy_bar, enemy_ratio, (220, 82, 94),
                                  f"{battle.enemy.name} HP {battle.enemy.hp}/{battle.enemy.max_hp}")
            enemy_name = self._text_surface(battle.enemy.name, BATTLE_TITLE_SIZE, (255, 240, 190))
            self.surface.blit(enemy_name, (w // 2 - enemy_name.get_width() // 2, 62))
            self._draw_battle_enemy_sprite(battle)
        else:
            # The enemy is rendered independently from the battle canvas, so
            # its fade alpha cannot alter the background or player UI.
            enemy_layer = pg.Surface((w, h), pg.SRCALPHA)
            self._draw_health_bar(enemy_bar, enemy_ratio, (220, 82, 94),
                                  f"{battle.enemy.name} HP {battle.enemy.hp}/{battle.enemy.max_hp}",
                                  text_color=(190, 190, 190), surface=enemy_layer)
            enemy_name = self._text_surface(battle.enemy.name, BATTLE_TITLE_SIZE, (190, 190, 190))
            enemy_layer.blit(enemy_name, (w // 2 - enemy_name.get_width() // 2, 62))
            self._draw_battle_enemy_sprite(battle, monochrome=True, surface=enemy_layer)
            if victory.enemy_alpha > 0:
                enemy_layer.set_alpha(victory.enemy_alpha)
                self.surface.blit(enemy_layer, (0, 0))

        state_name = battle.state.name.replace("_", " ").title()
        # Keep the menu-state label in the lower-left gutter so it does not
        # compete with the battle field or the dialogue panel above it.
        state_label = self._text_surface(state_name, BATTLE_SMALL_TEXT_SIZE, (180, 185, 220))
        dialogue_bottom = self._dialogue_rect().bottom
        state_label_y = min(h - state_label.get_height() - 7, dialogue_bottom + 6)
        self.surface.blit(state_label, (12, state_label_y))
        # Enemy speech is the only text shown between the player's attack and
        # the defensive phase. Telegraph details remain in the battle log.

        defense = battle.active_defense
        if defense and battle.state.name in {"DEFENSE_OPENING", "DEFENSE", "DEFENSE_CLOSING"}:
            self._draw_defense_arena(defense, battle.defense_window_scale)

        attack = battle.active_attack
        if attack and battle.state.name == "PLAYER_ATTACK":
            # Attack canvases deliberately replace the compact instruction
            # panel.  Tutorial text is retained in the QTE data for a future
            # tutorial flow; live attacks get a large, readable play space.
            if attack.qte_type in {"precision_bar", "rhythm_combo"}:
                # A timing bar needs horizontal room for its hit zones; a
                # square playfield makes this pattern feel cramped. Rhythm
                # uses the same wide, short canvas for its shared track.
                canvas_width = min(480, w - 48)
                canvas_height = 80
                canvas = pg.Rect(w // 2 - canvas_width // 2, h // 2 - canvas_height // 2,
                                 canvas_width, canvas_height)
            elif attack.qte_type == "rapid_slash":
                # Its falling vertical bar needs a deliberately tall, narrow
                # playfield, with room below the strike region for the bar to
                # continue falling before it exits.
                canvas_height = min(330, h - 130)
                canvas_width = max(110, min(160, round(canvas_height * .46)))
                canvas = pg.Rect(w // 2 - canvas_width // 2, 62, canvas_width, canvas_height)
            else:
                canvas_size = min(230, h - 130)
                canvas = pg.Rect(w // 2 - canvas_size // 2, 62, canvas_size, canvas_size)
            self._draw_attack_qte(attack, canvas)

        if battle.state.name == "GEAR":
            gear = battle.gear_data()
            pane = pg.Rect(54, 120, w - 108, 145)
            self._draw_transparent_box(pane)
            lines = [f"Gear  HP {gear['hp']}/{gear['max_hp']}  ATK {gear['stats']['attack']}  DEF {gear['stats']['defense']}",
                     f"Weapon: {gear['weapon'] or 'None'}"]
            lines += [f"{entry['slot']}: {entry['name']} {entry['bonuses']}" for entry in gear["equipment"]]
            if gear["weapon_moves"]:
                lines.append("Weapon moves: " + ", ".join(gear["weapon_moves"]))
            lines.append("B / Backspace: return")
            y = pane.y + 10
            for line in lines[:6]:
                self.surface.blit(self._text_surface(line, BATTLE_SMALL_TEXT_SIZE, (245, 245, 255)), (pane.x + 10, y))
                y += 20
        elif battle.state.name == "ITEM_MENU":
            detail = battle.item_detail()
            if detail:
                pane = pg.Rect(38, 228, w - 76, 48)
                self._draw_transparent_box(pane)
                effect_text = ", ".join(str(effect) for effect in detail["effects"])
                self.surface.blit(self._text_surface(f"{detail['name']} x{detail['quantity']}: {detail['description']}", BATTLE_SMALL_TEXT_SIZE, (245, 245, 255)), (pane.x + 7, pane.y + 6))
                self.surface.blit(self._text_surface(effect_text, BATTLE_SMALL_TEXT_SIZE, (150, 230, 180)), (pane.x + 7, pane.y + 24))

        if battle.state.name == "DIALOGUE" and battle.dialogue_text and battle.opponent_dialogue_started:
            if battle.dialogue_type == "opponent":
                panel = pg.Rect(OPPONENT_DIALOGUE_RECT)
            else:
                panel = self._dialogue_rect()
            self._draw_transparent_box(panel)
            text = battle.visible_dialogue_text
            text_size = OPPONENT_DIALOGUE_TEXT_SIZE if battle.dialogue_type == "opponent" else BATTLE_TEXT_SIZE
            text_rect = pg.Rect(panel.x + 6, panel.y + 6, panel.width - 12, panel.height - 12)
            prepared = (text or "") if battle.dialogue_type == "opponent" else self.prepare_dialogue_text(text or "", text_rect, text_size)
            self._draw_prepared_text(prepared, text_rect, text_size, (255, 255, 255))
        elif battle.state.name in {"VICTORY", "DEFEAT", "ESCAPE"}:
            panel = pg.Rect(130, 132, w - 260, 88 if getattr(battle, "test_sequence_victory", False) else 72)
            self._draw_transparent_box(panel)
            text = {"VICTORY": "Victory!", "DEFEAT": "Defeat...", "ESCAPE": "Escaped."}[battle.state.name]
            self.surface.blit(self._text_surface(text, BATTLE_TITLE_SIZE, (255, 230, 135)), (panel.x + 12, panel.y + 13))
            prompt = ("A / Enter: replay sequence" if getattr(battle, "test_sequence_victory", False)
                      else "Press A / Enter to continue")
            self.surface.blit(self._text_surface(prompt, BATTLE_SMALL_TEXT_SIZE, (245, 245, 255)), (panel.x + 12, panel.y + 43))
            if getattr(battle, "test_sequence_victory", False):
                self.surface.blit(self._text_surface("B / Backspace: difficulty menu", BATTLE_SMALL_TEXT_SIZE, (245, 245, 255)), (panel.x + 12, panel.y + 61))
        else:
            menu_y = 165 if battle.state.name == "ITEM_MENU" else 285
            self._draw_battle_menu(battle.menu_entries(), battle.selected, (w // 2, menu_y))

        if battle.remark_text:
            panel = pg.Rect(POST_DEFEND_REMARK_RECT)
            self._draw_transparent_box(panel)
            self._draw_single_line(battle.remark_text, pg.Rect(panel.x + 6, panel.y + 2, panel.width - 12, panel.height - 4), BATTLE_SMALL_TEXT_SIZE, (255, 255, 255))

        environment_text = getattr(battle, "visible_environment_text", None)
        if environment_text and battle.state.name in {"COMMAND", "MOVE_MENU", "INVENTORY_MENU", "ITEM_MENU", "GEAR"}:
            panel = pg.Rect(ENVIRONMENT_DIALOGUE_RECT)
            self._draw_transparent_box(panel)
            self._draw_single_line(environment_text, pg.Rect(panel.x + 6, panel.y + 2, panel.width - 12, panel.height - 4), BATTLE_SMALL_TEXT_SIZE, (255, 255, 255))

        for animation in battle.animations.active:
            if animation.kind == "feedback":
                alpha = max(0, min(255, round(255 * (1 - animation.progress))))
                glyph = self._text_surface(animation.text, BATTLE_TEXT_SIZE + 4, animation.color).copy()
                glyph.set_alpha(alpha)
                self.surface.blit(glyph, (w // 2 - glyph.get_width() // 2, 112 - round(animation.progress * 15)))
            elif animation.kind == "flash":
                overlay = pg.Surface((w, h), pg.SRCALPHA)
                overlay.fill((*animation.color, round(75 * (1 - animation.progress))))
                self.surface.blit(overlay, (0, 0))
        for index, line in enumerate(battle.debug_data()):
            self.surface.blit(self._text_surface(line, 10, (145, 230, 230)), (8, h - 40 + index * 11))
        if any(animation.kind == "shake" for animation in battle.animations.active):
            shaken = self.surface.copy()
            offset = 3 if int(sum(animation.elapsed for animation in battle.animations.active if animation.kind == "shake") * 60) % 2 else -3
            self.surface.fill((12, 12, 28))
            self.surface.blit(shaken, (offset, 0))
        if revival is not None and revival.stage_name == "background_fade":
            duration = max(0.001, float(battle.REVIVAL_BACKGROUND_FADE_DURATION))
            alpha = max(0, min(255, round(255 * (1.0 - revival.stage_elapsed / duration))))
            overlay = pg.Surface((w, h), pg.SRCALPHA)
            overlay.fill((0, 0, 0, alpha))
            self.surface.blit(overlay, (0, 0))
        self._present()

    # -- exploration rendering ---------------------------------------------
    def _draw_scene_art(self, scene: dict[str, Any], objects: list[dict[str, Any]] | None = None,
                        sprite_overrides: dict[str, str] | None = None,
                        object_animations: dict[str, str] | None = None) -> None:
        """Draw a scene's reusable art layers without drawing a dialogue UI.

        Legacy scenes still use their single ``sprite`` field.  Exploration
        scenes may additionally provide already-resolved object mappings;
        visibility is deliberately resolved by the exploration layer rather
        than mutating YAML data cached by :class:`AssetLoader`.
        """
        pg = self.pygame
        art_area = pg.Rect(0, 0, self.config.width, self.config.height)
        background = scene.get("background")
        if isinstance(background, str) and background:
            if self.assets.is_image_asset(background):
                path, image = self._image_reference("backgrounds", background)
                self.surface.blit(self._scaled_image(path, image, art_area.size), art_area.topleft)
            else:
                self._draw_text_art("backgrounds", background, art_area)

        overrides = sprite_overrides or {}
        animations = object_animations or {}
        ordered_objects = sorted(
            enumerate(objects or []),
            key=lambda pair: (int(pair[1].get("z", 0)) if isinstance(pair[1], dict) else 0, pair[0]),
        )
        for index, obj in ordered_objects:
            if not isinstance(obj, dict) or obj.get("visible") is False:
                continue
            object_id = obj.get("id") if isinstance(obj.get("id"), str) else ""
            sprite = overrides.get(object_id, obj.get("sprite"))
            pos = obj.get("position", [0, 0])
            if not isinstance(pos, (list, tuple)) or len(pos) != 2:
                pos = [0, 0]
            x, y = int(pos[0]), int(pos[1])
            size = obj.get("size")
            bounds = pg.Rect(x, y, 1, 1)
            if isinstance(sprite, str) and sprite:
                if self.assets.is_image_asset(sprite):
                    path, image = self._image_reference("sprites", sprite)
                    if isinstance(size, (list, tuple)) and len(size) == 2:
                        image = self._scaled_image(path, image, (max(1, int(size[0])), max(1, int(size[1]))))
                    self.surface.blit(image, (x, y))
                    bounds.size = image.get_size()
                else:
                    # Text-art objects retain the compact legacy style.  A
                    # configured size gives them a stable local art region.
                    if isinstance(size, (list, tuple)) and len(size) == 2:
                        bounds.size = (max(1, int(size[0])), max(1, int(size[1])))
                    else:
                        bounds.size = (80, 60)
                    self._draw_text_art("sprites", sprite, bounds)
            elif isinstance(size, (list, tuple)) and len(size) == 2:
                bounds.size = (max(1, int(size[0])), max(1, int(size[1])))

            animation = animations.get(object_id, obj.get("animation"))
            if isinstance(animation, str) and animation:
                self._draw_lines(self._animation_frame(animation), bounds)

        # The legacy protagonist/character sprite draws after set dressing.
        sprite = scene.get("sprite")
        if isinstance(sprite, str) and sprite:
            if self.assets.is_image_asset(sprite):
                path, image = self._image_reference("sprites", sprite)
                pos = scene.get("sprite_position", [0, 0])
                self.surface.blit(image, (art_area.x + int(pos[0]), art_area.y + int(pos[1])))
            else:
                self._draw_text_art("sprites", sprite, art_area)
        if scene.get("animation"):
            self._draw_lines(self._animation_frame(scene["animation"]), art_area)

    def _draw_exploration_menu(self, entries: list[str], selected: int, rect: Any, *, horizontal: bool = False,
                               title: str | None = None, page_start: int = 0) -> None:
        """Small shared menu primitive for exploration actions and modals."""
        pg = self.pygame
        self._draw_transparent_box(rect)
        title_height = 0
        if title:
            glyph = self._text_surface(title, 16, (255, 230, 135))
            self.surface.blit(glyph, (rect.centerx - glyph.get_width() // 2, rect.y + 6))
            title_height = glyph.get_height() + 5
        if not entries:
            glyph = self._text_surface("Nothing available.", 14, (220, 220, 230))
            self.surface.blit(glyph, glyph.get_rect(center=rect.center))
            return
        font_size = 16
        highlight = self._ui_color("highlight_option_color", OPTION_HIGHLIGHT_COLOR)
        selected_color = self._ui_color("selected_option_color", SELECTED_OPTION_COLOR)
        if horizontal:
            glyphs = [self._text_surface(entry, font_size, (255, 255, 255)) for entry in entries]
            gap = 8
            widths = [glyph.get_width() + 16 for glyph in glyphs]
            total = sum(widths) + gap * (len(widths) - 1)
            x = rect.centerx - total // 2
            y = rect.y + title_height + max(4, (rect.height - title_height - max(glyph.get_height() for glyph in glyphs)) // 2)
            for index, (entry, glyph, width) in enumerate(zip(entries, glyphs, widths)):
                row = pg.Rect(x, y, width, glyph.get_height() + 6)
                if index == selected:
                    pg.draw.rect(self.surface, highlight, row)
                    glyph = self._text_surface(entry, font_size, selected_color)
                self.surface.blit(glyph, glyph.get_rect(center=row.center))
                x = row.right + gap
            return

        line_height = self._font(font_size).get_linesize() + 3
        capacity = max(1, (rect.height - title_height - 12) // line_height)
        start = max(0, min(page_start, max(0, len(entries) - capacity)))
        if selected < start:
            start = selected
        elif selected >= start + capacity:
            start = selected - capacity + 1
        visible = entries[start:start + capacity]
        y = rect.y + title_height + 6
        for offset, entry in enumerate(visible):
            index = start + offset
            row = pg.Rect(rect.x + 7, y, rect.width - 14, line_height)
            if index == selected:
                pg.draw.rect(self.surface, highlight, row)
                glyph = self._text_surface(entry, font_size, selected_color)
            else:
                glyph = self._text_surface(entry, font_size, (255, 255, 255))
            self.surface.blit(glyph, (row.x + 5, row.y + 1))
            y += line_height
        if start > 0:
            self.surface.blit(self._text_surface("^", 12, (230, 230, 240)), (rect.right - 17, rect.y + title_height + 3))
        if start + capacity < len(entries):
            self.surface.blit(self._text_surface("v", 12, (230, 230, 240)), (rect.right - 17, rect.bottom - 15))

    def _draw_exploration_cursor(self, cursor: dict[str, Any]) -> None:
        """Draw Look's centered cursor sprite and its interaction animation."""
        x, y = int(cursor.get("x", 0)), int(cursor.get("y", 0))
        if cursor.get("pressed"):
            filename = "click.png"
        else:
            interaction = cursor.get("interaction")
            frame = 1 + (self.pygame.time.get_ticks() // 500) % 2
            if interaction == "inspect":
                filename = f"inspect{frame}.png"
            elif interaction == "action":
                filename = f"activate{frame}.png"
            else:
                filename = "default.png"
        _path, image = self._image_reference("sprites", f"cursor/{filename}")
        self.surface.blit(image, image.get_rect(center=(x, y)))

    def _draw_exploration_dialogue(self, scene: dict[str, Any], text: str) -> None:
        panel = self._dialogue_rect()
        self._draw_transparent_box(panel)
        font_size = int(scene.get("font_size", self.render_config.get("font_size", 14)))
        text_rect = self.pygame.Rect(panel.x + 6, panel.y + 5, panel.width - 12, panel.height - 10)
        self._draw_prepared_text(text, text_rect, font_size, (255, 255, 255))

    def _draw_inventory_icon(self, item: dict[str, Any], cell: Any) -> None:
        """Draw an item icon without making legacy icon-less items crash."""
        pg = self.pygame
        icon = item.get("icon")
        if isinstance(icon, str) and self.assets.is_image_asset(icon):
            try:
                path, image = self._image_reference("items", icon)
                fitted = self._fit_image(path, image, (max(1, cell.width - 8), max(1, cell.height - 17)))
                self.surface.blit(fitted, fitted.get_rect(center=(cell.centerx, cell.y + (cell.height - 13) // 2)))
                return
            except AssetNotFoundError:
                pass
        # A letter tile is deliberately more useful than an empty/broken
        # image box for old item definitions.
        pg.draw.rect(self.surface, (68, 72, 106), cell.inflate(-10, -18))
        name = str(item.get("name", item.get("id", "?")))
        glyph = self._text_surface(name[:1].upper() or "?", 18, (235, 235, 248))
        self.surface.blit(glyph, glyph.get_rect(center=(cell.centerx, cell.y + (cell.height - 13) // 2)))

    def _draw_inventory(self, data: dict[str, Any]) -> None:
        """Render the reusable two-pane exploration inventory layout."""
        pg = self.pygame
        w, h = self.config.width, self.config.height
        left = pg.Rect(14, 18, max(132, w * 43 // 100), h - 36)
        right = pg.Rect(left.right + 8, 18, w - left.right - 22, h - 36)
        self._draw_transparent_box(left)
        self._draw_transparent_box(right)
        columns = max(1, int(data.get("columns", 4)))
        rows = max(1, int(data.get("rows", 3)))
        slots = columns * rows
        entries = list(data.get("items", []))
        selected = int(data.get("selected", 0))
        page = max(0, int(data.get("page", selected // slots if slots else 0)))
        page_items = entries[page * slots:(page + 1) * slots]
        grid = pg.Rect(left.x + 10, left.y + 26, left.width - 20, left.height - 50)
        # Keep slots square even when the configured row/column count does
        # not match the inventory pane's proportions.
        cell_size = max(24, min(grid.width // columns, grid.height // rows))
        for local_index in range(slots):
            column, row = local_index % columns, local_index // columns
            cell = pg.Rect(grid.x + column * cell_size, grid.y + row * cell_size,
                           cell_size - 3, cell_size - 3)
            absolute_index = page * slots + local_index
            pg.draw.rect(self.surface, (55, 58, 88), cell, 1)
            if absolute_index == selected and absolute_index < len(entries):
                pg.draw.rect(self.surface, self._ui_color("highlight_option_color", OPTION_HIGHLIGHT_COLOR), cell, 2)
            if local_index < len(page_items):
                item = page_items[local_index]
                self._draw_inventory_icon(item, cell)
                quantity = int(item.get("quantity", 1))
                if quantity > 1:
                    glyph = self._text_surface(f"x{quantity}", 11, (255, 255, 255))
                    self.surface.blit(glyph, (cell.right - glyph.get_width() - 2, cell.bottom - glyph.get_height() - 1))
                if item.get("equipped"):
                    glyph = self._text_surface("E", 11, (120, 245, 165))
                    self.surface.blit(glyph, (cell.x + 3, cell.y + 2))
        title = self._text_surface("Bag", 16, (255, 230, 135))
        self.surface.blit(title, (left.x + 9, left.y + 6))
        if len(entries) > slots:
            indicator = self._text_surface(f"{page + 1}/{max(1, math.ceil(len(entries) / slots))}", 12, (220, 220, 235))
            self.surface.blit(indicator, (left.right - indicator.get_width() - 8, left.y + 8))

        item = data.get("item")
        if not isinstance(item, dict):
            self.surface.blit(self._text_surface("Bag is empty.", 16, (235, 235, 245)), (right.x + 12, right.y + 14))
            return
        name = str(item.get("name", item.get("id", "Item")))
        item_type = str(item.get("type", "item")).replace("_", " ").title()
        self.surface.blit(self._text_surface(name, 20, (255, 230, 135)), (right.x + 12, right.y + 12))
        self.surface.blit(self._text_surface(item_type, 14, (160, 220, 245)), (right.x + 12, right.y + 38))
        if item.get("equipped"):
            self.surface.blit(self._text_surface("Equipped", 13, (120, 245, 165)), (right.right - 82, right.y + 15))
        description_rect = pg.Rect(right.x + 12, right.y + 62, right.width - 24, max(30, right.height // 3))
        y = description_rect.y
        for line in self._wrapped_lines(str(item.get("description", "")), description_rect.width, 14):
            self.surface.blit(self._text_surface(line, 14, (242, 242, 250)), (description_rect.x, y))
            y += self._font(14).get_linesize()
            if y >= description_rect.bottom:
                break
        stats = item.get("stats", {}) if isinstance(item.get("stats"), dict) else {}
        stat_y = max(description_rect.bottom + 8, right.y + right.height * 58 // 100)
        for label, key in (("HP", "hp"), ("Attack", "attack"), ("Defense", "defense")):
            value = int(stats.get(key, 0))
            rendered = f"{label}:  {value:+d}" if value else f"{label}:  0"
            self.surface.blit(self._text_surface(rendered, 15, (235, 235, 248)), (right.x + 12, stat_y))
            stat_y += self._font(15).get_linesize() + 3
        hint = self._text_surface("A / Enter: actions   B / Backspace: return", 11, (195, 195, 215))
        self.surface.blit(hint, (right.x + 12, right.bottom - hint.get_height() - 8))

    def _inventory_context_rect(self, data: dict[str, Any]) -> Any:
        """Place an item context menu beside its currently selected cell."""
        pg = self.pygame
        w, h = self.config.width, self.config.height
        left = pg.Rect(14, 18, max(132, w * 43 // 100), h - 36)
        columns = max(1, int(data.get("columns", 4)))
        rows = max(1, int(data.get("rows", 3)))
        slots = columns * rows
        selected = max(0, int(data.get("selected", 0)))
        page = max(0, int(data.get("page", selected // slots)))
        local = max(0, selected - page * slots)
        grid = pg.Rect(left.x + 10, left.y + 26, left.width - 20, left.height - 50)
        cell_size = max(24, min(grid.width // columns, grid.height // rows))
        cell = pg.Rect(grid.x + (local % columns) * cell_size, grid.y + (local // columns) * cell_size,
                       cell_size - 3, cell_size - 3)
        width, height = min(150, w * 35 // 100), min(125, h * 35 // 100)
        x = cell.right + 5
        if x + width > w - 8:
            x = max(8, cell.left - width - 5)
        y = min(max(8, cell.y), h - height - 8)
        return pg.Rect(x, y, width, height)

    def render_exploration(self, scene: dict[str, Any], view: dict[str, Any]) -> None:
        """Draw an opt-in exploration scene on the normal logical surface.

        ``scene`` is the current isolated legacy scene mapping supplied by
        GameEngine; it is not looked up by scene id here.  ``view`` is a
        deliberately simple runtime render model built by GameEngine, keeping
        pygame objects out of the exploration and inventory rules.
        """
        pg = self.pygame
        self.surface.fill((12, 12, 28))
        self._draw_scene_art(
            scene,
            list(view.get("objects", [])),
            view.get("sprite_overrides") if isinstance(view.get("sprite_overrides"), dict) else None,
            view.get("object_animations") if isinstance(view.get("object_animations"), dict) else None,
        )
        cursor = view.get("cursor")
        if isinstance(cursor, dict):
            self._draw_exploration_cursor(cursor)

        dialogue = view.get("dialogue")
        if isinstance(dialogue, str):
            self._draw_exploration_dialogue(scene, dialogue)
            self._present()
            return

        mode = str(view.get("mode", "EXPLORATION_MENU"))
        w, h = self.config.width, self.config.height
        if mode == "EXPLORATION_MENU":
            self._draw_exploration_menu(["Move", "Look", "Bag"], int(view.get("selected", 0)),
                                        pg.Rect(w // 2 - min(270, w - 30) // 2, h - 70, min(270, w - 30), 42), horizontal=True)
        elif mode == "MOVE_MENU":
            destinations = [str(entry.get("label", entry.get("scene", "?"))) if isinstance(entry, dict) else str(entry)
                            for entry in view.get("destinations", [])]
            self._draw_exploration_menu(destinations, int(view.get("selected", 0)),
                                        pg.Rect(w // 2 - min(250, w - 34) // 2, 45, min(250, w - 34), h - 90), title="Move")
            hint = self._text_surface("B / Backspace: return", 12, (225, 225, 235))
            self.surface.blit(hint, hint.get_rect(center=(w // 2, h - 25)))
        elif mode in {"BAG", "ITEM_ACTION_MENU", "TOSS_CONFIRMATION"}:
            inventory = view.get("inventory") if isinstance(view.get("inventory"), dict) else {}
            self._draw_inventory(inventory)
            if mode == "ITEM_ACTION_MENU":
                actions = [str(value).title() for value in view.get("item_actions", [])]
                self._draw_exploration_menu(actions, int(view.get("modal_selected", 0)),
                                            self._inventory_context_rect(inventory), title="Item")
            elif mode == "TOSS_CONFIRMATION":
                self._draw_exploration_menu(["No", "Yes"], int(view.get("modal_selected", 0)),
                                            pg.Rect(w // 2 - 88, h // 2 - 54, 176, 108), title="Toss item?")
        elif mode == "LOOK_MODE":
            hint = self._text_surface("Move cursor  |  A / Enter: interact  |  B / Backspace: return", 12, (245, 245, 255))
            panel = pg.Rect(10, h - 28, min(w - 20, hint.get_width() + 16), 20)
            self._draw_transparent_box(panel)
            self.surface.blit(hint, (panel.x + 8, panel.y + 4))
        self._present()

    def render(self, scene: dict[str, Any], choices: list[dict[str, Any]], selected: int, message: str | None = None, battle_lines: list[str] | None = None, text_page: str | None = None, show_options: bool = True) -> None:
        """Draw the supplied scene mapping to the logical surface.

        GameEngine supplies the isolated mapping produced by its canonical
        StoryProject-backed scene-entry path.  Asset loading below is limited
        to the referenced runtime assets; this method does not establish a
        second authored scene-definition source.
        """
        pg = self.pygame
        self.surface.fill((12, 12, 28))
        w, h = self.config.width, self.config.height
        art_area = pg.Rect(0, 0, w, h)
        background = scene.get("background")
        if background:
            if self.assets.is_image_asset(background):
                path, image = self._image("backgrounds", background)
                self.surface.blit(self._scaled_image(path, image, art_area.size), art_area.topleft)
            else:
                self._draw_text_art("backgrounds", background, art_area)
        sprite = scene.get("sprite")
        if sprite and self.assets.is_image_asset(sprite):
            path, image = self._image("sprites", sprite)
            pos = scene.get("sprite_position", [0, 0])
            self.surface.blit(image, (art_area.x + int(pos[0]), art_area.y + int(pos[1])))
        elif sprite:
            self._draw_text_art("sprites", sprite, art_area)
        if scene.get("animation"):
            self._draw_lines(self._animation_frame(scene["animation"]), art_area)

        panel = self._dialogue_rect()
        self._draw_transparent_box(panel)
        # font_size is the normal dialogue size; art always uses its fixed
        # monospace presentation size and has no story configuration.
        font_size = int(scene.get("font_size", self.render_config.get("font_size", 14)))
        text_rect = pg.Rect(panel.x + 6, panel.y + 5, panel.width - 12, panel.height - 10)
        if text_page is not None:
            y = self._draw_prepared_text(text_page, text_rect, font_size, (255, 255, 255))
        else:
            content = message if message is not None else scene.get("text", "")
            y = self._draw_prepared_text(self.prepare_dialogue_text(content, text_rect, font_size), text_rect, font_size, (255, 255, 255))
        visible_choices = choices if show_options else []
        if visible_choices:
            option_size = int(self.render_config.get("option_font_size", font_size))
            glyphs = [self._text_surface(choice["text"], option_size, (255, 255, 255)) for choice in visible_choices]
            padding, row_gap = 7, 3
            option_width = min(w - 12, max(glyph.get_width() for glyph in glyphs) + padding * 2)
            option_height = sum(glyph.get_height() + row_gap for glyph in glyphs) + padding * 2 - row_gap
            center_x, center_y = self._ui_position("options_position", OPTIONS_POSITION)
            option_box = pg.Rect(center_x - option_width // 2, center_y - option_height // 2, option_width, option_height)
            self._draw_transparent_box(option_box)
            y = option_box.y + padding
            highlight = self._ui_color("highlight_option_color", OPTION_HIGHLIGHT_COLOR)
            selected_color = self._ui_color("selected_option_color", SELECTED_OPTION_COLOR)
            for index, glyph in enumerate(glyphs):
                selected_here = index == selected
                row = pg.Rect(option_box.x + padding // 2, y, option_box.width - padding, glyph.get_height())
                if selected_here:
                    pg.draw.rect(self.surface, highlight, row)
                    selected_glyph = self._text_surface(visible_choices[index]["text"], option_size, selected_color)
                    self.surface.blit(selected_glyph, (row.x + padding // 2, row.y))
                else:
                    self.surface.blit(glyph, (row.x + padding // 2, row.y))
                y = row.bottom + row_gap
        if battle_lines:
            y = panel.y + 4
            for line in battle_lines[-4:]:
                glyph = self._text_surface(line, max(8, font_size - 2), (255, 220, 170))
                self.surface.blit(glyph, (panel.x + 5, y))
                y += glyph.get_height() + 1
        self.window.fill((0, 0, 0))
        scaled = pg.transform.scale(self.surface, self.destination[2:])
        self.window.blit(scaled, self.destination[:2])
        pg.display.flip()
        self.dirty = False
