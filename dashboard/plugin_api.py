"""CT2 dashboard plugin — backend API routes.

Mounted at /api/plugins/ct2/ by the dashboard plugin system.

Thin proxy: each handler delegates to the CT2 REST API on localhost:7890
and returns JSON verbatim. Connection errors surface as 503 so the frontend
can show a friendly "CT2 server unavailable" banner instead of a raw 5xx.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

router = APIRouter()

CT2_BASE = "http://localhost:7890"
TIMEOUT = httpx.Timeout(30.0)

# Shared sync client — re-used across requests to reuse the connection pool.
_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=CT2_BASE, timeout=TIMEOUT)
    return _client


def _proxy(path: str, params: dict | None = None) -> JSONResponse:
    """GET *path* on the CT2 server and return its JSON response."""
    try:
        resp = _get_client().get(path, params={k: v for k, v in (params or {}).items() if v is not None})
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        log.warning("CT2 proxy %s → %s: %s", path, exc.response.status_code, exc)
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except httpx.RequestError as exc:
        log.error("CT2 connection error for %s: %s", path, exc)
        raise HTTPException(
            status_code=503,
            detail={"error": "CT2 server unavailable", "detail": str(exc)},
        )
    return JSONResponse(content=data)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/health")
def health():
    """Probe CT2 server liveness; returns {ok, detail} rather than raising."""
    try:
        resp = _get_client().get("/health", timeout=httpx.Timeout(5.0))
        resp.raise_for_status()
        return {"ok": True, "detail": resp.json()}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "CT2 server unavailable", "detail": str(exc)},
        )


@router.get("/projects")
def get_projects():
    return _proxy("/api/projects")


@router.get("/projects/{slug}/tasks")
def get_project_tasks(
    slug: str,
    status: Optional[str] = Query(None),
    sprint: Optional[int] = Query(None),
    limit: Optional[int] = Query(None),
):
    return _proxy(f"/api/projects/{slug}/tasks", params={"status": status, "sprint": sprint, "limit": limit})


@router.get("/projects/{slug}/sprints")
def get_project_sprints(slug: str):
    return _proxy(f"/api/projects/{slug}/sprints")


@router.get("/projects/{slug}/auditorias")
def get_project_audits(
    slug: str,
    limit: Optional[int] = Query(10),
):
    return _proxy(f"/api/projects/{slug}/auditorias", params={"limit": limit})


@router.get("/stats")
def get_stats():
    return _proxy("/api/stats")


# ---------------------------------------------------------------------------
# HTML detail pages — proxy HTML content from CT2
# ---------------------------------------------------------------------------

def _proxy_html(path: str) -> Response:
    """GET *path* on the CT2 server and return its HTML response."""
    from fastapi.responses import Response
    try:
        resp = _get_client().get(path)
        resp.raise_for_status()
        return Response(content=resp.content, media_type="text/html", status_code=resp.status_code)
    except httpx.HTTPStatusError as exc:
        log.warning("CT2 proxy %s → %s: %s", path, exc.response.status_code, exc)
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except httpx.RequestError as exc:
        log.error("CT2 connection error for %s: %s", path, exc)
        raise HTTPException(
            status_code=503,
            detail={"error": "CT2 server unavailable", "detail": str(exc)},
        )


@router.get("/tasks/{slug}/{task_number}")
def task_html(slug: str, task_number: int):
    return _proxy_html(f"/tasks/{slug}/{task_number}")


@router.get("/auditorias/{audit_id}")
def audit_html(audit_id: int):
    return _proxy_html(f"/auditorias/{audit_id}")
