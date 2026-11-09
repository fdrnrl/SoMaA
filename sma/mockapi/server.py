"""Тестовый API: Flask-сервер, который ведет себя как API платформ (раздел 9).

Адреса: /{platform}/api/v1/analytics/{endpoint}, где platform — ege, oge, vpr, niko.
Данные — снимок из папки набора генератора. Поведение по разделу 6.1: токен (401),
только GET (405), неверный параметр (400), since, сортировка, страницы, обертка.

Управляющие адреса /_admin/... (раздел 9.3) включаются только при MOCK_ADMIN=1
и требуют свой токен MOCK_ADMIN_TOKEN.

Запуск:
    python -m sma.mockapi.server --data data/generated/medium --port 8000
"""

import argparse
import copy
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request

from sma import contract
from sma.contract import INT, STRING
from sma.loader.sources import FileSource, api_order, is_after
from sma.timeutil import parse_datetime, utc_now

logger = logging.getLogger(__name__)

API_PREFIX = "/<platform>/api/v1/analytics/<path:endpoint>"
LIST_ENDPOINTS = list(contract.ENDPOINT_FIELDS)
LIVE_STATUS = "live-status"
NO_SINCE = {"dictionaries/regions", "dictionaries/schools", "dictionaries/subjects"}
MAX_LIMIT = 1000
MODES = ("normal", "down", "slow", "broken_json", "no_wrapper")
BAD_KINDS = ("missing_field", "pii_field", "wrong_type", "bad_enum")


class MockState:
    """Данные и настройки сервера. Все изменения — под одной блокировкой (сервер многопоточный)."""

    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """Исходное состояние: данные из файлов, режим normal, пустой журнал запросов."""
        manifest = json.loads((self.data_root / "manifest.json").read_text(encoding="utf-8"))
        records: dict[str, dict[str, dict[str, dict]]] = {}
        live: dict[str, dict] = {}
        exam_end: dict[str, datetime] = {}
        clock: dict[str, datetime] = {}
        for info in manifest["platforms"].values():
            platform = info["folder"]
            source = FileSource(self.data_root / platform)
            records[platform] = {
                endpoint: {item["record_id"]: item for item in source.read_items(endpoint)}
                for endpoint in LIST_ENDPOINTS
            }
            live[platform] = source.read_live_status()
            exam_end[platform] = parse_datetime(info["exam_end"])
            clock[platform] = parse_datetime(info["snapshot_at"])
        with self.lock:
            self.records = records
            self.live = live
            self.exam_end = exam_end
            self.clock = clock  # «текущее время» платформы: в снимке — момент снимка
            self.modes = {platform: {"mode": "normal", "delay_sec": 0.0} for platform in records}
            self.requests: list[dict] = []
            self.injected = 0
            self.generation = getattr(self, "generation", 0) + 1  # номер «эпохи» после reset

    def platform_now(self, platform: str) -> datetime:
        return self.clock[platform]


def error(status: int, message: str) -> Response:
    response = jsonify({"error": message})
    response.status_code = status
    return response


def bearer(header: str | None) -> str | None:
    """Токен из заголовка Authorization: Bearer <токен>."""
    if header and header.startswith("Bearer "):
        return header[len("Bearer ") :]
    return None


def parse_params(endpoint: str) -> tuple[datetime | None, int, int]:
    """since, page, limit из строки запроса. Неверное значение → ValueError с текстом для 400."""
    since = None
    raw_since = request.args.get("since")
    if raw_since is not None and endpoint not in NO_SINCE:
        try:
            since = parse_datetime(raw_since)
        except ValueError as problem:
            raise ValueError(f"since: {problem}") from problem
    try:
        page = int(request.args.get("page", "1"))
        limit = int(request.args.get("limit", str(MAX_LIMIT)))
    except ValueError as problem:
        raise ValueError("page и limit — целые числа") from problem
    if page < 1:
        raise ValueError("page начинается с 1")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit — от 1 до {MAX_LIMIT}")
    return since, page, limit


def make_bad_record(template: dict, endpoint: str, kind: str, key: str) -> tuple[dict, str]:
    """Копия записи с дефектом указанного вида. Возвращает запись и имя испорченного поля."""
    record = copy.deepcopy(template)
    fields = contract.ENDPOINT_FIELDS[endpoint]
    if kind == "missing_field":
        name = fields[0].name
        del record[name]
        return record, name
    if kind == "pii_field":
        record[key] = "synthetic-value@example.org"
        return record, key
    if kind == "wrong_type":
        for field in fields:
            if field.type == INT:
                record[field.name] = str(record[field.name])
                return record, field.name
            if field.type == STRING:
                record[field.name] = 12345
                return record, field.name
    if kind == "bad_enum":
        for field in fields:
            if field.enum is not None:
                record[field.name] = "not_in_enum"
                return record, field.name
    raise ValueError(f"дефект {kind} для адреса {endpoint} не поддерживается")


def create_app(
    data_root: Path,
    token: str,
    admin_enabled: bool = False,
    admin_token: str | None = None,
) -> Flask:
    """Создает приложение тестового API по набору генератора в data_root."""
    if not token:
        raise ValueError("Не задан токен тестового API (MOCK_API_TOKEN)")
    if admin_enabled and not admin_token:
        raise ValueError("MOCK_ADMIN=1, но не задан MOCK_ADMIN_TOKEN")

    app = Flask(__name__)
    app.json.sort_keys = False  # ключи — в порядке контракта
    app.json.ensure_ascii = False
    state = MockState(data_root)
    app.config["MOCK_STATE"] = state

    def log_request(
        platform: str, endpoint: str, status: int, arrived: float, generation: int
    ) -> None:
        """Пишет запрос в журнал. Время — момент прихода запроса, а не ответа.

        Запрос, начатый до /_admin/reset (например, «медленный»), в новый журнал не попадает.
        """
        with state.lock:
            if generation != state.generation:
                return
            state.requests.append(
                {
                    "time": utc_now().isoformat(),
                    "monotonic": arrived,
                    "platform": platform,
                    "endpoint": endpoint,
                    "since": request.args.get("since"),
                    "page": request.args.get("page"),
                    "limit": request.args.get("limit"),
                    "status": status,
                }
            )

    @app.get(API_PREFIX)
    def api(platform: str, endpoint: str) -> Response:
        arrived, generation = time.monotonic(), state.generation
        response = handle_api(platform, endpoint)
        log_request(platform, endpoint, response.status_code, arrived, generation)
        return response

    @app.route(API_PREFIX, methods=["POST", "PUT", "PATCH", "DELETE"])
    def not_allowed(platform: str, endpoint: str) -> Response:
        log_request(platform, endpoint, 405, time.monotonic(), state.generation)
        return error(405, "разрешен только GET")

    def handle_api(platform: str, endpoint: str) -> Response:
        if bearer(request.headers.get("Authorization")) != token:
            return error(401, "нужен заголовок Authorization: Bearer <токен>")
        if platform not in state.records:
            return error(404, f"нет платформы {platform}")
        if endpoint != LIVE_STATUS and endpoint not in LIST_ENDPOINTS:
            return error(404, f"нет адреса {endpoint}")

        with state.lock:
            mode = dict(state.modes[platform])
        if mode["mode"] == "down":
            return error(503, "платформа недоступна")
        if mode["mode"] == "slow":
            time.sleep(mode["delay_sec"])
        if mode["mode"] == "broken_json":
            return Response('{"page": 1, "items": [', status=200, mimetype="application/json")

        if endpoint == LIVE_STATUS:
            with state.lock:
                return jsonify(state.live[platform])

        try:
            since, page, limit = parse_params(endpoint)
        except ValueError as problem:
            return error(400, str(problem))

        with state.lock:
            items = list(state.records[platform][endpoint].values())
            exam_finished = state.platform_now(platform) >= state.exam_end[platform]
        if endpoint == "dictionaries/kim" and not exam_finished:
            items = []  # структура КИМ — только после окончания экзамена (6.10)
        if since is not None:
            items = [item for item in items if is_after(item, since)]
        items.sort(key=api_order)
        total_pages = max(1, -(-len(items) // limit))
        chunk = items[(page - 1) * limit : page * limit]
        if mode["mode"] == "no_wrapper":
            return jsonify(chunk)
        return jsonify({"page": page, "total_pages": total_pages, "items": chunk})

    if admin_enabled:
        register_admin(app, state, admin_token)
    return app


def register_admin(app: Flask, state: MockState, admin_token: str) -> None:
    """Управляющие адреса для тестов (раздел 9.3)."""

    @app.before_request
    def check_admin_token():
        if request.path.startswith("/_admin/"):
            if bearer(request.headers.get("Authorization")) != admin_token:
                return error(401, "нужен токен MOCK_ADMIN_TOKEN")
        return None

    def body() -> dict:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    @app.post("/_admin/mode")
    def set_mode():
        data = body()
        platform, mode = data.get("platform"), data.get("mode")
        if platform not in state.records or mode not in MODES:
            return error(400, f"нужны platform из {list(state.records)} и mode из {list(MODES)}")
        with state.lock:
            state.modes[platform] = {"mode": mode, "delay_sec": float(data.get("delay_sec", 0))}
        return jsonify(state.modes[platform])

    @app.post("/_admin/inject")
    def inject():
        data = body()
        platform, endpoint, record = data.get("platform"), data.get("endpoint"), data.get("record")
        if platform not in state.records or endpoint not in LIST_ENDPOINTS:
            return error(400, "нужны platform и endpoint")
        if not isinstance(record, dict) or not isinstance(record.get("record_id"), str):
            return error(400, "нужна запись record с record_id")
        with state.lock:
            state.records[platform][endpoint][record["record_id"]] = record
        return jsonify({"record_id": record["record_id"]})

    @app.post("/_admin/inject_bad")
    def inject_bad():
        data = body()
        platform, endpoint, kind = data.get("platform"), data.get("endpoint"), data.get("kind")
        if platform not in state.records or endpoint not in LIST_ENDPOINTS or kind not in BAD_KINDS:
            return error(400, f"нужны platform, endpoint и kind из {list(BAD_KINDS)}")
        with state.lock:
            existing = list(state.records[platform][endpoint].values())
            if not existing:
                return error(400, f"у {endpoint} нет записей, которые можно испортить")
            template = max(existing, key=api_order)
            newest = parse_datetime(template["updated_at"])
            state.injected += 1
            template = {
                **template,
                "record_id": f"bad-{kind}-{state.injected}",
                "updated_at": (newest + timedelta(seconds=state.injected)).isoformat(),
            }
            try:
                record, field = make_bad_record(template, endpoint, kind, data.get("key", "email"))
            except ValueError as problem:
                return error(400, str(problem))
            state.records[platform][endpoint][record["record_id"]] = record
        return jsonify({"record_id": record["record_id"], "field": field, "record": record})

    @app.get("/_admin/requests")
    def requests_log():
        with state.lock:
            return jsonify(list(state.requests))

    @app.post("/_admin/reset")
    def reset():
        state.reset()
        return jsonify({"status": "reset"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Тестовый API платформ СМА")
    parser.add_argument("--data", required=True, type=Path, help="папка набора генератора")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    load_dotenv()

    app = create_app(
        args.data,
        token=os.environ.get("MOCK_API_TOKEN", ""),
        admin_enabled=os.environ.get("MOCK_ADMIN", "0") == "1",
        admin_token=os.environ.get("MOCK_ADMIN_TOKEN"),
    )
    logger.info("Тестовый API: http://%s:%d, данные %s", args.host, args.port, args.data)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
