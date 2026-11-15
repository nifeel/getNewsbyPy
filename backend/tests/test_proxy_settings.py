from app.proxy_settings import ProxyConfig, build_proxy_url, parse_proxy_url


def test_parse_and_build_roundtrip() -> None:
    cfg = parse_proxy_url("http://127.0.0.1:7897")
    assert cfg.enabled
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 7897
    assert build_proxy_url(cfg) == "http://127.0.0.1:7897"


def test_disabled_proxy_url_is_empty() -> None:
    cfg = ProxyConfig(enabled=False, host="127.0.0.1", port=7897)
    assert build_proxy_url(cfg) == ""


def test_proxy_url_includes_auth() -> None:
    cfg = ProxyConfig(
        enabled=True,
        scheme="http",
        host="127.0.0.1",
        port=7897,
        username="user",
        password="p@ss",
    )
    assert build_proxy_url(cfg) == "http://user:p%40ss@127.0.0.1:7897"
