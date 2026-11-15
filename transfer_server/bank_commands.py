# pyright: strict

from .config import online_hosts
from .replies import TextSender
from .state import (
    SessionState,
    active_socket,
    remote_key,
)


async def bank_command(
    command: str,
    args: list[str],
    ws: TextSender,
    session: SessionState,
) -> bool:
    if command != "ROB":
        return False

    if len(args) != 1:
        await ws.send_text("Usage: ROB <amount>")
        return True

    sock = active_socket(session)

    if sock is None:
        await ws.send_text("No active socket.")
        return True

    destination = remote_key(sock)

    if destination is None:
        await ws.send_text("You have no connection.")
        return True

    amount = float(args[0])

    if online_hosts[destination] != "The Bank":
        await ws.send_text("You cannot rob your current connection!")
        return True

    await ws.send_text(f"Stole ${amount:.2f} from {destination}")
    await ws.send_text("The police caught you!")
    await ws.send_text("You were removed.")
    return True
