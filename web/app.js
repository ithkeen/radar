const CHANNEL_LABELS = {
  ai: "AI",
  finance: "Finance",
};

const FOCUS_LABELS = {
  priority: "Top signals",
  models: "Model updates",
  evaluations: "Model evaluations",
  "coding-agents": "Coding agents",
  "agent-practice": "Agent practice",
  builders: "Builder opinions",
  health: "Source health",
};

const state = {
  rangeDays: 30,
  channel: "ai",
  focus: "priority",
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
  items: document.querySelector("#items"),
  source: document.querySelector("#source"),
  tag: document.querySelector("#tag"),
  archive: document.querySelector("#archive"),
  query: document.querySelector("#query"),
  feedTitle: document.querySelector("#feed-title"),
  feedSubtitle: document.querySelector("#feed-subtitle"),
  sortNote: document.querySelector("#sort-note"),
  emptyTemplate: document.querySelector("#empty-template"),
};

const MODEL_UPDATE_KEYWORDS = [
  "new model",
  "model update",
  "model release",
  "frontier model",
  "open weights",
  "released model",
  "technical report",
  "new capabilities",
];

const MODEL_NAME_KEYWORDS = [
  "weights",
  "gpt",
  "claude",
  "gemini",
  "llama",
  "qwen",
  "deepseek",
  "mistral",
  "o3",
  "o4",
];

const MODEL_UPDATE_ACTION_KEYWORDS = [
  "announce",
  "announcing",
  "introduce",
  "introducing",
  "launch",
  "launches",
  "release",
  "released",
  "available",
  "capabilities",
  "preview",
];

const EVALUATION_KEYWORDS = [
  "benchmark",
  "benchmarks",
  "evaluation",
  "evaluations",
  "evaluate",
  "validated",
  "validation",
  "leaderboard",
  "arena",
  "swe-bench",
  "terminal-bench",
  "red-team",
  "robustness",
];

const CODING_AGENT_KEYWORDS = [
  "codex",
  "claude code",
  "coding agent",
  "code agent",
  "software engineering",
  "swe-bench",
  "developer tool",
  "github copilot",
  "cursor",
  "windsurf",
  "pull request",
  "repository issue",
  "github issue",
  "code generation",
];

const AGENT_TERMS = [
  "agent",
  "agents",
  "agentic",
  "multi-agent",
  "tool use",
  "computer use",
  "browser use",
  "mcp",
];

const AGENT_PRACTICE_TERMS = [
  "build",
  "building",
  "deploy",
  "production",
  "workflow",
  "practice",
  "guide",
  "pattern",
  "lessons",
  "implementation",
  "orchestration",
  "developer",
];

const BUILDER_OPINION_KEYWORDS = [
  "opinion",
  "perspective",
  "interview",
  "essay",
  "memo",
  "notes",
  "lessons",
  "how we",
  "why we",
  "behind",
  "researcher",
  "engineer",
  "team",
];

const PAPER_DOMAIN_EXCLUSIONS = [
  "robot",
  "robotics",
  "clinical",
  "medical",
  "healthcare",
  "cardiac",
  "legal",
  "judicial",
  "astronomical",
  "biology",
  "sensor",
  "cyber-defense",
  "cyber threat",
  "red agent",
];

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
  return new Intl.DateTimeFormat("en-US", {
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

// Builds a single lower-case search surface; it does not mutate persisted item fields.
function itemSearchText(item) {
  return [item.title, item.summary, item.source_name, ...(item.tags || [])].join(" ").toLowerCase();
}

// Uses substring checks because feed summaries are short and already normalized by the collector.
function matchesAny(text, keywords) {
  return keywords.some((keyword) => text.includes(keyword));
}

// The AI channel deliberately narrows papers to practical model, eval, and coding-agent signals.
function isTargetAiPaper(item) {
  if (item.source_type !== "paper") return false;
  const text = itemSearchText(item);
  const excludedDomain = matchesAny(text, PAPER_DOMAIN_EXCLUSIONS);
  const evaluationSignal = matchesEvaluation(item);
  const codingAgentSignal = matchesCodingAgent(item);
  const modelReleaseSignal =
    matchesModelUpdate(item) &&
    matchesAny(text, ["model release", "technical report", "open weights", "released model", "release of"]);
  if (excludedDomain && !codingAgentSignal && !evaluationSignal) return false;
  return codingAgentSignal || evaluationSignal || modelReleaseSignal;
}

// Treats model names as relevant only when paired with release or capability-update language.
function matchesModelUpdate(item) {
  const text = itemSearchText(item);
  return (
    matchesAny(text, MODEL_UPDATE_KEYWORDS) ||
    (matchesAny(text, MODEL_NAME_KEYWORDS) && matchesAny(text, MODEL_UPDATE_ACTION_KEYWORDS))
  );
}

// Keeps benchmarks tied to models, agents, or coding rather than broad academic evaluation.
function matchesEvaluation(item) {
  const text = itemSearchText(item);
  const hasEvaluation = matchesAny(text, EVALUATION_KEYWORDS);
  const hasAiSubject = matchesAny(text, [
    ...MODEL_UPDATE_KEYWORDS,
    ...MODEL_NAME_KEYWORDS,
    ...AGENT_TERMS,
    ...CODING_AGENT_KEYWORDS,
    "llm",
    "language model",
  ]);
  return hasEvaluation && hasAiSubject;
}

// Captures Codex, Claude Code, and adjacent software-engineering agent updates.
function matchesCodingAgent(item) {
  const text = itemSearchText(item);
  return matchesAny(text, CODING_AGENT_KEYWORDS);
}

// Requires both an agent term and a practical-build term to avoid generic agent research.
function matchesAgentPractice(item) {
  const text = itemSearchText(item);
  return matchesAny(text, AGENT_TERMS) && matchesAny(text, AGENT_PRACTICE_TERMS);
}

// Employee and builder viewpoints are expected from company/blog feeds, not arXiv papers.
function matchesBuilderOpinion(item) {
  if (item.source_type === "paper") return false;
  const text = itemSearchText(item);
  return matchesAny(text, BUILDER_OPINION_KEYWORDS);
}

// Defines the AI channel's top-level admission rule before focus-specific filtering.
function isPreferredAiSignal(item) {
  if (item.source_type === "paper") {
    return isTargetAiPaper(item);
  }
  return (
    matchesModelUpdate(item) ||
    matchesEvaluation(item) ||
    matchesCodingAgent(item) ||
    matchesAgentPractice(item) ||
    matchesBuilderOpinion(item)
  );
}

// Finance is intentionally reserved, so only AI items can enter the current channel feed.
function itemMatchesChannel(item) {
  return state.channel === "ai" && isPreferredAiSignal(item);
}

// Applies the selected AI focus after the channel-level admission rule has passed.
function itemMatchesFocus(item) {
  if (!isPreferredAiSignal(item)) return false;
  if (state.focus === "models") return matchesModelUpdate(item);
  if (state.focus === "evaluations") return matchesEvaluation(item);
  if (state.focus === "coding-agents") return matchesCodingAgent(item);
  if (state.focus === "agent-practice") return matchesAgentPractice(item);
  if (state.focus === "builders") return matchesBuilderOpinion(item);
  return ["must_read", "noteworthy"].includes(item.tier);
}

function itemMatchesFilters(item) {
  const query = state.query.trim().toLowerCase();
  const searchable = itemSearchText(item);
  return (
    itemMatchesRange(item) &&
    itemMatchesFocus(item) &&
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
  const shard = state.activeArchive === "latest" ? `latest ${state.rangeDays}d` : `${state.activeArchive} archive`;
  els.feedTitle.textContent = `${CHANNEL_LABELS[state.channel] || "AI"} Channel`;

  if (state.channel === "finance") {
    els.feedSubtitle.textContent = "Finance is reserved for a future channel.";
    els.sortNote.textContent = "Reserved";
    return;
  }

  if (state.focus === "health") {
    els.feedSubtitle.textContent = "Source status from the latest collection run.";
    els.sortNote.textContent = "Run status";
    return;
  }

  const focusName = FOCUS_LABELS[state.focus] || "Top signals";
  els.feedSubtitle.textContent = `${focusName} · ${shard} · ${visibleCount} visible signals.`;
  els.sortNote.textContent = "AI relevance";
}

function renderMetrics(visibleCount) {
  const data = state.data || {};
  const index = state.index || data;
  els.lastUpdated.textContent = `Last updated: ${formatDate(index.generated_at)}`;
  els.totalCount.textContent = String(index.total_retained ?? (data.items || []).length);
  els.loadedCount.textContent = String((data.items || []).length);
  els.visibleCount.textContent = String(visibleCount);
  els.retentionDays.textContent = `${index.retention_days || "--"}d`;
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

// Renders the placeholder channel without inventing finance data or metrics.
function renderReservedChannel() {
  els.items.innerHTML = `
    <div class="empty-state reserved-state">
      <h2>Finance channel is reserved.</h2>
      <p>The current build keeps the feed focused on AI signals.</p>
    </div>
  `;
}

function hydrateFilters(items) {
  const sources = uniqueSorted(items.map((item) => item.source_id));
  const tags = uniqueSorted(items.flatMap((item) => item.tags || []));
  setOptions(els.source, sources, "All sources");
  setOptions(els.tag, tags, "All tags");
}

function render() {
  const channelItems = (state.data?.items || []).filter(itemMatchesChannel);
  hydrateArchiveOptions();
  hydrateFilters(channelItems);

  if (state.channel === "finance") {
    renderMetrics(0);
    updateSubtitle(0);
    renderReservedChannel();
    return;
  }

  if (state.focus === "health") {
    const visibleCount = Object.keys(currentStatuses()).length;
    renderMetrics(visibleCount);
    updateSubtitle(visibleCount);
    renderHealthFeed(currentStatuses());
    return;
  }

  const filtered = channelItems.filter(itemMatchesFilters);
  renderMetrics(filtered.length);
  updateSubtitle(filtered.length);
  renderItems(filtered);
}

function setActiveButton(selector, activeValue, attrName) {
  document.querySelectorAll(selector).forEach((control) => {
    control.classList.toggle("is-active", control.dataset[attrName] === activeValue);
  });
}

function bindControls() {
  document.querySelectorAll("[data-channel]").forEach((button) => {
    button.addEventListener("click", () => {
      state.channel = button.dataset.channel;
      setActiveButton("[data-channel]", state.channel, "channel");
      render();
    });
  });

  document.querySelectorAll("[data-focus]").forEach((button) => {
    button.addEventListener("click", () => {
      state.focus = button.dataset.focus;
      setActiveButton("[data-focus]", state.focus, "focus");
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
