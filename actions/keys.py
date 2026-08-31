"""Virtual keyboard input actions via uinput.

Provides synthetic media key injection with thread-safe access.
"""

import threading
import logging
from typing import Optional

from evdev import UInput, ecodes as e

logger = logging.getLogger("canbusd.actions.keys")

# -------------------------------------------------
# Singleton virtual keyboard
# -------------------------------------------------

_caps = {
    e.EV_KEY: [
        e.KEY_PLAYPAUSE, e.KEY_NEXTSONG, e.KEY_PREVIOUSSONG, e.KEY_STOPCD,
        e.KEY_MUTE, e.KEY_VOLUMEUP, e.KEY_VOLUMEDOWN, e.KEY_LEFT, e.KEY_RIGHT,
        e.KEY_UP, e.KEY_DOWN, e.KEY_ENTER, e.KEY_ESC
    ]
}

_ui: Optional[UInput] = None
_lock = threading.Lock()


def _get_ui() -> UInput:
    """Create or return the persistent virtual input device."""
    global _ui
    with _lock:
        if _ui is None:
            try:
                _ui = UInput(_caps, name="canbusd-input")
                logger.debug("Virtual input device created")
            except Exception as err:
                logger.error(f"Failed to create virtual input device: {err}")
                raise
        return _ui


def _press(code: int) -> None:
    """Atomically inject a key press and release."""
    try:
        ui = _get_ui()
        with _lock:
            ui.write(e.EV_KEY, code, 1)  # press
            ui.write(e.EV_KEY, code, 0)  # release
            ui.syn()
    except Exception as err:
        logger.error(f"Key press failed: {err}")


# -------------------------------------------------
# Media key actions
# -------------------------------------------------

def play_pause() -> None:
    _press(e.KEY_PLAYPAUSE)

def next_track() -> None:
    _press(e.KEY_NEXTSONG)

def prev_track() -> None:
    _press(e.KEY_PREVIOUSSONG)

def stop() -> None:
    _press(e.KEY_STOPCD)

def mute_toggle() -> None:
    _press(e.KEY_MUTE)

def volume_up() -> None:
    _press(e.KEY_VOLUMEUP)

def volume_down() -> None:
    _press(e.KEY_VOLUMEDOWN)

def arrow_left() -> None:
    _press(e.KEY_LEFT)

def arrow_right() -> None:
    _press(e.KEY_RIGHT)

def arrow_up() -> None:
    _press(e.KEY_UP)

def arrow_down() -> None:
    _press(e.KEY_DOWN)

def confirm() -> None:
    _press(e.KEY_ENTER)

def back() -> None:
    _press(e.KEY_ESC)