"""
engine/events/random_events.py

Weighted random selection from an event pool, gated by an overall trigger
chance. A pool looks like:

    chance: 0.3
    events:
      - {id: find_gold, weight: 50}
      - {id: ambush, weight: 20}

`chance` decides whether anything happens at all this time; `weight`
decides which specific event fires if something does.
"""

from __future__ import annotations

import random
from typing import Any, Callable


def pick_weighted_event(events: list[dict[str, Any]], rng: Callable = random.random) -> dict[str, Any]:
    weights = [e.get("weight", 1) for e in events]
    total = sum(weights)
    roll = rng() * total
    cumulative = 0.0
    for event, weight in zip(events, weights):
        cumulative += weight
        if roll <= cumulative:
            return event
    return events[-1]


def maybe_trigger(event_pool: dict[str, Any], rng: Callable = random.random) -> str | None:
    """Return the selected event's scene id, or ``None`` on a missed roll.

    Event entries deliberately only select an id. The visual content, actions,
    and choices live in ``scenes/<id>.yaml``, so an event is rendered through
    the normal scene pipeline rather than as an unrendered data fragment.
    """
    events = event_pool.get("events", [])
    if not events:
        return None
    if rng() > event_pool.get("chance", 0):
        return None
    selected = pick_weighted_event(events, rng=rng)
    event_id = selected.get("id")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("Random event entries require a non-empty string id")
    return event_id
