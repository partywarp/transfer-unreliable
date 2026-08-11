# pyright: strict

import hashlib

from fastapi import WebSocket

from .config import (
    ACK_BYTES,
    DATA_HEADER_BYTES,
    datagram_dropped,
    online_hosts,
)
from .state import (
    SessionState,
    TransferState,
    active_socket,
    remote_key,
)


def _missing_chunks(transfer: TransferState) -> list[int]:
    return [
        sequence
        for sequence in range(transfer.total_chunks)
        if sequence not in transfer.chunks
    ]


def _missing_text(missing: list[int]) -> str:
    text = ",".join(str(sequence) for sequence in missing[:25])
    return f"{text},..." if len(missing) > 25 else text


async def news_site_command(
    command: str,
    args: list[str],
    message: str,
    ws: WebSocket,
    session: SessionState,
) -> bool:
    if command == "START":
        if len(args) != 3:
            await ws.send_text("Usage: START <filename> <total_chunks> <sha256>")
            return True

        sock = active_socket(session)

        if sock is None:
            await ws.send_text("No active socket.")
            return True

        destination = remote_key(sock)

        if destination is None or online_hosts[destination] != "NY Times":
            await ws.send_text("File transfers are only accepted by NY Times.")
            return True

        if sock.transfer is not None:
            await ws.send_text("A transfer is already active. Use CLOSE to cancel it.")
            return True

        filename = args[0]
        total_chunks = int(args[1])

        if total_chunks < 1 or total_chunks > 10_000:
            await ws.send_text("The total chunk count must be between 1 and 10000.")
            return True

        sock.transfer = TransferState(
            filename=filename,
            total_chunks=total_chunks,
            expected_sha256=args[2].lower(),
        )

        await ws.send_text(f"READY {filename} {total_chunks}")
        return True

    if command == "DATA":
        sock = active_socket(session)

        if sock is None:
            await ws.send_text("No active socket.")
            return True

        destination = remote_key(sock)

        if destination is None or online_hosts[destination] != "NY Times":
            await ws.send_text("You must connect to NY Times first.")
            return True

        transfer = sock.transfer

        if transfer is None:
            await ws.send_text("No transfer active. Use START first.")
            return True

        _, separator, remainder = message.partition(" ")

        if not separator:
            await ws.send_text("Usage: DATA <sequence_number> <text>")
            return True

        sequence_text, separator, payload = remainder.partition(" ")

        if not separator:
            await ws.send_text("Usage: DATA <sequence_number> <text>")
            return True

        sequence = int(sequence_text)

        if sequence < 0 or sequence >= transfer.total_chunks:
            await ws.send_text(
                f"Sequence must be between 0 and {transfer.total_chunks - 1}."
            )
            return True

        payload_bytes = payload.encode("utf-8")

        transfer.packet_attempts += 1
        transfer.simulated_wire_bytes += len(payload_bytes) + DATA_HEADER_BYTES

        if await datagram_dropped(payload_bytes):
            transfer.dropped_attempts += 1
            return True

        if sequence in transfer.chunks:
            transfer.duplicate_packets += 1
        else:
            transfer.chunks[sequence] = payload

        transfer.simulated_wire_bytes += ACK_BYTES

        await ws.send_text(f"ACK {sequence}")
        return True

    if command == "STATUS":
        sock = active_socket(session)

        if sock is None:
            await ws.send_text("No active socket.")
            return True

        transfer = sock.transfer

        if transfer is None:
            await ws.send_text("No transfer active.")
            return True

        missing = _missing_chunks(transfer)

        if missing:
            await ws.send_text(
                f"RECEIVED {len(transfer.chunks)}/{transfer.total_chunks} "
                f"MISSING {_missing_text(missing)}"
            )
        else:
            await ws.send_text(
                f"RECEIVED {len(transfer.chunks)}/{transfer.total_chunks} "
                "MISSING none"
            )

        return True

    if command == "DONE":
        sock = active_socket(session)

        if sock is None:
            await ws.send_text("No active socket.")
            return True

        transfer = sock.transfer

        if transfer is None:
            await ws.send_text("No transfer active.")
            return True

        missing = _missing_chunks(transfer)

        if missing:
            await ws.send_text(f"TRANSFER INCOMPLETE MISSING {_missing_text(missing)}")
            return True

        reconstructed = "".join(
            transfer.chunks[sequence] for sequence in range(transfer.total_chunks)
        )
        reconstructed_bytes = reconstructed.encode("utf-8")
        actual_sha256 = hashlib.sha256(reconstructed_bytes).hexdigest()

        if actual_sha256 != transfer.expected_sha256:
            await ws.send_text(f"CHECKSUM FAILED {actual_sha256}")
            return True

        await ws.send_text(
            f"TRANSFER COMPLETE "
            f"{transfer.filename} "
            f"{len(reconstructed_bytes)} bytes "
            f"{transfer.packet_attempts} attempts "
            f"{transfer.dropped_attempts} dropped "
            f"{transfer.duplicate_packets} duplicates "
            f"{transfer.simulated_wire_bytes} simulated-wire-bytes"
        )
        sock.transfer = None
        return True

    return False
