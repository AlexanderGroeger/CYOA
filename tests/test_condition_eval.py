import pytest

from engine.core.condition_eval import evaluate_condition
from engine.core.game_state import GameState
from engine.errors import ConditionError


@pytest.fixture
def state():
    s = GameState()
    s.set_flag("wolf_alive", True)
    s.add_var("gold", 10)
    s.add_item("torch")
    return s


def test_empty_condition_always_true(state):
    assert evaluate_condition(None, state) is True
    assert evaluate_condition("", state) is True


def test_flag_comparisons(state):
    assert evaluate_condition("flags.wolf_alive == true", state) is True
    assert evaluate_condition("flags.wolf_alive == false", state) is False
    assert evaluate_condition("not flags.wolf_alive", state) is False


def test_has_item(state):
    assert evaluate_condition("has_item('torch')", state) is True
    assert evaluate_condition("has_item('sword')", state) is False


def test_boolean_combinators(state):
    assert evaluate_condition("has_item('torch') and vars.gold >= 10", state) is True
    assert evaluate_condition("has_item('torch') and vars.gold >= 11", state) is False
    assert evaluate_condition("vars.gold > 5 or flags.nonexistent", state) is True


def test_chained_comparison(state):
    assert evaluate_condition("0 <= vars.gold < 100", state) is True


@pytest.mark.parametrize("bad_expr", [
    "__import__('os').system('echo pwned')",
    "open('/etc/passwd').read()",
    "1 + 1",
    "[x for x in range(3)]",
    "lambda: 1",
    "flags.__class__",
    "vars.__init__",
])
def test_rejects_unsafe_or_unsupported(state, bad_expr):
    with pytest.raises(ConditionError):
        evaluate_condition(bad_expr, state)


def test_invalid_syntax_raises_condition_error(state):
    with pytest.raises(ConditionError):
        evaluate_condition("flags.x ==", state)
