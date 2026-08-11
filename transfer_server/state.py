# pyright: strict

import random
from dataclasses import dataclass, field

from .config import SOCKET_PORT_MAX, SOCKET_PORT_MIN


@dataclass(slots=True)
class TransferState:
    filename: str
    total_chunks: int
    expected_sha256: str
    chunks: dict[int, str] = field(default_factory=dict[int, str])
    packet_attempts: int = 0
    dropped_attempts: int = 0
    duplicate_packets: int = 0
    simulated_wire_bytes: int = 0


@dataclass(slots=True)
class SocketState:
    local_port: int
    remote: tuple[str, int] | None = None
    transfer: TransferState | None = None


@dataclass(slots=True)
class SessionState:
    ip: str
    sockets: dict[int, SocketState] = field(default_factory=dict[int, SocketState])
    active_socket_id: int | None = None
    next_socket_id: int = 0


def _random_free_port(
    used_ports: set[int],
    first: int,
    last: int,
    exhausted_message: str,
) -> int:
    if len(used_ports) >= last - first + 1:
        raise RuntimeError(exhausted_message)

    while True:
        port = random.randint(first, last)

        if port not in used_ports:
            return port


def new_socket(session: SessionState) -> tuple[int, SocketState]:
    used_ports = {sock.local_port for sock in session.sockets.values()}
    local_port = _random_free_port(
        used_ports,
        SOCKET_PORT_MIN,
        SOCKET_PORT_MAX,
        "No UDP ports available.",
    )
    socket_id = session.next_socket_id
    sock = SocketState(local_port)

    session.next_socket_id += 1
    session.sockets[socket_id] = sock
    session.active_socket_id = socket_id

    return socket_id, sock


def active_socket(session: SessionState) -> SocketState | None:
    if session.active_socket_id is None:
        return None

    return session.sockets.get(session.active_socket_id)


def select_socket(session: SessionState, socket_id: int) -> bool:
    if socket_id not in session.sockets:
        return False

    session.active_socket_id = socket_id
    return True


def close_socket(session: SessionState, socket_id: int) -> SocketState | None:
    sock = session.sockets.pop(socket_id, None)

    if sock is None:
        return None

    if session.active_socket_id == socket_id:
        session.active_socket_id = next(iter(session.sockets), None)

    return sock


def connect_socket(sock: SocketState, remote_ip: str, remote_port: int) -> None:
    sock.remote = (remote_ip, remote_port)


def disconnect_socket(sock: SocketState) -> None:
    sock.remote = None
    sock.transfer = None


def remote_key(sock: SocketState) -> str | None:
    if sock.remote is None:
        return None

    remote_ip, remote_port = sock.remote
    return f"{remote_ip}:{remote_port}"
