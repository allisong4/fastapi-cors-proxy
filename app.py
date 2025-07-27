# app.py

from fastapi import FastAPI, Query, Request, Response, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware # Import the middleware
import httpx
import os
import random

app = FastAPI()

# --- Add this middleware section ---
# This will automatically add the required CORS headers to every response,
# allowing your website to make requests to the proxy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins, you could restrict this to your domain
    allow_credentials=True,
    allow_methods=["*"],  # Allows all HTTP methods
    allow_headers=["*"],  # Allows all headers
)
# --- End of middleware section ---


# Get keys from environment variable, split by comma, and remove whitespace.
API_KEYS_STR = os.environ.get("YOUTUBE_API_KEYS", "")
YOUTUBE_API_KEYS = [key.strip() for key in API_KEYS_STR.split(',') if key.strip()]

@app.get("/youtube/v3/{endpoint:path}")
async def youtube_proxy(endpoint: str, request: Request):
    """
    Proxies requests to the YouTube Data API v3, securely adding an API key on the server-side.
    """
    if not YOUTUBE_API_KEYS:
        raise HTTPException(status_code=500, detail="YouTube API keys are not configured on the server.")

    query_params = dict(request.query_params)
    
    keys_to_try = random.sample(YOUTUBE_API_KEYS, len(YOUTUBE_API_KEYS))
    
    async with httpx.AsyncClient() as client:
        for api_key in keys_to_try:
            query_params['key'] = api_key
            youtube_api_url = f"https://www.googleapis.com/youtube/v3/{endpoint}"
            
            try:
                response = await client.get(youtube_api_url, params=query_params, timeout=10.0)
                
                if response.status_code == 403:
                    print(f"API key ending in ...{api_key[-4:]} failed with 403. Trying next key.")
                    continue
                
                # The middleware adds CORS headers, so we just return the response.
                return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get('content-type'))

            except httpx.RequestError as e:
                print(f"Error contacting YouTube API: {e}")
                raise HTTPException(status_code=502, detail=f"Bad Gateway: Could not contact YouTube API. {e}")

    raise HTTPException(status_code=403, detail="All available YouTube API keys have exceeded their quota.")


@app.get("/proxy")
async def proxy(request: Request, url: str = Query(...)):
    """
    Asynchronously proxies a file from a given URL.
    """
    client = httpx.AsyncClient()

    try:
        forward_headers = {}
        if "range" in request.headers:
            forward_headers["range"] = request.headers["range"]

        req = client.build_request("GET", url, headers=forward_headers, timeout=None)
        remote_response = await client.send(req, stream=True)
        
        # The middleware handles CORS, so we only need content-related headers.
        response_headers = {
            "Content-Type": remote_response.headers.get("content-type", "application/octet-stream"),
        }
        for h in ["accept-ranges", "content-range", "content-length", "content-disposition"]:
            if h in remote_response.headers:
                response_headers[h] = remote_response.headers[h]

        if remote_response.status_code >= 400:
            error_content = await remote_response.aread()
            await remote_response.aclose()
            await client.aclose()
            return Response(content=error_content, status_code=remote_response.status_code, headers=response_headers)

        async def body_iterator():
            try:
                async for chunk in remote_response.aiter_bytes():
                    yield chunk
            finally:
                await remote_response.aclose()
                await client.aclose()
                print("Cleaned up remote response and client.")

        return StreamingResponse(
            body_iterator(),
            status_code=remote_response.status_code,
            headers=response_headers
        )

    except httpx.RequestError as e:
        print(f"Upstream request error: {e}")
        await client.aclose()
        return JSONResponse(content={"error": f"Failed to connect to upstream server: {e}"}, status_code=502)
    
    except Exception as e:
        print(f"An unexpected proxy error occurred: {e}")
        if not client.is_closed:
            await client.aclose()
        return JSONResponse(content={"error": str(e)}, status_code=500)