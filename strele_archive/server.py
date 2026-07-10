"""Majhen HTTP strežnik: API + statična stran z grafi."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from strele_archive.auth import get_private_api_key, require_private_api_key
from strele_archive.config import ROOT, get_settings
from strele_archive.data_source import (
    get_archive_info,
    get_latest_date,
    get_obcine_gostota_top,
    get_obcine_map,
    get_obcine_top,
    get_regije,
    get_si_daily,
    get_si_hourly,
)
from strele_archive.strelko_auth import (
    auth_enabled,
    extract_bearer_token,
    gate_html,
    is_public_path,
    verify_strelko_token,
    wants_html,
)

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
DATA_DIR = ROOT / "data"

PUBLIC_ONLY = os.getenv("STRELE_PUBLIC_ONLY", "").strip().lower() in ("1", "true", "yes")
WEB_BASE_PATH = os.getenv("WEB_BASE_PATH", "/arhiv").strip().rstrip("/")
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
]

app = FastAPI(title="Strele arhiv", version="2.2.0")

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["GET"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def require_strelko_login(request: Request, call_next):
    if not auth_enabled():
        return await call_next(request)

    path = request.url.path
    if is_public_path(path):
        return await call_next(request)

    token = extract_bearer_token(request)
    if not token:
        if wants_html(request):
            return HTMLResponse(gate_html(), status_code=401)
        return JSONResponse(
            status_code=401,
            content={"detail": "Prijava na Strelko je obvezna."},
        )

    _, error = verify_strelko_token(token)
    if error == "unauthenticated":
        if wants_html(request):
            return HTMLResponse(gate_html(), status_code=401)
        return JSONResponse(
            status_code=401,
            content={"detail": "Neveljavna ali potekla seja. Prijavite se znova."},
        )
    if error == "forbidden":
        if wants_html(request):
            return HTMLResponse(gate_html(forbidden=True), status_code=403)
        return JSONResponse(
            status_code=403,
            content={"detail": "Nimate dostopa do arhiva."},
        )

    return await call_next(request)


def _with_source(response: Response, source: str) -> None:
    response.headers["X-Data-Source"] = source


def _static_url_prefix() -> str:
    """Absolutna pot do /static (brand.css, charts-shared.css) glede na WEB_BASE_PATH."""
    if WEB_BASE_PATH:
        return f"{WEB_BASE_PATH}/static"
    return "/static"


def _rewrite_static_asset_urls(html: str) -> str:
    """../static/ ali /static/ → /arhiv/static/ ko je embed za proxy pod /arhiv/."""
    prefix = _static_url_prefix()
    html = html.replace("../static/", f"{prefix}/")
    if WEB_BASE_PATH:
        html = html.replace('href="/static/', f'href="{prefix}/')
        html = html.replace("href='/static/", f"href='{prefix}/")
    return html


def _prepare_html(html: str, *, inject_private: bool = False) -> str:
    html = _rewrite_static_asset_urls(html)
    scripts: list[str] = []
    if auth_enabled():
        scripts.append(
            "<script>"
            "function strelkoAuthHeaders(){"
            "const t=localStorage.getItem('strelko_token');"
            "return t?{Authorization:'Bearer '+t}:{};"
            "}"
            "</script>\n"
        )
    if WEB_BASE_PATH:
        scripts.append(
            "<script>"
            f"window.STRELE_WEB_BASE=window.STRELE_API_BASE={json.dumps(WEB_BASE_PATH)};"
            "</script>\n"
        )
    if inject_private:
        private_key = get_private_api_key()
        if private_key:
            scripts.append(
                f"<script>window.STRELE_PRIVATE_API_KEY={json.dumps(private_key)};</script>\n"
            )
    if scripts:
        html = html.replace("</head>", "".join(scripts) + "</head>", 1)
    return html


def _serve_html(path: Path, *, inject_private: bool = False) -> Response:
    html = _prepare_html(path.read_text(encoding="utf-8"), inject_private=inject_private)
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/api/health")
def api_health(response: Response) -> dict:
    settings = get_settings()
    db_ok = False
    dnevi = 0
    try:
        with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM strele_si_dnevno")
                dnevi = cur.fetchone()[0]
        db_ok = True
        db_detail = "connected"
    except Exception as exc:
        db_detail = str(exc)

    archive = get_archive_info()
    _with_source(response, "local")

    ingest_age_min: float | None = None
    ingest_ok = False
    heartbeat = ROOT / ".ingest-heartbeat"
    if heartbeat.is_file():
        try:
            import time

            age_s = time.time() - float(heartbeat.read_text(encoding="utf-8").strip())
            ingest_age_min = round(age_s / 60, 1)
            # Dovoljeno do 2× poll intervala + rezerva
            ingest_ok = age_s <= max(900, settings.poll_interval_sec * 2 + 120)
        except (OSError, ValueError):
            pass

    return {
        "ok": db_ok,
        "database": db_detail if db_ok else db_detail,
        "vir_podatkov": "lokalna_baza",
        "dnevi_v_arhivu": dnevi,
        "arhiv": archive,
        "ingest_ok": ingest_ok,
        "ingest_age_min": ingest_age_min,
    }


@app.get("/api/archive-info")
def api_archive_info(response: Response) -> dict:
    info = get_archive_info()
    _with_source(response, "local")
    return info


@app.get("/api/latest-date")
def api_latest_date(response: Response) -> dict:
    try:
        latest, source = get_latest_date()
        _with_source(response, source)
        return {"datum": latest.isoformat() if latest else None, "source": source}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/si-daily")
def api_si_daily(
    response: Response,
    days: int = Query(30, ge=1, le=365),
) -> list[dict]:
    try:
        data, source = get_si_daily(days)
        _with_source(response, source)
        return data
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/si-hourly")
def api_si_hourly(
    response: Response,
    day: date | None = Query(None, description="Datum (YYYY-MM-DD)"),
    days: int | None = Query(None, ge=1, le=365, description="Agregat zadnjih N dni"),
) -> list[dict]:
    if day is None and days is None:
        raise HTTPException(status_code=422, detail="Podaj day ali days")
    data, source = get_si_hourly(day, days=days)
    _with_source(response, source)
    return data


@app.get("/api/regije-daily")
def api_regije_daily(
    response: Response,
    day: date | None = Query(None, description="Datum (YYYY-MM-DD)"),
    days: int | None = Query(None, ge=1, le=365, description="Agregat zadnjih N dni"),
) -> list[dict]:
    if day is None and days is None:
        raise HTTPException(status_code=422, detail="Podaj day ali days")
    data, source = get_regije(day, days=days)
    _with_source(response, source)
    return data


if not PUBLIC_ONLY:

    @app.get("/api/obcine-top")
    def api_obcine_top(
        response: Response,
        day: date | None = Query(None, description="Datum (YYYY-MM-DD)"),
        days: int | None = Query(None, ge=1, le=365, description="Agregat zadnjih N dni"),
        limit: int = Query(10, ge=1, le=50),
        _: None = Depends(require_private_api_key),
    ) -> list[dict]:
        if day is None and days is None:
            raise HTTPException(status_code=422, detail="Podaj day ali days")
        data, source = get_obcine_top(day, days=days, limit=limit)
        _with_source(response, source)
        return data

    @app.get("/api/obcine-gostota")
    def api_obcine_gostota(
        response: Response,
        day: date | None = Query(None, description="Datum (YYYY-MM-DD)"),
        days: int | None = Query(None, ge=1, le=365, description="Agregat zadnjih N dni"),
        limit: int = Query(10, ge=1, le=50),
        _: None = Depends(require_private_api_key),
    ) -> list[dict]:
        if day is None and days is None:
            raise HTTPException(status_code=422, detail="Podaj day ali days")
        data, source = get_obcine_gostota_top(day, days=days, limit=limit)
        _with_source(response, source)
        return data

    @app.get("/api/obcine-map")
    def api_obcine_map(
        response: Response,
        day: date = Query(..., description="Datum (YYYY-MM-DD)"),
        _: None = Depends(require_private_api_key),
    ) -> list[dict]:
        try:
            data, source = get_obcine_map(day)
            _with_source(response, source)
            return data
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/")
    def index() -> Response:
        return _serve_html(WEB_DIR / "index.html", inject_private=True)

    @app.get("/map")
    def map_page() -> Response:
        return _serve_html(WEB_DIR / "map.html", inject_private=True)

else:

    @app.get("/")
    def index_public() -> Response:
        return _serve_html(WEB_DIR / "public" / "index.html")


@app.get("/public")
@app.get("/public/")
def public_page() -> Response:
    return _serve_html(WEB_DIR / "public" / "index.html")


@app.get("/public/embed")
@app.get("/embed")
def embed_page() -> Response:
    return _serve_html(WEB_DIR / "public" / "embed.html")


@app.get("/public/data/{filename}")
def public_data(filename: str) -> Response:
    path = WEB_DIR / "public" / "data" / filename
    if not path.is_file() or path.resolve().parent != (WEB_DIR / "public" / "data").resolve():
        raise HTTPException(status_code=404, detail="Datoteka ne obstaja")
    media = "application/json" if filename.endswith(".json") else "application/octet-stream"
    return Response(
        content=path.read_bytes(),
        media_type=media,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/public/embed.js")
def embed_script() -> Response:
    return Response(
        content=(WEB_DIR / "public" / "embed.js").read_text(encoding="utf-8"),
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )


app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def main() -> None:
    import uvicorn

    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", "8080"))
    mode = "javno" if PUBLIC_ONLY else "polno"
    print(f"Strele arhiv ({mode}): http://127.0.0.1:{port}")
    if not PUBLIC_ONLY:
        print(f"  Javna stran: http://127.0.0.1:{port}/public")
    if host == "0.0.0.0":
        print(f"  (v omrežju: http://<tvoj-ip>:{port})")
    uvicorn.run(
        "strele_archive.server:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
