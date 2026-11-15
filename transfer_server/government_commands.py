# pyright: strict

import base64
import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .replies import TextSender
from .state import SessionState, active_socket


class AttackMode(StrEnum):
    FORWARD = "FORWARD"
    FORGE = "FORGE"
    REPLAY = "REPLAY"


@dataclass(frozen=True, slots=True)
class SignedReceipt:
    message: dict[str, str]
    signature: str

    def wire_text(self) -> str:
        # JSON escapes control characters so every response remains one line.
        return json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=False)


def receipt_message_bytes(message: dict[str, str]) -> bytes:
    # Field names and JSON quoting preserve boundaries. Both values are strings;
    # sorted keys, compact separators, and UTF-8 define the signing format.
    return json.dumps(
        message, separators=(",", ":"), sort_keys=True, ensure_ascii=False
    ).encode("utf-8")


@dataclass(slots=True)
class MITMState:
    mode: AttackMode = AttackMode.FORWARD
    previous_receipt: SignedReceipt | None = None
    receipt_count: int = 0
    _attacker_key: Ed25519PrivateKey = field(
        default_factory=Ed25519PrivateKey.generate, repr=False
    )

    def intercept(self, receipt: SignedReceipt) -> SignedReceipt | None:
        previous = self.previous_receipt
        self.previous_receipt = receipt
        self.receipt_count += 1

        if self.mode == AttackMode.REPLAY:
            # Never substitute the current transaction when history is empty.
            return previous
        if self.mode == AttackMode.FORGE:
            # Preserve the expected fields and a real Ed25519 signature's shape,
            # but use an independent attacker key rather than the government key.
            signature = self._attacker_key.sign(receipt_message_bytes(receipt.message))
            return SignedReceipt(
                receipt.message.copy(), base64.b64encode(signature).decode("ascii")
            )
        return receipt


# Only this key creates government signatures. Instructor controls never receive
# it or a government signing operation. State lives per server process.
_signing_key = Ed25519PrivateKey.generate()
mitm_state = MITMState()


def public_key_info() -> dict[str, str]:
    return {
        "algorithm": "Ed25519",
        "encoding": "base64",
        "public_key": base64.b64encode(
            _signing_key.public_key().public_bytes_raw()
        ).decode("ascii"),
    }


async def government_command(
    command: str,
    args: list[str],
    ws: TextSender,
    session: SessionState,
) -> bool:
    if command != "PAY":
        return False
    if len(args) != 2:
        await ws.send_text("Usage: PAY <amount> <nonce>")
        return True

    sock = active_socket(session)
    if sock is None or sock.remote is None:
        await ws.send_text("You have no connection.")
        return True
    if sock.remote != ("10.0.0.8", 443):
        await ws.send_text("Tax payments are only accepted by Secure Government Site.")
        return True

    if re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,2})?", args[0]) is None:
        await ws.send_text("Amount must be positive, with at most 12 digits and 2 decimal places.")
        return True
    amount = Decimal(args[0])
    if amount <= 0:
        await ws.send_text("Amount must be positive.")
        return True
    if not args[1] or len(args[1]) > 128:
        await ws.send_text("Nonce must contain between 1 and 128 characters.")
        return True

    message = {
        "amount": format(amount, ".2f"),
        "nonce": args[1],
    }
    receipt = SignedReceipt(
        message,
        base64.b64encode(_signing_key.sign(receipt_message_bytes(message))).decode("ascii"),
    )
    intercepted = mitm_state.intercept(receipt)
    if intercepted is not None:
        await ws.send_text(intercepted.wire_text())
    return True
