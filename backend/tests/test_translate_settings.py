from app.translate_settings import TranslateConfig, build_translate_url, parse_translate_url


def test_parse_translate_url() -> None:
    cfg = parse_translate_url("http://10.0.0.202:8765")
    assert cfg.enabled
    assert cfg.scheme == "http"
    assert cfg.host == "10.0.0.202"
    assert cfg.port == 8765


def test_build_translate_url_requires_host() -> None:
    empty = TranslateConfig(enabled=True, host="", port=8765)
    assert build_translate_url(empty) == ""
    cfg = TranslateConfig(enabled=True, scheme="http", host="10.0.0.202", port=8765)
    assert build_translate_url(cfg) == "http://10.0.0.202:8765"


def test_disabled_translate_has_empty_url() -> None:
    cfg = TranslateConfig(enabled=False, host="10.0.0.202", port=8765)
    assert build_translate_url(cfg) == ""
