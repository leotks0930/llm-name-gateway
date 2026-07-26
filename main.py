import asyncio
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import aiosqlite
import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-gateway")

# ---------- 環境變數 ----------
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
DEFAULT_TARGET_BASE_URL = os.environ.get("DEFAULT_TARGET_BASE_URL")
DB_PATH = os.environ.get("DB_PATH", "/data/ai-gateway.db")

security = HTTPBasic(auto_error=False)

EXCLUDED_HEADERS = {
    "host",
    "connection",
    "content-length",
    "content-encoding",
    "transfer-encoding",
    "accept-encoding",
}

# ---------- 生命週期：初始化資料庫與 httpx client ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            source_model TEXT,
            target_model TEXT,
            target_base_url TEXT NOT NULL,
            api_key TEXT,
            add_headers TEXT,
            priority INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            method TEXT,
            path TEXT,
            client_ip TEXT,
            model_in TEXT,
            model_out TEXT,
            target_url TEXT,
            status_code INTEGER,
            error TEXT
        );
        """
    )
    await db.commit()
    await db.close()

    # 全局 httpx client，支援 connection pool 與 stream
    app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))
    yield
    await app.state.client.aclose()


app = FastAPI(
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------- 工具函式 ----------
async def get_mapping(source_model: Optional[str]) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # 1. 精確比對 model 名稱
        if source_model:
            cur = await db.execute(
                "SELECT * FROM mappings WHERE source_model = ? "
                "ORDER BY priority DESC, id DESC LIMIT 1",
                (source_model,),
            )
            row = await cur.fetchone()
            await cur.close()
            if row:
                return dict(row)

        # 2. 萬用 / 預設規則
        cur = await db.execute(
            "SELECT * FROM mappings WHERE source_model IS NULL OR source_model = '' OR source_model = '*' "
            "ORDER BY priority DESC, id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row else None


async def log_request(
    method: str,
    path: str,
    client_ip: str,
    model_in: Optional[str],
    model_out: Optional[str],
    target_url: str,
    status_code: int,
    error: str,
):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO logs (method, path, client_ip, model_in, model_out, target_url, status_code, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (method, path, client_ip, model_in, model_out, target_url, status_code, error),
            )
            await db.commit()
    except Exception as exc:
        logger.error("Log write error: %s", exc)


# ---------- 後台驗證 ----------
async def admin_auth(credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    if not ADMIN_PASSWORD:
        return None
    if (
        not credentials
        or credentials.username != ADMIN_USERNAME
        or not secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials


# ---------- 後台 Web GUI ----------
@app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def admin_page(request: Request):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM mappings ORDER BY priority DESC, id DESC")
        mappings = [dict(r) for r in await cur.fetchall()]
    return templates.TemplateResponse(
        request=request, 
        name="admin.html", 
        context={"request": request, "mappings": mappings}
    )


@app.get("/admin/logs", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
async def logs_page(request: Request):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 200")
        logs = [dict(r) for r in await cur.fetchall()]
    return templates.TemplateResponse(
        request=request, 
        name="logs.html", 
        context={"request": request, "logs": logs}
    )


@app.post("/admin/api/mappings", dependencies=[Depends(admin_auth)])
async def create_mapping(
    name: str = Form(""),
    source_model: str = Form(""),
    target_model: str = Form(""),
    target_base_url: str = Form(...),
    api_key: str = Form(""),
    add_headers: str = Form(""),
    priority: int = Form(0),
):
    if add_headers.strip():
        try:
            json.loads(add_headers)
        except Exception:
            raise HTTPException(status_code=400, detail="add_headers 必須是合法 JSON")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO mappings (name, source_model, target_model, target_base_url, api_key, add_headers, priority) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                name or None,
                source_model or None,
                target_model or None,
                target_base_url,
                api_key or None,
                add_headers or None,
                priority,
            ),
        )
        await db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/api/mappings/{mapping_id}/delete", dependencies=[Depends(admin_auth)])
async def delete_mapping(mapping_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM mappings WHERE id = ?", (mapping_id,))
        await db.commit()
    return RedirectResponse("/admin", status_code=303)


# ---------- 核心 Proxy（catch-all）----------
@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def proxy(request: Request, path: str):
    # 根目錄直接導到後台
    if not path and request.method == "GET":
        return RedirectResponse("/admin")

    body_bytes = await request.body()
    content_type = request.headers.get("content-type", "")

    model_in: Optional[str] = None
    model_out: Optional[str] = None
    mapping: Optional[dict] = None

    # 對 JSON 請求做 model 改寫
    if body_bytes and "application/json" in content_type:
        try:
            payload = json.loads(body_bytes)
            if isinstance(payload, dict):
                model_in = payload.get("model")
                mapping = await get_mapping(model_in)
                if mapping and mapping["target_model"]:
                    model_out = mapping["target_model"]
                    payload["model"] = model_out
                    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                elif mapping:
                    model_out = model_in
        except Exception:
            pass
    else:
        # 非 JSON（音訊/影像/純二進位）仍可取萬用規則決定上游與 key
        mapping = await get_mapping(None)

    # 決定上游 Base URL
    target_base = mapping["target_base_url"] if mapping and mapping["target_base_url"] else DEFAULT_TARGET_BASE_URL
    if not target_base:
        return JSONResponse(
            {"error": "找不到上游目標，請設定 DEFAULT_TARGET_BASE_URL 或對應規則"},
            status_code=502,
        )

    target_url = target_base.rstrip("/") + request.url.path
    if request.url.query:
        target_url += "?" + request.url.query

    # 組轉發 Headers
    headers = {}
    for k, v in request.headers.items():
        if k.lower() not in EXCLUDED_HEADERS:
            headers[k] = v

    if mapping and mapping["api_key"]:
        headers["authorization"] = f"Bearer {mapping['api_key']}"

    if mapping and mapping.get("add_headers"):
        try:
            headers.update(json.loads(mapping["add_headers"]))
        except Exception:
            pass

    try:
        client: httpx.AsyncClient = request.app.state.client
        req = client.build_request(request.method, target_url, headers=headers, content=body_bytes or b"")
        resp = await client.send(req, stream=True)

        # 記錄（非同步丟著，不阻塞 response）
        asyncio.create_task(
            log_request(
                request.method,
                request.url.path,
                request.client.host if request.client else "",
                model_in,
                model_out,
                target_url,
                resp.status_code,
                "",
            )
        )

        out_headers = {
            k: v for k, v in resp.headers.items() if k.lower() not in EXCLUDED_HEADERS
        }

        return StreamingResponse(
            resp.aiter_bytes(),
            status_code=resp.status_code,
            headers=out_headers,
            media_type=resp.headers.get("content-type"),
        )

    except Exception as exc:
        asyncio.create_task(
            log_request(
                request.method,
                request.url.path,
                request.client.host if request.client else "",
                model_in,
                model_out,
                target_url,
                0,
                str(exc),
            )
        )
        return JSONResponse({"error": "轉發失敗", "detail": str(exc)}, status_code=502)