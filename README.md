# FinancialJuice 新闻采集器

从 FinancialJuice 抓取需要登录才能访问的新闻，存储到本地 SQLite 数据库，并提供 Web 界面用于浏览新闻、监控任务。

---

## 工作原理

```
Watchdog (app.robot.watchdog)  ← 守护 Robot，崩溃自动重启
  └─ Robot (app.robot.robot)
       ├─ Realtime Browser  — 管理 Chrome，维持登录，拦截 WebSocket 推送，写 news 表
       ├─ Realtime API      — 定时轮询 Startup API，写 news 表
       └─ History API       — 按任务翻页抓取历史数据，写 news 表

GUI Server (app.gui.server)  ← 可选：Web 面板，内置 Watchdog
```

| 进程 | 模块 | 职责 |
|---|---|---|
| **Watchdog** | `app.robot.watchdog` | 守护 Robot：PID 存活检测、崩溃自动重启、每小时自我重启防内存膨胀 |
| **Robot** | `app.robot.robot` | 主协调器：创建任务、启停子进程、监控崩溃重启；启动时自动运行 Startup 采集并创建历史任务 |
| **Realtime Browser** | `app.services.realtime_browser` | 独占 Chrome 窗口；登录检测与自动重登；拦截 WebSocket 推送；同时处理 HTTP API 响应 |
| **Realtime API** | `app.services.realtime_api` | 定时轮询 Startup API，补充 WebSocket 可能遗漏的条目 |
| **History API** | `app.services.history_api` | 按 `tasks` 表中的任务，通过 `GetPreviousNews` 向前翻页 |

### 登录管理（Realtime Browser 内置）

Realtime Browser 持有唯一的 Chrome 窗口，通过以下双重机制检测登出：

- **MutationObserver**（实时）：注入 JS 监听 `#signup` modal 出现，秒级触发
- **周期检查**（兜底）：每 60 秒扫描一次登出指示器

检测到登出后自动使用硬编码的 FinancialJuice 表单字段 ID 填写凭据并提交。登出/登录事件写入 `login_events` 表。

### 数据采集路径

所有路径最终写入同一张 `news` 表，以 `NewsID` 唯一索引去重（`INSERT OR IGNORE`）。

| 来源 | source_method |
|---|---|
| Startup API（HTTP） | `Startup` |
| GetPreviousNews API（HTTP） | `GetPreviousNews` |
| 浏览器 WebSocket 推送 | `browser_ws` |
| 浏览器 HTTP 拦截 | `browser` |

### 历史任务调度

- **长历史任务**：`end_time` 非空，从最新 NewsID 向前翻页至指定时间
- **缺口填补任务**：`end_news_id` 非空、`end_time` 为空，填补实时与历史之间的 ID 缺口
- **优先级**：ID 更大的任务优先运行，旧任务暂停为 `pending` 等新任务完成后继续

### 断点恢复

- **任务恢复**：history 任务重启时，优先从 `news` 表查询该 `task_id` 实际存储的最大 `NewsID` 作为起点继续翻页，不重复
- **崩溃/登出恢复**：`stopped` 状态的历史任务在 Robot 重启时自动重置为 `pending` 并恢复运行
- **登录恢复**：登出导致暂停的任务在登录恢复后自动重启；若无活跃历史任务则自动创建新任务

---

## 安装

**依赖：** Python 3.11+、Google Chrome

```bash
pip install -e .
python -m playwright install chromium
```

---

## 首次登录

首次运行前，先保存 FinancialJuice 会话 Cookie：

```bash
python -m app.browser.login
```

若已配置 `FJ_ACCOUNT` / `FJ_PASSWORD`，自动登录；否则打开浏览器窗口手动登录后按 Enter 保存。会话保存到 `data/storage_state.json`（已加入 `.gitignore`，勿提交）。

---

## 启动

**方式 1：Watchdog（推荐生产环境）**

```bash
python -m app.robot.watchdog
```

Watchdog 守护 Robot 进程，崩溃自动重启。无需 GUI，占用最小。

**方式 2：GUI Server（开发/监控）**

```bash
python -m app.gui.server
```

打开 `http://127.0.0.1:8000`，包含 Web 面板、资源监控、日志查看，内置 Watchdog 功能。

---

## 配置

所有配置通过 `.env` 文件或 `FJ_` 前缀的环境变量读取。

### 认证

| 变量 | 说明 |
|---|---|
| `FJ_ACCOUNT` | 登录邮箱（配置后支持自动重登） |
| `FJ_PASSWORD` | 登录密码 |

### 浏览器

| 变量 | 默认值 | 说明 |
|---|---|---|
| `FJ_HEADLESS` | `false` | 无头模式 |
| `FJ_BROWSER_CHANNEL` | `chrome` | `chrome` / `msedge` / `chromium` |

### 浏览器优化

| 变量 | 默认值 | 说明 |
|---|---|---|
| `FJ_BROWSER_RESTART_HOURS` | `6` | 定时重启浏览器间隔（小时），0 为不重启 |
| `FJ_BROWSER_MAX_MEMORY_MB` | `200` | 浏览器进程内存超限阈值（MB），0 为不限制 |
| `FJ_BROWSER_MEMORY_OPTIMIZE` | `false` | 启用 Chrome 内存优化参数（`--memory-pressure-off` 等） |

### 采集间隔

| 变量 | 默认值 | 说明 |
|---|---|---|
| `FJ_REALTIME_INTERVAL_SECONDS` | `30` | 实时 API 轮询间隔（秒） |
| `FJ_HISTORY_SYNC_INTERVAL_SECONDS` | `60` | 历史翻页请求间隔（秒） |
| `FJ_INTERVAL_JITTER_SECONDS` | `10` | 间隔随机抖动范围（秒） |
| `FJ_COLLECTOR_RETRY_SECONDS` | `10` | 出错后重试等待（秒） |
| `FJ_LOGIN_CHECK_INTERVAL_SECONDS` | `60` | 登录周期检查间隔（秒） |
| `FJ_LOGIN_RETRY_ATTEMPTS` | `10` | 登录最大重试次数 |
| `FJ_LOGIN_RETRY_INTERVAL_SECONDS` | `30` | 登录重试间隔（秒） |
| `FJ_ROBOT_INTERVAL_SECONDS` | `30` | Robot 主循环检查间隔（秒） |

### Watchdog

| 变量 | 默认值 | 说明 |
|---|---|---|
| `FJ_WATCHDOG_CHECK_INTERVAL_SECONDS` | `60` | Robot 存活检查间隔（秒） |
| `FJ_WATCHDOG_RESTART_INTERVAL_SECONDS` | `3600` | Watchdog 自我重启间隔（秒） |

### 功能开关

| 变量 | 默认值 | 说明 |
|---|---|---|
| `FJ_REALTIME_API_ENABLED` | `true` | 启用实时 API 采集 |
| `FJ_REALTIME_BROWSER_ENABLED` | `true` | 启用浏览器 WebSocket 采集 |

### GUI 与路径

| 变量 | 默认值 | 说明 |
|---|---|---|
| `FJ_GUI_HOST` | `127.0.0.1` | GUI 绑定地址（`0.0.0.0` 对外暴露） |
| `FJ_GUI_PORT` | `8000` | GUI 端口 |
| `FJ_DATABASE_PATH` | `data/news.db` | SQLite 数据库路径 |
| `FJ_STORAGE_STATE_PATH` | `data/storage_state.json` | 浏览器 Cookie 文件路径 |

---

## 数据库

`data/news.db`（SQLite，WAL 模式，`PRAGMA synchronous=NORMAL`）

所有时间字段均为 CST（UTC+8）ISO 8601 字符串，无时区后缀。

### 表结构

| 表名 | 用途 |
|---|---|
| `news` | 所有采集的新闻，`NewsID` 唯一索引 |
| `tasks` | 子服务任务状态与进度 |
| `login_events` | 登出检测与登录结果记录 |
| `task_logs` | Watchdog / Robot / Realtime Browser 运行日志 |
| `gui_settings` | GUI 配置键值存储 |

### `news` 主要字段

| 字段 | 说明 |
|---|---|
| `NewsID` | FinancialJuice 原始 ID（唯一索引） |
| `Title` / `Description` | 标题 / 正文 |
| `DatePublished` | 发布时间（来自 API，美东时间） |
| `FCName` | 来源名称 |
| `Level` | 分类标签 |
| `EURL` | 外部链接 |
| `source_method` | 采集方式（`Startup` / `GetPreviousNews` / `browser` / `browser_ws`） |
| `task_id` | 关联的采集任务 ID（0 表示无对应独立任务） |
| `fetched_at` / `created_at` | 抓取时间 / 入库时间（CST） |

### `login_events` 字段

| 字段 | 说明 |
|---|---|
| `event` | `logged_out` / `login_ok` / `login_failed` |
| `detail` | 附加信息（触发原因、重试次数、错误信息） |
| `created_at` | 事件时间（CST） |

### `gui_settings` 说明

| key | 说明 |
|---|---|
| `history_since` | 历史同步截止时间，Robot 读取后创建 `history_api` 任务 |
| `login_status` | Realtime Browser 写入（`ok` / `checking` / `failed`） |
| `robot_pid` | Robot 进程 PID，Watchdog 用于存活检测 |

---

## API 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/stats` | 新闻总量、今日数量、本次实时采集数量、分类与来源分布 |
| GET | `/api/news` | 分页查询，支持 `q`（关键词）、`category`、`limit`、`offset` |
| GET | `/api/news/:id` | 单条新闻完整字段 |
| GET | `/api/tasks` | 所有任务当前状态与进度 |
| GET | `/api/history-tasks` | 最近 20 条历史任务 |
| GET | `/api/history-config` | 读取历史同步截止时间 |
| POST | `/api/history-config` | 保存历史同步截止时间（`{"since": "2026-01-01T00:00"}`） |
| GET | `/api/login-events` | 最近 10 条登录事件 |
| GET | `/api/memory` | 各进程 CPU 与内存占用 |
| GET | `/api/logs/stream` | 实时日志 SSE 流 |

---

## 项目结构

```
app/
  browser/
    context.py            BrowserContext 辅助（启动参数、图片屏蔽）
    login.py              登录凭据管理与自动重登
    session.py            storage_state 文件读写
    network_sniffer.py    从实时流量捕获 API URL
  collectors/
    startup_api_client.py urllib 直接调用 API + URL 自动发现
    startup_collector.py  单次 Startup 请求，写入 news 表
  gui/
    server.py             HTTP 服务器 + TaskManager（管理 Robot 子进程，内置 Watchdog）
    static/               index.html, app.js, app.css
  parsers/
    financialjuice.py     Startup/GetPreviousNews JSON → NewsItem
  robot/
    robot.py              主协调器：任务创建、子进程管理、断点恢复
    watchdog.py           进程守护：存活检测、崩溃重启、自我重启
  services/
    realtime_browser.py   独占 Chrome；登录管理；WebSocket 推送采集
    realtime_api.py       定时轮询 Startup API
    history_api.py        GetPreviousNews 翻页历史采集
  storage/
    database.py           Schema、init_db、WAL 配置
    repository.py         NewsRepository（insert_many 等）
    task_repository.py    TaskRepository（create、update、get_active 等）
    task_log.py           写入 task_logs 表
    task_status.py        cst_now、utc_now 时间辅助
  config.py               pydantic-settings（FJ_* 环境变量）
  models.py               NewsItem 数据类

data/                     运行时数据（已加入 .gitignore）
  news.db
  storage_state.json
  logs/
```
