# -*- coding: utf-8 -*-
"""核心逻辑单元测试（不联网、不动真实配置）。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 必须在 import app.core 之前指定临时配置目录，避免污染 %APPDATA%
_TMP = tempfile.mkdtemp(prefix="qno-test-")
os.environ["NPM_MIRROR_TOOL_CONFIG_DIR"] = _TMP

from app import core, i18n  # noqa: E402


class RankingTest(unittest.TestCase):
    def _result(self, lat, speed, ok=True):
        return {"ok": ok, "lat": {"min": lat, "median": lat} if lat else None,
                "speed": speed, "version": None, "error": "", "score": 0.0, "rank": None}

    def test_fastest_and_slowest(self):
        results = [self._result(80, 1.0), self._result(20, 5.0), self._result(40, 3.0)]
        order = core.rank_results(results)
        self.assertEqual(order, [1, 2, 0])
        self.assertEqual([r["rank"] for r in results], [3, 1, 2])

    def test_latency_outweighs_speed(self):
        # 延迟权重 70%，速度优势不足以翻盘
        results = [self._result(10, 1.0), self._result(30, 9.0)]
        order = core.rank_results(results)
        self.assertEqual(order[0], 0)

    def test_dead_sources_excluded(self):
        results = [self._result(None, None, ok=False), self._result(50, 2.0)]
        order = core.rank_results(results)
        self.assertEqual(order, [1])
        self.assertIsNone(results[0]["rank"])
        self.assertEqual(results[0]["score"], 0.0)

    def test_no_speed_does_not_crash(self):
        results = [self._result(30, None), self._result(20, None)]
        order = core.rank_results(results)
        self.assertEqual(order, [1, 0])

    def test_all_dead(self):
        self.assertEqual(core.rank_results([self._result(None, None, ok=False)]), [])

    def test_scores_bounded(self):
        results = [self._result(10, 5.0), self._result(20, 2.0), self._result(15, 3.0)]
        core.rank_results(results)
        for item in results:
            self.assertGreater(item["score"], 0)
            self.assertLessEqual(item["score"], 1.0 + 1e-9)


class VersionTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(core.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(core.parse_version(""), ())
        self.assertEqual(core.parse_version(None), ())

    def test_newest(self):
        self.assertEqual(core.newest_version(["1.9.0", "1.10.0", None]), "1.10.0")
        self.assertIsNone(core.newest_version([None, ""]))

    def test_rank_text(self):
        self.assertEqual(core.rank_text(1), "①")
        self.assertEqual(core.rank_text(10), "⑩")
        self.assertEqual(core.rank_text(11), "11")
        self.assertEqual(core.rank_text(None), "—")


class ProxyTest(unittest.TestCase):
    def test_parse_without_scheme(self):
        proxy = core.parse_proxy_url("127.0.0.1:7890")
        self.assertTrue(proxy.enabled)
        self.assertEqual((proxy.host, proxy.port), ("127.0.0.1", 7890))

    def test_default_port(self):
        self.assertEqual(core.parse_proxy_url("proxy.local").port, 8080)

    def test_auth_header(self):
        proxy = core.parse_proxy_url("http://user:pass@127.0.0.1:8080")
        header = proxy.header()["Proxy-Authorization"]
        self.assertTrue(header.startswith("Basic "))

    def test_bypass_rules(self):
        proxy = core.parse_proxy_url("127.0.0.1:8080",
                                     ["localhost", "*.example.com", "<local>"])
        self.assertTrue(proxy.bypass("localhost"))
        self.assertTrue(proxy.bypass("api.example.com"))
        self.assertTrue(proxy.bypass("intranet"))       # <local>
        self.assertFalse(proxy.bypass("registry.npmjs.org"))

    def test_bad_value(self):
        self.assertIsNone(core.parse_proxy_url(""))
        self.assertIsNone(core.parse_proxy_url("   "))

    def test_label(self):
        self.assertEqual(core.proxy_label(core.parse_proxy_url("1.2.3.4:80")), "1.2.3.4:80")
        self.assertEqual(core.proxy_label(None), "")


class SourceConfigTest(unittest.TestCase):
    def setUp(self):
        self.original = [dict(item) for item in core.sources()]

    def tearDown(self):
        core.set_sources(self.original)

    def test_reject_bad_url(self):
        core.set_sources([{"name": "ok", "url": "https://a.example"}])
        core.set_sources([{"name": "bad", "url": "ftp://a.example"}])
        self.assertEqual([s["name"] for s in core.sources()], [])

    def test_trailing_slash_removed(self):
        core.set_sources([{"name": "a", "url": "https://a.example/npm/"}])
        self.assertEqual(core.sources()[0]["url"], "https://a.example/npm")

    def test_roundtrip_persisted(self):
        core.set_sources([{"name": "自定义", "url": "https://mirror.example"}])
        with open(os.path.join(_TMP, "config.json"), encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["sources"][0]["url"], "https://mirror.example")


class NpmrcTest(unittest.TestCase):
    def test_write_and_read(self):
        folder = tempfile.mkdtemp(prefix="qno-proj-")
        path = core.write_project_npmrc(folder, "https://mirror.example")
        self.assertTrue(os.path.exists(path))
        self.assertEqual(core.read_npmrc(path)["registry"], "https://mirror.example")

    def test_replace_existing_registry(self):
        folder = tempfile.mkdtemp(prefix="qno-proj-")
        path = os.path.join(folder, ".npmrc")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("registry=https://old.example\nsave-exact=true\n")
        core.write_project_npmrc(folder, "https://new.example")
        data = core.read_npmrc(path)
        self.assertEqual(data["registry"], "https://new.example")
        self.assertEqual(data["save-exact"], "true")     # 其他行保留

    def test_comment_lines_ignored(self):
        folder = tempfile.mkdtemp(prefix="qno-proj-")
        path = os.path.join(folder, ".npmrc")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("# comment\n@company:registry=https://private.example\n")
        rows = core.list_scoped({"mode": "project", "dir": folder})
        self.assertEqual(rows, [{"scope": "@company", "url": "https://private.example"}])


class ExportTest(unittest.TestCase):
    def test_rows_and_csv(self):
        sources = [{"name": "A", "url": "https://a"}, {"name": "B", "url": "https://b"}]
        results = [
            {"ok": True, "lat": {"min": 12.345, "median": 20.0}, "speed": 3.14159,
             "version": "1.2.3", "score": 0.9, "rank": 1, "error": ""},
            {"ok": False, "lat": None, "speed": None, "version": None,
             "score": 0.0, "rank": None, "error": "超时"},
        ]
        rows = core.results_to_rows(results, sources, stamp="2026-01-01 00:00:00")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["latency_min_ms"], 12.3)
        self.assertEqual(rows[1]["ok"], "0")

        path = os.path.join(_TMP, "out.csv")
        self.assertEqual(core.export_csv(path, rows), 2)
        with open(path, encoding="utf-8-sig") as handle:
            text = handle.read()
        self.assertIn("A", text)
        self.assertIn("超时", text)


class HistoryTest(unittest.TestCase):
    def test_append_and_load_newest_first(self):
        before = len(core.load_history())
        core.append_history({"ok": 5, "total": 6, "fastest": "A", "elapsed": 1.5})
        core.append_history({"ok": 6, "total": 6, "fastest": "B", "elapsed": 1.2})
        rows = core.load_history()
        self.assertEqual(len(rows), before + 2)
        self.assertEqual(rows[0]["fastest"], "B")
        self.assertIn("time", rows[0])


class I18nTest(unittest.TestCase):
    def test_all_keys_have_both_languages(self):
        for key in i18n.keys():
            self.assertTrue(i18n.t(key, "zh"), key)
            self.assertTrue(i18n.t(key, "en"), key)

    def test_formatting(self):
        self.assertIn("3", i18n.t("main.testing_n", "zh", done=3, total=9))
        self.assertIn("9", i18n.t("main.testing_n", "en", done=3, total=9))

    def test_missing_key_returns_key(self):
        self.assertEqual(i18n.t("no.such.key"), "no.such.key")


if __name__ == "__main__":
    unittest.main(verbosity=2)
