from app.fj_settings import FjDbConfig, _safe_error, build_conninfo


def test_build_conninfo_requires_host_db_user() -> None:
    empty = FjDbConfig(enabled=True, host="", database="FinancialJuice", username="reader")
    assert build_conninfo(empty) == ""
    cfg = FjDbConfig(
        enabled=True,
        host="10.0.0.202",
        port=5432,
        database="FinancialJuice",
        username="reader",
        password="secret",
    )
    text = build_conninfo(cfg)
    assert "10.0.0.202" in text
    assert "5432" in text
    assert "FinancialJuice" in text or "dbname" in text


def test_disabled_config_has_empty_conninfo() -> None:
    cfg = FjDbConfig(enabled=False, host="10.0.0.202", database="FinancialJuice", username="reader")
    assert build_conninfo(cfg) == ""


def test_error_message_strips_password() -> None:
    message = _safe_error(RuntimeError("password=abc123 failed"), "abc123")
    assert message == "RuntimeError"
    assert "abc123" not in _safe_error(RuntimeError("boom"), "abc123")
