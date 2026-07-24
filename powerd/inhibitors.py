"""Open MMI transaction inhibitors used by the power policy."""

from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path
from typing import Sequence


LOCK_PATHS = (
    Path("/run/open-mmi/lifecycle.lock"),
    Path("/run/open-mmi/update.lock"),
    Path("/run/open-mmi/vehicle-configuration.lock"),
)


def transaction_active(
    lock_paths: Sequence[Path] = LOCK_PATHS,
    *,
    expected_uid: int = 0,
) -> bool:
    """Fail closed when a transaction lock is held, missing, or untrusted."""

    descriptors: list[int] = []
    try:
        for path in lock_paths:
            try:
                metadata = path.lstat()
            except OSError:
                return True
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != expected_uid
                or metadata.st_nlink != 1
                or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                return True

            try:
                descriptor = os.open(
                    path,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                )
            except OSError:
                return True
            descriptors.append(descriptor)
            opened = os.fstat(descriptor)
            if opened.st_dev != metadata.st_dev or opened.st_ino != metadata.st_ino:
                return True
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
        return False
    finally:
        for descriptor in descriptors:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)
