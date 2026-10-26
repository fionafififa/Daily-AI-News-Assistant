"""Offload synchronous database work and drain it before propagating cancellation."""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def call_store(function, *args, **kwargs):
    operation = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        # Cancelling an await cannot stop a SQL call already running in a thread.
        while not operation.done():
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not operation.cancelled() and (error := operation.exception()) is not None:
            logger.error("Storage operation failed during cancellation: %s", type(error).__name__)
        raise
