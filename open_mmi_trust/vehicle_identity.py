"""Fail-closed guard for vehicle identity material crossing remote egress.

Open MMI may use a VIN locally for owner-authorized telemetry binding, but no
runtime purpose authorizes sending identity-bearing material to an external
service for lookup or resolution.  This module deliberately recognizes only
identity forms that Open MMI itself understands; it is not a general PII
classifier.
"""

from __future__ import annotations

import re
from typing import Iterable


_VIN_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Z0-9])",
    re.IGNORECASE,
)
_IDENTITY_LABEL_RE = re.compile(
    r"\b(?:"
    r"vin(?:[._ -]?(?:hash|fingerprint))?"
    r"|vehicle[._ -]?id(?:entity)?"
    r"|registration(?:[._ -]?(?:number|plate))?"
    r"|licen[cs]e[._ -]?plate"
    r")\b\s*(?:=|:)",
    re.IGNORECASE,
)


class RemoteVehicleIdentityDenied(RuntimeError):
    """Identity-bearing material was rejected before external egress."""


def contains_vehicle_identity_material(value: object) -> bool:
    """Return True for identity material Open MMI knows how to recognize.

    Canonical VIN tokens are detected even when embedded in a normal search
    string.  Explicit identity-labelled values are denied even when incomplete
    or hashed, because their declared purpose is identity lookup rather than
    media retrieval.
    """

    if not isinstance(value, str) or not value:
        return False
    return bool(_VIN_TOKEN_RE.search(value) or _IDENTITY_LABEL_RE.search(value))


def require_remote_identity_safe(values: Iterable[object]) -> None:
    """Reject recognized vehicle identity without reflecting it in errors."""

    if any(contains_vehicle_identity_material(value) for value in values):
        raise RemoteVehicleIdentityDenied(
            "vehicle identity material is prohibited from remote egress"
        )
