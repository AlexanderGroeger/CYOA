"""Qt widgets composing the Story Designer shell."""

from .diagnostics import DiagnosticsWidget
from .inspector import InspectorWidget
from .project_browser import ProjectBrowser
from .property_editors import AssetPathEditor, PropertyEditorFactory, ReferenceComboBox
from .workspace import WorkspaceWidget

__all__ = [
    "AssetPathEditor",
    "DiagnosticsWidget",
    "InspectorWidget",
    "ProjectBrowser",
    "PropertyEditorFactory",
    "ReferenceComboBox",
    "WorkspaceWidget",
]
