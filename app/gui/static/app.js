const state = {
  categories: [],
  busy: false,
};

const HISTORY_SINCE_KEY = "financialjuice.historySince";

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

function formatTime(value) {
  if (!value) return "-";
  const date = parseDate(value);
  if (!date) return value;
  return date.toLocaleString();
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(String(value).endsWith("Z") ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function setBusy(value) {
  state.busy = value;
  $("startBtn").disabled = value;
  $("stopBtn").disabled = value;
  $("runOnceBtn").disabled = value;
  $("historyStartBtn").disabled = value;
  $("historyStopBtn").disabled = value;
}

function renderStats(stats) {
  $("totalCount").textContent = stats.total ?? 0;
  $("todayCount").textContent = stats.today ?? 0;
  $("latestTitle").textContent = stats.latest?.title || "-";

  state.categories = stats.categories || [];
  $("categoryList").innerHTML = state.categories
    .map((item) => `<button class="chip" data-category="${escapeHtml(item.category)}"><span>${escapeHtml(item.category)}</span><strong>${item.count}</strong></button>`)
    .join("");

  $("sourceList").innerHTML = (stats.sources || [])
    .map((item) => `<div><span>${escapeHtml(item.source)}</span><strong>${item.count}</strong></div>`)
    .join("");

  const selected = $("categoryFilter").value;
  $("categoryFilter").innerHTML = '<option value="">鍏ㄩ儴鍒嗙被</option>' + state.categories
    .map((item) => `<option value="${escapeHtml(item.category)}">${escapeHtml(item.category)}</option>`)
    .join("");
  $("categoryFilter").value = selected;

  document.querySelectorAll(".chip[data-category]").forEach((button) => {
    button.addEventListener("click", () => {
      $("categoryFilter").value = button.dataset.category;
      loadNews();
    });
  });
}

function renderTasks(data) {
  applyHistorySinceFromTasks(data.items || []);
  const items = data.items || [];
  $("taskList").innerHTML = items.length
    ? items.map(renderTask).join("")
    : '<div class="task"><div class="taskMeta">鏆傛棤浠诲姟鐘舵€?/div></div>';
  $("logBox").textContent = (data.logs || []).join("\n") || "鏆傛棤鏃ュ織";
  $("logBox").scrollTop = $("logBox").scrollHeight;
  $("refreshTime").textContent = `鍒锋柊 ${new Date().toLocaleTimeString()}`;
}

function normalizeDateTimeLocalValue(value) {
  if (!value) return "";
  const normalized = String(value).trim().replace(" ", "T").replace(/Z$/, "");
  const match = normalized.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})/);
  return match ? match[1] : "";
}

function setHistorySince(value, { persist = false } = {}) {
  const normalized = normalizeDateTimeLocalValue(value);
  if (!normalized) return;
  $("historySince").value = normalized;
  if (persist) {
    localStorage.setItem(HISTORY_SINCE_KEY, normalized);
  }
}

function restoreHistorySince() {
  setHistorySince(localStorage.getItem(HISTORY_SINCE_KEY) || "");
}

function applyHistorySinceFromTasks(items) {
  if ($("historySince").value) return;
  const historyTask = items.find((item) => item.task_name === "history_sync_collector" && item.target_since);
  if (historyTask) {
    setHistorySince(historyTask.target_since, { persist: true });
  }
}

function renderTask(item) {
  const status = escapeHtml(item.status || "unknown");
  return `
    <article class="task">
      <div class="taskTop">
        <strong>${escapeHtml(taskLabel(item.task_name))}</strong>
        <span class="status ${status}">${status}</span>
      </div>
      <div class="taskMeta">cycle=${item.cycle ?? 0} inserted=${item.inserted ?? 0} skipped=${item.skipped ?? 0} total=${item.total ?? 0}</div>
      ${renderHistoryProgress(item)}
      ${renderLatestCatchupProgress(item)}
      <div class="taskMeta">pid=${item.pid || "-"} updated=${formatTime(item.updated_at)}</div>
      ${item.last_error ? `<div class="taskMeta">error=${escapeHtml(item.last_error)}</div>` : ""}
    </article>
  `;
}

function taskLabel(name) {
  const labels = {
    continuous_collector: "持续采集",
    startup_collector: "单次采集",
    history_sync_collector: "历史补全",
    latest_catchup_collector: "最新缺口补偿",
  };
  return labels[name] || name || "未知任务";
}

function renderHistoryProgress(item) {
  if (item.task_name !== "history_sync_collector") return "";
  const progress = calculateHistoryProgress(item);
  const parts = [
    `pages=${item.pages ?? 0}`,
    item.target_since ? `since=${item.target_since}` : "",
    item.current_old_id ? `oldID=${item.current_old_id}` : "",
    item.current_oldest_at ? `oldest=${formatTime(item.current_oldest_at)}` : "",
  ].filter(Boolean).join(" ");
  return `
    <div class="progressBlock">
      <div class="progressHeader">
        <span>历史覆盖进度</span>
        <strong>${progress.label}</strong>
      </div>
      <div class="progressTrack"><div style="width: ${progress.percent}%"></div></div>
      <div class="progressGrid">
        <span>目标：${escapeHtml(item.target_since || "-")}</span>
        <span>当前最旧：${escapeHtml(item.current_oldest_at ? formatTime(item.current_oldest_at) : "-")}</span>
      </div>
    </div>
    <div class="taskMeta">${escapeHtml(parts)}</div>
    ${item.message ? `<div class="taskMeta">${escapeHtml(item.message)}</div>` : ""}
  `;
}

function renderLatestCatchupProgress(item) {
  if (item.task_name !== "latest_catchup_collector") return "";
  return `
    <div class="progressBlock compact">
      <div class="progressHeader">
        <span>同步期间新增数据补偿</span>
        <strong>${escapeHtml(item.status || "-")}</strong>
      </div>
      <div class="taskMeta">pages=${item.pages ?? 0} oldID=${escapeHtml(item.current_old_id || "0")} oldest=${escapeHtml(item.current_oldest_at ? formatTime(item.current_oldest_at) : "-")}</div>
      ${item.message ? `<div class="taskMeta">${escapeHtml(item.message)}</div>` : ""}
    </div>
  `;
}

function calculateHistoryProgress(item) {
  if (item.status === "completed") {
    return { percent: 100, label: "100%" };
  }
  const target = parseDate(item.target_since);
  const oldest = parseDate(item.current_oldest_at);
  const anchor = new Date();
  if (!target || !oldest || anchor <= target) {
    return { percent: 0, label: "-" };
  }
  const total = anchor.getTime() - target.getTime();
  const done = anchor.getTime() - oldest.getTime();
  const percent = Math.max(0, Math.min(100, Math.round((done / total) * 100)));
  return { percent, label: `${percent}%` };
}

function renderNews(data) {
  const items = data.items || [];
  $("newsList").innerHTML = items.length
    ? items.map(renderNewsItem).join("")
    : '<div class="newsItem"><h3>娌℃湁鍖归厤鐨勬柊闂?/h3></div>';

  document.querySelectorAll("[data-detail-id]").forEach((button) => {
    button.addEventListener("click", () => showDetail(button.dataset.detailId));
  });
}

function renderNewsItem(item) {
  const meta = [
    item.published_at ? formatTime(item.published_at) : "",
    item.source || "",
    item.category || "",
    item.external_id ? `#${item.external_id}` : "",
  ].filter(Boolean).join(" / ");
  const content = item.content ? `<p class="newsContent">${escapeHtml(item.content)}</p>` : "";
  return `
    <article class="newsItem">
      <h3>${escapeHtml(item.title)}</h3>
      ${content}
      <div class="newsFooter">
        <span class="newsMeta">${escapeHtml(meta)}</span>
        <button class="linkButton" data-detail-id="${item.id}">璇︽儏</button>
      </div>
    </article>
  `;
}

async function showDetail(id) {
  const item = await api(`/api/news/${id}`);
  $("detailTitle").textContent = item.title || "璇︽儏";
  $("detailBody").textContent = JSON.stringify(item, null, 2);
  $("detailDialog").showModal();
}

async function loadStats() {
  renderStats(await api("/api/stats"));
}

async function loadTasks() {
  renderTasks(await api("/api/tasks"));
}

async function loadNews() {
  const params = new URLSearchParams();
  const q = $("searchInput").value.trim();
  const category = $("categoryFilter").value;
  if (q) params.set("q", q);
  if (category) params.set("category", category);
  params.set("limit", "100");
  renderNews(await api(`/api/news?${params.toString()}`));
}

async function refreshAll() {
  await Promise.all([loadStats(), loadTasks(), loadNews()]);
}

async function postAction(path) {
  setBusy(true);
  try {
    await api(path, { method: "POST" });
    await Promise.all([loadTasks(), loadStats()]);
  } finally {
    setBusy(false);
  }
}

async function startHistorySync() {
  const since = $("historySince").value;
  const interval = Number.parseInt($("historyInterval").value || "60", 10);
  if (!since) {
    $("logBox").textContent = "璇峰厛閫夋嫨鍘嗗彶鍚屾璧峰鏃堕棿";
    return;
  }
  setHistorySince(since, { persist: true });
  setBusy(true);
  try {
    await api("/api/tasks/history/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ since, interval_seconds: Number.isNaN(interval) ? 60 : interval }),
    });
    await loadTasks();
  } finally {
    setBusy(false);
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

$("startBtn").addEventListener("click", () => postAction("/api/tasks/continuous/start"));
$("stopBtn").addEventListener("click", () => postAction("/api/tasks/continuous/stop"));
$("runOnceBtn").addEventListener("click", () => postAction("/api/tasks/startup/run"));
$("historyStartBtn").addEventListener("click", startHistorySync);
$("historyStopBtn").addEventListener("click", () => postAction("/api/tasks/history/stop"));
$("historySince").addEventListener("change", () => setHistorySince($("historySince").value, { persist: true }));
$("refreshBtn").addEventListener("click", refreshAll);
$("categoryFilter").addEventListener("change", loadNews);
$("searchInput").addEventListener("input", () => {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(loadNews, 250);
});
$("closeDialog").addEventListener("click", () => $("detailDialog").close());

restoreHistorySince();
refreshAll().catch((error) => {
  $("logBox").textContent = `鍔犺浇澶辫触: ${error.message}`;
});
setInterval(() => {
  Promise.all([loadStats(), loadTasks()]).catch(() => {});
}, 2000);
