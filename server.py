# pyright: strict

import os

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from transfer_server.api import sixseven
from transfer_server.websocket_endpoint import ws_endpoint

app = FastAPI()

app.mount("/assets", StaticFiles(directory="dist/assets"), name="static")

@app.get("/")
async def health():
    return FileResponse("dist/websocket_client.html", media_type="text/html")


app.get("/6767420", response_class=HTMLResponse)(sixseven)
app.websocket("/ws")(ws_endpoint)


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
