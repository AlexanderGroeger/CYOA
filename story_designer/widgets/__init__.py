"""Qt widgets composing the Story Designer shell."""

from .diagnostics import DiagnosticsWidget
from .inspector import InspectorWidget
from .project_browser import ProjectBrowser
from .property_editors import AssetPathEditor, PropertyEditorFactory, ReferenceComboBox
from .scene_editor import ResizeHandleItem, SceneCanvasView, SceneEditorWidget, SceneGraphicsItem
from .navigation_panel import NavigationPanel
from .dialogue_editor import DialogueEditorWidget
from .workspace import WorkspaceWidget
from .test_state import TestStateDialog
from .condition_editor import ConditionEditorWidget

__all__ = [
    "AssetPathEditor",
    "DiagnosticsWidget",
    "InspectorWidget",
    "ProjectBrowser",
    "PropertyEditorFactory",
    "ReferenceComboBox",
    "SceneCanvasView",
    "SceneEditorWidget",
    "SceneGraphicsItem",
    "ResizeHandleItem",
    "NavigationPanel",
    "DialogueEditorWidget",
    "WorkspaceWidget",
    "TestStateDialog",
    "ConditionEditorWidget",
]
