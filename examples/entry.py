from typing import Protocol

from edgekit import Request, Response, WorkerEntrypoint
from edgekit.bindings import D1Database, StaticAssets


class Env(Protocol):
    ASSETS: StaticAssets
    DB: D1Database


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request: Request) -> Response:
        ok = await self.env.DB.prepare("select 1 as ok").first("ok", type=int)
        asset = await self.env.ASSETS.fetch("/hello.txt")
        return Response.json(
            {
                "ok": ok if ok is not None else 0,
                "asset": await asset.read_text(),
            }
        )
    
