# EdgeKit: Cloudflare Workers Python-native SDK

EdgeKit には 2 つの役割があります。

1. Workers Python を書くための、小さくて Python-first な API を提供すること。
2. プロジェクトを解析し、tree-shaking と依存関係の vendoring を行って、ランタイムバンドルを構築すること。

## What EdgeKit Provides

- 型付き Worker のための `WorkerEntrypoint[Env]`
- Workers Web API を wrap する型安全な `Request`、`Response`、`Headers`、`URL`
- 以下に対応する typed-binding:
  - static assets
  - D1
  - KV
  - R2
  - Queues
  - Durable Objects
- フレームワーク統合のための `WSGI` および `ASGI` adapters
- 以下のための CLI:
  - プロジェクトの解析
  - tree-shaking 及び vendoring に対するリスクチェック
  - bundle の出力
  - report の生成

## 要件

- Python `>=3.12`
- 依存関係管理のための `uv`
- 開発およびデプロイのための wrangler CLI

## ローカル開発

リポジトリのルートで Python と Node の依存関係をインストールします。

```bash
uv sync
```

よく使うコマンド:

```bash
edgekit analyze
edgekit doctor
edgekit build
npm run dev
```

ルートの `wrangler.jsonc` で `wrangler dev` の前に `edgekit build` を呼び出すよう設定してください。

## Quick Start

まず新しいプロジェクトを初期化します。

```bash
uv run pywrangler init
cd <your-project>
uv add edgekit
```

最小限の Worker の形は次のようになります。

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

`pyproject.toml` で Worker のエントリを記述します。

```toml
[tool.edgekit.builder]
entry = "src/app.py"
compatibility_date = "2026-04-13"
```

続いて、ビルド済み出力を使うように wrangler を設定します。

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

実行:

```bash
wrangler dev
```

## Typed Environment Bindings

EdgeKit では、Workers の `env` オブジェクトを型付き Protocol またはクラスに bind できます。

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

`edgekit.runtime` は 2 つの helper を公開しています。

- `current_env([EnvType])`
  - 現在のリクエストスコープにおけるアクティブな env オブジェクトを返します。
  - WSGI や ASGI のような adapters がフレームワーク内部のコードから現在の Worker env にアクセスできるようにするために使われます。
- `await_sync(awaitable)`
  - Pyodide のランタイムバインディングを通じて awaitable を同期的に実行します。

例:

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
