import asyncio
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from .agent import run_demo, run_live
from .async_storage import call_store
from .delivery import send_email
from .storage import utcnow
from .tools import Toolbox

logger = logging.getLogger(__name__)


class NewsService:
    def __init__(self, settings, store, live_runner=None):
        self.settings, self.store = settings, store
        self.live_runner = live_runner or run_live
        self.tasks = set()
        self.reservations = set()
        self.semaphore = asyncio.Semaphore(2)
        self.stopping = False

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.task_done)
        return task

    def task_done(self, task):
        self.tasks.discard(task)
        self.reservations.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("Background task failed: %s", type(error).__name__)

    async def enqueue(self, user_id, trigger="manual", scheduled_date=None):
        if self.stopping:
            return None
        reservation = asyncio.create_task(self.reserve_run(user_id, trigger, scheduled_date))
        self.reservations.add(reservation)
        reservation.add_done_callback(self.task_done)
        # Once accepted, reserving and starting the run belongs to the service.
        return await asyncio.shield(reservation)

    async def reserve_run(self, user_id, trigger, scheduled_date):
        if self.stopping:
            return None
        run = await call_store(self.store.new_run, user_id, self.settings.mode, trigger, scheduled_date)
        return await self.start_reserved_run(run)

    async def start_reserved_run(self, run):
        if run:
            if self.stopping:
                await call_store(
                    self.store.update_run,
                    run["id"],
                    status="failed",
                    completed_at=utcnow(),
                    error="服务关闭，生成任务中断。",
                    delivery_status="not_sent",
                )
                return None
            self.spawn(self.execute(run["id"]))
        return run

    async def execute(self, run_id):
        toolbox = None
        generated = False
        try:
            async with self.semaphore:
                run = await call_store(self.store.run, run_id)
                user = await call_store(self.store.user, run["user_id"])
                await call_store(self.store.update_run, run_id, status="running")
                toolbox = await call_store(Toolbox, self.settings, self.store, run, user)
                runner = run_demo if run["mode"] == "demo" else self.live_runner
                brief = await asyncio.wait_for(runner(toolbox), timeout=self.settings.agent_timeout)
                toolbox.active = False
                await call_store(
                    self.store.update_run, run_id, status="completed", completed_at=utcnow(), brief=brief
                )
                generated = True
                await self.deliver(run_id)
        except asyncio.CancelledError:
            if (await call_store(self.store.run, run_id))["status"] != "completed":
                await call_store(
                    self.store.update_run,
                    run_id,
                    status="failed",
                    completed_at=utcnow(),
                    error="服务关闭，生成任务中断。",
                    delivery_status="not_sent",
                )
            raise
        except Exception as exc:
            # SDK exceptions can include request details. Never persist credentials or raw provider errors.
            if isinstance(exc, TimeoutError):
                error = "Agent 执行超时，可查看工具记录后重试。"
            elif isinstance(exc, ValueError):
                error = str(exc)[:400]
            else:
                error = f"{type(exc).__name__}：生成失败，请检查模型配置、网络或工具记录。"
            if generated:
                await call_store(
                    self.store.update_run, run_id, delivery_status="failed", delivery_error=error
                )
            else:
                await call_store(
                    self.store.update_run,
                    run_id,
                    status="failed",
                    completed_at=utcnow(),
                    error=error,
                    delivery_status="not_sent",
                )
        finally:
            if toolbox:
                toolbox.active = False

    async def deliver(self, run_id):
        try:
            if not await call_store(self.store.claim_delivery, run_id):
                return
            run = await call_store(self.store.run, run_id)
            user = await call_store(self.store.user, run["user_id"])
            # Persist inbox first; disconnected browsers retrieve notifications on reconnect.
            await call_store(self.store.notify, user["id"], run_id, run["brief"]["title"])
            if user["channel"] == "email":
                await asyncio.to_thread(send_email, self.settings, user, run)
            await call_store(self.store.update_run, run_id, delivery_status="sent")
        except asyncio.CancelledError:
            current = await call_store(self.store.run, run_id)
            if current and current["delivery_status"] == "sending":
                await call_store(
                    self.store.update_run,
                    run_id,
                    delivery_status="unknown",
                    delivery_error="推送中断，请先核对收件箱。",
                )
            raise
        except Exception as exc:
            await call_store(
                self.store.update_run,
                run_id,
                delivery_status="failed",
                delivery_error=f"{type(exc).__name__}：邮件发送失败；简报已保存在站内，可单独重试推送。",
            )

    async def tick(self, now=None):
        now = now or datetime.now(UTC)
        for user in await call_store(self.store.users):
            local = now.astimezone(ZoneInfo(user["timezone"]))
            if user["enabled"] and local.strftime("%H:%M") >= user["delivery_time"]:
                await self.enqueue(user["id"], trigger="scheduled", scheduled_date=local.date().isoformat())

    async def scheduler(self):
        while not self.stopping:
            try:
                await self.tick()
            except Exception as exc:
                logger.error("Scheduler tick failed: %s", type(exc).__name__)
            await asyncio.sleep(30)

    async def close(self):
        self.stopping = True
        if self.reservations:
            await asyncio.gather(*list(self.reservations), return_exceptions=True)
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
