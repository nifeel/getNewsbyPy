import sqlite3

from app.services.translate_api import api_body
from app.services.translate_tmt import (
    TranslateRequestError,
    TmtQuotaError,
    _split_by_request_limit,
    _translate_chunk,
    chunked,
    fill_title_zh_for_news_ids,
    is_quota_error,
    normalize_texts,
)


def test_chunked_splits_evenly() -> None:
    assert chunked([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]


def test_chunked_keeps_remainder() -> None:
    assert chunked(["a", "b", "c"], 2) == [["a", "b"], ["c"]]


def test_chunked_empty() -> None:
    assert chunked([], 40) == []


def test_normalize_texts_wraps_string() -> None:
    assert normalize_texts("hello") == ["hello"]


def test_normalize_texts_keeps_list() -> None:
    assert normalize_texts(["a", "b"]) == ["a", "b"]


def test_normalize_texts_rejects_empty_and_bad_types() -> None:
    try:
        normalize_texts([])
        assert False
    except TranslateRequestError:
        pass
    try:
        normalize_texts(["ok", "  "])
        assert False
    except TranslateRequestError:
        pass
    try:
        normalize_texts(1)
        assert False
    except TranslateRequestError:
        pass


def test_split_by_request_limit_breaks_on_chars() -> None:
    chunks = _split_by_request_limit(["a" * 3000, "b" * 3000])
    assert len(chunks) == 2


def test_fill_title_zh_empty_ids_does_nothing() -> None:
    assert fill_title_zh_for_news_ids(None, []) == 0


def test_fill_title_zh_writes_only_requested_news_ids() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE news (
            id INTEGER PRIMARY KEY,
            NewsID INTEGER NOT NULL,
            Title TEXT NOT NULL DEFAULT '',
            title_zh TEXT
        )
        """
    )
    connection.execute("INSERT INTO news (NewsID, Title) VALUES (11, 'Hello world')")
    connection.execute("INSERT INTO news (NewsID, Title) VALUES (22, 'Leave me')")
    connection.commit()

    done = fill_title_zh_for_news_ids(
        connection,
        [11],
        translate=lambda texts, source="en", target="zh": ["你好世界"],
    )
    assert done == 1
    assert connection.execute("SELECT title_zh FROM news WHERE NewsID = 11").fetchone()[0] == "你好世界"
    assert connection.execute("SELECT title_zh FROM news WHERE NewsID = 22").fetchone()[0] is None


def test_fill_title_zh_skips_already_translated() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE news (
            id INTEGER PRIMARY KEY,
            NewsID INTEGER NOT NULL,
            Title TEXT NOT NULL DEFAULT '',
            title_zh TEXT
        )
        """
    )
    connection.execute(
        "INSERT INTO news (NewsID, Title, title_zh) VALUES (11, 'Hello', '已有')"
    )
    connection.commit()

    def boom(texts, source="en", target="zh"):
        raise AssertionError("already translated rows must not call TMT")

    assert fill_title_zh_for_news_ids(connection, [11], translate=boom) == 0
    assert connection.execute("SELECT title_zh FROM news WHERE NewsID = 11").fetchone()[0] == "已有"


def test_is_quota_error_detects_free_amount() -> None:
    assert is_quota_error("FailedOperation.NoFreeAmount", "本月免费额度已用完")
    assert is_quota_error("LimitExceeded", "")
    assert not is_quota_error("UnsupportedOperation.TextTooLong", "too long")


def test_translate_chunk_switches_after_quota() -> None:
    calls: list[str] = []

    def fake_batch(texts, secret_id, secret_key, region, source, target):
        calls.append(secret_id)
        if secret_id == "id-1":
            raise TmtQuotaError("FailedOperation.NoFreeAmount", "used up")
        return [f"{item}-zh" for item in texts]

    out, index = _translate_chunk(
        ["hello"],
        "en",
        "zh",
        [("id-1", "k1"), ("id-2", "k2")],
        0,
        "ap-guangzhou",
        fake_batch,
    )
    assert out == ["hello-zh"]
    assert index == 1
    assert calls == ["id-1", "id-2"]


def test_translate_chunk_raises_when_all_keys_exhausted() -> None:
    def fake_batch(texts, secret_id, secret_key, region, source, target):
        raise TmtQuotaError("FailedOperation.NoFreeAmount", "used up")

    try:
        _translate_chunk(
            ["hello"],
            "en",
            "zh",
            [("id-1", "k1"), ("id-2", "k2")],
            0,
            "ap-guangzhou",
            fake_batch,
        )
        assert False
    except TmtQuotaError:
        pass


def test_api_body_keeps_data_out_of_message() -> None:
    payload = api_body(200, "ok", {"items": [{"text": "a", "translated": "啊"}]})
    assert payload["code"] == 200
    assert payload["message"] == "ok"
    assert payload["data"]["items"][0]["translated"] == "啊"
