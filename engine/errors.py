"""
engine/errors.py

Central place for the engine's exception types. Story-authoring mistakes
(a typo'd scene id, a malformed condition, a missing asset) should raise
one of these with enough context to fix the problem, rather than a bare
KeyError/FileNotFoundError with no indication of which story file caused it.
"""


class EngineError(Exception):
    """Base class for all engine-raised errors."""


class StoryValidationError(EngineError):
    """A story data file is structurally invalid: unknown scene id, a
    choice with no goto/battle/random_event, an unrecognized action type,
    etc. These should always be fixable by editing story YAML, never by
    editing engine code."""


class BattleConfigError(StoryValidationError):
    """A battle YAML file has an invalid, incomplete, or unsupported field."""


class AssetNotFoundError(EngineError):
    """An asset (background, sprite, animation, sound) referenced by a
    story file wasn't found in either the story's own assets/ folder or
    shared_assets/."""


class ConditionError(EngineError):
    """A choice/scene `condition:` expression is invalid or uses a
    construct outside the restricted condition grammar."""


class SaveVersionError(EngineError):
    """A save file doesn't match the current story (wrong story id) or
    was produced by an incompatible save format version."""
