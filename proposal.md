# FinancialJuice 登录态实时新闻采集项目需求文档

## 1. 项目背景

本项目目标是构建一个 Python 应用，用于采集 FinancialJuice 网站登录后可见的实时新闻内容，并将新闻数据结构化保存，方便后续查询、筛选、推送和分析。

由于 FinancialJuice 的公开 RSS 数据较少，项目重点不放在 RSS 抓取，而是围绕用户本人账号登录后可见的实时新闻流进行采集。

## 2. 项目目标

### 2.1 核心目标

- 使用用户自己的 FinancialJuice 登录态访问实时新闻页面。
- 采集登录后页面中出现的实时新闻。
- 对新闻内容进行结构化解析。
- 对重复新闻进行去重。
- 将新闻保存到本地数据库。
- 为后续查询、推送和分析能力预留接口。

### 2.2 非目标

- 不绕过登录机制。
- 不破解、逆向或规避付费权限限制。
- 不进行高频、攻击性或异常请求。
- 不采集用户账号无权访问的数据。
- 不以规避反爬为核心目标。

## 3. 合规与使用边界

本项目仅用于采集用户本人账号正常登录后可以浏览的内容。采集逻辑应尽量模拟正常浏览器访问行为，避免对网站服务造成压力。

建议遵守以下原则：

- 使用真实浏览器会话访问页面。
- 控制请求频率。
- 保存新闻来源、发布时间和原始链接。
- 不共享账号凭据。
- 不绕过付费墙或访问权限。
- 如项目用于商业或公开服务，应进一步确认 FinancialJuice 的服务条款。

## 4. 技术选型

### 4.1 核心采集技术

推荐使用 Playwright 作为核心浏览器自动化工具。

原因：

- 支持真实 Chromium 浏览器环境。
- 支持登录态持久化。
- 支持监听网络请求、XHR、fetch、WebSocket。
- 比 Selenium 更现代，异步能力更好。
- 适合调试动态网页和实时新闻流。

### 4.2 推荐技术栈

| 模块 | 技术选型 | 说明 |
|---|---|---|
| 浏览器自动化 | Playwright | 登录态维护、页面访问、网络监听 |
| HTTP 请求 | httpx | 后续复用接口时使用 |
| 数据解析 | pydantic | 标准化新闻数据结构 |
| 数据库 | SQLite / PostgreSQL | MVP 用 SQLite，正式环境用 PostgreSQL |
| ORM | SQLAlchemy | 数据模型和持久化 |
| 去重 | 数据库唯一索引 + hash | 避免重复写入 |
| 定时任务 | APScheduler | 定时守护、重连、健康检查 |
| 日志 | loguru | 结构化日志 |
| 重试 | tenacity | 网络异常和页面异常重试 |
| API 服务 | FastAPI | 后续提供查询和管理接口 |
| 部署 | Docker / docker compose | 后续长期运行 |

### 4.3 初始依赖

```bash
pip install playwright pydantic sqlalchemy loguru tenacity apscheduler fastapi uvicorn
playwright install chromium
```

如果后续确认实时数据通过 WebSocket 推送，可增加：

```bash
pip install websockets
```

## 5. 推荐采集方案

### 5.1 总体思路

项目优先通过 Playwright 打开 FinancialJuice 页面，维护登录态，并监听页面中的网络数据。

优先级如下：

1. 监听页面 XHR / fetch 返回的 JSON 数据。
2. 监听 WebSocket / SSE 实时数据。
3. 如果网络层无法直接解析，再退回到 DOM 页面元素解析。

原则：如果能拿到结构化 JSON，就不要解析 HTML。

### 5.2 登录态处理

首次运行时使用可视化浏览器登录：

1. 启动 Playwright Chromium。
2. 打开 FinancialJuice 登录页。
3. 用户手动完成登录。
4. 程序保存浏览器登录态到本地文件。

后续运行时：

1. 加载本地登录态文件。
2. 直接进入实时新闻页面。
3. 如果登录态失效，提示用户重新登录。

建议登录态文件路径：

```text
data/storage_state.json
```

该文件可能包含敏感 cookie，不应提交到 Git 仓库。

## 6. 功能需求

### 6.1 登录态初始化

提供一个命令用于首次登录：

```bash
python -m app.browser.login
```

功能：

- 打开浏览器。
- 进入 FinancialJuice 登录页面。
- 等待用户手动登录。
- 登录成功后保存 `storage_state.json`。

### 6.2 网络探测

提供一个网络探测脚本：

```bash
python -m app.browser.network_sniffer
```

功能：

- 加载登录态。
- 打开实时新闻页面。
- 监听所有 XHR、fetch、WebSocket 请求。
- 输出 URL、状态码、内容类型和响应片段。
- 帮助判断实时新闻的真实数据来源。

### 6.3 实时新闻采集

提供实时采集器：

```bash
python -m app.collectors.realtime_collector
```

功能：

- 启动浏览器。
- 加载登录态。
- 打开实时新闻页面。
- 监听新闻数据来源。
- 解析新增新闻。
- 保存到数据库。
- 自动去重。
- 异常时自动重试或重连。

### 6.4 DOM 兜底采集

如果无法直接从网络层获取结构化新闻，则使用 DOM 解析作为兜底方案。

功能：

- 定时读取页面新闻列表节点。
- 提取标题、时间、来源、分类等信息。
- 与历史数据比对并保存新内容。

### 6.5 数据库存储

MVP 阶段使用 SQLite：

```text
data/news.db
```

后续正式运行可迁移到 PostgreSQL。

### 6.6 去重逻辑

优先使用网站返回的新闻 ID。

如果没有稳定 ID，则使用以下字段生成 hash：

```text
title + published_at + source
```

数据库层增加唯一索引，避免重复写入。

## 7. 数据模型

建议新闻表字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| id | integer | 本地自增 ID |
| external_id | string | 网站侧新闻 ID，如存在 |
| title | string | 新闻标题 |
| content | text | 新闻正文或摘要 |
| source | string | 新闻来源 |
| category | string | 分类 |
| url | string | 原始链接 |
| published_at | datetime | 新闻发布时间 |
| fetched_at | datetime | 本地采集时间 |
| raw_payload | json/text | 原始数据 |
| content_hash | string | 去重 hash |

## 8. 推荐目录结构

```text
getfinancialjuice/
  app/
    browser/
      __init__.py
      login.py
      session.py
      network_sniffer.py
    collectors/
      __init__.py
      realtime_collector.py
      dom_collector.py
    parsers/
      __init__.py
      financialjuice.py
    storage/
      __init__.py
      models.py
      repository.py
      database.py
    services/
      __init__.py
      dedupe.py
      notifier.py
    config.py
    main.py
  data/
    .gitkeep
  tests/
    test_parser.py
    test_dedupe.py
  .gitignore
  proposal.md
  pyproject.toml
  README.md
```

## 9. MVP 开发计划

### 阶段 1：项目初始化

- 创建 Python 项目结构。
- 配置依赖管理。
- 增加 `.gitignore`，避免提交登录态和数据库。
- 创建基础配置文件。

### 阶段 2：登录态保存

- 实现 `login.py`。
- 支持手动登录并保存 `storage_state.json`。
- 支持检测登录态文件是否存在。

### 阶段 3：网络探测

- 实现 `network_sniffer.py`。
- 打印 XHR / fetch / WebSocket 请求。
- 识别实时新闻数据来源。

### 阶段 4：新闻解析

- 根据探测结果实现解析器。
- 将不同来源的数据统一转换为标准新闻模型。

### 阶段 5：数据入库

- 实现 SQLite 数据库。
- 实现新闻表。
- 实现插入、去重、查询。

### 阶段 6：实时采集器

- 实现长期运行的采集进程。
- 支持重连。
- 支持错误日志。
- 支持去重入库。

### 阶段 7：扩展能力

- 增加 FastAPI 查询接口。
- 增加关键词过滤。
- 增加 Telegram、Discord、企业微信或邮件推送。
- 增加 Docker 部署。

## 10. 后续可扩展功能

- 关键词监控。
- 重要新闻推送。
- 新闻分类统计。
- 数据导出 CSV / Excel。
- Web 管理界面。
- 多账号隔离。
- PostgreSQL 持久化。
- 新闻情绪分析。
- 与交易系统或提醒系统集成。

## 11. PyCharm 协作建议

后续在 PyCharm 中开发时，可以按以下顺序与 Codex 协作：

1. 先让 Codex 根据本需求文档创建项目骨架。
2. 再实现登录态保存脚本。
3. 运行登录脚本，手动登录 FinancialJuice。
4. 使用网络探测脚本收集实时新闻接口信息。
5. 将探测日志提供给 Codex。
6. 让 Codex 根据真实接口实现解析器和采集器。
7. 再补充数据库、去重、推送和 API。

建议每次只让 Codex 实现一个明确阶段，避免一次性改动过多。

## 12. 风险点

| 风险 | 说明 | 应对 |
|---|---|---|
| 登录态失效 | Cookie 或 session 过期 | 提供重新登录命令 |
| 页面结构变化 | DOM selector 失效 | 优先使用网络层数据 |
| WebSocket 协议变化 | 实时数据格式改变 | 保留 raw payload 方便排查 |
| 数据重复 | 刷新或重连导致重复采集 | 使用 hash 和唯一索引 |
| 网站访问限制 | 请求过频或异常行为 | 降低频率，模拟正常浏览器 |
| 合规风险 | 登录后内容可能受服务条款限制 | 仅采集自用且有权限内容 |

## 13. 初始验收标准

MVP 完成时应满足：

- 可以通过脚本完成手动登录并保存登录态。
- 可以加载登录态打开实时新闻页面。
- 可以识别至少一种实时新闻数据来源。
- 可以采集新增新闻。
- 可以将新闻写入 SQLite。
- 可以避免重复写入相同新闻。
- 程序异常时有清晰日志。

## 14. 推荐下一步

下一步建议先实现项目骨架和登录态保存脚本，然后运行一次浏览器登录流程。只有拿到登录后的真实网络请求之后，才能准确决定实时新闻采集器应该监听 XHR、WebSocket 还是 DOM。
