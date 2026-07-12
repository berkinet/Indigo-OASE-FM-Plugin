"""Indigo-independent state mapping for the OASE FM plugin."""

from __future__ import annotations

from dataclasses import dataclass


SWITCHED_SOCKET_TO_PROTOCOL_OUTLET = {1: 1, 2: 2, 4: 3}


@dataclass(frozen=True)
class FmState:
    switched: dict[int, bool]
    dimmer_on: bool
    dimmer_brightness: int


def dimmer_raw_to_percent(value: int) -> int:
    if value not in range(256):
        raise ValueError("dimmer value must be 0-255")
    if value == 0xFF:
        return 100
    return min(99, value // 2)


def dimmer_percent_to_raw(percent: int) -> int:
    if percent not in range(101):
        raise ValueError("brightness must be 0-100")
    if percent == 100:
        return 0xFF
    return percent * 2


def egc_percent_to_raw(percent: int) -> int:
    if percent not in range(101):
        raise ValueError("brightness must be 0-100")
    return percent * 255 // 100


def map_fm_state(state: object) -> FmState:
    """Translate protocol channel names to the physical socket numbering."""
    return FmState(
        switched={
            1: bool(state.outlet1),
            2: bool(state.outlet2),
            4: bool(state.outlet3),
        },
        dimmer_on=bool(state.outlet4),
        dimmer_brightness=dimmer_raw_to_percent(state.dimmer4),
    )
