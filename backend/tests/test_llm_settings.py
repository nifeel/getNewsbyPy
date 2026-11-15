from app.llm_settings import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LlmConfig,
    default_llm_config,
)
from app.summary_service import _clean_markdown


def test_default_llm_config() -> None:
    cfg = default_llm_config()
    assert not cfg.enabled
    assert cfg.base_url == DEFAULT_BASE_URL
    assert cfg.model == DEFAULT_MODEL
    assert cfg.auto_generate
    assert cfg.auto_generate_hour == 0
    assert cfg.auto_generate_minute == 5


def test_llm_api_key_masking() -> None:
    cfg = LlmConfig(api_key="sk-1234567890abcdef")
    pub = cfg.to_public_dict()
    assert pub["has_api_key"] is True
    assert pub["api_key_masked"] == "sk-****cdef"

    cfg_short = LlmConfig(api_key="12345")
    pub_short = cfg_short.to_public_dict()
    assert pub_short["has_api_key"] is True
    assert pub_short["api_key_masked"] == "******"

    cfg_empty = LlmConfig(api_key="")
    pub_empty = cfg_empty.to_public_dict()
    assert pub_empty["has_api_key"] is False
    assert pub_empty["api_key_masked"] == ""


def test_clean_markdown() -> None:
    raw = "```markdown\n# 2026-11-04 科技与财经日报\n\n正文内容\n```"
    cleaned = _clean_markdown(raw)
    assert cleaned.startswith("# 2026-11-04")
    assert cleaned.strip().endswith("正文内容")
    assert "```" not in cleaned
