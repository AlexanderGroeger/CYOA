"""Application-wide mouse-wheel behavior for Designer input controls."""

from __future__ import annotations

from PySide6.QtCore import QChildEvent, QEvent, QObject, Qt
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QDial, QSlider, QWidget


class InputWheelGuard(QObject):
    """Prevent scrolling over value/select controls from changing them.

    Rich text widgets are deliberately not included here: Qt should continue
    to use the wheel to scroll their contents.  Checking ancestors also covers
    internal line edits used by spin boxes and editable combo boxes.
    """

    _VALUE_WIDGET_TYPES = (QAbstractSpinBox, QComboBox, QSlider, QDial)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API name
        if event.type() == QEvent.Type.ChildAdded:
            self._remove_wheel_focus(watched)
            if isinstance(event, QChildEvent):
                self._remove_wheel_focus(event.child())
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.Show:
            self._remove_wheel_focus(watched)
            return super().eventFilter(watched, event)
        if event.type() != QEvent.Type.Wheel:
            return super().eventFilter(watched, event)
        if self._belongs_to_value_widget(watched):
            return True
        return super().eventFilter(watched, event)

    @classmethod
    def _remove_wheel_focus(cls, watched: QObject) -> None:
        if not isinstance(watched, cls._VALUE_WIDGET_TYPES):
            return
        policy = watched.focusPolicy()
        if policy == Qt.FocusPolicy.WheelFocus:
            watched.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @classmethod
    def _belongs_to_value_widget(cls, watched: QObject) -> bool:
        current: QObject | None = watched
        while current is not None:
            if isinstance(current, cls._VALUE_WIDGET_TYPES):
                return True
            current = current.parent()
        return False


def install_input_wheel_guard(application: QObject) -> InputWheelGuard:
    """Install and return the Designer's shared input-wheel guard."""

    guard = InputWheelGuard(application)
    application.installEventFilter(guard)
    for widget in application.allWidgets():
        guard._remove_wheel_focus(widget)
    return guard


__all__ = ["InputWheelGuard", "install_input_wheel_guard"]
