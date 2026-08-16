"""Qt widgets composing the Story Designer shell."""

from .diagnostics import DiagnosticsWidget
from .inspector import InspectorWidget
from .project_browser import ProjectBrowser
from .property_editors import AssetPathEditor, PropertyEditorFactory, ReferenceComboBox
from .scene_editor import ResizeHandleItem, SceneCanvasView, SceneEditorWidget, SceneGraphicsItem
from .navigation_panel import NavigationPanel
from .dialogue_editor import DialogueEditorWidget
from .workspace import ContextSummary, ToolShell, WorkspaceTabs, WorkspaceWidget
from .test_state import TestStateDialog
from .condition_editor import ConditionEditorWidget
from .asset_browser import AssetBrowserDialog, AssetBrowserWidget
from .scene_graph import SceneGraphCanvas, SceneGraphEdgeItem, SceneGraphNodeItem, SceneGraphWidget
from .battle_editor import BattleEditorWidget
from .combat_move_editor import CombatMoveEditorWidget
from .defense_pattern_editor import DefensePatternEditorWidget

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
    "ToolShell",
    "WorkspaceTabs",
    "ContextSummary",
    "TestStateDialog",
    "ConditionEditorWidget",
    "AssetBrowserDialog",
    "AssetBrowserWidget",
    "SceneGraphCanvas",
    "SceneGraphEdgeItem",
    "SceneGraphNodeItem",
    "SceneGraphWidget",
    "BattleEditorWidget",
    "CombatMoveEditorWidget",
    "DefensePatternEditorWidget",
]
