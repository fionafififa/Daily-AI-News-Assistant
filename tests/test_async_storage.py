import asyncio
import threading

import pytest

from app.async_storage import call_store
from app.service import NewsService


async def test_storage_work_runs_outside_event_loop_and_drains_on_cancel():
    started = threading.Event()
    release = threading.Event()
    finished = []
    loop_thread = threading.get_ident()

    def operation():
        assert threading.get_ident() != loop_thread
        started.set()
        assert release.wait(timeout=5)
        finished.append("committed")

    task = asyncio.create_task(call_store(operation))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished == ["committed"]
    finally:
        release.set()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)


async def test_cancelled_request_does_not_abandon_accepted_task(settings, store, user, monkeypatch):
    service = NewsService(settings, store)
    started = threading.Event()
    release = threading.Event()
    executed = []
    original_new_run = store.new_run

    def reserve(*args):
        run = original_new_run(*args)
        started.set()
        assert release.wait(timeout=5)
        return run

    async def execute(run_id):
        executed.append(run_id)

    monkeypatch.setattr(store, "new_run", reserve)
    monkeypatch.setattr(service, "execute", execute)
    request = asyncio.create_task(service.enqueue(user["id"]))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        release.set()
        await asyncio.gather(*list(service.reservations))
        await asyncio.gather(*list(service.tasks))
        assert executed == [store.runs(user["id"])[0]["id"]]
    finally:
        release.set()
        await service.close()


async def test_shutdown_waits_for_reservation_and_marks_it_interrupted(settings, store, user, monkeypatch):
    service = NewsService(settings, store)
    started = threading.Event()
    release = threading.Event()
    original_new_run = store.new_run

    def reserve(*args):
        run = original_new_run(*args)
        started.set()
        assert release.wait(timeout=5)
        return run

    monkeypatch.setattr(store, "new_run", reserve)
    request = asyncio.create_task(service.enqueue(user["id"]))
    shutdown = None
    try:
        assert await asyncio.to_thread(started.wait, 3)
        shutdown = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        assert not shutdown.done()
        release.set()
        await shutdown
        assert await request is None
        run = store.runs(user["id"])[0]
        assert run["status"] == "failed"
        assert run["delivery_status"] == "not_sent"
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)
        if shutdown is not None:
            await shutdown
        await service.close()
