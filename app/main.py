import asyncio
import json
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import ROOT, Settings
from .schemas import Subscription
from .service import NewsService
from .sources import SOURCES
from .storage import Store


def create_app(settings=None):
    settings = settings or Settings.load()
    store = Store(settings.data_dir)
    service = NewsService(settings, store)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        if settings.mode == "demo" and not store.users():
            store.save_user(Subscription(name="示例订阅", excludes=["融资"], enabled=False).model_dump())
        if settings.scheduler_enabled:
            service.spawn(service.scheduler())
        yield
        await service.close()

    app = FastAPI(title="每日 AI 新闻助手", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.service, app.state.settings = store, service, settings
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_app_guard(request: Request, call_next):
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "不允许跨站修改本地订阅"}, status_code=403)
            if int(request.headers.get("content-length", "0")) > 100_000:
                return JSONResponse({"detail": "请求内容过大"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def require_user(user_id):
        user = store.user(user_id)
        if not user:
            raise HTTPException(404, "订阅不存在")
        return user

    def require_run(run_id):
        run = store.run(run_id)
        if not run:
            raise HTTPException(404, "任务不存在")
        return run

    def check_channel(profile):
        if profile.channel == "email" and not (settings.smtp_host and settings.smtp_from):
            raise HTTPException(400, "邮件推送需要先在 .env 配置 SMTP_HOST 和 SMTP_FROM")

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": settings.mode}

    @app.get("/api/status")
    def status():
        return {
            "mode": settings.mode,
            "model": settings.model if settings.mode == "live" else None,
            "model_ready": settings.mode == "demo" or bool(settings.api_key),
            "smtp_ready": bool(settings.smtp_host and settings.smtp_from),
            "scheduler_enabled": settings.scheduler_enabled,
            "sources": [{"id": k, "name": v["name"], "url": v["url"]} for k, v in SOURCES.items()],
        }

    @app.get("/api/users")
    def users():
        return store.users()

    @app.post("/api/users", status_code=201)
    def create_user(profile: Subscription):
        check_channel(profile)
        return store.save_user(profile.model_dump())

    @app.put("/api/users/{user_id}")
    def update_user(user_id: str, profile: Subscription):
        require_user(user_id)
        check_channel(profile)
        return store.save_user(profile.model_dump(), user_id)

    @app.post("/api/users/{user_id}/generate", status_code=202)
    async def generate(user_id: str):
        require_user(user_id)
        if settings.mode == "live" and not settings.api_key:
            raise HTTPException(503, "真实模式尚未配置 OPENAI_API_KEY；请修改 .env 后重启。")
        run = service.enqueue(user_id)
        if not run:
            raise HTTPException(409, "已有任务正在生成，请等待完成")
        return run

    @app.get("/api/users/{user_id}/runs")
    def runs(user_id: str):
        require_user(user_id)
        return store.runs(user_id)

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str):
        return {**require_run(run_id), "traces": store.traces(run_id)}

    @app.post("/api/runs/{run_id}/retry-delivery", status_code=202)
    async def retry_delivery(run_id: str):
        run = require_run(run_id)
        if run["status"] != "completed" or run["delivery_status"] not in {"failed", "unknown"}:
            raise HTTPException(409, "此任务不需要重试推送")
        service.spawn(service.deliver(run_id))
        return {"message": "正在重试推送；不会重新生成简报。"}

    @app.get("/api/users/{user_id}/events")
    async def events(user_id: str, request: Request, after: int = 0):
        require_user(user_id)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            cursor = after

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                messages = store.notifications(user_id, cursor)
                for message in messages:
                    cursor = message["id"]
                    yield f"id: {cursor}\nevent: brief\ndata: {json.dumps(message, ensure_ascii=False)}\n\n"
                if not messages:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/")
    def index():
        return FileResponse(ROOT / "app/static/index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "app/static"), name="static")
    return app
