from __future__ import annotations

import asyncio

from .broker import RunBroker
from .database import Database
from .settings import get_settings


async def check_dependencies() -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    broker = RunBroker.from_settings(settings)
    try:
        async with asyncio.timeout(3):
            checks = [database.ping(), broker.ping()]
            if settings.dense_retrieval_mode != "off":
                checks.append(database.vector_ping())
            await asyncio.gather(*checks)
    finally:
        await broker.close()
        await database.close()


def run() -> None:
    asyncio.run(check_dependencies())


if __name__ == "__main__":
    run()
