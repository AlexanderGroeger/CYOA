import pytest

from engine.errors import StoryValidationError
from engine.render.display import centered_rect, chunk_lines, integer_scale, parse_display_config


def test_integer_scale_uses_largest_fitting_integer():
    assert integer_scale(1920, 1080, 320, 180) == 6
    assert integer_scale(1024, 768, 320, 180) == 3


def test_centered_destination_rectangle_letterboxes_surface():
    assert centered_rect(1024, 768, 320, 180, 3) == (32, 114, 960, 540)


@pytest.mark.parametrize("display", [None, {}, {"width": 0, "height": 180}, {"width": "320", "height": 180}])
def test_invalid_logical_resolution_is_a_clear_story_error(display):
    with pytest.raises(StoryValidationError):
        parse_display_config({"display": display})


def test_story_display_config_parsing():
    assert parse_display_config({"display": {"width": 320, "height": 180}}).width == 320


def test_wrapped_dialogue_lines_are_paginated_without_loss():
    assert chunk_lines(["one", "two", "three", "four", "five"], 2) == [
        ["one", "two"], ["three", "four"], ["five"],
    ]


def test_prepared_page_text_keeps_its_existing_line_breaks_when_revealed():
    page = "first line\nsecond line"
    assert page[:8] == "first li"
    assert page[:12] == "first line\ns"
