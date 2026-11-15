from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.charts_db import charts_configured, get_charts_engine, read_snapshot
from app.db import SessionLocal
from app.digest import build_digest, day_bounds, fj_range_keys
from app.fj_read import count_fj_news, fj_configured
from app.llm_settings import load_llm_config
from app.models import Article
from app.summaries import summaries_dir
from app.timeutil import today_biz

logger = logging.getLogger(__name__)

WEEKDAYS_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _clean_markdown(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1 :]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip() + "\n"


async def generate_daily_summary(
    target_day: date | None = None,
    *,
    overwrite: bool = False,
    session: Session | None = None,
) -> Path:
    day = target_day or (today_biz() - timedelta(days=1))
    target_file = summaries_dir() / f"{day.isoformat()}.md"

    if target_file.is_file() and not overwrite:
        logger.info("日报 %s 已存在，跳过生成", day.isoformat())
        return target_file

    owns_session = session is None
    db_session: Session = session if session is not None else SessionLocal()

    try:
        cfg = load_llm_config(db_session)
        if not cfg.enabled:
            raise RuntimeError("AI 报告生成服务未启用，请前往「系统设置」开启")
        if not cfg.base_url.strip():
            raise RuntimeError("AI 服务的 Base URL 尚未配置")

        start, end = day_bounds(day)

        # 1. 统计本地时间线文章数
        try:
            local_cnt = (
                db_session.scalar(
                    select(func.count(Article.id)).where(
                        Article.published_at >= start, Article.published_at < end
                    )
                )
                or 0
            )
        except Exception:
            local_cnt = 0

        # 2. 统计 FinancialJuice 条数
        fj_cnt = 0
        if fj_configured():
            try:
                start_utc, end_utc = fj_range_keys(start, end)
                fj_cnt = count_fj_news(published_from=start_utc, published_to=end_utc)
            except Exception:
                fj_cnt = 0

        # 3. 获取当天 Digest 数据包
        digest = await build_digest(db_session, kind="daily", day=day)

        crit_cnt = sum(1 for x in digest.finance_items if x.extra == "重要")
        act_cnt = sum(1 for x in digest.finance_items if x.extra == "数据")

        # 4. 获取榜单快照（GitHub & App Store）
        github_slug = "thedotmack/claude-mem"
        github_desc = "持久记忆插件"
        app_name = "抖音商城"

        if charts_configured():
            try:
                engine = get_charts_engine()
                _, _, gh_items = read_snapshot(
                    engine, board="github", region="global", chart="daily", day=day
                )
                if gh_items:
                    top_gh = gh_items[0]
                    github_slug = top_gh.get("item_id") or github_slug
                    github_desc = (top_gh.get("subtitle") or "").strip() or github_desc

                _, _, app_items = read_snapshot(
                    engine, board="appstore", region="cn", chart="free", day=day
                )
                if app_items:
                    top_app = app_items[0]
                    app_name = top_app.get("title") or app_name
            except Exception as chart_err:
                logger.warning("获取榜单快照失败: %s", chart_err)

        # 5. 组装微博热搜
        weibo_list = [h.title for h in digest.hotspot_items[:5] if h.title]
        weibo_text = "、".join(weibo_list) if weibo_list else "科技与热点社会讨论"

        # 6. 整理科技与金融要闻供 LLM 参考
        tech_snippets = [f"- {t.title} ({t.source})" for t in digest.tech_items[:15]]
        finance_snippets = [
            f"- [{f.extra or '要闻'}] {f.title} ({f.source}, {f.time})"
            for f in digest.finance_items[:18]
        ]
        next_snippets = [f"- {e.start_date}: {e.title}" for e in digest.next_events[:6]]

        weekday_cn = WEEKDAYS_CN[day.weekday()]

        prompt = f"""\
你是一位专业的金融科技日报主编。请根据以下素材，为业务日【{day.isoformat()}】撰写一份高质量的科技与财经日报。

### 事实素材：
- 日期：{day.isoformat()}（{weekday_cn}）
- 统计：本地稿 {local_cnt} 条，FJ 约 {fj_cnt or '1500+'} 条。重要档 {crit_cnt} 条、数据档 {act_cnt} 条。
- 热搜：{weibo_text}
- 榜单：GitHub 每日热榜第一为 `{github_slug}`（{github_desc}）；App Store 中国免费榜第一为 {app_name}。
- 金融重点素材：
{chr(10).join(finance_snippets) if finance_snippets else "（暂无重点金融素材）"}
- 科技重点素材：
{chr(10).join(tech_snippets) if tech_snippets else "（暂无重点科技素材）"}
- 往后看事件候选：
{chr(10).join(next_snippets) if next_snippets else "（暂无前瞻素材）"}

### 格式与排版要求（必须严格遵守）：
1. 必须输出纯 Markdown 格式，不要用 ```markdown 或任何代码块包裹全文。
2. 标题规范：
   # {day.isoformat()} 科技与财经日报
3. 元信息行：
   业务日：{day.isoformat()}（Asia/Shanghai，{weekday_cn}）。时间线本地稿 {local_cnt} 条，FJ 约 {fj_cnt} 条。重要档 {crit_cnt} 条、数据档 {act_cnt} 条。主要数据请注明权威信源机构。
4. 结构章节：
   ## 一句话
   **【粗体概括全天最核心金融或科技大事件】** 其余要点跟进补充 1-2 句。

   ## 金融
   - 提取 3~5 条最重要的财经宏观、央行政策、重点公司财报。包含具体数值时请对齐预期、前值并加粗重点。

   ## 科技
   - 提取 3~5 条最重大的 AI 进展、芯片硬件、操作系统或科技前沿突破。

   ## 热点
   - 微博：{weibo_text}。以当天 `hot_items` 为准。
   - 榜单：GitHub 每日热榜第一为 `{github_slug}`（{github_desc}）；App Store 中国免费榜第一为{app_name}。

   ## 往后看
   - 列举未来数日内需关注的财经数据或科技发布会。

   ## 口径
   覆盖上海时区 {day.isoformat()} 全天。相关经济数据对齐官方披露值。

请严格依据事实材料客观撰写，语言凝练专业、金融口吻严密。直接输出 Markdown 正文：
"""

        target_url = f"{cfg.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if cfg.api_key.strip():
            headers["Authorization"] = f"Bearer {cfg.api_key.strip()}"

        payload = {
            "model": cfg.model.strip(),
            "messages": [
                {
                    "role": "system",
                    "content": "你是专业的金融与科技分析师，严格输出符合模板排版的 Markdown 日报，绝不输出任何多余说明。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }

        logger.info("向 CLIProxyAPI (%s) 发送日报生成请求: %s", cfg.model, day.isoformat())
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0), trust_env=False) as client:
            resp = await client.post(target_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("CLIProxyAPI 返回 choices 为空")

        content = choices[0].get("message", {}).get("content", "")
        if not content.strip():
            raise RuntimeError("CLIProxyAPI 生成的日报内容为空")

        markdown_body = _clean_markdown(content)
        target_file.write_text(markdown_body, encoding="utf-8")
        logger.info("成功生成日报并落盘: %s", target_file)
        return target_file

    finally:
        if owns_session:
            db_session.close()
