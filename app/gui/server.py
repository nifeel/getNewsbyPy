import json
import os
import signal
import subprocess
import sys
import threading
from collections import deque
from contextlib import closing
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _dependency_name(requirement: str) -> str:
    requirement = requirement.split(";", 1)[0].strip()
    for separator in ("[", "<", ">", "=", "!", "~"):
        if separator in requirement:
            requirement = requirement.split(separator, 1)[0]
    return requirement.strip()


def _load_project_dependencies() -> list[str]:
    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    if not pyproject_path.exists():
        return []

    try:
        import tomllib
    except ModuleNotFoundError:
        tomllib = None

    if tomllib is not None:
        with pyproject_path.open("rb") as file:
            project = tomllib.load(file).get("project", {})
        dependencies = project.get("dependencies", [])
        return [item for item in dependencies if isinstance(item, str)]

    dependencies: list[str] = []
    in_dependencies = False
    for raw_line in pyproject_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("dependencies"):
            in_dependencies = True
            continue
        if in_dependencies and line == "]":
            break
        if in_dependencies and line.startswith('"'):
            dependencies.append(line.split('"', 2)[1])
    return dependencies


def _ensure_project_dependencies() -> None:
    dependencies = _load_project_dependencies()
    missing = []
    for requirement in dependencies:
        name = _dependency_name(requirement)
        if not name:
            continue
        try:
            metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.append(requirement)

    if not missing:
        return

    print(f"Missing Python packages detected: {', '.join(missing)}", flush=True)
    command = [sys.executable, "-m", "pip", "install", *missing]
    print("Installing missing Python packages...", flush=True)
    try:
        subprocess.check_call(command, cwd=PROJECT_ROOT)
    except subprocess.CalledProcessError as exc:
        print(f"Failed to install Python packages. Command exited with {exc.returncode}.", flush=True)
        sys.exit(exc.returncode)


_ensure_project_dependencies()

from loguru import logger

from app.config import get_settings
from app.storage.database import connect, init_db
from app.storage.task_status import TaskStatusRepository, utc_now


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
CONTINUOUS_TASK = "continuous_collector"
RUN_ONCE_TASK = "startup_collector"
HISTORY_TASK = "history_sync_collector"


def row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: BaseHTTPRequestHandler, payload: str, content_type: str, status: int = 200) -> None:
    body = payload.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def safe_print(message: str) -> None:
    try:
        print(message, flush=True)
    except Exception:
        logger.info(message)


class TaskManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.processes: dict[str, subprocess.Popen[str]] = {}
        self.logs: deque[str] = deque(maxlen=300)

    def append_log(self, message: str) -> None:
        line = f"{utc_now()} {message}".strip()
        with self.lock:
            self.logs.append(line)

    def start(self, task_name: str, module_name: str, args: list[str] | None = None) -> dict[str, Any]:
        with self.lock:
            process = self.processes.get(task_name)
            if process and process.poll() is None:
                return {"ok": True, "message": f"{task_name} already running", "pid": process.pid}

            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONPATH"] = os.pathsep.join(
                item for item in [str(PROJECT_ROOT), env.get("PYTHONPATH", "")] if item
            )
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            command = [sys.executable, "-m", module_name]
            if args:
                command.extend(args)

            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                startupinfo=startupinfo,
            )
            self.processes[task_name] = process
            self.append_log(f"{task_name} started pid={process.pid}")
            threading.Thread(target=self._stream_output, args=(task_name, process), daemon=True).start()

        self._update_task_status(task_name, "running", pid=process.pid, last_started_at=utc_now(), last_error="")
        return {"ok": True, "message": f"{task_name} started", "pid": process.pid}

    def stop(self, task_name: str) -> dict[str, Any]:
        with self.lock:
            process = self.processes.get(task_name)

        if not process or process.poll() is not None:
            with self.lock:
                self.processes.pop(task_name, None)
            self._update_task_status(task_name, "stopped", last_finished_at=utc_now())
            return {"ok": True, "message": f"{task_name} is not running"}

        self.append_log(f"{task_name} stopping pid={process.pid}")
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

        self._update_task_status(task_name, "stopped", pid=process.pid, last_finished_at=utc_now())
        self.append_log(f"{task_name} stopped pid={process.pid}")
        with self.lock:
            self.processes.pop(task_name, None)
        return {"ok": True, "message": f"{task_name} stopped"}

    def list_logs(self) -> list[str]:
        with self.lock:
            return list(self.logs)

    def refresh_statuses(self) -> None:
        with self.lock:
            items = list(self.processes.items())

        for task_name, process in items:
            if process.poll() is not None:
                self._update_task_status(task_name, "stopped", pid=process.pid, last_finished_at=utc_now())
                with self.lock:
                    self.processes.pop(task_name, None)

    def _stream_output(self, task_name: str, process: subprocess.Popen[str]) -> None:
        if process.stdout:
            for line in process.stdout:
                self.append_log(f"{task_name}: {line.rstrip()}")
        return_code = process.wait()
        self.append_log(f"{task_name} exited code={return_code}")
        if return_code != 0 or not self._has_terminal_status(task_name):
            self._update_task_status(task_name, "stopped", pid=process.pid, last_finished_at=utc_now())
        with self.lock:
            self.processes.pop(task_name, None)

    def _update_task_status(self, task_name: str, status: str, **values: object) -> None:
        settings = get_settings()
        with closing(connect(settings.database_file)) as connection:
            init_db(connection)
            TaskStatusRepository(connection).upsert(task_name, status, **values)

    def _has_terminal_status(self, task_name: str) -> bool:
        settings = get_settings()
        with closing(connect(settings.database_file)) as connection:
            init_db(connection)
            row = TaskStatusRepository(connection).get(task_name)
        return bool(row and row["status"] in {"completed", "error", "stopped"})


TASK_MANAGER = TaskManager()


def get_stats() -> dict[str, Any]:
    settings = get_settings()
    with closing(connect(settings.database_file)) as connection:
        init_db(connection)
        total = connection.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        today = connection.execute(
            "SELECT COUNT(*) FROM news WHERE date(COALESCE(DatePublished, created_at)) = date('now')"
        ).fetchone()[0]
        latest = connection.execute(
            """
            SELECT NewsID AS external_id, Title AS title, DatePublished AS published_at,
                   fetched_at, FCName AS source, Level AS category
            FROM news
            ORDER BY COALESCE(DatePublished, fetched_at, created_at) DESC
            LIMIT 1
            """
        ).fetchone()
        categories = connection.execute(
            """
            SELECT COALESCE(NULLIF(Level, ''), 'uncategorized') AS category, COUNT(*) AS count
            FROM news
            GROUP BY COALESCE(NULLIF(Level, ''), 'uncategorized')
            ORDER BY count DESC
            LIMIT 8
            """
        ).fetchall()
        sources = connection.execute(
            """
            SELECT COALESCE(NULLIF(FCName, ''), 'FinancialJuice') AS source, COUNT(*) AS count
            FROM news
            GROUP BY COALESCE(NULLIF(FCName, ''), 'FinancialJuice')
            ORDER BY count DESC
            LIMIT 8
            """
        ).fetchall()

    return {
        "total": total,
        "today": today,
        "latest": row_to_dict(latest),
        "categories": [row_to_dict(row) for row in categories],
        "sources": [row_to_dict(row) for row in sources],
    }


def list_news(query: dict[str, list[str]]) -> dict[str, Any]:
    settings = get_settings()
    limit = min(int(query.get("limit", ["80"])[0] or 80), 200)
    search = (query.get("q", [""])[0] or "").strip()
    category = (query.get("category", [""])[0] or "").strip()

    clauses: list[str] = []
    params: list[Any] = []
    if search:
        clauses.append("(Title LIKE ? OR Description LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if category:
        clauses.append("Level = ?")
        params.append(category)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT id, NewsID AS external_id, Title AS title, Description AS content,
               FCName AS source, Level AS category, EURL AS url,
               DatePublished AS published_at, fetched_at, created_at
        FROM news
        {where_sql}
        ORDER BY COALESCE(DatePublished, fetched_at, created_at) DESC
        LIMIT ?
    """
    params.append(limit)

    with closing(connect(settings.database_file)) as connection:
        init_db(connection)
        rows = connection.execute(sql, params).fetchall()
    return {"items": [row_to_dict(row) for row in rows]}


def get_news_detail(news_id: str) -> dict[str, Any]:
    settings = get_settings()
    with closing(connect(settings.database_file)) as connection:
        init_db(connection)
        row = connection.execute("SELECT * FROM news WHERE id = ?", (news_id,)).fetchone()
    if not row:
        return {}
    item = row_to_dict(row)
    item["external_id"] = item.get("NewsID")
    item["title"] = item.get("Title")
    item["content"] = item.get("Description")
    item["source"] = item.get("FCName")
    item["category"] = item.get("Level")
    item["url"] = item.get("EURL")
    item["published_at"] = item.get("DatePublished")
    try:
        item["raw_json"] = json.loads(item["raw_json"])
    except (TypeError, json.JSONDecodeError):
        pass
    return item


def list_task_statuses() -> dict[str, Any]:
    TASK_MANAGER.refresh_statuses()
    settings = get_settings()
    with closing(connect(settings.database_file)) as connection:
        init_db(connection)
        statuses = [row_to_dict(row) for row in TaskStatusRepository(connection).list_all()]
    return {"items": statuses, "logs": TASK_MANAGER.list_logs()}


def remember_history_since(since: str, interval_seconds: int) -> None:
    settings = get_settings()
    with closing(connect(settings.database_file)) as connection:
        init_db(connection)
        TaskStatusRepository(connection).upsert(
            HISTORY_TASK,
            "configured",
            target_since=since,
            message=f"last requested history sync interval={interval_seconds}s",
        )


class GuiHandler(BaseHTTPRequestHandler):
    server_version = "FinancialJuiceGUI/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        if parsed.path == "/static/app.css":
            return self._serve_file(STATIC_DIR / "app.css", "text/css; charset=utf-8")
        if parsed.path == "/static/app.js":
            return self._serve_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
        if parsed.path == "/api/stats":
            return json_response(self, get_stats())
        if parsed.path == "/api/news":
            return json_response(self, list_news(parse_qs(parsed.query)))
        if parsed.path.startswith("/api/news/"):
            item = get_news_detail(parsed.path.rsplit("/", 1)[-1])
            status = 200 if item else 404
            return json_response(self, item or {"error": "not found"}, status)
        if parsed.path == "/api/tasks":
            return json_response(self, list_task_statuses())
        return json_response(self, {"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/tasks/continuous/start":
            return json_response(self, TASK_MANAGER.start(CONTINUOUS_TASK, "app.collectors.continuous_collector"))
        if parsed.path == "/api/tasks/continuous/stop":
            return json_response(self, TASK_MANAGER.stop(CONTINUOUS_TASK))
        if parsed.path == "/api/tasks/startup/run":
            return json_response(self, TASK_MANAGER.start(RUN_ONCE_TASK, "app.collectors.startup_collector"))
        if parsed.path == "/api/tasks/history/start":
            payload = self._read_json_body()
            since = str(payload.get("since") or "").strip()
            interval_seconds = int(payload.get("interval_seconds") or 60)
            if not since:
                return json_response(self, {"error": "since is required"}, HTTPStatus.BAD_REQUEST)
            remember_history_since(since, interval_seconds)
            args = ["--since", since, "--interval-seconds", str(interval_seconds)]
            return json_response(self, TASK_MANAGER.start(HISTORY_TASK, "app.collectors.history_sync_collector", args))
        if parsed.path == "/api/tasks/history/stop":
            return json_response(self, TASK_MANAGER.stop(HISTORY_TASK))
        return json_response(self, {"error": "not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        if len(args) >= 2:
            try:
                status_code = int(str(args[1]))
            except ValueError:
                status_code = 0
            if 0 < status_code < 400:
                return
        try:
            message = format % args
        except TypeError:
            message = " ".join([format, *(str(arg) for arg in args)])
        logger.warning(message)

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            return json_response(self, {"error": "not found"}, HTTPStatus.NOT_FOUND)
        text_response(self, path.read_text(encoding="utf-8"), content_type)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}


def main() -> None:
    settings = get_settings()
    with closing(connect(settings.database_file)) as connection:
        init_db(connection)

    server = ThreadingHTTPServer((settings.gui_host, settings.gui_port), GuiHandler)
    url = f"http://{settings.gui_host}:{settings.gui_port}"
    safe_print(f"FinancialJuice GUI running at {url}")
    logger.info("FinancialJuice GUI running at {}", url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        TASK_MANAGER.stop(CONTINUOUS_TASK)
        TASK_MANAGER.stop(RUN_ONCE_TASK)
        TASK_MANAGER.stop(HISTORY_TASK)
        server.server_close()


if __name__ == "__main__":
    main()
