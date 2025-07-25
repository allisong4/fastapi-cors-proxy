from fastapi import FastAPI, Query, Response
from fastapi.responses import StreamingResponse, JSONResponse
import httpx

app = FastAPI()

@app.get("/proxy")
async def proxy(url: str = Query(...)):
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", url) as remote:
                if remote.status_code >= 400:
                    content = await remote.aread()
                    return Response(content, status_code=remote.status_code, headers={
                        "Content-Type": remote.headers.get("content-type", "application/octet-stream"),
                        "Access-Control-Allow-Origin": "*"
                    })
                headers = {
                    "Content-Type": remote.headers.get("content-type", "application/octet-stream"),
                    "Access-Control-Allow-Origin": "*"
                }
                return StreamingResponse(remote.aiter_bytes(), headers=headers, status_code=remote.status_code)
    except Exception as e:
        return JSONResponse(
            content={"error": str(e)},
            status_code=500,
            headers={"Access-Control-Allow-Origin": "*"}
        )
