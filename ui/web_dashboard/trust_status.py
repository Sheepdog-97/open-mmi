"""Read-only dashboard adapter for the local Open MMI Trust Inspector."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from open_mmi_trust.inspector import FAIL, PASS, UNVERIFIED, inspect_system


_VALID_STATUSES = frozenset({PASS, FAIL, UNVERIFIED})


def trust_status_payload(
    inspector: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return fresh local Trust Inspector evidence without changing trust state."""

    collect = inspector or inspect_system

    try:
        report = collect()
    except Exception:
        return {
            "api_version": 1,
            "status": UNVERIFIED,
            "report": None,
            "error": "Trust inspection evidence is unavailable.",
        }

    if not isinstance(report, Mapping):
        return {
            "api_version": 1,
            "status": UNVERIFIED,
            "report": None,
            "error": "Trust inspection evidence is malformed.",
        }

    status = report.get("status")
    if status not in _VALID_STATUSES:
        return {
            "api_version": 1,
            "status": UNVERIFIED,
            "report": None,
            "error": "Trust inspection status is unavailable.",
        }

    return {
        "api_version": 1,
        "status": status,
        "report": dict(report),
        "error": None,
    }
