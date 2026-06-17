import argparse
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import collect


FIXTURES = ROOT / "tests" / "fixtures"
NOW = dt.datetime(2026, 6, 16, 12, 0, tzinfo=dt.timezone.utc)


def source(**overrides):
    data = {
        "id": "test-source",
        "name": "Test Source",
        "type": "official",
        "url": "file:///unused",
        "language": "en",
        "category": "company",
        "weight": 50,
        "enabled": True,
    }
    data.update(overrides)
    return data


class CollectTests(unittest.TestCase):
    def parse_fixture(self, name, src=None):
        xml_text = (FIXTURES / name).read_text(encoding="utf-8")
        return collect.parse_feed(xml_text, src or source())

    def test_parse_supported_feed_formats(self):
        cases = [
            ("rss.xml", "official"),
            ("atom.xml", "official"),
            ("arxiv.xml", "paper"),
            ("github_releases.atom", "github_release"),
        ]
        for filename, source_type in cases:
            with self.subTest(filename=filename):
                entries = self.parse_fixture(filename, source(type=source_type))
                self.assertGreaterEqual(len(entries), 1)
                self.assertTrue(entries[0]["title"])
                self.assertTrue(entries[0]["url"])
                self.assertTrue(entries[0]["published_at"])

    def test_dedupe_retention_and_stable_order(self):
        rss_entries = self.parse_fixture("rss.xml")
        atom_entries = self.parse_fixture("atom.xml")
        all_entries = rss_entries + atom_entries
        items = [collect.build_item(entry, source(), NOW, NOW) for entry in all_entries]

        old_item = dict(items[1])
        old_item["id"] = "old-item"
        old_item["published_at"] = "2024-01-01T00:00:00Z"

        first = collect.merge_items([old_item], items, NOW, retention_days=365)
        second = collect.merge_items([old_item], items, NOW, retention_days=365)

        titles = [item["title"] for item in first]
        self.assertEqual(first, second)
        self.assertEqual(titles.count("OpenAI releases new reasoning model"), 1)
        self.assertNotIn("old-item", [item["id"] for item in first])

    def test_missing_publish_date_uses_fetch_time_and_detects_language(self):
        entry = self.parse_fixture("missing_date.xml", source(language="mixed"))[0]
        item = collect.build_item(entry, source(language="mixed"), NOW, NOW)

        self.assertEqual(item["published_at"], "2026-06-16T12:00:00Z")
        self.assertEqual(item["language"], "zh")
        self.assertTrue(any("missing published date" in reason for reason in item["score_reasons"]))

    def test_source_error_does_not_abort_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sources_path = tmp_path / "sources.json"
            data_path = tmp_path / "items.json"
            valid_url = (FIXTURES / "rss.xml").resolve().as_uri()
            sources_path.write_text(
                json.dumps(
                    {
                        "sources": [
                            source(id="valid", name="Valid", url=valid_url),
                            source(id="broken", name="Broken", url="file:///no/such/feed.xml"),
                        ]
                    }
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                sources=str(sources_path),
                data_dir=str(tmp_path),
                display_window_days=30,
                retention_days=365,
                timeout=2,
            )
            payload = collect.run_collection(args, now=NOW)

            self.assertGreaterEqual(len(payload["latest"]["items"]), 1)
            self.assertEqual(payload["index"]["source_status"]["valid"]["status"], "live")
            self.assertEqual(payload["index"]["source_status"]["broken"]["status"], "error")
            self.assertTrue((tmp_path / "index.json").exists())
            self.assertTrue((tmp_path / "latest.json").exists())
            self.assertTrue(list((tmp_path / "archive").glob("*.json")))
            self.assertFalse(data_path.exists())

    def test_quality_tier_is_stable_and_explained(self):
        entry = {
            "title": "OpenAI announces new GPT model API",
            "url": "https://example.com/new-gpt",
            "summary": "A major model launch with a new API and benchmark improvements for agents.",
            "published_at": "2026-06-16T10:00:00Z",
            "categories": [],
        }
        src = source(id="openai", name="OpenAI", type="official", weight=56)
        item = collect.build_item(entry, src, NOW, NOW)
        first = collect.apply_quality_tiers([item], [src], NOW, display_window_days=30)
        second = collect.apply_quality_tiers([item], [src], NOW, display_window_days=30)

        self.assertEqual(first, second)
        self.assertEqual(first[0]["tier"], "must_read")
        self.assertTrue(first[0]["quality_reasons"])

    def test_low_signal_content_is_demoted_to_raw(self):
        entry = {
            "title": "New AI Academy course and webinar recap",
            "url": "https://example.com/course",
            "summary": "A course and webinar recap for a customer story.",
            "published_at": "2026-06-16T10:00:00Z",
            "categories": [],
        }
        src = source(id="official", type="official", weight=56)
        item = collect.build_item(entry, src, NOW, NOW)
        ranked = collect.apply_quality_tiers([item], [src], NOW, display_window_days=30)

        self.assertEqual(ranked[0]["tier"], "raw")
        self.assertTrue(any("low-signal" in reason for reason in ranked[0]["quality_reasons"]))

    def test_broad_arxiv_paper_is_demoted_to_raw(self):
        entry = {
            "title": "Learning Red Agent Policy from Observations for Autonomous Cyber Agents",
            "url": "https://example.com/arxiv-cyber-agent",
            "summary": "A reinforcement learning paper about autonomous cyber-defense policies in simulated networks.",
            "published_at": "2026-06-16T10:00:00Z",
            "categories": ["cs.AI"],
        }
        src = source(id="arxiv", type="paper", weight=46)
        item = collect.build_item(entry, src, NOW, NOW)
        ranked = collect.apply_quality_tiers([item], [src], NOW, display_window_days=30)

        self.assertEqual(ranked[0]["tier"], "raw")
        self.assertTrue(any("broad arXiv" in reason for reason in ranked[0]["quality_reasons"]))

    def test_source_quota_limits_curated_homepage_items(self):
        src = source(id="papers", type="paper", weight=48, daily_limit=2, homepage_limit=2)
        items = []
        for index in range(5):
            entry = {
                "title": f"Benchmark paper announces new agent model {index}",
                "url": f"https://example.com/paper-{index}",
                "summary": "A benchmark paper about new model and agent evaluation.",
                "published_at": "2026-06-16T10:00:00Z",
                "categories": ["cs.AI"],
            }
            items.append(collect.build_item(entry, src, NOW, NOW))

        ranked = collect.apply_quality_tiers(items, [src], NOW, display_window_days=30)
        curated = [item for item in ranked if item["tier"] != "raw"]

        self.assertEqual(len(curated), 2)
        self.assertTrue(any("source diversity quota" in " ".join(item["quality_reasons"]) for item in ranked))

    def test_url_normalization_removes_tracking_query(self):
        normalized = collect.normalize_url("https://Example.com/path/?utm_source=x&keep=1#section")
        self.assertEqual(normalized, "https://example.com/path?keep=1")


if __name__ == "__main__":
    unittest.main()
