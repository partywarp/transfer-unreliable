# pyright: strict

from fastapi import WebSocket, WebSocketDisconnect

from .bank_commands import bank_command
from .clientside_commands import clientside_command
from .config import COMPUTER_IP
from .news_site_commands import news_site_command
from .state import SessionState, new_socket
from .time_server_commands import time_server_command


async def send_help(ws: WebSocket) -> None:
    for line in (
        "Accepted commands:",
        "SOCKET NEW",
        "SOCKET LIST",
        "SOCKET USE <id>",
        "SOCKET CLOSE <id>",
        "CONNECT <ip> <port>",
        "CLOSE",
        "ROB <amount>",
        "START <filename> <total_chunks> <sha256>",
        "DATA <sequence_number> <text>",
        "STATUS",
        "DONE",
        "TRACE <ip> <ttl>",
        "REMOTE_TRACE <ttl>",
        "TIME",
        "VERIFY <seconds>",
        "HELP",
    ):
        await ws.send_text(line)


async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()

    session = SessionState(COMPUTER_IP)

    # Preserve compatibility with clients that immediately
    # issue CONNECT without first creating a socket.
    new_socket(session)

    try:
        while True:
            message = await ws.receive_text()

            if not message:
                await ws.send_text("Empty command.")
                continue

            command, *args = message.split()
            command = command.upper()

            if await clientside_command(
                command,
                args,
                ws,
                session,
            ):
                continue

            if await news_site_command(
                command,
                args,
                message,
                ws,
                session,
            ):
                continue

            if await bank_command(
                command,
                args,
                ws,
                session,
            ):
                continue

            if await time_server_command(
                command,
                args,
                ws,
                session,
            ):
                continue

            await send_help(ws)

    except WebSocketDisconnect:
        pass
