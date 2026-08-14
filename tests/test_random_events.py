from engine.events.random_events import pick_weighted_event, maybe_trigger


def test_pick_weighted_event():
    events = [{"id": "a", "weight": 70}, {"id": "b", "weight": 30}]
    assert pick_weighted_event(events, rng=lambda: 0.0)["id"] == "a"
    assert pick_weighted_event(events, rng=lambda: 0.99)["id"] == "b"


def test_maybe_trigger_chance_gate():
    events = [{"id": "a", "weight": 70}, {"id": "b", "weight": 30}]
    pool = {"chance": 0.3, "events": events}
    assert maybe_trigger(pool, rng=lambda: 0.5) is None
    result = maybe_trigger(pool, rng=lambda: 0.1)
    assert result == "a"


def test_maybe_trigger_empty_pool():
    assert maybe_trigger({"chance": 1.0, "events": []}, rng=lambda: 0.0) is None
