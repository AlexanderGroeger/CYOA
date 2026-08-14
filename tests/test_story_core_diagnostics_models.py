from __future__ import annotations

from pathlib import Path

import pytest

from engine.story_core.diagnostics import Diagnostic, DiagnosticSeverity, Diagnostics
from engine.story_core.models import ItemDefinition, SceneDefinition


def test_diagnostics_are_source_qualified_and_collect_without_fail_fast(tmp_path: Path) -> None:
    source = tmp_path / "scenes" / "intro.yaml"
    diagnostic = Diagnostic(
        source=source,
        path=("choices", 2, "goto"),
        code="unknown_scene_reference",
        severity=DiagnosticSeverity.ERROR,
        message="Unknown scene 'missing'.",
    )
    diagnostics = Diagnostics([diagnostic])
    diagnostics.warning(
        "deprecated_field",
        "Use the newer field name.",
        source=source,
        path=("legacy_field",),
    )

    assert diagnostic.source == source
    assert diagnostic.field_path == ("choices", 2, "goto")
    assert diagnostic.path_text == "choices[2].goto"
    assert "unknown_scene_reference" in diagnostic.format()
    assert diagnostics.has_errors is True
    assert diagnostics.errors == (diagnostic,)
    assert diagnostics.warnings[0].source == source


def test_static_definition_models_keep_immutable_isolated_authored_payloads(tmp_path: Path) -> None:
    scene_payload = {
        "id": "intro",
        "text": "Hello",
        "choices": [{"text": "Continue", "goto": "ending"}],
        "future_extension": {"keep": True},
    }
    item_payload = {
        "name": "Token",
        "type": "key",
        "future_extension": {"keep": True},
    }
    scene = SceneDefinition.from_mapping(scene_payload, tmp_path / "scenes" / "intro.yaml")
    item = ItemDefinition.from_mapping(item_payload, tmp_path / "items" / "items.yaml", identifier="token")

    scene_payload["future_extension"]["keep"] = False
    item_payload["future_extension"]["keep"] = False

    assert scene.source == tmp_path / "scenes" / "intro.yaml"
    assert scene.field_path == ()
    assert scene.authored["future_extension"]["keep"] is True
    assert item.id == "token"
    assert item.authored["future_extension"]["keep"] is True

    with pytest.raises(TypeError):
        scene.authored["new_field"] = "not mutable"  # type: ignore[index]

    legacy_mapping = scene.to_mapping()
    legacy_mapping["future_extension"]["keep"] = "changed only in caller copy"
    assert scene.authored["future_extension"]["keep"] is True
