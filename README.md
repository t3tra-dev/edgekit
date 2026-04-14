# EdgeKit: Cloudflare Workers Python-native SDK

EdgeKit has two jobs:

1. Provide a small Python-first API for writing Workers Python.
2. Build a runtime bundle by analyzing your project, tree-shaking, vendoring dependencies.

## What EdgeKit Provides

- `WorkerEntrypoint[Env]` for typed Worker classes
- `Request`, `Response`, `Headers`, and `URL` wrappers around the Workers Web APIs
- Typed binding wrappers for:
  - static assets
  - D1
  - KV
  - R2
  - Queues
  - Durable Objects
- `WSGI` and `ASGI` adapters for framework integration
- A CLI for:
  - project analysis
  - risk checks for tree-shaking and bundling
  - bundle emission
  - report rendering

## Requirements

- Python `>=3.12`
- `uv` for dependency management
- wrangler CLI for development and deployment

## Local Development

Install Python and Node dependencies in the repository root:

```bash
uv sync
```

Useful commands:

```bash
edgekit analyze
edgekit doctor
edgekit build
npm run dev
```

The root `wrangler.jsonc` should be configured to call `edgekit build` before `wrangler dev`.

## Quick Start

Initialize a new project first:

```bash
uvx --from workers-py==1.9.1 pywrangler init # the latest version of workers-py, v1.9.2, is causing errors at the moment
cd <your-project>
uv add edgekit
```

The minimal Worker shape looks like this:

```python
from edgekit import Request, Response, WorkerEntrypoint


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        return Response.json(
            {
                "ok": True,
                "method": request.method,
                "pathname": request.url.pathname,
            }
        )
```

Set the Worker entrypoint in `pyproject.toml`:

```toml
[tool.edgekit.builder]
entry = "src/app.py"
compatibility_date = "2026-04-13"
```

Then point Wrangler at the built output:

```jsonc
{
  "main": "build/edgekit/wrangler/python_modules/app.py",
  "compatibility_date": "2026-04-13",
  "compatibility_flags": ["python_workers"],
  "build": {
    "command": "uv run edgekit build",
  },
}
```

Run:

```bash
wrangler dev
```

## Typed Environment Bindings

EdgeKit can bind the Workers `env` object to a typed Python protocol or class.

```python
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
```

## Runtime Helpers

`edgekit.runtime` exports two helpers:

- `current_env([EnvType])`
  - Returns the active env object for the current request scope.
  - Used by adapters such as WSGI and ASGI to expose the current Worker env inside framework code.
- `await_sync(awaitable)`
  - Runs an awaitable synchronously via Pyodide runtime bindings.

Example:

```python
from typing import Protocol

from edgekit.adapters import WSGI
from edgekit.bindings import StaticAssets
from edgekit.runtime import await_sync, current_env


class Env(Protocol):
    ASSETS: StaticAssets


def read_asset_text(path: str) -> str:
    env = current_env(Env)
    response = await_sync(env.ASSETS.fetch(path))
    return await_sync(response.read_text())
```

## License

MIT License
