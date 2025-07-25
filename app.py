from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import StreamingResponse, JSONResponse
import httpx

app = FastAPI()

@app.get("/proxy")
async def proxy(request: Request, url: str = Query(...)):
    try:
        # Pass Range header if present (for audio/video streaming)
        forward_headers = {}
        if "range" in request.headers:
            forward_headers["range"] = request.headers["range"]

        async with httpx.AsyncClient() as client:
            async with client.stream("GET", url, headers=forward_headers) as remote:
                # Build response headers up front *before* streaming
                response_headers = {
                    "Content-Type": remote.headers.get("content-type", "application/octet-stream"),
                    "Access-Control-Allow-Origin": "*",
                }
                # Forward range-related headers if present
                for h in ["accept-ranges", "content-range", "content-length", "content-disposition"]:
                    if remote.headers.get(h) is not None:
                        # Capitalize as needed for Content-Length, etc.
                        proper_header = h.title() if h != "content-disposition" else "Content-Disposition"
                        response_headers[proper_header] = remote.headers[h]

                if remote.status_code >= 400:
                    content = await remote.aread()
                    return Response(content, status_code=remote.status_code, headers=response_headers)

                # Stream the response WHILE the context is open!
                return StreamingResponse(
                    remote.aiter_bytes(),
                    status_code=remote.status_code,
                    headers=response_headers
                )
    except Exception as e:
        print(f"Proxy error: {e}")  # Log to Render dashboard
        return JSONResponse(
            content={"error": str(e)},
            status_code=500,
            headers={"Access-Control-Allow-Origin": "*"}
        )