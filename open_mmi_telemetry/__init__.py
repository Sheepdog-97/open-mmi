"""Owner-controlled Telemetry Guard for Open MMI."""

from .guard import (
    DEFAULT_AUTHORIZATION_PATH,
    CollectionDecision,
    TelemetryDenied,
    TelemetryGuardError,
    collect_with_guard,
    collection_decision,
    load_scope_file,
    normalize_scope,
    read_authorization,
    require_collection_allowed,
    scope_digest,
)

__all__ = [
    "DEFAULT_AUTHORIZATION_PATH",
    "CollectionDecision",
    "TelemetryDenied",
    "TelemetryGuardError",
    "collect_with_guard",
    "collection_decision",
    "load_scope_file",
    "normalize_scope",
    "read_authorization",
    "require_collection_allowed",
    "scope_digest",
]
