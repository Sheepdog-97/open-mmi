"""Open MMI trust-boundary declarations.

The release manifest is evidence supplied by a release.  It is deliberately
separate from future owner-accepted trust state: a candidate release may
*declare* capabilities, but it cannot authorize its own boundary expansion.
"""

from .manifest import (
    DEFAULT_MANIFEST_PATH,
    ManifestError,
    canonical_manifest_bytes,
    load_manifest,
    manifest_digest,
    validate_manifest,
)

__all__ = [
    "DEFAULT_MANIFEST_PATH",
    "ManifestError",
    "canonical_manifest_bytes",
    "load_manifest",
    "manifest_digest",
    "validate_manifest",
]
