"""Independent, non-overlapping background jobs with structured cancellation."""
import asyncio
import logging


async def run_background(jobs, interval=15):
    async def repeat(name, job):
        while True:
            try:
                await job()
            except Exception as error:  # noqa: BLE001 - isolate and report a failed background job
                # Do not log exception messages: tracker URLs can contain secrets.
                logging.getLogger(__name__).warning("Background job %s failed (%s)", name, type(error).__name__)
            await asyncio.sleep(interval)

    async with asyncio.TaskGroup() as group:
        for name, job in jobs:
            group.create_task(repeat(name, job), name="mediaseed-"+name)
