#!/usr/bin/env python3
"""Decode the observed VAG PQ 0x5B5 four-channel rear-PDC payload.

This is a reverse-engineering probe, not a maintained runtime decoder.  The
controlled 2012 Superb capture shows bytes 2..6 behaving as four little-endian
10-bit proximity channels packed into 40 bits.  The channels rise as the car
approaches an obstacle, but physical left/right ordering and conversion to
centimetres are intentionally left unclaimed until independently calibrated.

Examples:
    candump -L can0,5B5:7FF | python3 tools/pq_pdc_probe.py
    python3 tools/pq_pdc_probe.py capture.log
    python3 tools/pq_pdc_probe.py --candidate-labels capture.log
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, TextIO

CAN_ID = 0x5B5
_CANDUMP_RE = re.compile(
    r"^\s*(?:\((?P<timestamp>[^)]+)\)\s+)?(?P<interface>\S+)\s+"
    r"(?P<can_id>[0-9A-Fa-f]+)#(?P<data>[0-9A-Fa-f]*)\s*$"
)


@dataclass(frozen=True)
class PdcSample:
    timestamp: str | None
    interface: str
    channels: tuple[int, int, int, int]
    payload: bytes


def decode_5b5_payload(payload: bytes) -> tuple[int, int, int, int]:
    """Return the four observed 10-bit proximity channels from one 0x5B5 frame."""

    if len(payload) < 7:
        raise ValueError("0x5B5 payload must contain at least 7 bytes")
    packed = int.from_bytes(payload[2:7], byteorder="little", signed=False)
    return tuple((packed >> (10 * index)) & 0x03FF for index in range(4))  # type: ignore[return-value]


def parse_candump_line(line: str) -> PdcSample | None:
    """Parse one candump/candump -L line, ignoring frames other than 0x5B5."""

    match = _CANDUMP_RE.match(line)
    if not match or int(match.group("can_id"), 16) != CAN_ID:
        return None
    hex_data = match.group("data")
    if len(hex_data) % 2:
        return None
    try:
        payload = bytes.fromhex(hex_data)
        channels = decode_5b5_payload(payload)
    except ValueError:
        return None
    return PdcSample(
        timestamp=match.group("timestamp"),
        interface=match.group("interface"),
        channels=channels,
        payload=payload,
    )


def iter_samples(lines: Iterable[str]) -> Iterator[PdcSample]:
    for line in lines:
        sample = parse_candump_line(line)
        if sample is not None:
            yield sample


def _bar(value: int, width: int = 16) -> str:
    # Observed values are currently within 0..127.  Keep the display useful if a
    # future capture exercises the full 10-bit field without treating 127 as a
    # protocol maximum.
    display_max = max(127, value)
    filled = min(width, round((value / display_max) * width))
    return "#" * filled + "." * (width - filled)


def format_sample(sample: PdcSample, candidate_labels: bool = False) -> str:
    if candidate_labels:
        labels = ("rear_outer_left?", "rear_inner_left?", "rear_inner_right?", "rear_outer_right?")
    else:
        labels = ("ch0", "ch1", "ch2", "ch3")
    prefix = f"({sample.timestamp}) " if sample.timestamp else ""
    fields = "  ".join(
        f"{label}={value:4d} [{_bar(value)}]"
        for label, value in zip(labels, sample.channels)
    )
    return f"{prefix}{fields}"


def _open_input(path: str | None) -> tuple[TextIO, bool]:
    if path in (None, "-"):
        return sys.stdin, False
    return Path(path).open("r", encoding="utf-8", errors="replace"), True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Decode observed VAG PQ 0x5B5 PDC proximity channels from candump text. "
            "Output is diagnostic only; channel ordering and centimetre scaling are not yet authoritative."
        )
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="candump text file; omit or use '-' to read stdin",
    )
    parser.add_argument(
        "--candidate-labels",
        action="store_true",
        help="show the provisional rear left-to-right sensor labels from the Superb square-on test",
    )
    parser.add_argument(
        "--changes-only",
        action="store_true",
        help="print only when one or more decoded channels changes",
    )
    args = parser.parse_args(argv)

    stream, should_close = _open_input(args.path)
    previous: tuple[int, int, int, int] | None = None
    seen = 0
    try:
        for sample in iter_samples(stream):
            if args.changes_only and sample.channels == previous:
                continue
            print(format_sample(sample, candidate_labels=args.candidate_labels), flush=True)
            previous = sample.channels
            seen += 1
    finally:
        if should_close:
            stream.close()

    if seen == 0 and args.path not in (None, "-"):
        print("no decodable 0x5B5 frames found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
