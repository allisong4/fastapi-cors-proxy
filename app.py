# app.py

from fastapi import FastAPI, Query, Request, Response, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware # Import the middleware
import httpx
import os
import random
import json
import yt_dlp

app = FastAPI()

# --- Add this middleware section ---
# This will automatically add the required CORS headers to every response,
# allowing your website to make requests to the proxy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://allisonmarx.com",
        "https://huggingface.co"
    ],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
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


# ---------------------------------------------------------------------------
# Transcript endpoint (added Nov 2026)
# Hugging Face blocks YouTube from inside Spaces, so the Space asks this
# service for transcripts instead. yt-dlp runs here and reaches YouTube
# through the Webshare rotating residential proxy.
# ---------------------------------------------------------------------------
WEBSHARE_UN = os.environ.get("WEBSHARE_PROXY_UN", "")
WEBSHARE_PW = os.environ.get("WEBSHARE_PROXY_PW", "")
WEBSHARE_PROXY = (
    f"http://{WEBSHARE_UN}:{WEBSHARE_PW}@p.webshare.io:80"
    if (WEBSHARE_UN and WEBSHARE_PW) else None
)
TRANSCRIPT_TOKEN = os.environ.get("TRANSCRIPT_TOKEN", "")
MAX_TRANSCRIPT_CHARS = 8000

# yt-dlp needs a JavaScript runtime (deno) to solve YouTube's player challenge.
# Without it YouTube answers "This video is not available". The `deno` pip
# package ships the binary inside site-packages, which may not be on PATH, so
# point yt-dlp at it explicitly.
import shutil
try:
    import deno as _deno_pkg
    DENO_PATH = _deno_pkg.find_deno_bin()
except Exception:
    DENO_PATH = shutil.which("deno")

print(
    "Transcript endpoint: Webshare proxy "
    + ("CONFIGURED (user ends with -rotate: %s)" % WEBSHARE_UN.endswith("-rotate") if WEBSHARE_PROXY else "NOT configured - YouTube will bot-block Render's IP")
    + "; TRANSCRIPT_TOKEN " + ("set" if TRANSCRIPT_TOKEN else "NOT set")
    + "; deno " + (DENO_PATH or "NOT FOUND"),
    flush=True,
)


def _scrub(msg):
    msg = str(msg)
    for secret in (WEBSHARE_PW, WEBSHARE_UN):
        if secret:
            msg = msg.replace(secret, "***")
    return msg


def _track_text(ydl, track):
    url = next((f["url"] for f in track if f.get("ext") == "json3"), track[0]["url"])
    data = json.loads(ydl.urlopen(url).read().decode("utf-8"))
    parts = [s.get("utf8", "") for e in data.get("events", []) for s in (e.get("segs") or [])]
    return "".join(parts).replace("\n", " ").strip()[:MAX_TRANSCRIPT_CHARS]


@app.get("/transcript/{video_id}")
def transcript(video_id: str, request: Request, langs: str = "en"):
    if TRANSCRIPT_TOKEN and request.headers.get("x-token") != TRANSCRIPT_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing x-token header.")
    opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": False,  # keep warnings so JS-runtime problems show in Render logs
        "socket_timeout": 15,
        "extractor_retries": 2,
        "remote_components": ["ejs:github"],  # YouTube JS challenge solver (uses deno)
    }
    if WEBSHARE_PROXY:
        opts["proxy"] = WEBSHARE_PROXY
    if DENO_PATH:
        opts["js_runtimes"] = {"deno": {"path": DENO_PATH}}
    last_msg = ""
    for attempt in range(3):  # each attempt opens new connections -> new Webshare IP
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                subs = info.get("subtitles") or {}
                auto = info.get("automatic_captions") or {}
                out = {}
                for lang in [l.strip() for l in langs.split(",") if l.strip()]:
                    if lang == "en":
                        # English: human captions first, then YouTube's auto-captions.
                        track = subs.get("en") or auto.get("en") or auto.get("en-orig")
                        out[lang] = _track_text(ydl, track) if track else ""
                    else:
                        # Other languages: human-made captions ONLY. YouTube's
                        # machine-translated tracks get rate-limited (HTTP 429), and
                        # the Space translates from English anyway when this is "".
                        track = subs.get(lang)
                        try:
                            out[lang] = _track_text(ydl, track) if track else ""
                        except Exception as e:
                            print(f"Optional {lang} track failed for {video_id}: {_scrub(e)}", flush=True)
                            out[lang] = ""
                return out
        except Exception as e:
            last_msg = _scrub(e)
            print(f"Transcript attempt {attempt + 1}/3 failed for {video_id}: {last_msg}", flush=True)
            retryable = ("not a bot", "429", "not available", "timed out", "EOF")
            if not any(r in last_msg for r in retryable):
                break
    raise HTTPException(status_code=502, detail=f"YouTube fetch failed: {last_msg}")
