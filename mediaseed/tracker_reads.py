"""Bounded read sharing; mutations and import revalidation never use TTL results."""
import asyncio
import copy
import hashlib
import time
from collections import OrderedDict
from mediaseed.tracker_sites import identity


class TrackerReads:
    def __init__(self):
        self.saved = OrderedDict()
        self.pending = {}
        self.client = None
        self.blocked = {}
        self.slots = asyncio.Semaphore(2)

    async def read(self, account, path, params, fetch, ttl=0):
        key = (identity(account), path,
               tuple(sorted((str(k), str(v)) for k, v in (params or {}).items())))
        cached = self.saved.get(key)
        if ttl and cached and 0 <= time.monotonic()-cached[0] < ttl:
            self.saved.move_to_end(key)
            return copy.deepcopy(cached[1])
        task = self.pending.get(key)
        if task is None:
            async def run():
                async with self.slots:
                    result = await fetch()
                if ttl:
                    self.saved[key] = (time.monotonic(), result)
                    self.saved.move_to_end(key)
                    while len(self.saved) > 64:
                        self.saved.popitem(last=False)
                return result
            task = asyncio.create_task(run())
            self.pending[key] = task
            def done(result):
                if self.pending.get(key) is result:
                    self.pending.pop(key, None)
                if not result.cancelled():
                    result.exception()
            task.add_done_callback(done)
        return copy.deepcopy(await asyncio.shield(task))

    async def close(self):
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
        self.saved.clear()
        self.blocked.clear()
        if self.client:
            await self.client.aclose()
            self.client = None
