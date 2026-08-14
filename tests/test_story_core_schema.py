from __future__ import annotations

from engine.story_core.schema import FieldSpec, Schema, TypeSpec, default_schema_registry


def test_default_schema_registry_exposes_editor_metadata_for_principal_content() -> None:
    registry = default_schema_registry()

    scene = registry.require("scene")
    background = scene.field("background")
    animation = scene.field("animation")
    actions = scene.field("actions")

    assert registry.get("scenes") is scene
    assert background is not None and background.asset_kind == "backgrounds"
    assert animation is not None and animation.reference_target == "animation"
    assert actions is not None and actions.type.kind == "list"
    assert scene.field("text").ui_hint == "multiline"  # type: ignore[union-attr]


def test_schema_normalizes_known_aliases_without_dropping_tolerated_extensions() -> None:
    battle = default_schema_registry().require("battle")
    normalized = battle.normalize_mapping(
        {
            "enemy_patterns": [{"id": "legacy_pattern"}],
            "future_extension": {"preserve": True},
        }
    )

    assert normalized["defense_sequences"] == [{"id": "legacy_pattern"}]
    assert "enemy_patterns" not in normalized
    assert normalized["future_extension"] == {"preserve": True}


def test_field_metadata_supports_defaults_aliases_and_applicability_rules() -> None:
    field = FieldSpec(
        key="advanced_value",
        display_name="Advanced value",
        type=TypeSpec.integer(),
        aliases=("legacy_value",),
        default=3,
        minimum=0,
        applicable_when={"mode": "advanced"},
    )
    schema = Schema("test", (field,))

    assert schema.canonical_key("legacy_value") == "advanced_value"
    assert schema.defaults() == {"advanced_value": 3}
    assert field.is_applicable({"mode": "advanced"}) is True
    assert field.is_applicable({"mode": "simple"}) is False
