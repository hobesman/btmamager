"""FastAPI web UI: status, run now, settings."""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..core.config import EDITABLE, ConfigError, ConfigStore, get_store
from ..core.manager import Manager

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
security = HTTPBasic(realm="btmamager")


def human_size(n: int | None) -> str:
    if n is None:
        return "–"
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return str(n)


def iso(ts: float | None) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else ""


templates.env.filters["size"] = human_size
templates.env.filters["iso"] = iso


def create_app(store: ConfigStore | None = None, manager: Manager | None = None) -> FastAPI:
    store = store or get_store()
    boot = store.current()
    if not boot.web_password:
        raise RuntimeError("WEB_PASSWORD must be set to run the web UI")
    mgr = manager or Manager(store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        mgr.start()
        try:
            yield
        finally:
            await mgr.stop()

    app = FastAPI(title="btmamager", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.state.manager = mgr
    app.state.store = store

    def auth(creds: HTTPBasicCredentials = Depends(security)) -> str:
        ok_user = secrets.compare_digest(creds.username.encode(), boot.web_user.encode())
        ok_pass = secrets.compare_digest(creds.password.encode(), boot.web_password.encode())
        if not (ok_user and ok_pass):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized",
                                headers={"WWW-Authenticate": 'Basic realm="btmamager"'})
        return creds.username

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"ok": True}

    r = APIRouter(dependencies=[Depends(auth)])

    def ctx(request: Request, **kw):
        return {"request": request, "s": store.current(), "mgr": mgr, **kw}

    # ---- status ------------------------------------------------------------------
    @r.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "status.html", ctx(request, last=mgr.last,
                                                                      history=list(mgr.history)))

    @r.post("/run")
    async def run_now():
        await mgr.run_once()
        return RedirectResponse("/", status_code=303)

    # ---- settings ----------------------------------------------------------------
    def _fields(values: dict) -> list[dict]:
        return [{"f": f, "value": values.get(f.name), "locked": store.is_locked(f.name)} for f in EDITABLE]

    def _settings_page(request: Request, values: dict, errors: dict | None = None, saved: bool = False,
                       code: int = 200):
        return templates.TemplateResponse(request, "settings.html", ctx(
            request, fields=_fields(values), errors=errors or {}, saved=saved), status_code=code)

    @r.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request, saved: bool = False):
        return _settings_page(request, store.current().model_dump(), saved=saved)

    @r.post("/settings", response_class=HTMLResponse)
    async def settings_save(request: Request):
        form = {k: str(v) for k, v in (await request.form()).items()}
        try:
            store.save(form)
        except ConfigError as e:
            values = {**store.current().model_dump(), **form}
            return _settings_page(request, values, errors=e.errors, code=400)
        mgr.wake()
        return RedirectResponse("/settings?saved=1", status_code=303)

    @r.post("/settings/test", response_class=HTMLResponse)
    async def settings_test(request: Request):
        form = {k: str(v) for k, v in (await request.form()).items()}
        try:
            ok, msg = await mgr.test_connection(store.preview(form))
        except ConfigError as e:
            ok, msg = False, str(e)
        return templates.TemplateResponse(request, "_test.html", {"request": request, "ok": ok, "msg": msg})

    app.include_router(r)
    return app


def run() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    store = get_store()
    uvicorn.run(create_app(store), host="0.0.0.0", port=store.current().web_port, proxy_headers=True)
