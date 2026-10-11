"""Request-scoped image connection reuse, never a global credential-bearing client."""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import httpx

@dataclass
class Pool:
    client: httpx.AsyncClient | None = None
    users: int = 0
    closed: bool = False

_active: ContextVar[Pool | None] = ContextVar('photo_image_transport',default=None)

@asynccontextmanager
async def image_scope():
    pool=_active.get()
    if pool is None or pool.closed:
        pool=Pool()
    token=_active.set(pool);pool.users+=1
    try:
        yield pool
    finally:
        pool.users-=1
        try:
            # Shielded panorama tasks may still own a lease after the caller exits.
            if pool.users==0:
                pool.closed=True
                if pool.client is not None:await pool.client.aclose()
        finally:
            _active.reset(token)

@asynccontextmanager
async def image_transport():
    async with image_scope() as pool:
        if pool.client is None:
            pool.client=httpx.AsyncClient(timeout=25,follow_redirects=False,
                limits=httpx.Limits(max_connections=64,max_keepalive_connections=32))
        yield pool.client

def pooled_images(operation):
    @wraps(operation)
    async def wrapped(*args,**kwargs):
        async with image_scope():
            return await operation(*args,**kwargs)
    return wrapped
