import base64
import json
import os
import unittest
from unittest.mock import patch

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException, Response, WebSocket
from fastapi.security import HTTPBasicCredentials

from transfer_server import government_commands as government, network
from transfer_server.config import COMPUTER_IP, LOCAL_ROUTER
from transfer_server.instructor import require_instructor
from transfer_server.state import SessionState, connect_socket, new_socket
from transfer_server.websocket_endpoint import ws_endpoint


class RecordingSender:
    def __init__(self):
        self.messages = []

    async def send_text(self, data):
        self.messages.append(data)


class GovernmentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        state = patch.object(government, "mitm_state", government.MITMState())
        state.start()
        self.addCleanup(state.stop)
        self.public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(government.public_key_info()["public_key"])
        )

    def session(self, host="10.0.0.8", port=443):
        session = SessionState(COMPUTER_IP)
        _, sock = new_socket(session)
        connect_socket(sock, host, port)
        return session

    async def pay(self, nonce, amount="25.00", session=None):
        output = RecordingSender()
        handled = await government.government_command(
            "PAY", [amount, nonce], output, session or self.session()
        )
        self.assertTrue(handled)
        for message in output.messages:
            self.assertNotIn("\n", message)
            self.assertNotIn("\r", message)
        return output.messages

    def verify(self, receipt):
        self.public_key.verify(
            base64.b64decode(receipt["signature"], validate=True),
            json.dumps(
                receipt["message"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8"),
        )

    async def websocket_messages(self, commands):
        incoming = iter([
            {"type": "websocket.connect"},
            *({"type": "websocket.receive", "text": command} for command in commands),
            {"type": "websocket.disconnect", "code": 1000},
        ])
        outgoing = []

        async def receive():
            return next(incoming)

        async def send(message):
            outgoing.append(message)

        await ws_endpoint(WebSocket({"type": "websocket", "path": "/ws"}, receive, send))
        messages = [message["text"] for message in outgoing if message["type"] == "websocket.send"]
        for message in messages:
            self.assertNotIn("\n", message)
            self.assertNotIn("\r", message)
        return messages

    async def test_forward_signs_exact_message_and_binds_transaction(self):
        first = json.loads((await self.pay("nonce-one", "25"))[0])
        second = json.loads((await self.pay("nonce-two"))[0])
        for receipt in (first, second):
            self.verify(receipt)
        body = first["message"]
        self.assertEqual(body["nonce"], "nonce-one")
        self.assertEqual(body["amount"], "25.00")
        self.assertEqual(set(body), {"nonce", "amount"})
        self.assertNotEqual(body["nonce"], second["message"]["nonce"])

    async def test_forge_preserves_fields_but_fails_government_verification(self):
        government.mitm_state.mode = government.AttackMode.FORGE
        receipt = json.loads((await self.pay("nonce"))[0])
        original = government.mitm_state.previous_receipt
        body = receipt["message"]
        self.assertEqual(set(body), {"amount", "nonce"})
        self.assertEqual(body["amount"], "25.00")
        self.assertEqual(body["nonce"], "nonce")
        self.assertEqual(original.message, {"amount": "25.00", "nonce": "nonce"})
        self.assertEqual(body, original.message)
        self.assertNotEqual(receipt["signature"], original.signature)
        signature = base64.b64decode(receipt["signature"], validate=True)
        self.assertEqual(len(signature), 64)
        government.mitm_state._attacker_key.public_key().verify(
            signature,
            json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        )
        with self.assertRaises(InvalidSignature):
            self.verify(receipt)

    async def test_forge_preserves_boundary_amounts(self):
        government.mitm_state.mode = government.AttackMode.FORGE
        for amount in ("0.01", "999999999999.99"):
            with self.subTest(amount=amount):
                receipt = json.loads((await self.pay("nonce", amount))[0])
                self.assertEqual(receipt["message"], {"amount": amount, "nonce": "nonce"})
                with self.assertRaises(InvalidSignature):
                    self.verify(receipt)

    async def test_live_mode_and_replay_are_shared_across_student_sessions(self):
        first = (await self.pay("student-one", session=self.session()))[0]
        government.mitm_state.mode = government.AttackMode.REPLAY
        replay = (await self.pay("student-two", session=self.session()))[0]
        self.assertEqual(replay, first)
        receipt = json.loads(replay)
        self.verify(receipt)
        self.assertEqual(receipt["message"]["nonce"], "student-one")
        self.assertNotEqual(receipt["message"]["nonce"], "student-two")
        government.mitm_state.mode = government.AttackMode.FORWARD
        third = json.loads((await self.pay("student-three"))[0])
        self.verify(third)
        self.assertEqual(third["message"]["nonce"], "student-three")

    async def test_replay_without_history_never_uses_current_receipt(self):
        government.mitm_state.mode = government.AttackMode.REPLAY
        self.assertEqual(await self.pay("first"), [])
        replay = json.loads((await self.pay("second"))[0])
        self.verify(replay)
        self.assertEqual(replay["message"]["nonce"], "first")

    async def test_invalid_payments_never_get_signed(self):
        for amount in ("0", "-1", "NaN", "Infinity", "1e1000", "1.001", "9999999999999"):
            with self.subTest(amount=amount):
                messages = await self.pay("nonce", amount)
                self.assertEqual(len(messages), 1)
                self.assertTrue(messages[0].startswith("Amount must be positive"))
        self.assertEqual(await self.pay("x" * 129), ["Nonce must contain between 1 and 128 characters."])
        self.assertEqual(
            await self.pay("nonce", session=self.session("10.0.0.6")),
            ["Tax payments are only accepted by Secure Government Site."],
        )
        self.assertEqual(government.mitm_state.receipt_count, 0)

    async def test_pay_uses_existing_websocket_dispatch_and_return_route(self):
        with patch.object(network, "failed_routers", set()):
            messages = await self.websocket_messages([
                "CONNECT 10.0.0.8 443", "PAY 25.00 nonce", "CLOSE"
            ])
        self.assertEqual(messages[0], "CONNECTED: Secure Government Site")
        self.verify(json.loads(messages[1]))
        self.assertEqual(messages[2], "Connection closed.")

    async def test_outgoing_failure_prevents_government_processing(self):
        failed = network.first_hops[(LOCAL_ROUTER, "10.0.0.8")]
        with patch.object(network, "failed_routers", {failed}):
            messages = await self.websocket_messages([
                "CONNECT 10.0.0.8 443", "PAY 25.00 nonce", "CLOSE"
            ])
        self.assertEqual(messages[1], "Router replied: Destination unreachable.")
        self.assertEqual(government.mitm_state.receipt_count, 0)

    async def test_reply_failure_times_out_after_government_processing(self):
        routes = {
            (LOCAL_ROUTER, "10.0.0.8"): "10.0.1.2",
            ("10.0.1.2", "10.0.0.8"): "10.0.0.8",
            ("10.0.0.8", COMPUTER_IP): "10.0.1.6",
        }
        with patch.dict(network.first_hops, routes), patch.object(network, "failed_routers", {"10.0.1.6"}):
            commands = ["CONNECT 10.0.0.8 443", "PAY 25.00 nonce", "CLOSE"]
            messages = await self.websocket_messages(commands)
            self.assertEqual(messages[1], "Timeout")
            self.assertEqual(government.mitm_state.receipt_count, 1)


class InstructorCredentialTests(unittest.TestCase):
    def test_credentials_fail_closed_without_configured_password(self):
        credentials = HTTPBasicCredentials(username="instructor", password="")
        for environment in ({}, {"INSTRUCTOR_PASSWORD": ""}):
            with patch.dict(os.environ, environment, clear=True), self.assertRaises(HTTPException) as error:
                require_instructor(Response(), credentials)
            self.assertEqual(error.exception.status_code, 401)
            self.assertIn("Basic", error.exception.headers["WWW-Authenticate"])

    def test_credentials_require_both_correct_username_and_password(self):
        with patch.dict(os.environ, {"INSTRUCTOR_PASSWORD": "classroom-test-password"}):
            for credentials in (
                None,
                HTTPBasicCredentials(username="student", password="classroom-test-password"),
                HTTPBasicCredentials(username="instructor", password="wrong"),
            ):
                with self.assertRaises(HTTPException) as error:
                    require_instructor(Response(), credentials)
                self.assertEqual(error.exception.status_code, 401)
            response = Response()
            require_instructor(response, HTTPBasicCredentials(
                username="instructor", password="classroom-test-password"
            ))
            self.assertEqual(response.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
