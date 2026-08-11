# pyright: strict

from fastapi import WebSocket

from .config import COMPUTER_IP, online_hosts
from .network import trace_packet
from .state import (
    SessionState,
    active_socket,
    close_socket,
    connect_socket,
    disconnect_socket,
    new_socket,
    remote_key,
    select_socket,
)


async def clientside_command(
    command: str,
    args: list[str],
    ws: WebSocket,
    session: SessionState,
) -> bool:
    if command == "SOCKET":
        if not args:
            await ws.send_text("Usage: SOCKET NEW | LIST | USE <id> | CLOSE <id>")
            return True

        action = args[0].upper()

        if action == "NEW":
            socket_id, sock = new_socket(session)
            await ws.send_text(
                f"SOCKET {socket_id} "
                f"BOUND {session.ip}:{sock.local_port}"
            )

        elif action == "LIST":
            if not session.sockets:
                await ws.send_text("No sockets.")
                return True

            for socket_id, sock in session.sockets.items():
                marker = "*" if socket_id == session.active_socket_id else "-"
                remote = remote_key(sock) or "unconnected"
                await ws.send_text(
                    f"{marker} SOCKET {socket_id} "
                    f"{session.ip}:{sock.local_port} "
                    f"-> {remote}"
                )

        elif action == "USE":
            if len(args) != 2:
                await ws.send_text("Usage: SOCKET USE <id>")
                return True

            socket_id = int(args[1])

            if not select_socket(session, socket_id):
                await ws.send_text("No such socket.")
                return True

            await ws.send_text(f"USING SOCKET {socket_id}")

        elif action == "CLOSE":
            if len(args) != 2:
                await ws.send_text("Usage: SOCKET CLOSE <id>")
                return True

            socket_id = int(args[1])

            if close_socket(session, socket_id) is None:
                await ws.send_text("No such socket.")
                return True

            await ws.send_text(f"SOCKET {socket_id} CLOSED")

        else:
            await ws.send_text("Usage: SOCKET NEW | LIST | USE <id> | CLOSE <id>")

        return True

    if command == "CONNECT":
        if len(args) != 2:
            await ws.send_text("Usage: CONNECT <ip> <port>")
            return True

        sock = active_socket(session)

        if sock is None:
            await ws.send_text("Create a socket first.")
            return True

        if sock.remote is not None:
            await ws.send_text("Please close your active connection.")
            return True

        remote_ip = args[0]
        remote_port = int(args[1])
        key = f"{remote_ip}:{remote_port}"

        if key not in online_hosts:
            await ws.send_text("Host offline (or incorrect port).")
            return True

        connect_socket(sock, remote_ip, remote_port)
        await ws.send_text(f"CONNECTED: {online_hosts[key]}")
        return True

    if command == "CLOSE":
        sock = active_socket(session)

        if sock is None:
            await ws.send_text("No active socket.")
            return True

        if sock.remote is None:
            await ws.send_text("Your active socket has no connection.")
            return True

        disconnect_socket(sock)
        await ws.send_text("Connection closed.")
        return True

    if command == "TRACE":
        if len(args) != 2:
            await ws.send_text("Usage: TRACE <destination-ip> <ttl>")
            return True

        await ws.send_text(trace_packet(COMPUTER_IP, args[0], int(args[1])))
        return True

    if command == "REMOTE_TRACE":
        if len(args) != 1:
            await ws.send_text("Usage: REMOTE_TRACE <ttl>")
            return True

        sock = active_socket(session)

        if sock is None:
            await ws.send_text("No active socket.")
            return True

        if sock.remote is None:
            await ws.send_text("You must connect to a host first.")
            return True

        await ws.send_text(trace_packet(sock.remote[0], COMPUTER_IP, int(args[0])))
        return True

    return False
