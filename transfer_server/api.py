# pyright: strict

import sys

from .config import (
    COMPUTER_IP,
    FRAME_LOSS_RATE,
    ROUTER_COUNT,
    VIRTUAL_FRAME_BYTES,
)


async def health() -> dict[str, object]:
    return {
        "version": 5.0,
        "secret_count": 4,
        "python_version": sys.version,
        "virtual_frame_bytes": VIRTUAL_FRAME_BYTES,
        "frame_loss_rate": FRAME_LOSS_RATE,
        "router_count": ROUTER_COUNT,
        "own_ip": COMPUTER_IP,
    }


async def sixseven() -> str:
    return "<h1> sixseven </h1>"
