# pyright: strict

import asyncio
import hashlib
import os
import random
import sys
from dataclasses import dataclass, field
from collections import deque
from typing import Final

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI()

NY_TIMES_ADDRESS = "10.0.0.7:21"

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

NAT_PUBLIC_IP: Final[str] = "203.0.113.10"
NAT_PORT_MIN: Final[int] = 40000
NAT_PORT_MAX: Final[int] = 63999


online_hosts: dict[str, str] = {
    "10.0.0.1:20": "The Bank",
    "10.0.0.7:21": "NY Times",
    "10.0.0.8:23": "white house",
    "10.0.0.9:24": "i knew it",
}

COMPUTER_IP: Final[str] = "10.0.2.100"
LOCAL_ROUTER: Final[str] = "10.0.1.1"
ROUTER_COUNT: Final[int] = 6

# Leave NETWORK_SEED unset for a new topology after each restart.
# Set it in Render when you need repeatable behavior.
random_source = random.Random(os.environ.get("NETWORK_SEED"))


def get_host_ips() -> list[str]:
    return sorted({address.rsplit(":", maxsplit=1)[0] for address in online_hosts})


def first_router_hop(
    start: str,
    destination: str,
    adjacency: dict[str, list[str]],
) -> str | None:
    if start == destination:
        return destination

    queue: deque[tuple[str, str]] = deque()
    visited: set[str] = {start}

    for neighbor in adjacency[start]:
        queue.append((neighbor, neighbor))
        visited.add(neighbor)

    while queue:
        current, first_hop = queue.popleft()

        if current == destination:
            return first_hop

        for neighbor in adjacency[current]:
            if neighbor in visited:
                continue

            visited.add(neighbor)
            queue.append((neighbor, first_hop))

    return None


def build_network(
    host_ips: list[str],
    router_count: int,
) -> tuple[
    dict[str, list[str]],
    dict[str, str],
    dict[str, dict[str, str]],
]:
    routers = [f"10.0.1.{number}" for number in range(1, router_count + 1)]

    adjacency: dict[str, list[str]] = {router: [] for router in routers}

    # Each new router attaches to one existing router.
    # This creates a connected tree with no routing loops.
    for index in range(1, len(routers)):
        router = routers[index]
        parent = random_source.choice(routers[:index])

        adjacency[parent].append(router)
        adjacency[router].append(parent)

    # Avoid attaching hosts directly to the student's local router
    # so traceroute normally contains multiple hops.
    possible_host_routers = routers[1:] if len(routers) > 1 else routers

    host_attachment: dict[str, str] = {}

    for host_ip in host_ips:
        host_attachment[host_ip] = random_source.choice(possible_host_routers)

    routing_tables: dict[str, dict[str, str]] = {router: {} for router in routers}

    for router in routers:
        for host_ip, attached_router in host_attachment.items():
            if router == attached_router:
                # The host is directly connected to this router.
                routing_tables[router][host_ip] = host_ip
                continue

            next_hop = first_router_hop(
                router,
                attached_router,
                adjacency,
            )

            if next_hop is None:
                raise RuntimeError(f"No route from {router} to {attached_router}")

            routing_tables[router][host_ip] = next_hop

    return adjacency, host_attachment, routing_tables


def build_reverse_routes(
    router_count: int,
) -> tuple[
    dict[str, list[str]],
    dict[str, str],
]:
    routers = [f"10.0.1.{number}" for number in range(1, router_count + 1)]

    adjacency: dict[str, list[str]] = {router: [] for router in routers}

    # Generate a second independent router tree.
    for index in range(1, len(routers)):
        router = routers[index]
        parent = random_source.choice(routers[:index])

        adjacency[parent].append(router)
        adjacency[router].append(parent)

    next_hops_to_computer: dict[str, str] = {}

    for router in routers:
        if router == LOCAL_ROUTER:
            continue

        next_hop = first_router_hop(
            router,
            LOCAL_ROUTER,
            adjacency,
        )

        if next_hop is None:
            raise RuntimeError(f"No reverse route from {router}")

        next_hops_to_computer[router] = next_hop

    return adjacency, next_hops_to_computer


router_links, host_attachment, routing_tables = build_network(
    get_host_ips(),
    ROUTER_COUNT,
)
reverse_router_links, reverse_next_hops = build_reverse_routes(ROUTER_COUNT)
computer_routing_table = {
    "default": LOCAL_ROUTER,
}


# Build a general ICMP routing table once at startup.
#
# This performs any required path searching now. TRACE itself only
# performs next-hop table lookups.
#
# No randomness is used here, so NETWORK_SEED produces exactly the
# same forward and reverse topology as before.
def build_icmp_routing_tables() -> dict[str, dict[str, str]]:
    routers = set(router_links)
    hosts = set(host_attachment)

    devices: set[str] = {
        COMPUTER_IP,
    }

    devices.update(routers)
    devices.update(hosts)

    tables: dict[str, dict[str, str]] = {device: {} for device in devices}

    for source in devices:
        for destination in devices:
            if source == destination:
                continue

            # The computer has one default gateway.
            if source == COMPUTER_IP:
                tables[source][destination] = LOCAL_ROUTER
                continue

            # A remote host sends all non-local traffic through
            # the router to which that host is attached.
            if source in hosts:
                tables[source][destination] = host_attachment[source]
                continue

            # From here onward, source is a router.

            # Traffic returning to the computer follows the
            # independently generated reverse topology.
            if destination == COMPUTER_IP:
                if source == LOCAL_ROUTER:
                    tables[source][destination] = COMPUTER_IP
                else:
                    next_hop = reverse_next_hops.get(source)

                    if next_hop is None:
                        raise RuntimeError(f"No route from {source} to {COMPUTER_IP}")

                    tables[source][destination] = next_hop

                continue

            # Forward routes to hosts already exist in the
            # tables generated by build_network().
            if destination in hosts:
                next_hop = routing_tables[source].get(destination)

                if next_hop is None:
                    raise RuntimeError(f"No route from {source} to {destination}")

                tables[source][destination] = next_hop
                continue

            # The destination is another router.
            #
            # Calculate this once during startup rather than
            # searching during TRACE.
            next_hop = first_router_hop(
                source,
                destination,
                router_links,
            )

            if next_hop is None:
                raise RuntimeError(f"No route from {source} to {destination}")

            tables[source][destination] = next_hop

    return tables


icmp_routing_tables = build_icmp_routing_tables()


def trace_packet(
    source_ip: str,
    destination_ip: str,
    ttl: int,
) -> str:
    if ttl < 1:
        return "TTL must be at least 1."

    if source_ip not in icmp_routing_tables:
        return f"UNKNOWN SOURCE {source_ip}"

    if destination_ip not in icmp_routing_tables:
        return f"DESTINATION UNREACHABLE {destination_ip}"

    current = source_ip
    remaining_ttl = ttl
    visited: set[str] = set()

    while True:
        if current == destination_ip:
            return f"REACHED {destination_ip}"

        if current in visited:
            return f"ROUTING LOOP AT {current}"

        visited.add(current)

        next_hop = icmp_routing_tables[current].get(destination_ip)

        if next_hop is None:
            return f"NO ROUTE FROM {current}"

        current = next_hop

        # If this device is the destination, it receives the packet.
        # It does not forward it and therefore does not decrement TTL.
        if current == destination_ip:
            return f"REACHED {destination_ip}"

        # Routers decrement TTL while forwarding.
        if current in router_links:
            remaining_ttl -= 1

            if remaining_ttl == 0:
                return f"TTL EXPIRED AT {current}"


@dataclass
class Transfer:
    filename: str
    total_chunks: int
    expected_sha256: str
    chunks: dict[int, str] = field(default_factory=dict[int, str])

    packet_attempts: int = 0
    dropped_attempts: int = 0
    duplicate_packets: int = 0
    simulated_wire_bytes: int = 0


@dataclass
class VirtualUDPSocket:
    local_port: int
    remote_ip: str | None = None
    remote_port: int | None = None
    transfer: Transfer | None = None

    def is_connected(self) -> bool:
        return self.remote_ip is not None and self.remote_port is not None

    def connect(
        self,
        remote_ip: str,
        remote_port: int,
    ) -> None:
        self.remote_ip = remote_ip
        self.remote_port = remote_port

    def disconnect(self) -> None:
        self.remote_ip = None
        self.remote_port = None
        self.transfer = None

    def remote_key(self) -> str | None:
        if self.remote_ip is None or self.remote_port is None:
            return None

        return f"{self.remote_ip}:{self.remote_port}"


class VirtualComputer:
    def __init__(
        self,
        ip: str,
    ) -> None:
        self.ip = ip
        self.sockets: dict[int, VirtualUDPSocket] = {}
        self.active_socket_id: int | None = None
        self.next_socket_id = 0

    def new_socket(
        self,
    ) -> tuple[int, VirtualUDPSocket]:
        used_ports = {sock.local_port for sock in self.sockets.values()}

        available_ports = [
            port
            for port in range(
                SOCKET_PORT_MIN,
                SOCKET_PORT_MAX + 1,
            )
            if port not in used_ports
        ]

        if not available_ports:
            raise RuntimeError("No UDP ports available.")

        socket_id = self.next_socket_id
        self.next_socket_id += 1

        # Do not use random_source here.
        # It is reserved for reproducible topology generation.
        sock = VirtualUDPSocket(local_port=random.choice(available_ports))

        self.sockets[socket_id] = sock
        self.active_socket_id = socket_id

        return socket_id, sock

    def get_active_socket(
        self,
    ) -> VirtualUDPSocket | None:
        if self.active_socket_id is None:
            return None

        return self.sockets.get(self.active_socket_id)

    def use_socket(
        self,
        socket_id: int,
    ) -> bool:
        if socket_id not in self.sockets:
            return False

        self.active_socket_id = socket_id
        return True

    def close_socket(
        self,
        socket_id: int,
    ) -> VirtualUDPSocket | None:
        sock = self.sockets.pop(
            socket_id,
            None,
        )

        if sock is None:
            return None

        if self.active_socket_id == socket_id:
            if self.sockets:
                self.active_socket_id = next(iter(self.sockets))
            else:
                self.active_socket_id = None

        return sock


@dataclass
class NATEntry:
    private_ip: str
    private_port: int
    public_port: int


class NATRouter:
    def __init__(
        self,
        public_ip: str,
    ) -> None:
        self.public_ip = public_ip

        self.entries: dict[
            tuple[str, int],
            NATEntry,
        ] = {}

    def get_mapping(
        self,
        private_ip: str,
        private_port: int,
    ) -> NATEntry | None:
        return self.entries.get(
            (
                private_ip,
                private_port,
            )
        )

    def translate_outbound(
        self,
        private_ip: str,
        private_port: int,
    ) -> NATEntry:
        key = (
            private_ip,
            private_port,
        )

        existing = self.entries.get(key)

        if existing is not None:
            return existing

        used_public_ports = {entry.public_port for entry in self.entries.values()}

        available_ports = [
            port
            for port in range(
                NAT_PORT_MIN,
                NAT_PORT_MAX + 1,
            )
            if port not in used_public_ports
        ]

        if not available_ports:
            raise RuntimeError("NAT has no available ports.")

        entry = NATEntry(
            private_ip=private_ip,
            private_port=private_port,
            public_port=random.choice(available_ports),
        )

        self.entries[key] = entry

        return entry

    def translate_inbound(
        self,
        public_port: int,
    ) -> NATEntry | None:
        for entry in self.entries.values():
            if entry.public_port == public_port:
                return entry

        return None

    def remove_mapping(
        self,
        private_ip: str,
        private_port: int,
    ) -> None:
        self.entries.pop(
            (
                private_ip,
                private_port,
            ),
            None,
        )


@app.get("/")
async def health() -> dict[str, object]:
    return {
        "version": 5.0,
        "secret_count": 4,
        "python_version": sys.version,
        "virtual_frame_bytes": VIRTUAL_FRAME_BYTES,
        "frame_loss_rate": FRAME_LOSS_RATE,
        "router_count": ROUTER_COUNT,
        "own_ip": COMPUTER_IP,
        "nat_public_ip": NAT_PUBLIC_IP,
    }


@app.get("/6767420", response_class=HTMLResponse)
async def sixseven() -> str:
    return "<h1> sixseven </h1>"


async def send_help(ws: WebSocket) -> None:
    for line in (
        "Accepted commands:",
        "SOCKET NEW",
        "SOCKET LIST",
        "SOCKET USE <id>",
        "SOCKET CLOSE <id>",
        "NAT",
        "CONNECT <ip> <port>",
        "CLOSE",
        "ROB <amount>",
        "START <filename> <total_chunks> <sha256>",
        "DATA <sequence_number> <text>",
        "STATUS",
        "DONE",
        "TRACE <ip> <ttl>",
        "REMOTE_TRACE <ttl>",
        "HELP",
    ):
        await ws.send_text(line)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()

    computer = VirtualComputer(COMPUTER_IP)

    nat_router = NATRouter(NAT_PUBLIC_IP)

    # Preserve compatibility with clients that immediately
    # issue CONNECT without first creating a socket.
    computer.new_socket()

    try:
        while True:
            message = await ws.receive_text()

            if not message:
                await ws.send_text("Empty command.")
                continue

            command = message.split(maxsplit=1)[0].upper()
            _, *args = message.split()

            if command == "SOCKET":
                if len(args) < 1:
                    await ws.send_text(
                        "Usage: SOCKET NEW | LIST | USE <id> | CLOSE <id>"
                    )
                    continue

                action = args[0].upper()

                if action == "NEW":
                    socket_id, sock = computer.new_socket()

                    await ws.send_text(
                        f"SOCKET {socket_id} " f"BOUND {computer.ip}:{sock.local_port}"
                    )

                elif action == "LIST":
                    if not computer.sockets:
                        await ws.send_text("No sockets.")
                        continue

                    lines: list[str] = []

                    for socket_id, sock in computer.sockets.items():
                        if socket_id == computer.active_socket_id:
                            marker = "*"
                        else:
                            marker = "-"

                        remote_key = sock.remote_key()

                        if remote_key is None:
                            remote = "unconnected"
                        else:
                            remote = remote_key

                        nat_entry = nat_router.get_mapping(
                            computer.ip,
                            sock.local_port,
                        )

                        if nat_entry is None:
                            nat_endpoint = "none"
                        else:
                            nat_endpoint = (
                                f"{nat_router.public_ip}:" f"{nat_entry.public_port}"
                            )

                        lines.append(
                            f"{marker} SOCKET {socket_id} "
                            f"{computer.ip}:{sock.local_port} "
                            f"NAT {nat_endpoint} "
                            f"-> {remote}"
                        )

                    for line in lines:
                        await ws.send_text(line)

                elif action == "USE":
                    if len(args) != 2:
                        await ws.send_text("Usage: SOCKET USE <id>")
                        continue

                    try:
                        socket_id = int(args[1])
                    except ValueError:
                        await ws.send_text("Socket ID must be an integer.")
                        continue

                    if not computer.use_socket(socket_id):
                        await ws.send_text("No such socket.")
                        continue

                    await ws.send_text(f"USING SOCKET {socket_id}")

                elif action == "CLOSE":
                    if len(args) != 2:
                        await ws.send_text("Usage: SOCKET CLOSE <id>")
                        continue

                    try:
                        socket_id = int(args[1])
                    except ValueError:
                        await ws.send_text("Socket ID must be an integer.")
                        continue

                    sock = computer.close_socket(socket_id)

                    if sock is None:
                        await ws.send_text("No such socket.")
                        continue

                    nat_router.remove_mapping(
                        computer.ip,
                        sock.local_port,
                    )

                    await ws.send_text(f"SOCKET {socket_id} CLOSED")

                else:
                    await ws.send_text(
                        "Usage: SOCKET NEW | LIST | USE <id> | CLOSE <id>"
                    )

            elif command == "NAT":
                if not nat_router.entries:
                    await ws.send_text("NAT table empty.")
                    continue

                entries = sorted(
                    nat_router.entries.values(),
                    key=lambda entry: entry.private_port,
                )

                lines = [
                    (
                        f"{entry.private_ip}:"
                        f"{entry.private_port} "
                        f"-> "
                        f"{nat_router.public_ip}:"
                        f"{entry.public_port}"
                    )
                    for entry in entries
                ]
                for line in lines:
                    await ws.send_text(line)

            # Similar to UDP socket.connect().
            elif command == "CONNECT":
                if len(args) != 2:
                    await ws.send_text("Usage: CONNECT <ip> <port>")
                    continue

                sock = computer.get_active_socket()

                if sock is None:
                    await ws.send_text("Create a socket first.")
                    continue

                if sock.is_connected():
                    await ws.send_text("Please close your active connection.")
                    continue

                try:
                    remote_port = int(args[1])
                except ValueError:
                    await ws.send_text("Port must be an integer.")
                    continue

                remote_ip = args[0]
                key = f"{remote_ip}:{remote_port}"

                if key not in online_hosts:
                    await ws.send_text("Host offline (or incorrect port).")
                    continue

                sock.connect(
                    remote_ip,
                    remote_port,
                )

                await ws.send_text(f"CONNECTED: {online_hosts[key]}")

            # Disconnect the active UDP socket from its
            # selected destination, but keep its local port.
            elif command == "CLOSE":
                sock = computer.get_active_socket()

                if sock is None:
                    await ws.send_text("No active socket.")
                    continue

                if not sock.is_connected():
                    await ws.send_text("Your active socket has no connection.")
                    continue

                sock.disconnect()

                await ws.send_text("Connection closed.")

            elif command == "ROB":
                if len(args) != 1:
                    await ws.send_text("Usage: ROB <amount>")
                    continue

                sock = computer.get_active_socket()

                if sock is None:
                    await ws.send_text("No active socket.")
                    continue

                remote_key = sock.remote_key()

                if remote_key is None:
                    await ws.send_text("You have no connection.")
                    continue

                try:
                    amount = float(args[0])
                except ValueError:
                    await ws.send_text("Amount must be a number.")
                    continue

                if online_hosts[remote_key] == "The Bank":
                    await ws.send_text(f"Stole ${amount:.2f} from {remote_key}")
                    await ws.send_text("The police caught you!")
                    await ws.send_text("You were removed.")

                    sock.disconnect()
                else:
                    await ws.send_text("You cannot rob your current connection!")

            elif command == "START":
                if len(args) != 3:
                    await ws.send_text(
                        "Usage: START " "<filename> <total_chunks> <sha256>"
                    )
                    continue

                sock = computer.get_active_socket()

                if sock is None:
                    await ws.send_text("No active socket.")
                    continue

                if sock.remote_key() != NY_TIMES_ADDRESS:
                    await ws.send_text("File transfers are only accepted by NY Times.")
                    continue

                if sock.transfer is not None:
                    await ws.send_text(
                        "A transfer is already active. " "Use CLOSE to cancel it."
                    )
                    continue

                filename = args[0]

                try:
                    total_chunks = int(args[1])
                except ValueError:
                    await ws.send_text("The total chunk count must be an integer.")
                    continue

                if total_chunks < 1 or total_chunks > 10_000:
                    await ws.send_text(
                        "The total chunk count must be " "between 1 and 10000."
                    )
                    continue

                expected_sha256 = args[2].lower()

                valid_sha256 = len(expected_sha256) == 64 and all(
                    character in "0123456789abcdef" for character in expected_sha256
                )

                if not valid_sha256:
                    await ws.send_text(
                        "The SHA-256 value must contain " "64 hexadecimal characters."
                    )
                    continue

                sock.transfer = Transfer(
                    filename=filename,
                    total_chunks=total_chunks,
                    expected_sha256=expected_sha256,
                )

                await ws.send_text(f"READY {filename} {total_chunks}")

            # Represents an unreliable simulated UDP datagram.
            elif command == "DATA":
                sock = computer.get_active_socket()

                if sock is None:
                    await ws.send_text("No active socket.")
                    continue

                if sock.remote_key() != NY_TIMES_ADDRESS:
                    await ws.send_text("You must connect to NY Times first.")
                    continue

                transfer = sock.transfer

                if transfer is None:
                    await ws.send_text("No transfer active. Use START first.")
                    continue

                _, separator, remainder = message.partition(" ")

                if not separator:
                    await ws.send_text("Usage: DATA <sequence_number> <text>")
                    continue

                sequence_text, separator, payload = remainder.partition(" ")

                if not separator:
                    await ws.send_text("Usage: DATA <sequence_number> <text>")
                    continue

                try:
                    sequence = int(sequence_text)
                except ValueError:
                    await ws.send_text("The sequence number must be an integer.")
                    continue

                if sequence < 0 or sequence >= transfer.total_chunks:
                    await ws.send_text(
                        f"Sequence must be between 0 and "
                        f"{transfer.total_chunks - 1}."
                    )
                    continue

                # UDP connect() itself sends nothing.
                # The first actual datagram creates the NAT mapping.
                nat_entry = nat_router.translate_outbound(
                    computer.ip,
                    sock.local_port,
                )

                payload_bytes = payload.encode("utf-8")

                frame_count = max(
                    1,
                    (len(payload_bytes) + VIRTUAL_FRAME_BYTES - 1)
                    // VIRTUAL_FRAME_BYTES,
                )

                transfer.packet_attempts += 1
                transfer.simulated_wire_bytes += len(payload_bytes) + DATA_HEADER_BYTES

                transmission_delay = (
                    BASE_DELAY_SECONDS
                    + len(payload_bytes) / LINK_BYTES_PER_SECOND
                    + random.uniform(
                        0.0,
                        JITTER_SECONDS,
                    )
                )

                await asyncio.sleep(transmission_delay)

                frame_was_lost = any(
                    random.random() < FRAME_LOSS_RATE for _ in range(frame_count)
                )

                if frame_was_lost:
                    transfer.dropped_attempts += 1

                    # No response. The client must time out
                    # and retransmit the complete datagram.
                    continue

                if sequence in transfer.chunks:
                    transfer.duplicate_packets += 1
                else:
                    transfer.chunks[sequence] = payload

                transfer.simulated_wire_bytes += ACK_BYTES

                # The reply is addressed to the public NAT
                # endpoint and translated back to the socket.
                inbound_entry = nat_router.translate_inbound(nat_entry.public_port)

                if inbound_entry is None:
                    continue

                await ws.send_text(f"ACK {sequence}")

            elif command == "STATUS":
                sock = computer.get_active_socket()

                if sock is None:
                    await ws.send_text("No active socket.")
                    continue

                transfer = sock.transfer

                if transfer is None:
                    await ws.send_text("No transfer active.")
                    continue

                missing = [
                    sequence
                    for sequence in range(transfer.total_chunks)
                    if sequence not in transfer.chunks
                ]

                if missing:
                    missing_text = ",".join(str(sequence) for sequence in missing[:25])

                    if len(missing) > 25:
                        missing_text += ",..."

                    await ws.send_text(
                        f"RECEIVED "
                        f"{len(transfer.chunks)}/"
                        f"{transfer.total_chunks} "
                        f"MISSING {missing_text}"
                    )
                else:
                    await ws.send_text(
                        f"RECEIVED "
                        f"{len(transfer.chunks)}/"
                        f"{transfer.total_chunks} "
                        f"MISSING none"
                    )

            elif command == "DONE":
                sock = computer.get_active_socket()

                if sock is None:
                    await ws.send_text("No active socket.")
                    continue

                transfer = sock.transfer

                if transfer is None:
                    await ws.send_text("No transfer active.")
                    continue

                missing = [
                    sequence
                    for sequence in range(transfer.total_chunks)
                    if sequence not in transfer.chunks
                ]

                if missing:
                    missing_text = ",".join(str(sequence) for sequence in missing[:25])

                    if len(missing) > 25:
                        missing_text += ",..."

                    await ws.send_text(
                        f"TRANSFER INCOMPLETE " f"MISSING {missing_text}"
                    )
                    continue

                reconstructed = "".join(
                    transfer.chunks[sequence]
                    for sequence in range(transfer.total_chunks)
                )

                reconstructed_bytes = reconstructed.encode("utf-8")

                actual_sha256 = hashlib.sha256(reconstructed_bytes).hexdigest()

                if actual_sha256 != transfer.expected_sha256:
                    await ws.send_text(f"CHECKSUM FAILED {actual_sha256}")
                    continue

                await ws.send_text(
                    f"TRANSFER COMPLETE "
                    f"{transfer.filename} "
                    f"{len(reconstructed_bytes)} bytes "
                    f"{transfer.packet_attempts} attempts "
                    f"{transfer.dropped_attempts} dropped "
                    f"{transfer.duplicate_packets} duplicates "
                    f"{transfer.simulated_wire_bytes} "
                    f"simulated-wire-bytes"
                )

                sock.transfer = None

            elif command == "TRACE":
                if len(args) != 2:
                    await ws.send_text("Usage: TRACE <destination-ip> <ttl>")
                    continue

                destination_ip = args[0]

                try:
                    ttl = int(args[1])
                except ValueError:
                    await ws.send_text("TTL must be an integer.")
                    continue

                await ws.send_text(
                    trace_packet(
                        COMPUTER_IP,
                        destination_ip,
                        ttl,
                    )
                )

            elif command == "REMOTE_TRACE":
                if len(args) != 1:
                    await ws.send_text("Usage: REMOTE_TRACE <ttl>")
                    continue

                sock = computer.get_active_socket()

                if sock is None:
                    await ws.send_text("No active socket.")
                    continue

                if sock.remote_ip is None:
                    await ws.send_text("You must connect to a host first.")
                    continue

                try:
                    ttl = int(args[0])
                except ValueError:
                    await ws.send_text("TTL must be an integer.")
                    continue

                await ws.send_text(
                    trace_packet(
                        sock.remote_ip,
                        COMPUTER_IP,
                        ttl,
                    )
                )

            elif command == "HELP":
                await send_help(ws)

            else:
                await send_help(ws)

    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn

    port = int(
        os.environ.get(
            "PORT",
            "9000",
        )
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )
