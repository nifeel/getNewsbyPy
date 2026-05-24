const HISTORY_PAGE_SIZE = 5;
const NEWS_PAGE_SIZE = 5;

const state = {
  categories: [],
  searchTimer: null,
  historySinceLoaded: false,
  historyItems: [],
  historyPage: 0,
  newsOffset: 0,
  newsTotal: 0,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(String(value).replace(" ", "T"));
  return Number.isNaN(date.getTime()) ? null : date;
}

function isRecent(timeStr, seconds) {
  if (!timeStr) return false;
  const date = parseDate(timeStr);
  if (!date) return false;
  return (Date.now() - date.getTime()) / 1000 < seconds;
}

function calcUptime(startedAt) {
  if (!startedAt) return "";
  const start = parseDate(startedAt);
  if (!start) return "";
  const elapsed = Math.max(0, (Date.now() - start.getTime()) / 1000);
  if (elapsed < 60) return `${Math.floor(elapsed)}s`;
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)}m ${Math.floor(elapsed % 60)}s`;
  const h = Math.floor(elapsed / 3600);
  const m = Math.floor((elapsed % 3600) / 60);
  return `${h}h ${m}m`;
}

function formatTime(value) {
  if (!value) return "-";
  const date = parseDate(value);
  if (!date) return value;
  return date.toLocaleString();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function taskLabel(type) {
  const labels = {
    robot: "Robot",
    realtime_browser: "实时采集",
    history_api: "历史数据同步",
  };
  return labels[type] || type || "未知";
}

function renderStats(stats) {
  $("totalCount").textContent = stats.total ?? 0;
  $("todayCount").textContent = stats.today ?? 0;
  $("collectedCount").textContent = stats.collected ?? 0;
  $("latestTitle").textContent = stats.latest?.title || "-";

  state.categories = stats.categories || [];
  $("categoryList").innerHTML = state.categories
    .map(
      (item) =>
        `<button class="chip" data-category="${escapeHtml(item.category)}">` +
        `<span>${escapeHtml(item.category)}</span><strong>${item.count}</strong></button>`
    )
    .join("");

  $("sourceList").innerHTML = (stats.sources || [])
    .map(
      (item) =>
        `<div><span>${escapeHtml(item.source)}</span><strong>${item.count}</strong></div>`
    )
    .join("");

  const selected = $("categoryFilter").value;
  $("categoryFilter").innerHTML =
    '<option value="">全部分类</option>' +
    state.categories
      .map(
        (item) =>
          `<option value="${escapeHtml(item.category)}">${escapeHtml(item.category)}</option>`
      )
      .join("");
  $("categoryFilter").value = selected;

  document.querySelectorAll(".chip[data-category]").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("categoryFilter").value = btn.dataset.category;
      loadNews(true);
    });
  });
}

function renderTasks(data) {
  const items = data.items || [];
  const serviceTypes = ["realtime_browser"];
  const seen = new Set();
  const serviceItems = [];
  for (const item of items) {
    if (!serviceTypes.includes(item.task_type)) continue;
    if (seen.has(item.task_type)) continue;
    seen.add(item.task_type);
    serviceItems.push(item);
  }

  $("serviceList").innerHTML = serviceItems.length
    ? serviceItems.map((item) => renderTask(item)).join("")
    : '<div class="task"><div class="taskMeta">暂无服务任务记录</div></div>';

  const now = new Date().toLocaleTimeString();
  $("refreshTime").textContent = `更新于 ${now}`;
  $("taskRefreshTime").textContent = now;
}

function renderTask(item) {
  const status = escapeHtml(item.status || "unknown");
  const label = taskLabel(item.task_type);
  const rows = [];

  // 用 updated_at 近 2 分钟内有更新判断存活
  const alive = item.updated_at && item.status === "running" && isRecent(item.updated_at, 120);
  if (item.task_type === "realtime_browser") {
    const dot = alive ? '<span style="color:#22c55e">●</span>' : '<span style="color:#ef4444">●</span>';
    const uptime = calcUptime(item.last_started_at);
    rows.push(`<div class="taskMeta">${dot} ${alive ? "活跃" : "超时"}${uptime ? ` | 运行 ${uptime}` : ""}</div>`);
  }

  rows.push(
    `<div class="taskMeta">inserted=${item.inserted ?? 0} skipped=${item.skipped ?? 0}</div>`
  );

  const parts = [];
  if (item.pages != null) parts.push(`pages=${item.pages}`);
  if (item.current_old_id) parts.push(`id=${item.current_old_id}`);
  if (item.current_oldest_at) parts.push(`oldest=${formatTime(item.current_oldest_at)}`);
  if (parts.length) {
    rows.push(`<div class="taskMeta">${escapeHtml(parts.join(" "))}</div>`);
  }

  rows.push(
    `<div class="taskMeta">pid=${item.pid || "-"} 更新=${formatTime(item.updated_at)}</div>`
  );

  if (item.last_error) {
    rows.push(
      `<div class="taskMeta" style="color:var(--danger)">${escapeHtml(item.last_error)}</div>`
    );
  }
  if (item.message) {
    rows.push(`<div class="taskMeta">${escapeHtml(item.message)}</div>`);
  }

  return `
    <article class="task">
      <div class="taskTop">
        <strong>${escapeHtml(label)}</strong>
        <span class="status ${status}">${status}</span>
      </div>
      ${rows.join("")}
    </article>
  `;
}

function renderHistoryTaskCard(t) {
  const status = escapeHtml(t.status || "unknown");
  const range = t.end_time
    ? `until=${escapeHtml(t.end_time)}`
    : t.end_news_id
    ? `end_id=${t.end_news_id}`
    : "";
  const progress =
    t.pages != null
      ? `pages=${t.pages} inserted=${t.inserted ?? 0} skipped=${t.skipped ?? 0}`
      : "";
  return `
    <div class="task compact">
      <div class="taskTop">
        <strong>历史同步 #${t.id}</strong>
        <span class="status ${status}">${status}</span>
      </div>
      <div class="taskMeta">start=${t.start_news_id ?? "-"} current=${t.current_news_id ?? "-"} ${range}</div>
      ${progress ? `<div class="taskMeta">${escapeHtml(progress)}</div>` : ""}
      ${t.message ? `<div class="taskMeta">${escapeHtml(t.message)}</div>` : ""}
    </div>
  `;
}

function renderHistoryTasks(data) {
  if (data) state.historyItems = data.items || [];
  const items = state.historyItems;

  if (!items.length) {
    $("historyTaskList").innerHTML =
      '<div class="taskMeta">暂无历史任务 — 保存截止时间后 Robot 会自动创建。</div>';
    return;
  }

  const totalPages = Math.ceil(items.length / HISTORY_PAGE_SIZE);
  state.historyPage = Math.min(state.historyPage, totalPages - 1);
  const start = state.historyPage * HISTORY_PAGE_SIZE;
  const page = items.slice(start, start + HISTORY_PAGE_SIZE);

  const cards = page.map(renderHistoryTaskCard).join("");

  const pagination = totalPages > 1 ? `
    <div class="historyPager">
      <button class="secondary historyPrev" ${state.historyPage === 0 ? "disabled" : ""}>&#8249;</button>
      <span class="muted">${state.historyPage + 1} / ${totalPages}</span>
      <button class="secondary historyNext" ${state.historyPage >= totalPages - 1 ? "disabled" : ""}>&#8250;</button>
    </div>
  ` : "";

  $("historyTaskList").innerHTML = cards + pagination;

  const prev = $("historyTaskList").querySelector(".historyPrev");
  const next = $("historyTaskList").querySelector(".historyNext");
  if (prev) prev.addEventListener("click", () => { state.historyPage--; renderHistoryTasks(null); });
  if (next) next.addEventListener("click", () => { state.historyPage++; renderHistoryTasks(null); });
}

const MEM_MAX_POINTS = 60;
let memChart = null;
const memSeries = { time: [], total: [], cpu: [] };

function initMemChart() {
  const container = $("memChart");
  if (!container || memChart) return;

  const opts = {
    width: container.offsetWidth,
    height: 180,
    cursor: { show: true },
    select: { show: false },
    legend: { show: false },
    series: [
      {},
      {
        label: "Memory (MB)",
        stroke: "#0f766e",
        width: 2,
        fill: "rgba(15, 118, 110, 0.12)",
        value: (_self, v) => (v != null ? v.toFixed(0) + " MB" : "-"),
      },
      {
        label: "CPU %",
        stroke: "#2563eb",
        width: 1.5,
        scale: "cpu",
        value: (_self, v) => (v != null ? v.toFixed(1) + "%" : "-"),
      },
    ],
    axes: [
      {},
      {
        stroke: "#0f766e",
        grid: { stroke: "#e5e7eb", width: 1 },
        values: (_self, ticks) => ticks.map((v) => v + " MB"),
      },
      {
        scale: "cpu",
        side: 1,
        stroke: "#2563eb",
        grid: { show: false },
        values: (_self, ticks) => ticks.map((v) => v + "%"),
      },
    ],
    scales: {
      cpu: { range: [0, 100] },
    },
  };

  memChart = new uPlot(opts, [memSeries.time, memSeries.total, memSeries.cpu], container);

  // resize on window change
  const ro = new ResizeObserver(() => {
    if (memChart && container.offsetWidth > 0) {
      memChart.setSize({ width: container.offsetWidth, height: 180 });
    }
  });
  ro.observe(container);
}

function updateMemChart(data) {
  const t = Date.now() / 1000;
  const cpu = (data.processes || []).reduce((s, p) => s + (p.cpu_pct || 0), 0);

  memSeries.time.push(t);
  memSeries.total.push(data.total_mb ?? 0);
  memSeries.cpu.push(Math.round(cpu * 10) / 10);

  while (memSeries.time.length > MEM_MAX_POINTS) {
    memSeries.time.shift();
    memSeries.total.shift();
    memSeries.cpu.shift();
  }

  if (!memChart) {
    initMemChart();
  }
  if (memChart) {
    memChart.setData([memSeries.time, memSeries.total, memSeries.cpu]);
  }
}

function renderMemory(data) {
  const items = data.processes || [];
  $("memoryTotal").textContent = data.total_mb != null ? `(${data.total_mb.toFixed(0)} MB)` : "";

  updateMemChart(data);

  if (!items.length) {
    $("memoryList").innerHTML = '<div class="taskMeta muted">暂无数据</div>';
    return;
  }
  $("memoryList").innerHTML = items
    .map(
      (p) =>
        `<div><span>${escapeHtml(p.label)} <small class="muted">pid=${p.pid}</small></span><span class="cpuMem"><strong>${p.cpu_pct != null ? p.cpu_pct.toFixed(1) : "0.0"}%</strong> <strong>${p.rss_mb} MB</strong></span></div>`
    )
    .join("");
}

function renderLoginEvents(data) {
  const items = data.items || [];
  if (!items.length) {
    $("loginEventList").innerHTML = '<div class="taskMeta muted">暂无登录事件</div>';
    return;
  }
  $("loginEventList").innerHTML = items.map((item) => {
    const cls = item.event === "login_ok" ? "ok" : item.event === "logged_out" ? "warning" : "running";
    const label = item.event === "login_ok" ? "登录成功" : item.event === "logged_out" ? "检测到登出" : "登录失败";
    return `
      <div class="loginEvent">
        <span class="status ${cls}" style="font-size:0.7rem;padding:2px 6px">${escapeHtml(label)}</span>
        <span class="taskMeta">${escapeHtml(item.detail || "")}</span>
        <span class="eventTime muted">${escapeHtml(item.created_at || "")}</span>
      </div>`;
  }).join("");
}

function renderNews(data) {
  const items = data.items || [];
  state.newsTotal = data.total ?? items.length;
  state.newsOffset = data.offset ?? state.newsOffset;

  const totalPages = Math.ceil(state.newsTotal / NEWS_PAGE_SIZE);
  const currentPage = Math.floor(state.newsOffset / NEWS_PAGE_SIZE);

  const cards = items.length
    ? items.map(renderNewsItem).join("")
    : '<div class="newsItem"><h3>没有找到新闻</h3></div>';

  const pagination = state.newsTotal > NEWS_PAGE_SIZE ? `
    <div class="newsPager">
      <button class="secondary newsPrev" ${currentPage === 0 ? "disabled" : ""}>&#8249;</button>
      <span class="muted">${currentPage + 1} / ${totalPages}（共 ${state.newsTotal} 条）</span>
      <button class="secondary newsNext" ${currentPage >= totalPages - 1 ? "disabled" : ""}>&#8250;</button>
    </div>
  ` : "";

  $("newsList").innerHTML = cards + pagination;

  document.querySelectorAll("[data-detail-id]").forEach((btn) => {
    btn.addEventListener("click", () => showDetail(btn.dataset.detailId));
  });

  const prev = $("newsList").querySelector(".newsPrev");
  const next = $("newsList").querySelector(".newsNext");
  if (prev) prev.addEventListener("click", () => {
    state.newsOffset = Math.max(0, state.newsOffset - NEWS_PAGE_SIZE);
    loadNews();
  });
  if (next) next.addEventListener("click", () => {
    state.newsOffset = state.newsOffset + NEWS_PAGE_SIZE;
    loadNews();
  });
}

function renderNewsItem(item) {
  const meta = [
    item.published_at ? formatTime(item.published_at) : "",
    item.source || "",
    item.category || "",
    item.external_id ? `#${item.external_id}` : "",
  ]
    .filter(Boolean)
    .join(" / ");
  const content = item.content
    ? `<p class="newsContent">${escapeHtml(item.content)}</p>`
    : "";
  return `
    <article class="newsItem">
      <h3>${escapeHtml(item.title)}</h3>
      ${content}
      <div class="newsFooter">
        <span class="newsMeta">${escapeHtml(meta)}</span>
        <button class="linkButton" data-detail-id="${item.id}">详情</button>
      </div>
    </article>
  `;
}

async function showDetail(id) {
  const item = await api(`/api/news/${id}`);
  $("detailTitle").textContent = item.title || "详情";
  $("detailBody").textContent = JSON.stringify(item, null, 2);
  $("detailDialog").showModal();
}

function appendLog(line) {
  const box = $("logBox");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.textContent += (box.textContent ? "\n" : "") + line;
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function initLogStream() {
  const es = new EventSource("/api/logs/stream");
  es.onmessage = (event) => {
    try {
      appendLog(JSON.parse(event.data));
    } catch {
      appendLog(event.data);
    }
  };
  es.onerror = () => {
    setTimeout(initLogStream, 5000);
    es.close();
  };
}

function normalizeDateTimeLocalValue(value) {
  if (!value) return "";
  const normalized = String(value).trim().replace(" ", "T").replace(/Z$/, "");
  const match = normalized.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})/);
  return match ? match[1] : "";
}

async function loadHistoryConfig() {
  const config = await api("/api/history-config");
  const normalized = normalizeDateTimeLocalValue(config.since);
  if (normalized) $("historySince").value = normalized;
  state.historySinceLoaded = true;
}

async function saveHistoryConfig() {
  await api("/api/history-config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ since: $("historySince").value }),
  });
  appendLog(`历史截止时间已保存: ${$("historySince").value}`);
}

async function loadNews(resetPage = false) {
  if (resetPage) state.newsOffset = 0;
  const params = new URLSearchParams();
  const q = $("searchInput").value.trim();
  const category = $("categoryFilter").value;
  if (q) params.set("q", q);
  if (category) params.set("category", category);
  params.set("limit", String(NEWS_PAGE_SIZE));
  params.set("offset", String(state.newsOffset));
  renderNews(await api(`/api/news?${params.toString()}`));
}

async function refreshAll() {
  const results = await Promise.allSettled([
    api("/api/stats").then(renderStats),
    api("/api/tasks").then(renderTasks),
    api("/api/history-tasks").then(renderHistoryTasks),
    api("/api/login-events").then(renderLoginEvents),
    api("/api/memory").then(renderMemory),
    loadNews(true),
  ]);
  results.forEach((r) => {
    if (r.status === "rejected") appendLog(`ERROR: ${r.reason?.message || r.reason}`);
  });
}

$("historySaveBtn").addEventListener("click", () =>
  saveHistoryConfig().catch((e) => appendLog(`保存失败: ${e.message}`))
);
$("refreshBtn").addEventListener("click", refreshAll);
$("categoryFilter").addEventListener("change", () => loadNews(true));
$("searchInput").addEventListener("input", () => {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(() => loadNews(true), 250);
});
$("closeDialog").addEventListener("click", () => $("detailDialog").close());

loadHistoryConfig().catch((e) => appendLog(`历史配置加载失败: ${e.message}`));
refreshAll().catch((e) => appendLog(`加载失败: ${e.message}`));

setInterval(() => {
  Promise.all([
    api("/api/stats").then(renderStats),
    api("/api/tasks").then(renderTasks),
    api("/api/history-tasks").then(renderHistoryTasks),
    api("/api/login-events").then(renderLoginEvents),
    api("/api/memory").then(renderMemory),
  ]).catch(() => {});
}, 3000);

initLogStream();
