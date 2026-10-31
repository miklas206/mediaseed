import asyncio
from mediaseed.tracker_reads import TrackerReads


def test_reads_share_inflight_cache_is_bounded_and_fresh_revalidation_bypasses_cache():
    async def scenario():
        cache=TrackerReads();calls=[]
        async def fetch():
            calls.append(True);await asyncio.sleep(.01);return {'result':[1]}
        a={'api_key':'a'}
        result=await asyncio.gather(*(cache.read(a,'/filter',{'name':'test'},fetch,30) for _ in range(20)))
        assert len(calls)==1
        result[0]['result'].append(2)
        assert result[1]['result']==[1]
        assert await cache.read(a,'/filter',{'name':'test'},fetch,30)=={'result':[1]}
        await cache.read(a,'/filter',{'name':'test'},fetch,0)
        assert len(calls)==2
        await cache.read({'api_key':'b'},'/filter',{'name':'test'},fetch,30)
        assert len(calls)==3
        for i in range(65):await cache.read(a,'/filter',{'name':i},fetch,30)
        assert len(cache.saved)==64
        await cache.close()
        assert not cache.pending and not cache.saved
    asyncio.run(scenario())


def test_cancelled_waiter_does_not_cancel_shared_read_and_errors_are_not_cached():
    async def scenario():
        cache=TrackerReads();gate=asyncio.Event();calls=[]
        async def fetch():calls.append(True);await gate.wait();return {'ok':True}
        first=asyncio.create_task(cache.read({'api_key':'a'},'/user',{},fetch))
        other=asyncio.create_task(cache.read({'api_key':'a'},'/user',{},fetch))
        await asyncio.sleep(.01);first.cancel()
        try:await first
        except asyncio.CancelledError:pass
        gate.set();assert await other=={'ok':True};assert len(calls)==1
        async def failed():raise ValueError('synthetic')
        for _ in range(2):
            try:await cache.read({'api_key':'a'},'/failed',{},failed,30)
            except ValueError:pass
        assert not cache.saved
        await cache.close()
    asyncio.run(scenario())
