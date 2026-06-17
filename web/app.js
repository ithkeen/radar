const VIEW_LABELS = {
  curated: "Curated",
  all: "All",
  papers: "Papers",
  "open-source": "Open source",
  health: "Source health",
};

const state = {
  rangeDays: 30,
  view: "curated",
  language: "all",
  source: "all",
  tag: "all",
  query: "",
  activeArchive: "latest",
  index: null,
  latest: null,
  data: null,
  archives: new Map(),
};

const els = {
  runHealth: document.querySelector("#run-health"),
  lastUpdated: document.querySelector("#last-updated"),
  totalCount: document.querySelector("#total-count"),
  loadedCount: document.querySelector("#loaded-count"),
  visibleCount: document.querySelector("#visible-count"),
  retentionDays: document.querySelector("#retention-days"),
  sourceCount: document.querySelector("#source-count"),
  items: document.querySelector("#items"),
  sourceHealth: document.querySelector("#source-health"),
  tagCloud: document.querySelector("#tag-cloud"),
  language: document.querySelector("#language"),
  source: document.querySelector("#source"),
  tag: document.querySelector("#tag"),
  archive: document.querySelector("#archive"),
  query: document.querySelector("#query"),
  feedSubtitle: document.querySelector("#feed-subtitle"),
  sortNote: document.querySelector("#sort-note"),
  emptyTemplate: document.querySelector("#empty-template"),
};

async function loadJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Unable to load ${path}: ${response.status}`);
  }
  return response.json();
}

async function loadInitialData() {
  const [index, latest] = await Promise.all([loadJson("data/index.json"), loadJson("data/latest.json")]);
  state.index = index;
  state.latest = latest;
  state.data = latest;
}

async function loadArchive(month) {
  if (month === "latest") {
    state.activeArchive = "latest";
    state.data = state.latest;
    return;
  }

  if (!state.archives.has(month)) {
    const payload = await loadJson(`data/archive/${month}.json`);
    state.archives.set(month, {
      ...payload,
      generated_at: state.index.generated_at,
      display_window_days: state.index.display_window_days,
      retention_days: state.index.retention_days,
      source_status: state.index.source_status,
    });
  }

  state.activeArchive = month;
  state.data = state.archives.get(month);
}

function parseDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDate(value) {
  const date = parseDate(value);
  if (!date) return "--";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function timeAgo(value) {
  const date = parseDate(value);
  if (!date) return "--";
  const diffMs = Date.now() - date.getTime();
  const diffHours = Math.max(0, Math.round(diffMs / 36e5));
  if (diffHours < 1) return "just now";
  if (diffHours < 24) return `${diffHours}h ago`;
  return `${Math.round(diffHours / 24)}d ago`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function uniqueSorted(values) {
  return [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

function setOptions(select, values, allLabel) {
  const current = select.value || "all";
  select.innerHTML = `<option value="all">${allLabel}</option>`;
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.append(option);
  }
  select.value = values.includes(current) ? current : "all";
}

function hydrateArchiveOptions() {
  const current = state.activeArchive || "latest";
  els.archive.innerHTML = '<option value="latest">Latest 30 days</option>';
  for (const month of state.index.available_months || []) {
    const option = document.createElement("option");
    option.value = month;
    option.textContent = month;
    els.archive.append(option);
  }
  els.archive.value = current;
}

function itemMatchesRange(item) {
  if (state.activeArchive !== "latest") {
    return true;
  }
  const date = parseDate(item.published_at);
  if (!date) return false;
  const cutoff = Date.now() - state.rangeDays * 24 * 60 * 60 * 1000;
  return date.getTime() >= cutoff;
}

function itemMatchesView(item) {
  if (state.view === "curated") {
    return ["must_read", "noteworthy"].includes(item.tier);
  }
  if (state.view === "papers") {
    return item.source_type === "paper" || (item.tags || []).includes("research");
  }
  if (state.view === "open-source") {
    return item.source_type === "github_release" || (item.tags || []).includes("open-source");
  }
  return true;
}

function itemMatchesFilters(item) {
  const query = state.query.trim().toLowerCase();
  const searchable = [item.title, item.summary, item.source_name, ...(item.tags || [])].join(" ").toLowerCase();
  return (
    itemMatchesRange(item) &&
    itemMatchesView(item) &&
    (state.language === "all" || item.language === state.language) &&
    (state.source === "all" || item.source_id === state.source) &&
    (state.tag === "all" || (item.tags || []).includes(state.tag)) &&
    (!query || searchable.includes(query))
  );
}

function statusLabel(status) {
  if (status === "live") return "Live";
  if (status === "delayed") return "Delayed";
  if (status === "empty") return "Empty";
  if (status === "disabled") return "Disabled";
  return "Error";
}

function tierLabel(tier) {
  if (tier === "must_read") return "Must read";
  if (tier === "noteworthy") return "Noteworthy";
  return "Raw";
}

function currentStatuses() {
  return state.index?.source_status || state.data?.source_status || {};
}

function updateRunHealth(statuses) {
  const values = Object.values(statuses);
  const errorCount = values.filter((status) => status.status === "error").length;
  const warningCount = values.filter((status) => ["delayed", "empty", "disabled"].includes(status.status)).length;
  els.runHealth.classList.toggle("has-error", errorCount > 0);
  els.runHealth.classList.toggle("has-warning", errorCount === 0 && warningCount > 0);
  if (errorCount > 0) {
    els.runHealth.textContent = `${errorCount} source errors`;
  } else if (warningCount > 0) {
    els.runHealth.textContent =
      warningCount === 1 ? "1 source needs review" : `${warningCount} sources need review`;
  } else {
    els.runHealth.textContent = "All sources live";
  }
}

function updateSubtitle(visibleCount) {
  const viewName = VIEW_LABELS[state.view] || "Curated";
  const shard = state.activeArchive === "latest" ? `latest ${state.rangeDays}d` : `${state.activeArchive} archive`;
  els.feedSubtitle.textContent =
    state.view === "health"
      ? "Source status from the latest collection run."
      : `${viewName} view · ${shard} · ${visibleCount} visible signals.`;
  els.sortNote.textContent = state.view === "health" ? "Run status" : "Quality score";
}

function renderMetrics(visibleCount) {
  const data = state.data || {};
  const index = state.index || data;
  els.lastUpdated.textContent = `Last updated: ${formatDate(index.generated_at)}`;
  els.totalCount.textContent = String(index.total_retained ?? (data.items || []).length);
  els.loadedCount.textContent = String((data.items || []).length);
  els.visibleCount.textContent = String(visibleCount);
  els.retentionDays.textContent = `${index.retention_days || "--"}d`;
  els.sourceCount.textContent = `${Object.keys(currentStatuses()).length} sources`;
  updateRunHealth(currentStatuses());
}

function renderItems(items) {
  els.items.innerHTML = "";
  if (!items.length) {
    els.items.append(els.emptyTemplate.content.cloneNode(true));
    return;
  }

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "result-card";
    const tags = (item.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
    const qualityReasons = (item.quality_reasons || item.score_reasons || [])
      .slice(0, 3)
      .map((reason) => `<span class="reason">${escapeHtml(reason)}</span>`)
      .join("");

    card.innerHTML = `
      <div>
        <a class="item-title" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a>
        <div class="item-meta">
          <span>${escapeHtml(item.source_name)}</span>
          <span>${formatDate(item.published_at)}</span>
          <span>${timeAgo(item.published_at)}</span>
          <span>${escapeHtml(item.language || "unknown")}</span>
          <span class="tier ${escapeHtml(item.tier || "raw")}">${tierLabel(item.tier)}</span>
        </div>
        <p class="item-summary">${escapeHtml(item.summary || "No feed summary available.")}</p>
        <div class="tag-row">${tags || '<span class="reason">untagged</span>'}</div>
        <div class="reason-list">${qualityReasons}</div>
      </div>
      <div class="score-block">
        <div class="score">${escapeHtml(item.score)}</div>
        <div class="score-label">quality</div>
      </div>
    `;
    els.items.append(card);
  }
}

function renderHealthFeed(statuses) {
  els.items.innerHTML = "";
  const entries = Object.entries(statuses).sort(([, a], [, b]) => a.name.localeCompare(b.name));
  if (!entries.length) {
    els.items.append(els.emptyTemplate.content.cloneNode(true));
    return;
  }

  for (const [sourceId, status] of entries) {
    const card = document.createElement("article");
    card.className = "result-card health-result";
    card.innerHTML = `
      <div>
        <div class="source-card-header">
          <span class="health-dot ${escapeHtml(status.status)}" aria-hidden="true"></span>
          <span>${escapeHtml(status.name || sourceId)}</span>
        </div>
        <p class="item-summary">
          ${statusLabel(status.status)} · ${escapeHtml(status.item_count || 0)} fetched · latest ${formatDate(status.latest_item_at)}
        </p>
        ${status.error ? `<div class="reason-list"><span class="reason source-error">${escapeHtml(status.error)}</span></div>` : ""}
      </div>
      <div class="score-block">
        <div class="score small-score">${escapeHtml(status.status)}</div>
        <div class="score-label">status</div>
      </div>
    `;
    els.items.append(card);
  }
}

function renderSources(statuses) {
  els.sourceHealth.innerHTML = "";
  const entries = Object.entries(statuses).sort(([, a], [, b]) => a.name.localeCompare(b.name));
  for (const [sourceId, status] of entries) {
    const card = document.createElement("div");
    card.className = "source-card";
    card.innerHTML = `
      <div class="source-card-header">
        <span class="health-dot ${escapeHtml(status.status)}" aria-hidden="true"></span>
        <span>${escapeHtml(status.name || sourceId)}</span>
      </div>
      <p>${statusLabel(status.status)} · ${escapeHtml(status.item_count || 0)} fetched · latest ${formatDate(status.latest_item_at)}</p>
      ${status.error ? `<p class="source-error">${escapeHtml(status.error)}</p>` : ""}
    `;
    els.sourceHealth.append(card);
  }
}

function renderTagCloud(items) {
  const counts = new Map();
  for (const item of items) {
    for (const tag of item.tags || []) {
      counts.set(tag, (counts.get(tag) || 0) + 1);
    }
  }
  const tags = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 14);
  els.tagCloud.innerHTML = tags.length
    ? tags.map(([tag, count]) => `<span class="tag">${escapeHtml(tag)} · ${count}</span>`).join("")
    : '<span class="reason">No tags yet</span>';
}

function hydrateFilters(items) {
  const languages = uniqueSorted(items.map((item) => item.language));
  const sources = uniqueSorted(items.map((item) => item.source_id));
  const tags = uniqueSorted(items.flatMap((item) => item.tags || []));
  setOptions(els.language, languages, "All languages");
  setOptions(els.source, sources, "All sources");
  setOptions(els.tag, tags, "All tags");
}

function render() {
  const items = state.data?.items || [];
  hydrateArchiveOptions();
  hydrateFilters(items);
  renderSources(currentStatuses());

  if (state.view === "health") {
    const visibleCount = Object.keys(currentStatuses()).length;
    renderMetrics(visibleCount);
    updateSubtitle(visibleCount);
    renderHealthFeed(currentStatuses());
    renderTagCloud(items);
    return;
  }

  const filtered = items.filter(itemMatchesFilters);
  renderMetrics(filtered.length);
  updateSubtitle(filtered.length);
  renderItems(filtered);
  renderTagCloud(filtered);
}

function setActiveButton(selector, activeValue, attrName) {
  document.querySelectorAll(selector).forEach((control) => {
    control.classList.toggle("is-active", control.dataset[attrName] === activeValue);
  });
}

function bindControls() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      state.view = button.dataset.view;
      setActiveButton("[data-view]", state.view, "view");
      render();
    });
  });

  document.querySelectorAll("[data-range]").forEach((button) => {
    button.addEventListener("click", () => {
      state.rangeDays = Number(button.dataset.range);
      setActiveButton("[data-range]", String(state.rangeDays), "range");
      render();
    });
  });

  els.language.addEventListener("change", () => {
    state.language = els.language.value;
    render();
  });
  els.source.addEventListener("change", () => {
    state.source = els.source.value;
    render();
  });
  els.tag.addEventListener("change", () => {
    state.tag = els.tag.value;
    render();
  });
  els.archive.addEventListener("change", async () => {
    try {
      await loadArchive(els.archive.value);
      render();
    } catch (error) {
      els.runHealth.textContent = "Archive load failed";
      els.runHealth.classList.add("has-error");
      els.items.innerHTML = `<div class="empty-state"><h2>Archive could not be loaded.</h2><p>${escapeHtml(error.message)}</p></div>`;
    }
  });
  els.query.addEventListener("input", () => {
    state.query = els.query.value;
    render();
  });
}

async function init() {
  bindControls();
  try {
    await loadInitialData();
    render();
  } catch (error) {
    state.index = { source_status: {}, retention_days: "--", generated_at: null, total_retained: 0 };
    state.data = { items: [], source_status: {}, retention_days: "--", generated_at: null };
    render();
    els.runHealth.textContent = "Data load failed";
    els.runHealth.classList.add("has-error");
    els.items.innerHTML = `<div class="empty-state"><h2>Data could not be loaded.</h2><p>${escapeHtml(error.message)}</p></div>`;
  }
}

init();
