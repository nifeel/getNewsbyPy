import logging
from time import perf_counter

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.fj_settings import (
    FjDbConfig,
    load_fj_config,
    save_fj_config,
    test_fj_connection,
)
from app.llm_settings import (
    LlmConfig,
    load_llm_config,
    save_llm_config,
)
from app.proxy_settings import (
    PROBE_URL,
    ProxyConfig,
    build_proxy_url,
    load_proxy_config,
    save_proxy_config,
)
from app.schemas import (
    FjDatabaseIn,
    FjDatabaseOut,
    FjDatabaseTestOut,
    LlmIn,
    LlmOut,
    LlmTestOut,
    ProxyIn,
    ProxyOut,
    ProxyTestOut,
    SettingsOut,
    TranslateIn,
    TranslateOut,
    TranslateTestOut,
    fail,
    ok,
)
from app.translate import parse_translate_payload
from app.translate_settings import (
    TranslateConfig,
    build_translate_url,
    load_translate_config,
    save_translate_config,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _proxy_out(cfg: ProxyConfig) -> ProxyOut:
    return ProxyOut(**cfg.to_public_dict())


def _fj_out(cfg: FjDbConfig) -> FjDatabaseOut:
    return FjDatabaseOut(**cfg.to_public_dict())


def _translate_out(cfg: TranslateConfig) -> TranslateOut:
    return TranslateOut(**cfg.to_public_dict())


def _llm_out(cfg: LlmConfig) -> LlmOut:
    return LlmOut(**cfg.to_public_dict())


def _settings_payload(session: Session) -> dict:
    return SettingsOut(
        proxy=_proxy_out(load_proxy_config(session)),
        fj_database=_fj_out(load_fj_config(session)),
        translate=_translate_out(load_translate_config(session)),
        llm=_llm_out(load_llm_config(session)),
    ).model_dump()


def _merge_llm(existing: LlmConfig, body: LlmIn) -> LlmConfig:
    api_key = existing.api_key
    if body.api_key is not None and body.api_key != "":
        api_key = body.api_key.strip()
    return LlmConfig(
        enabled=body.enabled,
        base_url=body.base_url.strip(),
        api_key=api_key,
        model=body.model.strip(),
        auto_generate=body.auto_generate,
        auto_generate_hour=body.auto_generate_hour,
        auto_generate_minute=body.auto_generate_minute,
    )


def _merge_proxy(existing: ProxyConfig, body: ProxyIn) -> ProxyConfig:
    password = existing.password
    if body.password is not None and body.password != "":
        password = body.password
    return ProxyConfig(
        enabled=body.enabled,
        scheme=body.scheme,
        host=body.host,
        port=body.port,
        username=body.username,
        password=password,
    )


def _merge_translate(body: TranslateIn) -> TranslateConfig:
    return TranslateConfig(
        enabled=body.enabled,
        scheme=body.scheme,
        host=body.host,
        port=body.port,
    )


def _merge_fj(existing: FjDbConfig, body: FjDatabaseIn) -> FjDbConfig:
    password = existing.password
    if body.password is not None and body.password != "":
        password = body.password
    return FjDbConfig(
        enabled=body.enabled,
        host=body.host,
        port=body.port,
        database=body.database,
        username=body.username,
        password=password,
    )


@router.get("/settings")
def get_settings(session: Session = Depends(get_session)) -> dict:
    return ok(_settings_payload(session))


@router.put("/settings")
def put_settings(body: ProxyIn, session: Session = Depends(get_session)) -> dict:
    existing = load_proxy_config(session)
    try:
        save_proxy_config(session, _merge_proxy(existing, body))
    except ValueError as exc:
        return fail(str(exc))
    return ok(_settings_payload(session))


@router.put("/settings/fj-database")
def put_fj_database(body: FjDatabaseIn, session: Session = Depends(get_session)) -> dict:
    existing = load_fj_config(session)
    try:
        save_fj_config(session, _merge_fj(existing, body))
    except ValueError as exc:
        return fail(str(exc))
    return ok(_settings_payload(session))


@router.post("/settings/fj-database/test")
def test_fj_database(body: FjDatabaseIn, session: Session = Depends(get_session)) -> dict:
    existing = load_fj_config(session)
    cfg = _merge_fj(existing, body)
    if not cfg.enabled:
        return ok(
            FjDatabaseTestOut(
                ok=False,
                latency_ms=0,
                message="请先启用 FinancialJuice 库再测试",
                host=cfg.host,
                database=cfg.database,
            ).model_dump()
        )
    ok_flag, latency, message = test_fj_connection(cfg)
    return ok(
        FjDatabaseTestOut(
            ok=ok_flag,
            latency_ms=latency,
            message=message,
            host=cfg.host.strip(),
            database=cfg.database.strip(),
        ).model_dump()
    )


@router.post("/settings/proxy/test")
async def test_proxy(body: ProxyIn, session: Session = Depends(get_session)) -> dict:
    existing = load_proxy_config(session)
    cfg = _merge_proxy(existing, body)
    if not cfg.enabled:
        return ok(
            ProxyTestOut(
                ok=False,
                latency_ms=0,
                message="请先启用代理再测试",
                probe_url=PROBE_URL,
            )
        )
    try:
        proxy_url = build_proxy_url(cfg)
    except ValueError as exc:
        return ok(ProxyTestOut(ok=False, latency_ms=0, message=str(exc), probe_url=PROBE_URL))
    if not proxy_url:
        return ok(
            ProxyTestOut(ok=False, latency_ms=0, message="主机或端口无效", probe_url=PROBE_URL)
        )

    started = perf_counter()
    try:
        async with httpx.AsyncClient(
            proxy=proxy_url,
            timeout=httpx.Timeout(10.0),
            follow_redirects=True,
            trust_env=False,
        ) as client:
            response = await client.get(PROBE_URL)
        latency = int((perf_counter() - started) * 1000)
        if response.status_code not in {200, 204}:
            return ok(
                ProxyTestOut(
                    ok=False,
                    latency_ms=latency,
                    message=f"探测失败，HTTP {response.status_code}",
                    probe_url=PROBE_URL,
                    proxy_url=f"{cfg.scheme}://{cfg.host}:{cfg.port}",
                )
            )
        return ok(
            ProxyTestOut(
                ok=True,
                latency_ms=latency,
                message=f"代理可用，耗时 {latency} ms",
                probe_url=PROBE_URL,
                proxy_url=f"{cfg.scheme}://{cfg.host}:{cfg.port}",
            )
        )
    except Exception as exc:  # noqa: BLE001 - 测试结果要回给界面
        latency = int((perf_counter() - started) * 1000)
        logger.warning("proxy test failed: %s", type(exc).__name__)
        return ok(
            ProxyTestOut(
                ok=False,
                latency_ms=latency,
                message=f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__,
                probe_url=PROBE_URL,
                proxy_url=f"{cfg.scheme}://{cfg.host}:{cfg.port}",
            )
        )


PROBE_TEXT = "Fed holds rates"


def _public_translate_url(cfg: TranslateConfig) -> str:
    return f"{cfg.scheme}://{cfg.host}:{cfg.port}"


@router.put("/settings/translate")
def put_translate(body: TranslateIn, session: Session = Depends(get_session)) -> dict:
    try:
        save_translate_config(session, _merge_translate(body))
    except ValueError as exc:
        return fail(str(exc))
    return ok(_settings_payload(session))


@router.post("/settings/translate/test")
def test_translate(body: TranslateIn) -> dict:
    cfg = _merge_translate(body)
    public_url = _public_translate_url(cfg)
    if not cfg.enabled:
        return ok(
            TranslateTestOut(
                ok=False,
                latency_ms=0,
                message="请先启用翻译服务再测试",
                url=public_url,
            ).model_dump()
        )
    base = build_translate_url(cfg)
    if not base:
        return ok(
            TranslateTestOut(
                ok=False,
                latency_ms=0,
                message="主机或端口无效",
                url=public_url,
            ).model_dump()
        )

    started = perf_counter()
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0), trust_env=False) as client:
            health = client.get(f"{base}/health")
            health.raise_for_status()
            health_payload = health.json()
            if isinstance(health_payload, dict):
                code = health_payload.get("code")
                if code not in (0, 200):
                    latency = int((perf_counter() - started) * 1000)
                    return ok(
                        TranslateTestOut(
                            ok=False,
                            latency_ms=latency,
                            message=str(health_payload.get("message") or "健康检查失败"),
                            url=public_url,
                        ).model_dump()
                    )
            response = client.post(
                f"{base}/translate",
                json={"q": PROBE_TEXT, "source": "en", "target": "zh"},
            )
            response.raise_for_status()
            sample = parse_translate_payload(response.json())[0]
        latency = int((perf_counter() - started) * 1000)
        return ok(
            TranslateTestOut(
                ok=True,
                latency_ms=latency,
                message=f"翻译可用，「{PROBE_TEXT}」→「{sample}」，耗时 {latency} ms",
                url=public_url,
                sample=sample,
            ).model_dump()
        )
    except Exception as exc:  # noqa: BLE001 - 测试结果要回给界面
        latency = int((perf_counter() - started) * 1000)
        logger.warning("translate test failed: %s", type(exc).__name__)
        return ok(
            TranslateTestOut(
                ok=False,
                latency_ms=latency,
                message=f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__,
                url=public_url,
            ).model_dump()
        )


@router.put("/settings/llm")
def put_llm(body: LlmIn, session: Session = Depends(get_session)) -> dict:
    existing = load_llm_config(session)
    cfg = _merge_llm(existing, body)
    try:
        save_llm_config(session, cfg)
    except ValueError as exc:
        return fail(str(exc))
    return ok(_settings_payload(session))


@router.post("/settings/llm/test")
async def test_llm(body: LlmIn, session: Session = Depends(get_session)) -> dict:
    existing = load_llm_config(session)
    cfg = _merge_llm(existing, body)
    if not cfg.enabled:
        return ok(
            LlmTestOut(
                ok=False,
                latency_ms=0,
                message="请先启用 AI 服务再测试",
                model=cfg.model,
            ).model_dump()
        )
    if not cfg.base_url.strip():
        return ok(
            LlmTestOut(
                ok=False,
                latency_ms=0,
                message="基础地址 (Base URL) 不能为空",
                model=cfg.model,
            ).model_dump()
        )

    target_url = f"{cfg.base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if cfg.api_key.strip():
        headers["Authorization"] = f"Bearer {cfg.api_key.strip()}"

    payload = {
        "model": cfg.model.strip(),
        "messages": [{"role": "user", "content": "Ping! Please reply with 'pong'."}],
        "max_tokens": 16,
        "temperature": 0.1,
    }

    started = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), trust_env=False) as client:
            resp = await client.post(target_url, json=payload, headers=headers)
        latency = int((perf_counter() - started) * 1000)
        if resp.status_code != 200:
            return ok(
                LlmTestOut(
                    ok=False,
                    latency_ms=latency,
                    message=f"请求失败，HTTP {resp.status_code}: {resp.text[:120]}",
                    model=cfg.model,
                ).model_dump()
            )
        res_data = resp.json()
        choices = res_data.get("choices") or []
        reply = choices[0].get("message", {}).get("content", "") if choices else ""
        return ok(
            LlmTestOut(
                ok=True,
                latency_ms=latency,
                message=f"连通成功，耗时 {latency} ms",
                model=cfg.model,
                reply=reply.strip()[:80],
            ).model_dump()
        )
    except Exception as exc:  # noqa: BLE001 - 测试结果回给界面
        latency = int((perf_counter() - started) * 1000)
        logger.warning("llm test failed: %s", type(exc).__name__)
        return ok(
            LlmTestOut(
                ok=False,
                latency_ms=latency,
                message=f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__,
                model=cfg.model,
            ).model_dump()
        )
