from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Article, Base
from app.schemas import ArticleOut, EventOut
from app.translate import (
    apply_event_title_zh,
    apply_title_zh,
    has_cjk,
    needs_translate,
    parse_translate_payload,
    translate_texts,
)


def test_needs_translate_skips_chinese() -> None:
    assert not needs_translate("谷歌避免拆分广告业务", "en")
    assert not needs_translate("Hello", "zh")
    assert needs_translate("Google avoids a breakup", "en")
    assert has_cjk("谷歌")


def test_parse_translate_payload_accepts_code_200() -> None:
    out = parse_translate_payload(
        {"code": 200, "message": "ok", "data": {"items": [{"text": "Hi", "translated": "你好"}]}}
    )
    assert out == ["你好"]


def test_parse_translate_payload_accepts_code_0() -> None:
    out = parse_translate_payload(
        {"code": 0, "message": "ok", "data": {"items": [{"text": "Hi", "translated": "你好"}]}}
    )
    assert out == ["你好"]


def test_translate_texts_posts_local_api_shape() -> None:
    captured: dict = {}

    def post(_url: str, payload: dict) -> object:
        captured["payload"] = payload
        return {
            "code": 200,
            "message": "ok",
            "data": {"items": [{"text": "Hello", "translated": "你好"}]},
        }

    assert translate_texts(["Hello"], post=post) == ["你好"]
    assert captured["payload"]["q"] == ["Hello"]
    assert captured["payload"]["source"] == "en"
    assert captured["payload"]["target"] == "zh"


def test_apply_title_zh_caches_and_skips_second_call() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    calls = {"n": 0}

    def translate(texts: list[str], source: str = "en", target: str = "zh") -> list[str]:
        calls["n"] += 1
        return ["谷歌避免拆分广告业务" for _ in texts]

    now = datetime(2026, 9, 3, 4, 0, tzinfo=UTC)
    item = ArticleOut(
        id=9,
        source_id="hacker-news",
        source_name="Hacker News",
        title="Google avoids a breakup of its ad tech business",
        url="https://example.com/a",
        summary="",
        published_at=now,
        fetched_at=now,
        lang="en",
        kind="news",
    )
    apply_title_zh(session, [item], translate=translate)
    assert item.title == "谷歌避免拆分广告业务"
    assert calls["n"] == 1

    again = ArticleOut(**{**item.model_dump(), "title": "Google avoids a breakup of its ad tech business"})
    apply_title_zh(session, [again], translate=translate)
    assert again.title == "谷歌避免拆分广告业务"
    assert calls["n"] == 1
    session.close()


def test_apply_title_zh_writes_local_article_column() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    now = datetime(2026, 9, 3, 4, 0, tzinfo=UTC)
    row = Article(
        source_id="techcrunch",
        title="Uber is laying off 10% of staff",
        url="https://example.com/u",
        summary="",
        published_at=now,
        fetched_at=now,
        url_hash="a" * 64,
        title_hash="b" * 64,
        lang="en",
        kind="news",
    )
    session.add(row)
    session.commit()
    item = ArticleOut(
        id=row.id,
        source_id=row.source_id,
        source_name="TechCrunch",
        title=row.title,
        url=row.url,
        summary="",
        published_at=now,
        fetched_at=now,
        lang="en",
        kind="news",
    )
    apply_title_zh(
        session,
        [item],
        translate=lambda texts, source="en", target="zh": ["优步裁员百分之十"],
    )
    session.refresh(row)
    assert row.title_zh == "优步裁员百分之十"
    session.close()


def test_apply_event_title_zh_caches_and_skips_second_call() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    calls = {"n": 0}

    def translate(texts: list[str], source: str = "en", target: str = "zh") -> list[str]:
        calls["n"] += 1
        return ["堪萨斯城开发者大会" for _ in texts]

    item = EventOut(
        id="techconf-kcdc-2026",
        title="Kansas City Developer Conference",
        start_date="2026-08-10",
        end_date="2026-08-12",
        location="Kansas US",
        url="https://techconf.directory/conferences/kcdc",
        status="confirmed",
        category="conference",
        note="in-person",
        timezone="UTC",
        source="techconf-directory",
    )
    apply_event_title_zh(session, [item], translate=translate)
    assert item.title == "堪萨斯城开发者大会"
    assert calls["n"] == 1

    again = EventOut(**{**item.model_dump(), "title": "Kansas City Developer Conference"})
    apply_event_title_zh(session, [again], translate=translate)
    assert again.title == "堪萨斯城开发者大会"
    assert calls["n"] == 1
    session.close()
