# pyright: strict

from typing import Protocol

from .network import packet_reaches


class TextSender(Protocol):
    async def send_text(self, data: str) -> None: ...


class RoutedReplies:
    def __init__(self, ws: TextSender, source_ip: str, destination_ip: str) -> None:
        self.ws = ws
        self.source_ip = source_ip
        self.destination_ip = destination_ip
        self.timed_out = False

    async def send_text(self, data: str) -> None:
        if packet_reaches(self.source_ip, self.destination_ip):
            await self.ws.send_text(data)
        elif not self.timed_out:
            # Report once per command, even when it generates several replies.
            self.timed_out = True
            await self.ws.send_text("Timeout")
