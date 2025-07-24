from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse
import httpx

app = FastAPI()

@app.get("/proxy")
async def proxy(url: str = Query(...)):
    async with httpx.AsyncClient() as client:
        remote = await client.get(url, stream=True)
        headers = {
            "Content-Type": remote.headers.get("content-type", "application/octet-stream"),
            "Access-Control-Allow-Origin": "*"
        }
        return StreamingResponse(remote.aiter_bytes(), headers=headers, status_code=remote.status_code)
