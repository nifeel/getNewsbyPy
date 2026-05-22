const state = {
  categories: [],
  busy: false,
  historySinceLoaded: false,
};

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
  $("syncRunnerStartBtn").disabled = value;
  $("syncRunnerStopBtn").disabled = value;
  $("historySaveBtn").disabled = value;
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
  $("categoryFilter").innerHTML = '<option value="">All categories</option>' + state.categories
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
  const items = data.items || [];
  $("taskList").innerHTML = items.length
    ? items.map(renderTask).join("")
    : '<div class="task"><div class="taskMeta">No tasks</div></div>';
  $("refreshTime").textContent = `Updated ${new Date().toLocaleTimeString()}`;
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
  if (normalized) {
    $("historySince").value = normalized;
  }
  state.historySinceLoaded = true;
}

async function saveHistoryConfig() {
  await api("/api/history-config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ since: $("historySince").value }),
  });
  appendLog(`History since saved: ${$("historySince").value}`);
}

function renderTask(item) {
  const status = escapeHtml(item.status || "unknown");
  const extra = item.task_name === "sync_task_runner"
    ? `<div class="taskMeta">pages=${item.pages ?? 0} current_id=${escapeHtml(item.current_old_id || "-")}</div>`
    : "";
  return `
    <article class="task">
      <div class="taskTop">
        <strong>${escapeHtml(taskLabel(item.task_name))}</strong>
        <span class="status ${status}">${status}</span>
      </div>
      <div class="taskMeta">cycle=${item.cycle ?? 0} inserted=${item.inserted ?? 0} skipped=${item.skipped ?? 0} total=${item.total ?? 0}</div>
      ${extra}
      <div class="taskMeta">pid=${item.pid || "-"} updated=${formatTime(item.updated_at)}</div>
      ${item.last_error ? `<div class="taskMeta error">${escapeHtml(item.last_error)}</div>` : ""}
      ${item.message ? `<div class="taskMeta">${escapeHtml(item.message)}</div>` : ""}
    </article>
  `;
}

function taskLabel(name) {
  const labels = {
    browser_keeper: "Browser Keeper",
    browser_collector: "Browser Collector",
    continuous_collector: "Continuous Collector",
    startup_collector: "Run Once",
    sync_task_runner: "Sync Task Runner",
  };
  return labels[name] || name || "Unknown";
}

function renderSyncTasks(data) {
  const items = data.items || [];
  if (!items.length) {
    $("syncTaskList").innerHTML = '<div class="taskMeta">No sync tasks yet — will be created automatically after the first collection cycle.</div>';
    return;
  }
  $("syncTaskList").innerHTML = items.map((t) => {
    const status = escapeHtml(t.status || "unknown");
    const type = t.target_since ? "history" : "gap";
    const range = t.target_since
      ? `since=${escapeHtml(t.target_since)}`
      : `end=${t.end_news_id ?? "-"}`;
    return `
      <div class="task compact">
        <div class="taskTop">
          <strong>${type} #${t.id}</strong>
          <span class="status ${status}">${status}</span>
        </div>
        <div class="taskMeta">start=${t.start_news_id} ${range} current=${t.current_news_id} inserted=${t.inserted} skipped=${t.skipped}</div>
      </div>
    `;
  }).join("");
}

function renderNews(data) {
  const items = data.items || [];
  $("newsList").innerHTML = items.length
    ? items.map(renderNewsItem).join("")
    : '<div class="newsItem"><h3>No news found</h3></div>';

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
        <button class="linkButton" data-detail-id="${item.id}">Detail</button>
      </div>
    </article>
  `;
}

async function showDetail(id) {
  const item = await api(`/api/news/${id}`);
  $("detailTitle").textContent = item.title || "Detail";
  $("detailBody").textContent = JSON.stringify(item, null, 2);
  $("detailDialog").showModal();
}

async function loadStats() {
  renderStats(await api("/api/stats"));
}

async function loadTasks() {
  renderTasks(await api("/api/tasks"));
}

async function loadSyncTasks() {
  renderSyncTasks(await api("/api/sync-tasks"));
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
  await Promise.all([loadStats(), loadTasks(), loadSyncTasks(), loadNews()]);
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
$("syncRunnerStartBtn").addEventListener("click", () => postAction("/api/tasks/sync-runner/start"));
$("syncRunnerStopBtn").addEventListener("click", () => postAction("/api/tasks/sync-runner/stop"));
$("historySaveBtn").addEventListener("click", () => {
  setBusy(true);
  saveHistoryConfig()
    .catch((e) => appendLog(`Save failed: ${e.message}`))
    .finally(() => setBusy(false));
});
$("refreshBtn").addEventListener("click", refreshAll);
$("categoryFilter").addEventListener("change", loadNews);
$("searchInput").addEventListener("input", () => {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(loadNews, 250);
});
$("closeDialog").addEventListener("click", () => $("detailDialog").close());

loadHistoryConfig().then(refreshAll).catch((error) => {
  appendLog(`Load failed: ${error.message}`);
});
setInterval(() => {
  Promise.all([loadStats(), loadTasks(), loadSyncTasks()]).catch(() => {});
}, 2000);
initLogStream();
