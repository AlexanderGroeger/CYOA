"""Compatibility wrapper for legacy story condition expressions.

The canonical safe parser/evaluator now lives in ``engine.story_core`` so a
headless Story Designer and the pygame runtime understand the same authored
expression grammar.  This module preserves the established public function
and engine exception type for runtime callers.
"""

from __future__ import annotations

from engine.core.game_state import GameState
from engine.errors import ConditionError
from engine.story_core.conditions import (
    ConditionError as StoryCoreConditionError,
    evaluate_legacy_condition as _evaluate_legacy_condition,
)


def evaluate_condition(expr: str | None, state: GameState) -> bool:
    """Evaluate the legacy safe expression dialect against ``GameState``.

    Empty/missing expressions remain true, and any invalid expression is
    surfaced as the existing :class:`engine.errors.ConditionError` type.
    """

    try:
        return _evaluate_legacy_condition(expr, state)
    except StoryCoreConditionError as exc:
        raise ConditionError(str(exc)) from None
