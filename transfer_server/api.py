# pyright: strict

from fastapi.responses import FileResponse


async def health():
    return FileResponse("websocket_client.html", media_type="text/html")

async def sixseven() -> str:
    return "<h1> sixseven </h1>"
