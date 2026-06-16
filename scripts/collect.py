#!/usr/bin/env python3
"""Collect trusted AI/technology feed items into the static dashboard dataset."""

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

DATA_VERSION = 1
USER_AGENT = "radar-ai-feed-collector/1.0 (+https://github.com/ithkeen/radar)"
DROP_QUERY_KEYS = {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "ref_src"}

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
    """Score items deterministically so GitHub Actions output is repeatable without an LLM."""
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
    """Normalize a raw feed entry into the public item schema documented in the project plan."""
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
        "content_hash": content_hash,
    }


def item_timestamp(item: dict[str, Any]) -> dt.datetime:
    parsed = parse_datetime(item.get("published_at")) or parse_datetime(item.get("fetched_at"))
    return parsed or dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


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

    return sorted(unique, key=lambda item: (-item_timestamp(item).timestamp(), -int(item.get("score", 0)), item.get("title", "")))


def merge_items(
    existing_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    now: dt.datetime,
    retention_days: int,
) -> list[dict[str, Any]]:
    """Merge new and stored items, then enforce the one-year retention boundary."""
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


def read_existing_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return list(payload.get("items", []))


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_collection(args: argparse.Namespace, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    sources_path = Path(args.sources)
    data_path = Path(args.data)
    sources = load_sources(sources_path)
    existing_items = read_existing_items(data_path)
    new_items, source_status = collect_from_sources(sources, args.timeout, now)
    merged_items = merge_items(existing_items, new_items, now, args.retention_days)

    payload = {
        "version": DATA_VERSION,
        "generated_at": iso_z(now),
        "display_window_days": args.display_window_days,
        "retention_days": args.retention_days,
        "source_status": source_status,
        "items": merged_items,
    }
    write_json(data_path, payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect trusted AI feed items for the static dashboard.")
    parser.add_argument("--sources", default="config/sources.json", help="Path to source configuration JSON.")
    parser.add_argument("--data", default="data/items.json", help="Path to the persistent dashboard data JSON.")
    parser.add_argument("--display-window-days", type=int, default=30, help="Default dashboard display window.")
    parser.add_argument("--retention-days", type=int, default=365, help="Stored item retention period.")
    parser.add_argument("--timeout", type=int, default=20, help="Per-source network timeout in seconds.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_collection(args)
    statuses = payload["source_status"].values()
    errors = sum(1 for status in statuses if status["status"] == "error")
    print(f"Collected {len(payload['items'])} retained items; {errors} source errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
