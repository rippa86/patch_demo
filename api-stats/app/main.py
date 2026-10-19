import os

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8001")
PEER_BACKEND_URL = os.environ.get("PEER_BACKEND_URL", "")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "")

app = FastAPI(title="webstore-api-stats")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://demo-vm.lab.rippa.io",
        "http://10.10.4.108",
        "http://demo-vm.lab.rippa.io:80",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _backend_urls() -> list[str]:
    urls = [BACKEND_URL]
    if PEER_BACKEND_URL and PEER_BACKEND_URL not in urls:
        urls.append(PEER_BACKEND_URL)
    return urls


def backend_json(method: str, path: str) -> httpx.Response:
    headers = {"X-Internal-Token": INTERNAL_TOKEN}
    last: Exception | None = None
    for base in _backend_urls():
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.request(method, f"{base}{path}", headers=headers)
            if response.status_code < 500:
                return response
            last = RuntimeError(response.text)
        except Exception as exc:
            last = exc
            continue
    raise HTTPException(status_code=503, detail=f"backend unreachable: {last}")


@app.get("/healthz")
def healthz() -> dict:
    response = backend_json("GET", "/healthz")
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="backend unhealthy")
    return {"ok": True}


@app.get("/api/stats/logged-in")
def logged_in() -> dict:
    response = backend_json("GET", "/internal/stats/logged-in")
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=response.text)
    return response.json()
