import asyncio
from contextlib import suppress

from mediaseed.background import run_background


def test_slow_job_does_not_delay_rss_and_cancellation_stops_children():
    async def check():
        entered, release, progressed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = 0
        active = 0
        peak = 0
        cancelled = False
        async def covers():
            nonlocal cancelled
            entered.set()
            try:
                await release.wait()
            finally:
                cancelled = True
        async def rss():
            nonlocal calls, active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.002)
            calls += 1
            active -= 1
            if calls >= 3:
                progressed.set()
        async def failed():
            raise ValueError("synthetic-secret-url")
        task = asyncio.create_task(run_background([("covers", covers), ("rss", rss), ("failed", failed)], interval=.002))
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(progressed.wait(), 1)
        assert not release.is_set() and peak == 1
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        assert cancelled
        previous = calls
        await asyncio.sleep(.01)
        assert calls == previous
    asyncio.run(check())
