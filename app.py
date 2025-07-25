from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import StreamingResponse, JSONResponse
import httpx

app = FastAPI()

@app.get("/proxy")
async def proxy(request: Request, url: str = Query(...)):
    """
    Asynchronously proxies a file from a given URL.
    It streams the content, keeping the connection to the source open
    until the client has received the entire file.
    """
    # We create a client that will be used for the request.
    client = httpx.AsyncClient()

    try:
        # Prepare the headers for the request to the remote server.
        # We forward the 'range' header if it exists, which is crucial for streaming audio/video.
        forward_headers = {}
        if "range" in request.headers:
            forward_headers["range"] = request.headers["range"]

        # Manually build and send the request to be able to handle the response stream.
        # Using stream=True tells httpx to not load the whole response body into memory.
        req = client.build_request("GET", url, headers=forward_headers, timeout=None)
        remote_response = await client.send(req, stream=True)

        # Prepare the headers for the response we will send to our client.
        # We copy essential headers from the remote server's response.
        response_headers = {
            "Content-Type": remote_response.headers.get("content-type", "application/octet-stream"),
            "Access-Control-Allow-Origin": "*",
        }
        for h in ["accept-ranges", "content-range", "content-length", "content-disposition"]:
            if h in remote_response.headers:
                response_headers[h] = remote_response.headers[h]

        # If the remote server returned an error (like 404 Not Found),
        # we read the error message and return a regular response, not a stream.
        if remote_response.status_code >= 400:
            error_content = await remote_response.aread()
            await remote_response.aclose()
            await client.aclose()
            return Response(content=error_content, status_code=remote_response.status_code, headers=response_headers)

        # This is the core of the solution. We create an async generator
        # that yields chunks of the file. FastAPI will iterate over this.
        async def body_iterator():
            try:
                # Yield each chunk of data as we receive it from the remote server.
                async for chunk in remote_response.aiter_bytes():
                    yield chunk
            finally:
                # This 'finally' block is guaranteed to run when the stream is finished
                # or if the client disconnects. We close our connections here.
                await remote_response.aclose()
                await client.aclose()
                print("Cleaned up remote response and client.")

        # Return a StreamingResponse. It will consume our async generator.
        # The connection will be kept alive until the generator is exhausted.
        return StreamingResponse(
            body_iterator(),
            status_code=remote_response.status_code,
            headers=response_headers
        )

    except httpx.RequestError as e:
        # Handle network-level errors during connection to the remote server.
        print(f"Upstream request error: {e}")
        await client.aclose()
        return JSONResponse(content={"error": f"Failed to connect to upstream server: {e}"}, status_code=502) # 502 Bad Gateway
    
    except Exception as e:
        # Handle any other unexpected errors.
        print(f"An unexpected proxy error occurred: {e}")
        if not client.is_closed:
            await client.aclose()
        return JSONResponse(
            content={"error": str(e)},
            status_code=500,
            headers={"Access-Control-Allow-Origin": "*"}
        )