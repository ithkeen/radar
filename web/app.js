const state = {
  rangeDays: 30,
  language: "all",
  source: "all",
  tag: "all",
  query: "",
  data: null,
};

const els = {
  runHealth: document.querySelector("#run-health"),
  lastUpdated: document.querySelector("#last-updated"),
  totalCount: document.querySelector("#total-count"),
  visibleCount: document.querySelector("#visible-count"),
  retentionDays: document.querySelector("#retention-days"),
  sourceCount: document.querySelector("#source-count"),
  items: document.querySelector("#items"),
  sourceHealth: document.querySelector("#source-health"),
  tagCloud: document.querySelector("#tag-cloud"),
  language: document.querySelector("#language"),
  source: document.querySelector("#source"),
  tag: document.querySelector("#tag"),
  query: document.querySelector("#query"),
  emptyTemplate: document.querySelector("#empty-template"),
};

async function loadData() {
  const response = await fetch("data/items.json", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Unable to load data/items.json: ${response.status}`);
  }
  return response.json();
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

function itemMatchesRange(item) {
  const date = parseDate(item.published_at);
  if (!date) return false;
  const cutoff = Date.now() - state.rangeDays * 24 * 60 * 60 * 1000;
  return date.getTime() >= cutoff;
}

function itemMatchesFilters(item) {
  const query = state.query.trim().toLowerCase();
  const searchable = [item.title, item.summary, item.source_name, ...(item.tags || [])].join(" ").toLowerCase();
  return (
    itemMatchesRange(item) &&
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

function renderMetrics(filteredItems) {
  const data = state.data;
  els.lastUpdated.textContent = `Last updated: ${formatDate(data.generated_at)}`;
  els.totalCount.textContent = String((data.items || []).length);
  els.visibleCount.textContent = String(filteredItems.length);
  els.retentionDays.textContent = `${data.retention_days || "--"}d`;
  els.sourceCount.textContent = `${Object.keys(data.source_status || {}).length} sources`;
  updateRunHealth(data.source_status || {});
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
    const tags = (item.tags || []).map((tag) => `<span class="tag">${tag}</span>`).join("");
    const reasons = (item.score_reasons || [])
      .slice(0, 3)
      .map((reason) => `<span class="reason">${reason}</span>`)
      .join("");

    card.innerHTML = `
      <div>
        <a class="item-title" href="${item.url}" target="_blank" rel="noopener noreferrer">${item.title}</a>
        <div class="item-meta">
          <span>${item.source_name}</span>
          <span>${formatDate(item.published_at)}</span>
          <span>${timeAgo(item.published_at)}</span>
          <span>${item.language || "unknown"}</span>
        </div>
        <p class="item-summary">${item.summary || "No feed summary available."}</p>
        <div class="tag-row">${tags || '<span class="reason">untagged</span>'}</div>
        <div class="reason-list">${reasons}</div>
      </div>
      <div class="score-block">
        <div class="score">${item.score}</div>
        <div class="score-label">score</div>
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
        <span class="health-dot ${status.status}" aria-hidden="true"></span>
        <span>${status.name || sourceId}</span>
      </div>
      <p>${statusLabel(status.status)} · ${status.item_count || 0} fetched · latest ${formatDate(status.latest_item_at)}</p>
      ${status.error ? `<p class="source-error">${status.error}</p>` : ""}
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
    ? tags.map(([tag, count]) => `<span class="tag">${tag} · ${count}</span>`).join("")
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
  const items = state.data.items || [];
  hydrateFilters(items);
  const filtered = items.filter(itemMatchesFilters);
  renderMetrics(filtered);
  renderItems(filtered);
  renderSources(state.data.source_status || {});
  renderTagCloud(filtered);
}

function bindControls() {
  document.querySelectorAll("[data-range]").forEach((button) => {
    button.addEventListener("click", () => {
      state.rangeDays = Number(button.dataset.range);
      document.querySelectorAll("[data-range]").forEach((control) => control.classList.remove("is-active"));
      button.classList.add("is-active");
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
  els.query.addEventListener("input", () => {
    state.query = els.query.value;
    render();
  });
}

async function init() {
  bindControls();
  try {
    state.data = await loadData();
    render();
  } catch (error) {
    state.data = { items: [], source_status: {}, retention_days: "--", generated_at: null };
    render();
    els.runHealth.textContent = "Data load failed";
    els.runHealth.classList.add("has-error");
    els.items.innerHTML = `<div class="empty-state"><h2>Data could not be loaded.</h2><p>${error.message}</p></div>`;
  }
}

init();
