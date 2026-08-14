"""Qt widgets composing the Story Designer shell."""

from .diagnostics import DiagnosticsWidget
from .inspector import InspectorWidget
from .project_browser import ProjectBrowser
from .workspace import WorkspaceWidget

__all__ = ["DiagnosticsWidget", "InspectorWidget", "ProjectBrowser", "WorkspaceWidget"]
