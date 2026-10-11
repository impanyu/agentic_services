import asyncio
import pytest
from agentic_services.photo_scout import image_transport as transport

@pytest.fixture
def clients(monkeypatch):
    made=[]
    class Client:
        def __init__(self,**kwargs):self.closed=False;made.append(self)
        async def aclose(self):self.closed=True
    monkeypatch.setattr(transport.httpx,'AsyncClient',Client)
    return made

def test_nested_downloads_share_one_client_and_close_after_all_leases(clients):
    async def run():
        async with transport.image_scope():
            async def borrow():
                async with transport.image_transport() as client:
                    await asyncio.sleep(.001)
                    assert not client.closed
                    return client
            downloaded=await asyncio.gather(*(borrow() for _ in range(64)))
            assert all(client is downloaded[0] for client in downloaded)
            assert not clients[0].closed
        assert clients[0].closed
    asyncio.run(run());assert len(clients)==1

def test_warm_scoring_scope_does_not_create_unused_http_client(clients):
    @transport.pooled_images
    async def warm():return 'already scored'
    assert asyncio.run(warm())=='already scored';assert clients==[]

def test_independent_requests_do_not_share_clients_or_context(clients):
    @transport.pooled_images
    async def request():
        async with transport.image_transport() as client:
            await asyncio.sleep(.001);return client
    async def run():return await asyncio.gather(request(),request())
    a,b=asyncio.run(run());assert a is not b and a.closed and b.closed

def test_shielded_panorama_keeps_connection_until_it_finishes(clients):
    async def run():
        opened=asyncio.Event();finish=asyncio.Event()
        async with transport.image_scope():
            async def panorama():
                async with transport.image_transport() as client:
                    opened.set();await finish.wait();assert not client.closed
            child=asyncio.create_task(panorama());await opened.wait()
        assert not clients[0].closed
        finish.set();await child;assert clients[0].closed
        async with transport.image_transport() as replacement:assert replacement is not clients[0]
    asyncio.run(run());assert len(clients)==2

def test_failures_close_scope_and_cannot_leak_into_next_request(clients):
    @transport.pooled_images
    async def broken():
        async with transport.image_transport():raise ValueError('bad image')
    with pytest.raises(ValueError):asyncio.run(broken())
    assert clients[0].closed
    async def next_request():
        async with transport.image_transport() as client:return client
    assert asyncio.run(next_request()) is not clients[0]
