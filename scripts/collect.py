#!/usr/bin/env python3
"""Collect trusted AI/technology feed items into static dashboard datasets."""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

DATA_VERSION = 2
USER_AGENT = "radar-ai-feed-collector/1.1 (+https://github.com/ithkeen/radar)"
DROP_QUERY_KEYS = {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "ref_src"}
ARCHIVE_DIRNAME = "archive"
LEGACY_ITEMS_FILENAME = "items.json"

TAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "model": ("model", "llm", "gpt", "claude", "gemini", "llama", "qwen", "deepseek", "模型", "大模型"),
    "multimodal": ("multimodal", "vision", "audio", "video", "image", "多模态", "视觉", "语音", "视频"),
    "agents": ("agent", "agents", "tool use", "workflow", "mcp", "代理", "智能体"),
    "research": ("paper", "arxiv", "benchmark", "evaluation", "dataset", "论文", "评测", "基准"),
    "open-source": ("open source", "github", "release", "weights", "开源", "权重"),
    "inference": ("inference", "latency", "serving", "quantization", "推理", "量化", "部署"),
    "safety": ("safety", "alignment", "policy", "risk", "安全", "对齐", "风险"),
    "product": ("launch", "product", "api", "app", "preview", "发布", "产品", "接口"),
    "regulation": ("regulation", "policy", "law", "standard", "监管", "政策", "标准"),
    "hardware": ("gpu", "chip", "accelerator", "cuda", "芯片", "算力"),
}

SOURCE_TYPE_BONUS = {
    "official": 10,
    "paper": 7,
    "github_release": 6,
    "blog": 5,
    "data": 5,
}

DEFAULT_HOMEPAGE_LIMIT = {
    "official": 12,
    "paper": 8,
    "github_release": 4,
    "blog": 8,
    "data": 6,
}

DEFAULT_DAILY_LIMIT = {
    "official": 6,
    "paper": 8,
    "github_release": 2,
    "blog": 5,
    "data": 4,
}

HIGH_IMPACT_KEYWORDS = (
    "announce",
    "announcing",
    "introduce",
    "introducing",
    "launch",
    "release",
    "new model",
    "frontier",
    "state-of-the-art",
    "open weights",
    "open-source",
    "api",
    "generally available",
    "benchmark",
    "safety",
    "policy",
    "发布",
    "推出",
    "开源",
    "模型",
    "基准",
)

LOW_VALUE_KEYWORDS = (
    "course",
    "webinar",
    "event",
    "hiring",
    "jobs",
    "career",
    "customer story",
    "case study",
    "academy",
    "podcast",
    "newsletter",
    "recap",
    "课程",
    "招聘",
    "活动",
    "客户案例",
)

SIGNAL_KEYWORDS = (
    "openai",
    "anthropic",
    "google",
    "microsoft",
    "hugging face",
    "gemini",
    "claude",
    "gpt",
    "codex",
    "llama",
    "qwen",
    "deepseek",
    "mcp",
    "agent",
    "agents",
    "safety",
    "benchmark",
)


def utc_now() -> dt.datetime:
    """Return an aware UTC timestamp so persisted output is timezone-stable."""
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_z(value: dt.datetime) -> str:
    """Serialize timestamps as compact UTC ISO strings used by the public JSON contract."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    value = value.astimezone(dt.timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


def parse_datetime(value: str | None) -> dt.datetime | None:
    """Parse common feed date formats without guessing when the value is absent."""
    if not value:
        return None
    text = html.unescape(value).strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).replace(microsecond=0)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def direct_child_text(element: ET.Element, names: set[str]) -> str:
    for child in list(element):
        if local_name(child.tag) in names:
            return "".join(child.itertext()).strip()
    return ""


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    unescaped = html.unescape(value)
    without_tags = re.sub(r"<[^>]+>", " ", unescaped)
    return re.sub(r"\s+", " ", without_tags).strip()


def truncate_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def normalize_url(url: str) -> str:
    """Canonicalize URLs for dedupe while preserving meaningful source paths."""
    parsed = urllib.parse.urlsplit(url.strip())
    query = []
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower.startswith("utm_") or key_lower in DROP_QUERY_KEYS:
            continue
        query.append((key, value))

    path = parsed.path.rstrip("/") or parsed.path
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            urllib.parse.urlencode(query, doseq=True),
            "",
        )
    )


def canonical_title(title: str) -> str:
    text = html.unescape(title).lower()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def stable_hash(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def fetch_feed(source: dict[str, Any], timeout: int) -> str:
    """Fetch exactly one machine-readable source URL; arbitrary page crawling is out of scope."""
    request = urllib.request.Request(source["url"], headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def extract_atom_link(entry: ET.Element) -> str:
    fallback = ""
    for child in list(entry):
        if local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href", "").strip()
        if not href:
            continue
        if child.attrib.get("rel", "alternate") == "alternate":
            return href
        fallback = fallback or href
    return fallback or direct_child_text(entry, {"id"})


def extract_categories(entry: ET.Element) -> list[str]:
    categories: list[str] = []
    for child in list(entry):
        name = local_name(child.tag)
        if name == "category":
            category = child.attrib.get("term") or "".join(child.itertext())
            if category.strip():
                categories.append(category.strip())
        elif name == "primary_category":
            category = child.attrib.get("term", "")
            if category.strip():
                categories.append(category.strip())
    return categories


def parse_feed(xml_text: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse RSS or Atom-like XML into raw entries consumed by the normalization pipeline."""
    root = ET.fromstring(xml_text)
    entries: list[dict[str, Any]] = []
    rss_items = [element for element in root.iter() if local_name(element.tag) == "item"]
    atom_entries = [element for element in root.iter() if local_name(element.tag) == "entry"]

    for item in rss_items:
        title = direct_child_text(item, {"title"})
        link = direct_child_text(item, {"link", "guid"})
        summary = direct_child_text(item, {"description", "summary", "encoded", "content"})
        published = direct_child_text(item, {"pubdate", "published", "updated", "date"})
        entries.append(
            {
                "title": strip_html(title),
                "url": link.strip(),
                "summary": strip_html(summary),
                "published_at": published,
                "categories": extract_categories(item),
            }
        )

    for entry in atom_entries:
        title = direct_child_text(entry, {"title"})
        summary = direct_child_text(entry, {"summary", "content", "subtitle"})
        published = direct_child_text(entry, {"published", "updated"})
        entries.append(
            {
                "title": strip_html(title),
                "url": extract_atom_link(entry),
                "summary": strip_html(summary),
                "published_at": published,
                "categories": extract_categories(entry),
            }
        )

    return [entry for entry in entries if entry.get("title") and entry.get("url")]


def detect_language(text: str, fallback: str) -> str:
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_chars = len(re.findall(r"[A-Za-z]", text))
    if chinese_chars >= 4:
        return "zh"
    if fallback in {"en", "zh"}:
        return fallback
    if latin_chars:
        return "en"
    return "unknown"


def infer_tags(title: str, summary: str, categories: list[str]) -> list[str]:
    text = " ".join([title, summary, " ".join(categories)]).lower()
    tags: list[str] = []
    for tag, keywords in TAG_KEYWORDS.items():
        if any(keyword.lower() in text for keyword in keywords):
            tags.append(tag)
    if any(category.lower().startswith(("cs.", "stat.")) for category in categories):
        if "research" not in tags:
            tags.append("research")
    return tags[:6]


def score_item(
    source: dict[str, Any],
    published_at: dt.datetime,
    tags: list[str],
    missing_date: bool,
    has_summary: bool,
    now: dt.datetime,
) -> tuple[int, list[str]]:
    """Score feed relevance deterministically before the V1.1 quality tiering pass."""
    base = int(source.get("weight", 45))
    score = base
    reasons = [f"trusted source weight {base}"]

    age_hours = max(0.0, (now - published_at).total_seconds() / 3600)
    if missing_date:
        recency_bonus = 4
        reasons.append("missing published date; used fetch time")
    elif age_hours <= 24:
        recency_bonus = 25
        reasons.append("published in the last 24h")
    elif age_hours <= 24 * 7:
        recency_bonus = 18
        reasons.append("published in the last 7 days")
    elif age_hours <= 24 * 30:
        recency_bonus = 10
        reasons.append("published in the last 30 days")
    else:
        recency_bonus = 2
        reasons.append("older than 30 days")
    score += recency_bonus

    type_bonus = SOURCE_TYPE_BONUS.get(str(source.get("type", "")), 4)
    score += type_bonus
    reasons.append(f"{source.get('type', 'source')} source")

    if tags:
        tag_bonus = min(14, len(tags) * 3)
        score += tag_bonus
        reasons.append("matched tags: " + ", ".join(tags[:4]))

    if not has_summary:
        score -= 4
        reasons.append("no feed summary")

    return max(0, min(100, score)), reasons


def build_item(entry: dict[str, Any], source: dict[str, Any], fetched_at: dt.datetime, now: dt.datetime) -> dict[str, Any]:
    """Normalize a raw feed entry into the public item schema before quality tiering."""
    published_dt = parse_datetime(entry.get("published_at"))
    missing_date = published_dt is None
    if published_dt is None:
        published_dt = fetched_at

    title = entry["title"].strip()
    url = normalize_url(entry["url"])
    raw_excerpt = truncate_text(entry.get("summary", ""), 600)
    summary = truncate_text(raw_excerpt, 240)
    categories = list(entry.get("categories", []))
    language = detect_language(" ".join([title, raw_excerpt]), str(source.get("language", "mixed")))
    tags = infer_tags(title, raw_excerpt, categories)
    score, score_reasons = score_item(source, published_dt, tags, missing_date, bool(raw_excerpt), now)

    content_hash = stable_hash("|".join([canonical_title(title), raw_excerpt[:500]]))
    identity_seed = url or f"{source['id']}:{canonical_title(title)}:{iso_z(published_dt)[:10]}"

    return {
        "id": stable_hash(identity_seed),
        "title": title,
        "url": url,
        "source_id": source["id"],
        "source_name": source["name"],
        "source_type": source["type"],
        "published_at": iso_z(published_dt),
        "fetched_at": iso_z(fetched_at),
        "language": language,
        "tags": tags,
        "summary": summary,
        "raw_excerpt": raw_excerpt,
        "score": score,
        "score_reasons": score_reasons,
        "tier": "raw",
        "quality_reasons": [],
        "content_hash": content_hash,
    }


def item_timestamp(item: dict[str, Any]) -> dt.datetime:
    parsed = parse_datetime(item.get("published_at")) or parse_datetime(item.get("fetched_at"))
    return parsed or dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


def archive_month(item: dict[str, Any]) -> str:
    timestamp = item_timestamp(item)
    return f"{timestamp.year:04d}-{timestamp.month:02d}"


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(items, key=lambda item: (-int(item.get("score", 0)), -item_timestamp(item).timestamp(), item.get("id", "")))
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in ordered:
        title_key = canonical_title(str(item.get("title", "")))
        keys = []
        if item.get("url"):
            keys.append("url:" + normalize_url(str(item["url"])))
        if len(title_key) >= 12:
            keys.append("title:" + title_key)
        if any(key in seen for key in keys):
            continue
        seen.update(keys)
        unique.append(item)

    return sorted(unique, key=quality_sort_key)


def merge_items(
    existing_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    now: dt.datetime,
    retention_days: int,
) -> list[dict[str, Any]]:
    """Merge new and stored archive items, then enforce the retention boundary."""
    cutoff = now - dt.timedelta(days=retention_days)
    retained = [item for item in existing_items + new_items if item_timestamp(item) >= cutoff]
    return dedupe_items(retained)


def load_sources(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    sources = payload.get("sources", payload)
    if not isinstance(sources, list):
        raise ValueError("sources config must be a list or an object with a sources list")
    return sources


def read_archive_items(data_dir: Path) -> list[dict[str, Any]]:
    """Load only V1.1 archive shards; the retired data/items.json contract is intentionally ignored."""
    archive_dir = data_dir / ARCHIVE_DIRNAME
    if not archive_dir.exists():
        return []

    items: list[dict[str, Any]] = []
    for path in sorted(archive_dir.glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            items.extend(payload.get("items", []))
    return items


def source_health_status(items: list[dict[str, Any]], fetched_at: dt.datetime) -> tuple[str, str | None]:
    if not items:
        return "empty", None
    latest = max(item_timestamp(item) for item in items)
    if fetched_at - latest > dt.timedelta(days=14):
        return "delayed", iso_z(latest)
    return "live", iso_z(latest)


def collect_from_sources(
    sources: list[dict[str, Any]],
    timeout: int,
    now: dt.datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch enabled sources independently so one broken feed never blocks the dashboard."""
    new_items: list[dict[str, Any]] = []
    source_status: dict[str, Any] = {}

    for source in sources:
        fetched_at = now
        source_id = source["id"]
        if not source.get("enabled", True):
            source_status[source_id] = {
                "name": source.get("name", source_id),
                "status": "disabled",
                "latest_item_at": None,
                "fetched_at": iso_z(fetched_at),
                "item_count": 0,
                "error": None,
            }
            continue

        try:
            xml_text = fetch_feed(source, timeout)
            entries = parse_feed(xml_text, source)
            source_items = [build_item(entry, source, fetched_at, now) for entry in entries]
            new_items.extend(source_items)
            status, latest_item_at = source_health_status(source_items, fetched_at)
            source_status[source_id] = {
                "name": source.get("name", source_id),
                "status": status,
                "latest_item_at": latest_item_at,
                "fetched_at": iso_z(fetched_at),
                "item_count": len(source_items),
                "error": None,
            }
        except (ET.ParseError, urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            source_status[source_id] = {
                "name": source.get("name", source_id),
                "status": "error",
                "latest_item_at": None,
                "fetched_at": iso_z(fetched_at),
                "item_count": 0,
                "error": str(error),
            }

    return new_items, source_status


def source_map(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(source["id"]): source for source in sources}


def item_text(item: dict[str, Any]) -> str:
    return " ".join([str(item.get("title", "")), str(item.get("summary", "")), " ".join(item.get("tags", []))]).lower()


def configured_keywords(source: dict[str, Any], key: str) -> tuple[str, ...]:
    rules = source.get("quality_rules") or {}
    values = rules.get(key) if isinstance(rules, dict) else None
    if isinstance(values, list):
        return tuple(str(value).lower() for value in values)
    return ()


def signal_keys(item: dict[str, Any]) -> set[str]:
    text = item_text(item)
    keys = {keyword for keyword in SIGNAL_KEYWORDS if keyword in text}
    keys.update(tag for tag in item.get("tags", []) if tag in {"model", "agents", "safety", "open-source"})
    return keys


def clustered_signal_sources(items: list[dict[str, Any]], now: dt.datetime) -> dict[str, set[str]]:
    """Find recent cross-source themes that deserve a deterministic quality boost."""
    cutoff = now - dt.timedelta(hours=48)
    clusters: dict[str, set[str]] = {}
    for item in items:
        if item_timestamp(item) < cutoff:
            continue
        for key in signal_keys(item):
            clusters.setdefault(key, set()).add(str(item.get("source_id", "")))
    return {key: sources for key, sources in clusters.items() if len(sources) > 1}


def tier_for_score(score: int) -> str:
    if score >= 88:
        return "must_read"
    if score >= 74:
        return "noteworthy"
    return "raw"


def evaluate_quality(
    item: dict[str, Any],
    source: dict[str, Any],
    multi_source_signals: dict[str, set[str]],
    now: dt.datetime,
) -> tuple[int, str, list[str]]:
    """Convert a normalized item into the public quality tier used by the dashboard."""
    base_score, _ = score_item(
        source,
        item_timestamp(item),
        list(item.get("tags", [])),
        False,
        bool(item.get("summary") or item.get("raw_excerpt")),
        now,
    )
    score = base_score
    reasons: list[str] = []
    text = item_text(item)
    source_type = str(item.get("source_type", source.get("type", "")))
    tags = set(item.get("tags", []))
    high_keywords = HIGH_IMPACT_KEYWORDS + configured_keywords(source, "high_impact_keywords")
    low_keywords = LOW_VALUE_KEYWORDS + configured_keywords(source, "low_value_keywords")

    if any(keyword in text for keyword in high_keywords):
        score += 8
        reasons.append("high-impact launch/research keywords")

    if source_type == "official" and {"model", "product"} & tags:
        score += 6
        reasons.append("official model or product signal")

    if source_type == "paper" and "research" in tags:
        score += 3
        reasons.append("research feed item")

    if source_type == "github_release":
        if re.search(r"\bv?\d+\.\d+(\.\d+)?\b", str(item.get("title", "")).lower()):
            score -= 6
            reasons.append("routine versioned release")
        if {"model", "agents", "inference", "open-source"} & tags:
            score += 5
            reasons.append("open-source AI infrastructure update")

    matched_low_value = [keyword for keyword in low_keywords if keyword in text]
    if matched_low_value:
        score -= 22
        reasons.append("low-signal content: " + ", ".join(matched_low_value[:3]))

    matching_signals = signal_keys(item) & set(multi_source_signals)
    if matching_signals:
        score += 7
        reasons.append("same theme appeared across multiple sources")

    if len(str(item.get("summary", ""))) < 80:
        score -= 3
        reasons.append("thin feed summary")

    final_score = max(0, min(100, score))
    tier = "raw" if matched_low_value else tier_for_score(final_score)
    if not reasons:
        reasons.append("baseline source and recency score")
    return final_score, tier, reasons


def quality_sort_key(item: dict[str, Any]) -> tuple[float, int, str]:
    return (-int(item.get("score", 0)), -item_timestamp(item).timestamp(), str(item.get("title", "")))


def apply_source_quotas(
    items: list[dict[str, Any]],
    sources_by_id: dict[str, dict[str, Any]],
    now: dt.datetime,
    display_window_days: int,
) -> None:
    """Demote lower-ranked homepage candidates when one source would dominate the feed."""
    cutoff = now - dt.timedelta(days=display_window_days)
    candidates = [
        item
        for item in sorted(items, key=quality_sort_key)
        if item.get("tier") != "raw" and item_timestamp(item) >= cutoff
    ]
    source_counts: dict[str, int] = {}
    source_day_counts: dict[tuple[str, str], int] = {}

    for item in candidates:
        source_id = str(item.get("source_id", ""))
        source = sources_by_id.get(source_id, {})
        source_type = str(item.get("source_type", source.get("type", "")))
        homepage_limit = int(source.get("homepage_limit", DEFAULT_HOMEPAGE_LIMIT.get(source_type, 6)))
        daily_limit = int(source.get("daily_limit", DEFAULT_DAILY_LIMIT.get(source_type, 4)))
        day_key = item_timestamp(item).date().isoformat()
        source_counts[source_id] = source_counts.get(source_id, 0) + 1
        source_day_key = (source_id, day_key)
        source_day_counts[source_day_key] = source_day_counts.get(source_day_key, 0) + 1

        if source_counts[source_id] > homepage_limit or source_day_counts[source_day_key] > daily_limit:
            item["tier"] = "raw"
            item["score"] = max(0, int(item.get("score", 0)) - 10)
            item.setdefault("quality_reasons", []).append("demoted by source diversity quota")


def apply_quality_tiers(
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    now: dt.datetime,
    display_window_days: int,
) -> list[dict[str, Any]]:
    """Apply V1.1 deterministic curation without changing item identity or requiring an API key."""
    sources_by_id = source_map(sources)
    multi_source_signals = clustered_signal_sources(items, now)
    enriched: list[dict[str, Any]] = []
    for item in items:
        source = sources_by_id.get(str(item.get("source_id", "")), {})
        updated = dict(item)
        score, tier, reasons = evaluate_quality(updated, source, multi_source_signals, now)
        updated["score"] = score
        updated["tier"] = tier
        updated["quality_reasons"] = reasons
        enriched.append(updated)

    apply_source_quotas(enriched, sources_by_id, now, display_window_days)
    return sorted(enriched, key=quality_sort_key)


def write_json_if_changed(path: Path, payload: dict[str, Any]) -> bool:
    """Write pretty JSON only when bytes changed so stable archive shards avoid Git churn."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True


def grouped_by_month(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(archive_month(item), []).append(item)
    return {month: sorted(month_items, key=quality_sort_key) for month, month_items in grouped.items()}


def archive_payload(month: str, items: list[dict[str, Any]], retention_days: int) -> dict[str, Any]:
    """Return the month shard contract consumed by the static dashboard's lazy loader."""
    return {
        "version": DATA_VERSION,
        "month": month,
        "retention_days": retention_days,
        "items": items,
    }


def write_archive_shards(
    data_dir: Path,
    items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    now: dt.datetime,
    retention_days: int,
) -> list[str]:
    """Write changed month shards and delete shards outside the retention window."""
    archive_dir = data_dir / ARCHIVE_DIRNAME
    grouped = grouped_by_month(items)
    new_months = {archive_month(item) for item in new_items}
    current_month = f"{now.year:04d}-{now.month:02d}"
    months_to_write = new_months | {current_month}

    for month, month_items in grouped.items():
        path = archive_dir / f"{month}.json"
        if month in months_to_write or not path.exists():
            write_json_if_changed(path, archive_payload(month, month_items, retention_days))

    cutoff = now - dt.timedelta(days=retention_days)
    if archive_dir.exists():
        for path in archive_dir.glob("*.json"):
            month = path.stem
            if month not in grouped:
                path.unlink()
                continue
            month_start = parse_datetime(f"{month}-01T00:00:00Z")
            if month_start and month_start < cutoff.replace(day=1, hour=0, minute=0, second=0, microsecond=0):
                path.unlink()

    return sorted(grouped, reverse=True)


def latest_items(items: list[dict[str, Any]], now: dt.datetime, display_window_days: int) -> list[dict[str, Any]]:
    cutoff = now - dt.timedelta(days=display_window_days)
    return [item for item in sorted(items, key=quality_sort_key) if item_timestamp(item) >= cutoff]


def build_index_payload(
    generated_at: dt.datetime,
    display_window_days: int,
    retention_days: int,
    source_status: dict[str, Any],
    available_months: list[str],
    total_retained: int,
    latest_count: int,
) -> dict[str, Any]:
    """Return the small boot metadata file loaded before any dashboard data shard."""
    return {
        "version": DATA_VERSION,
        "generated_at": iso_z(generated_at),
        "display_window_days": display_window_days,
        "retention_days": retention_days,
        "available_months": available_months,
        "source_status": source_status,
        "total_retained": total_retained,
        "latest_count": latest_count,
    }


def build_latest_payload(
    generated_at: dt.datetime,
    display_window_days: int,
    retention_days: int,
    source_status: dict[str, Any],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the default dashboard shard; it is intentionally much smaller than full history."""
    return {
        "version": DATA_VERSION,
        "generated_at": iso_z(generated_at),
        "display_window_days": display_window_days,
        "retention_days": retention_days,
        "source_status": source_status,
        "items": items,
    }


def remove_legacy_items_json(data_dir: Path) -> None:
    legacy_path = data_dir / LEGACY_ITEMS_FILENAME
    if legacy_path.exists():
        legacy_path.unlink()


def run_collection(args: argparse.Namespace, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    sources_path = Path(args.sources)
    data_dir = Path(args.data_dir)
    sources = load_sources(sources_path)
    existing_items = read_archive_items(data_dir)
    new_items, source_status = collect_from_sources(sources, args.timeout, now)
    merged_items = merge_items(existing_items, new_items, now, args.retention_days)
    enriched_items = apply_quality_tiers(merged_items, sources, now, args.display_window_days)
    latest = latest_items(enriched_items, now, args.display_window_days)
    available_months = write_archive_shards(data_dir, enriched_items, new_items, now, args.retention_days)

    latest_payload = build_latest_payload(now, args.display_window_days, args.retention_days, source_status, latest)
    index_payload = build_index_payload(
        now,
        args.display_window_days,
        args.retention_days,
        source_status,
        available_months,
        len(enriched_items),
        len(latest),
    )
    write_json_if_changed(data_dir / "latest.json", latest_payload)
    write_json_if_changed(data_dir / "index.json", index_payload)
    remove_legacy_items_json(data_dir)
    return {"index": index_payload, "latest": latest_payload}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect trusted AI feed items for the static dashboard.")
    parser.add_argument("--sources", default="config/sources.json", help="Path to source configuration JSON.")
    parser.add_argument("--data-dir", default="data", help="Directory for index/latest/archive dashboard data.")
    parser.add_argument("--display-window-days", type=int, default=30, help="Default dashboard display window.")
    parser.add_argument("--retention-days", type=int, default=365, help="Stored item retention period.")
    parser.add_argument("--timeout", type=int, default=20, help="Per-source network timeout in seconds.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_collection(args)
    statuses = payload["index"]["source_status"].values()
    errors = sum(1 for status in statuses if status["status"] == "error")
    total = payload["index"]["total_retained"]
    latest_count = payload["index"]["latest_count"]
    print(f"Collected {total} retained items; {latest_count} latest items; {errors} source errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
