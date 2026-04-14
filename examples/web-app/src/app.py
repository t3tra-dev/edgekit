from typing import Protocol

from flask import Flask, Blueprint, render_template, request as req

from edgekit.adapters import WSGI
from edgekit.bindings import StaticAssets, D1Database
from edgekit.runtime import await_sync, current_env


class Env(Protocol):
    ASSETS: StaticAssets
    DB: D1Database


flask_app = Flask(__name__, template_folder="templates")
api = Blueprint("api", __name__)

_SCHEMA_SQL = (
    "CREATE TABLE IF NOT EXISTS request_log ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "method TEXT NOT NULL, "
    "path TEXT NOT NULL"
    ");"
)


@flask_app.route("/")
def index():
    return render_template("root.html")


@api.route("/db", methods=["GET", "POST"])
def db_access():
    env = current_env(Env)
    await_sync(env.DB.exec(_SCHEMA_SQL))

    rows_affected = 0
    if req.method == "POST":
        insert_result = await_sync(
            env.DB.prepare("INSERT INTO request_log (method, path) VALUES (?, ?)")
            .bind(req.method, req.path)
            .run()
        )
        rows_affected = insert_result.rows_affected

    total = await_sync(env.DB.prepare("SELECT COUNT(*) AS total FROM request_log").first("total", type=int)) or 0
    rows = await_sync(
        env.DB.prepare(
            "SELECT id, method, path FROM request_log ORDER BY id DESC LIMIT 5"
        ).all()
    )
    return {
        "ok": True,
        "rows_affected": rows_affected,
        "total": total,
        "rows": rows,
    }


@api.route("/hello")
def hello_asset():
    env = current_env(Env)
    res = await_sync(env.ASSETS.fetch("/hello.txt"))
    return (
        await_sync(res.read_text()),
        res.status,
        res.headers.to_dict(),
    )


flask_app.register_blueprint(api, url_prefix="/api")


class Default(WSGI[Env]):
    app = flask_app

if __name__ == "__main__":
    flask_app.run(debug=True)
