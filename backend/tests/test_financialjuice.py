from datetime import UTC, datetime

from app.fj_read import _filters, merge_news_page, to_article_out
from app.ingest.fetchers.financialjuice import (
    _NEWS_SELECT,
    display_title,
    news_url,
    parse_fj_datetime,
    parse_news_id,
    row_from_mapping,
    to_raw_item,
)
from app.schemas import ArticleOut


def test_news_select_is_contract_columns_not_star() -> None:
    assert "*" not in _NEWS_SELECT
    for column in (
        "newsid",
        "title",
        "title_zh",
        "datepublished",
        "description",
        "fcname",
        "level",
        "eurl",
    ):
        assert column in _NEWS_SELECT.split(", ")


def test_prefers_title_zh_and_falls_back_to_title() -> None:
    zh = row_from_mapping(
        {
            "NewsID": 11,
            "Title": "Fed holds",
            "title_zh": "美联储按兵不动",
            "DatePublished": "2026-09-03 10:00:00",
            "Description": "body",
            "FCName": "Reuters",
            "Level": "High",
        }
    )
    assert zh is not None
    assert display_title(zh) == "美联储按兵不动"
    item = to_raw_item(zh)
    assert item is not None
    assert item.lang == "zh"
    assert item.replace_existing
    assert parse_news_id(item.url) == 11

    pending = row_from_mapping(
        {
            "newsid": 12,
            "title": "Fed holds",
            "title_zh": None,
            "datepublished": "2026-09-03 10:00:00",
            "description": "body",
            "fcname": "Reuters",
            "level": "High",
        }
    )
    assert pending is not None
    assert display_title(pending) == "Fed holds"
    en_item = to_raw_item(pending)
    assert en_item is not None
    assert en_item.lang == "en"


def test_article_out_keeps_level_and_origin() -> None:
    item = to_article_out(
        {
            "NewsID": 21,
            "Title": "CPI beat",
            "title_zh": "CPI 超预期",
            "DatePublished": "2026-09-03 10:00:00",
            "Description": "body",
            "FCName": "Reuters",
            "Level": "active active-critical",
        }
    )
    assert item is not None
    assert item.level == "active active-critical"
    assert item.origin == "Reuters"
    assert "Reuters" not in item.summary
    assert item.url == news_url(21)


def test_uses_eurl_as_article_link() -> None:
    expected = (
        "https://www.financialjuice.com/News/9748082/"
        "Chinese-central-bank-chiefs-remarks-at-G20.aspx"
    )
    item = to_article_out(
        {
            "id": 106024,
            "newsid": 9748082,
            "title": "Chinese central bank chief’s remarks at G20",
            "title_zh": "中国央行行长在G20上的讲话",
            "datepublished": "2026-09-03 08:16:00",
            "description": "body",
            "fcname": "South China Morning Post",
            "level": "",
            "eurl": expected,
        }
    )
    assert item is not None
    assert item.id == 9748082
    assert item.url == expected
    assert parse_news_id(item.url) == 9748082
    mapped = row_from_mapping(
        {
            "newsid": 9748082,
            "title": "Chinese central bank chief’s remarks at G20",
            "title_zh": None,
            "datepublished": "2026-09-03 08:16:00",
            "description": "body",
            "fcname": "South China Morning Post",
            "level": "",
            "eurl": expected,
        }
    )
    assert mapped is not None
    raw = to_raw_item(mapped)
    assert raw is not None
    assert raw.url == expected
    assert parse_news_id("https://www.financialjuice.com/home?newsId=11") == 11
    assert news_url(9748082) == "https://www.financialjuice.com/News/9748082"


def test_level_filter_sql() -> None:
    where_empty, params_empty = _filters(None, None, "__empty__")
    assert "btrim(level) = ''" in where_empty
    assert params_empty == []
    where_exact, params_exact = _filters(None, None, "active")
    assert "btrim(level) = %s" in where_exact
    assert params_exact == ["active"]
    where_range, params_range = _filters(
        None, None, "active", "2026-09-01T16:00:00", "2026-09-02T16:00:00"
    )
    assert "datepublished >= %s" in where_range
    assert "datepublished < %s" in where_range
    assert params_range == ["active", "2026-09-01T16:00:00", "2026-09-02T16:00:00"]


def test_parse_naive_datepublished_as_utc() -> None:
    parsed = parse_fj_datetime("2026-09-03T01:45:11.43")
    assert parsed is not None
    assert parsed == datetime(2026, 9, 3, 1, 45, 11, 430000, tzinfo=UTC)


def _out(item_id: int, source: str, minute: int) -> ArticleOut:
    ts = datetime(2026, 9, 3, 12, minute, tzinfo=UTC)
    return ArticleOut(
        id=item_id,
        source_id=source,
        source_name=source,
        title=str(item_id),
        url=f"https://example.com/{item_id}",
        summary="",
        published_at=ts,
        fetched_at=ts,
        lang="zh",
        kind="news",
    )


def test_merge_news_page_interleaves_by_time() -> None:
    local = [_out(1, "36kr", 1), _out(2, "36kr", 3)]
    fj = [_out(9, "financialjuice", 2), _out(8, "financialjuice", 4)]
    first = merge_news_page(local, fj, offset=0, limit=2)
    assert [item.id for item in first] == [8, 2]
    second = merge_news_page(local, fj, offset=2, limit=2)
    assert [item.id for item in second] == [9, 1]


def test_merge_treats_naive_published_at_as_shanghai() -> None:
    local = ArticleOut(
        id=1,
        source_id="infoq-cn",
        source_name="InfoQ",
        title="local",
        url="https://example.com/l",
        summary="",
        published_at=datetime(2026, 9, 3, 14, 5),
        fetched_at=datetime(2026, 9, 3, 6, 16, tzinfo=UTC),
        lang="zh",
        kind="news",
    )
    fj = ArticleOut(
        id=9,
        source_id="financialjuice",
        source_name="FinancialJuice",
        title="fj",
        url="https://example.com/f",
        summary="",
        published_at=datetime(2026, 9, 3, 8, 16, tzinfo=UTC),
        fetched_at=datetime(2026, 9, 3, 8, 16, tzinfo=UTC),
        lang="zh",
        kind="news",
    )
    merged = merge_news_page([local], [fj], offset=0, limit=2)
    assert [item.source_id for item in merged] == ["financialjuice", "infoq-cn"]
