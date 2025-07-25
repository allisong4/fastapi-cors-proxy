from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import StreamingResponse, JSONResponse
import httpx

app = FastAPI()

@app.get("/proxy")
async def proxy(request: Request, url: str = Query(...)):
    try:
        # Forward all headers, especially "Range"
        forward_headers = {}
        if "range" in request.headers:
            forward_headers["range"] = request.headers["range"]

        async with httpx.AsyncClient() as client:
            async with client.stream("GET", url, headers=forward_headers) as remote:
                # Forward status and all range/audio-relevant headers
                response_headers = {
                    "Content-Type": remote.headers.get("content-type", "application/octet-stream"),
                    "Access-Control-Allow-Origin": "*",
                }
                # Pass through Accept-Ranges, Content-Range, Content-Length if present
                for h in ["accept-ranges", "content-range", "content-length", "content-disposition"]:
                    if h in remote.headers:
                        response_headers[h.capitalize() if h != "content-disposition" else "Content-Disposition"] = remote.headers[h]

                if remote.status_code >= 400:
                    content = await remote.aread()
                    return Response(content, status_code=remote.status_code, headers=response_headers)
                return StreamingResponse(remote.aiter_bytes(), headers=response_headers, status_code=remote.status_code)
    except Exception as e:
        return JSONResponse(
            content={"error": str(e)},
            status_code=500,
            headers={"Access-Control-Allow-Origin": "*"}
        )
