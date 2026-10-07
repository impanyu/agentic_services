"""Run with python -m agentic_services.niche_agent.worker (separate process)."""
import asyncio
import os
import time

from ..config import Settings
from .runtime import AgentConfig, run_once
from .store import ManagerStore


async def serve():
    settings = Settings.from_environment()
    store = ManagerStore(settings.database_path)
    config = AgentConfig.environment()
    if not settings.openai_api_key:
        raise RuntimeError('OPENAI_API_KEY is not configured')
    with store.connect() as db:
        db.execute("UPDATE manager_events SET status='cancelled' WHERE kind='research.request' AND status='pending'")
        db.execute("UPDATE niche_research SET status='cancelled',updated=? WHERE result IS NULL AND status IN ('queued','waiting_for_budget')",(time.time(),))
    next_maintenance=0
    while True:
        with store.connect() as db:
            db.execute('INSERT OR REPLACE INTO manager_worker_health VALUES(1,?)', (time.time(),))
        if os.getenv('NICHE_AGENT_ENABLED') == '1':
            if time.time()>=next_maintenance:
                from .websub import maintain
                await maintain(store)
                store.prune_research()
                next_maintenance=time.time()+60
            store.schedule(config.tick_seconds)
            await run_once(store, settings.openai_api_key, config)
        await asyncio.sleep(5)


if __name__ == '__main__':
    asyncio.run(serve())
