import asyncio
import hashlib
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from fastapi import WebSocket

from transfer_server import network
from transfer_server.config import COMPUTER_IP, LOCAL_ROUTER
from transfer_server.websocket_endpoint import ws_endpoint


class RouterFailureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertEqual(network.failed_routers, set())
        self.failed = "10.0.1.6"
        failure = patch.object(network, "failed_routers", {self.failed})
        failure.start()
        self.addCleanup(failure.stop)
        self.host = "10.0.0.7"
        # Exercise both directions deterministically without changing generation.
        routes = patch.dict(network.first_hops, {
            (LOCAL_ROUTER, self.host): self.failed,
            (self.failed, self.host): self.host,
            (self.host, COMPUTER_IP): self.failed,
            (self.failed, COMPUTER_IP): LOCAL_ROUTER,
        })
        routes.start()
        self.addCleanup(routes.stop)

    @asynccontextmanager
    async def connection(self):
        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()
        ws = WebSocket(
            {"type": "websocket", "path": "/ws"},
            incoming.get,
            outgoing.put,
        )
        await incoming.put({"type": "websocket.connect"})
        task = asyncio.create_task(ws_endpoint(ws))
        self.assertEqual(
            await asyncio.wait_for(outgoing.get(), 1),
            {"type": "websocket.accept", "subprotocol": None, "headers": []},
        )

        async def send(command):
            await incoming.put({"type": "websocket.receive", "text": command})

        async def receive():
            message = await asyncio.wait_for(outgoing.get(), 1)
            self.assertEqual(message["type"], "websocket.send")
            text = message["text"]
            self.assertNotIn("\n", text)
            self.assertNotIn("\r", text)
            return text

        try:
            yield send, receive
        finally:
            await incoming.put({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(task, 1)

    def test_failure_disrupts_host_traffic_without_changing_routes(self):
        self.assertEqual(len(network.router_ips), 6)
        self.assertNotEqual(self.failed, LOCAL_ROUTER)
        routes = network.first_hops.copy()
        self.assertFalse(network.can_exchange_packets(COMPUTER_IP, self.host))
        self.assertEqual(network.trace_packet(self.host, COMPUTER_IP, 1), "TIMEOUT")
        self.assertEqual(network.trace_packet(self.failed, COMPUTER_IP, 10), "TIMEOUT")
        self.assertEqual(network.trace_packet(COMPUTER_IP, self.failed, 10), "TIMEOUT")
        self.assertEqual(
            network.trace_packet(COMPUTER_IP, self.host, 1),
            f"TTL EXPIRED AT {LOCAL_ROUTER}",
        )

        network.restart_router(self.failed)
        self.assertEqual(network.first_hops, routes)
        for source in network.device_ips:
            for destination in network.device_ips:
                self.assertEqual(
                    network.trace_packet(source, destination, 10),
                    f"REACHED {destination}",
                )

    def test_only_probes_crossing_failure_change(self):
        for source in network.device_ips:
            for destination in network.device_ips:
                for ttl in range(1, 8):
                    result = network.trace_packet(source, destination, ttl)
                    if result == "TIMEOUT":
                        continue
                    with patch.object(network, "failed_routers", set()):
                        self.assertEqual(
                            result, network.trace_packet(source, destination, ttl)
                        )

        self.assertEqual(network.trace_packet(COMPUTER_IP, self.host, 0), "TTL must be at least 1.")
        self.assertEqual(network.trace_packet("unknown", self.host, 10), "UNKNOWN SOURCE unknown")
        self.assertEqual(network.trace_packet(COMPUTER_IP, "unknown", 10), "DESTINATION UNREACHABLE unknown")

    async def test_two_students_share_outage_and_out_of_band_repair(self):
        port = next(
            address.split(":")[1]
            for address in network.online_hosts
            if address.startswith(self.host + ":")
        )
        async with self.connection() as (send1, receive1), self.connection() as (send2, receive2):
            for send, receive in ((send1, receive1), (send2, receive2)):
                await send(f"CONNECT {self.host} {port}")
                self.assertTrue((await receive()).startswith("CONNECTED:"))
                await send(f"TRACE {self.host} 10")
                self.assertEqual(await receive(), "TIMEOUT")
                await send("REMOTE_TRACE 1")
                self.assertEqual(await receive(), "TIMEOUT")

            await send1("RESTART")
            self.assertEqual(await receive1(), "Usage: RESTART <router-ip>")
            await send1("RESTART 10.0.9.9")
            self.assertEqual(await receive1(), "Unknown router.")
            await send1(f"RESTART {LOCAL_ROUTER}")
            self.assertEqual(await receive1(), f"RESTARTED {LOCAL_ROUTER}")
            self.assertEqual(network.failed_routers, {self.failed})

            await send1("CLOSE")
            self.assertEqual(await receive1(), "Connection closed.")
            await send1(f"RESTART {self.failed}")
            self.assertEqual(await receive1(), f"RESTARTED {self.failed}")
            await send2(f"TRACE {self.host} 10")
            self.assertEqual(await receive2(), f"REACHED {self.host}")
            await send2("REMOTE_TRACE 1")
            self.assertEqual(await receive2(), f"TTL EXPIRED AT {self.failed}")

            # New students inherit the repair rather than creating another fault.
            async with self.connection() as (send3, receive3):
                await send3(f"TRACE {self.host} 10")
                self.assertEqual(await receive3(), f"REACHED {self.host}")

    async def test_host_commands_block_before_side_effects_and_resume(self):
        for command in ("ROB 10", "START file 1 hash", "DATA 0 text", "STATUS", "DONE", "TIME", "VERIFY 0"):
            for host, port in (("10.0.0.6", 443), ("10.0.0.7", 80), ("10.0.0.9", 123)):
                with patch.object(network, "failed_routers", {network.first_hops[(LOCAL_ROUTER, host)]}):
                    async with self.connection() as (send, receive):
                        await send(f"CONNECT {host} {port}")
                        self.assertTrue((await receive()).startswith("CONNECTED:"))
                        await send(command)
                        self.assertEqual(await receive(), "Router replied: Destination unreachable.")

        with patch.object(network, "failed_routers", {self.failed}):
            async with self.connection() as (send, receive):
                await send("CONNECT 10.0.0.7 80")
                self.assertEqual(await receive(), "CONNECTED: NY Times")
                checksum = hashlib.sha256(b"hello").hexdigest()
                await send(f"START file 1 {checksum}")
                self.assertEqual(await receive(), "Router replied: Destination unreachable.")
                await send(f"RESTART {self.failed}")
                await receive()
                await send(f"START file 1 {checksum}")
                self.assertEqual(await receive(), "READY file 1")
                with patch("transfer_server.news_site_commands.datagram_dropped", return_value=False):
                    await send("DATA 0 hello")
                    self.assertEqual(await receive(), "ACK 0")
                await send("STATUS")
                self.assertEqual(await receive(), "RECEIVED 1/1 MISSING none")
                await send("DONE")
                self.assertTrue((await receive()).startswith("TRANSFER COMPLETE file 5 bytes"))

    async def test_offline_hosts_and_wrong_ports_still_reject_connections(self):
        with patch.object(network, "failed_routers", set()):
            async with self.connection() as (send, receive):
                for host, port in (("10.0.0.100", 443), ("10.0.0.7", 81)):
                    await send(f"CONNECT {host} {port}")
                    self.assertEqual(await receive(), "Host offline (or incorrect port).")
                await send("CONNECT 10.0.0.7 80")
                self.assertEqual(await receive(), "CONNECTED: NY Times")

    async def test_outage_hosts_connect_but_commands_timeout_until_shared_restart(self):
        address = next(
            address for address in network.online_hosts
            if address.startswith(self.host + ":")
        )
        command = "CONNECT " + address.replace(":", " ")
        async with self.connection() as (send1, receive1), self.connection() as (send2, receive2):
            for send, receive in ((send1, receive1), (send2, receive2)):
                await send(command)
                self.assertEqual(await receive(), f"CONNECTED: {network.online_hosts[address]}")
                await send("STATUS")
                self.assertEqual(await receive(), "Router replied: Destination unreachable.")
            await send1(f"RESTART {self.failed}")
            self.assertEqual(await receive1(), f"RESTARTED {self.failed}")
            for send, receive in ((send1, receive1), (send2, receive2)):
                await send("REMOTE_TRACE 10")
                self.assertEqual(await receive(), f"REACHED {COMPUTER_IP}")

    async def test_reply_route_failure_also_blocks_traffic(self):
        host = "10.0.0.9"
        healthy_gateway = next(
            router for router in network.router_ips
            if router not in (LOCAL_ROUTER, self.failed)
        )
        # Model an asymmetric route with failure only on the reply path.
        with patch.dict(network.first_hops, {
            (LOCAL_ROUTER, host): healthy_gateway,
            (healthy_gateway, host): host,
            (host, COMPUTER_IP): self.failed,
        }):
            self.assertEqual(network.trace_packet(COMPUTER_IP, host, 10), f"REACHED {host}")
            self.assertFalse(network.can_exchange_packets(COMPUTER_IP, host))
            async with self.connection() as (send, receive):
                await send(f"CONNECT {host} 123")
                self.assertEqual(await receive(), "CONNECTED: SNTP Time Server")
                with patch("transfer_server.time_server_commands.datagram_dropped", return_value=False):
                    await send("TIME")
                    self.assertEqual(await receive(), "Timeout")

    async def test_delivered_requests_take_effect_despite_lost_replies(self):
        healthy_router = "10.0.1.2"
        with patch.dict(network.first_hops, {
            (LOCAL_ROUTER, self.host): healthy_router,
            (healthy_router, self.host): self.host,
        }):
            async with self.connection() as (send, receive):
                await send(f"CONNECT {self.host} 80")
                self.assertEqual(await receive(), "CONNECTED: NY Times")
                checksum = hashlib.sha256(b"hello").hexdigest()
                await send(f"START file 1 {checksum}")
                self.assertEqual(await receive(), "Timeout")

                # Exercise-specific request loss stays silent, without a reply.
                with patch("transfer_server.news_site_commands.datagram_dropped", return_value=True):
                    await send("DATA 0 hello")
                    await send(f"TRACE {COMPUTER_IP} 1")
                    self.assertEqual(await receive(), f"REACHED {COMPUTER_IP}")

                with patch("transfer_server.news_site_commands.datagram_dropped", return_value=False):
                    await send("DATA 0 hello")
                    self.assertEqual(await receive(), "Timeout")
                await send(f"RESTART {self.failed}")
                self.assertEqual(await receive(), f"RESTARTED {self.failed}")
                await send("STATUS")
                self.assertEqual(await receive(), "RECEIVED 1/1 MISSING none")
                await send("DONE")
                self.assertTrue((await receive()).startswith("TRANSFER COMPLETE file 5 bytes"))

    async def test_multiple_lost_replies_report_one_timeout(self):
        host = "10.0.0.6"
        with patch.dict(network.first_hops, {
            (LOCAL_ROUTER, host): "10.0.1.2",
            ("10.0.1.2", host): host,
            (host, COMPUTER_IP): self.failed,
        }):
            async with self.connection() as (send, receive):
                await send(f"CONNECT {host} 443")
                self.assertEqual(await receive(), "CONNECTED: The Bank")
                await send("ROB 10")
                self.assertEqual(await receive(), "Timeout")
                await send(f"RESTART {self.failed}")
                self.assertEqual(await receive(), f"RESTARTED {self.failed}")
                await send("ROB 20")
                self.assertEqual(await receive(), f"Stole $20.00 from {host}:443")
                self.assertEqual(await receive(), "The police caught you!")
                self.assertEqual(await receive(), "You were removed.")
                await send("CLOSE")
                self.assertEqual(await receive(), "Connection closed.")

    def test_restart_repairs_only_the_selected_router(self):
        network.failed_routers.add("10.0.1.5")
        network.restart_router(self.failed)
        self.assertEqual(network.failed_routers, {"10.0.1.5"})
        self.assertEqual(network.trace_packet(COMPUTER_IP, "10.0.1.5", 10), "TIMEOUT")

    async def test_help_lists_recovery_without_disclosing_failure(self):
        async with self.connection() as (send, receive):
            await send("HELP")
            lines = []
            while not lines or lines[-1] != "HELP":
                lines.append(await receive())
            self.assertIn("RESTART <router-ip>", lines)
            self.assertNotIn(self.failed, " ".join(lines))
            self.assertFalse(any("PING" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
