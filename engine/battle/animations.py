"""Tiny delta-time battle presentation queue.

Animations deliberately carry only presentation data.  Damage, state
transitions, and inventory mutations happen in the controller before a
feedback animation is queued.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BattleAnimation:
    kind: str
    duration: float
    text: str = ""
    color: tuple[int, int, int] = (255, 255, 255)
    x: float = 0.5
    y: float = 0.5
    elapsed: float = 0.0

    @property
    def progress(self) -> float:
        return min(1.0, self.elapsed / self.duration) if self.duration > 0 else 1.0


@dataclass
class AnimationQueue:
    active: list[BattleAnimation] = field(default_factory=list)
    displayed_health: dict[str, float] = field(default_factory=dict)
    target_health: dict[str, float] = field(default_factory=dict)

    def feedback(self, text: str, color: tuple[int, int, int] = (255, 230, 120), duration: float = 0.75) -> None:
        self.active.append(BattleAnimation("feedback", duration, text, color))

    def flash(self, color: tuple[int, int, int] = (255, 255, 255), duration: float = 0.16) -> None:
        self.active.append(BattleAnimation("flash", duration, color=color))

    def shake(self, duration: float = 0.18) -> None:
        self.active.append(BattleAnimation("shake", duration))

    def enemy_shake(self, duration: float = 0.25) -> None:
        """Briefly jitter the opponent without moving the battle UI."""
        self.active.append(BattleAnimation("enemy_shake", duration))

    def set_health(self, key: str, current: float, maximum: float, *, immediate: bool = False) -> None:
        ratio = 0.0 if maximum <= 0 else max(0.0, min(1.0, current / maximum))
        self.target_health[key] = ratio
        if immediate:
            self.displayed_health[key] = ratio
        else:
            self.displayed_health.setdefault(key, ratio)

    def update(self, dt: float) -> bool:
        changed = False
        retained: list[BattleAnimation] = []
        for animation in self.active:
            animation.elapsed += max(0.0, dt)
            changed = True
            if animation.elapsed < animation.duration:
                retained.append(animation)
        self.active = retained
        # A fixed interpolation rate makes health-bar easing independent of
        # frame rate and keeps the visual system separate from health rules.
        for key, target in self.target_health.items():
            current = self.displayed_health.get(key, target)
            next_value = current + (target - current) * min(1.0, max(0.0, dt) * 10.0)
            if abs(next_value - current) > 0.0001:
                changed = True
            self.displayed_health[key] = next_value
        return changed
