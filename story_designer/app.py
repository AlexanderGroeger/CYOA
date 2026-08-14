"""Application bootstrap for the Story Designer."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def main(argv: Sequence[str] | None = None) -> int:
    """Create the Qt application and enter its event loop."""

    application = QApplication(list(argv) if argv is not None else sys.argv)
    application.setApplicationName("Story Designer")
    application.setOrganizationName("CYOA")
    window = MainWindow()
    window.show()
    return application.exec()
