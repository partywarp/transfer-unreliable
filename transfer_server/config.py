# pyright: strict

import asyncio
import os
import random
from typing import Final

# The simulated network divides each DATA payload into 100-byte frames.
# Every frame independently has a 20% chance of being lost.
VIRTUAL_FRAME_BYTES = 100
FRAME_LOSS_RATE = 0.20

# These settings simulate transmission time and per-message overhead.
BASE_DELAY_SECONDS = 0.02
LINK_BYTES_PER_SECOND = 5_000.0
JITTER_SECONDS = 0.01

DATA_HEADER_BYTES = 20
ACK_BYTES = 8

SOCKET_PORT_MIN: Final[int] = 64000
SOCKET_PORT_MAX: Final[int] = 65535

online_hosts: dict[str, str] = {
    "10.0.0.6:443": "The Bank",
    "10.0.0.7:80": "NY Times",
    "10.0.0.8:443": "Secure Government Site",
    "10.0.0.9:123": "SNTP Time Server",
}

COMPUTER_IP: Final[str] = "10.0.2.100"
LOCAL_ROUTER: Final[str] = "10.0.1.1"
ROUTER_COUNT: Final[int] = 6

# Leave NETWORK_SEED unset for a new topology after each restart.
# Set it in Render when you need repeatable behavior.
random_source = random.Random(os.environ.get("NETWORK_SEED"))


async def datagram_dropped(payload: bytes) -> bool:
    frame_count = max(
        1,
        (len(payload) + VIRTUAL_FRAME_BYTES - 1) // VIRTUAL_FRAME_BYTES,
    )

    await asyncio.sleep(
        BASE_DELAY_SECONDS
        + len(payload) / LINK_BYTES_PER_SECOND
        + random.uniform(0.0, JITTER_SECONDS)
    )

    return any(random.random() < FRAME_LOSS_RATE for _ in range(frame_count))
