# pyright: strict

import asyncio
import random
import time

from fastapi import WebSocket

from .config import datagram_delay, datagram_dropped, online_hosts
from .state import SessionState, active_socket, remote_key


clock_offset = time.time() - time.monotonic() + random.uniform(-60, 60)


def server_time() -> float:
    return clock_offset + time.monotonic()


async def time_server_command(
    command: str,
    args: list[str],
    ws: WebSocket,
    session: SessionState,
) -> bool:
    if command == "TIME":
        if len(args) != 0:
            await ws.send_text("Usage: TIME")
            return True

        sock = active_socket(session)

        if sock is None:
            await ws.send_text("No active socket.")
            return True

        destination = remote_key(sock)

        if destination is None:
            await ws.send_text("You have no connection.")
            return True

        if online_hosts[destination] != "SNTP Time Server":
            await ws.send_text("TIME is only accepted by SNTP Time Server.")
            return True

        if await datagram_dropped(b"TIME"):
            return True

        t2 = server_time()
        await asyncio.sleep(0)
        t3 = server_time()
        await ws.send_text(f"{t2} {t3}")
        return True

    if command == "VERIFY":
        if len(args) != 1:
            await ws.send_text("Usage: VERIFY <seconds>")
            return True

        sock = active_socket(session)

        if sock is None:
            await ws.send_text("No active socket.")
            return True

        destination = remote_key(sock)

        if destination is None:
            await ws.send_text("You have no connection.")
            return True

        if online_hosts[destination] != "SNTP Time Server":
            await ws.send_text("VERIFY is only accepted by SNTP Time Server.")
            return True

        submitted_time = float(args[0])
        await datagram_delay(f"VERIFY {args[0]}".encode("utf-8"))
        error = server_time() - submitted_time
        await ws.send_text(f"OFF BY {error:.3f}")
        return True

    return False
