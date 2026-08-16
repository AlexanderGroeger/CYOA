"""Temporary compatibility helpers for the incremental Story/Core migration.

These adapters are deliberately one-way: they let legacy/runtime-facing
callers read ordinary mutable mappings from immutable Story/Core definitions,
and let tooling inspect version-1 save references.  They do not take over
runtime loading or save persistence.
"""

from .legacy_views import LegacyProjectView
from .object_interaction import DEFAULT_OBJECT_SIZE, has_legacy_object_interactions, migrate_legacy_object_interactions
from .save_adapter import (
    SaveCompatibilityAdapter,
    SaveReferenceValidator,
    validate_save_references,
    validate_v1_save_references,
)

__all__ = [
    "LegacyProjectView",
    "DEFAULT_OBJECT_SIZE",
    "has_legacy_object_interactions",
    "migrate_legacy_object_interactions",
    "SaveCompatibilityAdapter",
    "SaveReferenceValidator",
    "validate_save_references",
    "validate_v1_save_references",
]
