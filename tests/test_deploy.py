# -*- coding: utf-8 -*-
"""部署模块的纯逻辑测试（不联网、不安装）。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import deploy  # noqa: E402


class FilenameTest(unittest.TestCase):
    """安装包命名规则最容易出错，逐家钉住。"""

    def test_node_x64(self):
        with mock.patch("platform.machine", return_value="AMD64"):
            self.assertEqual(deploy.node_filename("26.13.1"), "node-v26.13.1-x64.msi")

    def test_node_arm64(self):
        with mock.patch("platform.machine", return_value="ARM64"):
            self.assertEqual(deploy.node_filename("26.13.1"), "node-v26.13.1-arm64.msi")

    def test_python_x64(self):
        with mock.patch("platform.machine", return_value="AMD64"):
            self.assertEqual(deploy.python_filename("3.15.2"), "python-3.15.2-amd64.exe")

    def test_python_arm64(self):
        with mock.patch("platform.machine", return_value="ARM64"):
            self.assertEqual(deploy.python_filename("3.15.2"), "python-3.15.2-arm64.exe")

    def test_git_uses_64_bit_suffix(self):
        # Git for Windows 用 -64-bit.exe，不是 -x64.exe
        with mock.patch("platform.machine", return_value="AMD64"):
            self.assertEqual(deploy.git_filename("2.56.1.windows.1"), "Git-2.56.1-64-bit.exe")

    def test_git_arm64(self):
        with mock.patch("platform.machine", return_value="ARM64"):
            self.assertEqual(deploy.git_filename("2.56.1.windows.1"), "Git-2.56.1-arm64.exe")

    def test_git_base_strips_windows_suffix(self):
        self.assertEqual(deploy.git_base("2.47.1.windows.1"), "2.47.1")
        self.assertEqual(deploy.git_base("2.47.1"), "2.47.1")


class VersionSortTest(unittest.TestCase):
    def test_numeric_not_lexical(self):
        versions = ["2.9.0", "2.10.0", "2.10.1"]
        self.assertEqual(sorted(versions, key=deploy._version_key),
                         ["2.9.0", "2.10.0", "2.10.1"])

    def test_windows_suffix(self):
        versions = ["2.56.0.windows.4", "2.56.1.windows.1", "2.56.1.windows.10"]
        self.assertEqual(max(versions, key=deploy._version_key), "2.56.1.windows.10")


class ChecksumTest(unittest.TestCase):
    def test_parse_shasums(self):
        text = (
            "abc123  node-v26.13.1-x64.msi\n"
            "def456 *node-v26.13.1-arm64.msi\n"
        )
        with mock.patch.object(deploy, "_fetch_text", return_value=text):
            self.assertEqual(deploy.expected_sha("https://x/y", "node-v26.13.1-x64.msi"),
                             "abc123")
            self.assertEqual(deploy.expected_sha("https://x/y", "node-v26.13.1-arm64.msi"),
                             "def456")

    def test_missing_file(self):
        with mock.patch.object(deploy, "_fetch_text", return_value="abc  other.zip"):
            self.assertEqual(deploy.expected_sha("https://x/y", "node.msi"), "")


class PeHeaderTest(unittest.TestCase):
    def test_pe_detection(self):
        import tempfile

        folder = tempfile.mkdtemp()
        good = os.path.join(folder, "a.exe")
        with open(good, "wb") as handle:
            handle.write(b"MZ\x90\x00rest")
        self.assertTrue(deploy._pe_ok(good))

        # MSI 是 OLE 复合文档，不是 PE，但要认得
        msi = os.path.join(folder, "b.msi")
        with open(msi, "wb") as handle:
            handle.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest")
        self.assertTrue(deploy._pe_ok(msi))

        bad = os.path.join(folder, "c.exe")
        with open(bad, "wb") as handle:
            handle.write(b"<html>404</html>")
        self.assertFalse(deploy._pe_ok(bad))
        self.assertFalse(deploy._pe_ok(os.path.join(folder, "missing.exe")))


class CatalogFallbackTest(unittest.TestCase):
    def test_python_skips_versions_without_installer(self):
        """3.13.18 只有源码包，应自动回退到有安装包的补丁版本。"""
        calls = []

        def fake_has(version, proxy=None):
            calls.append(version)
            return version in ("3.13.16",)

        with mock.patch.object(deploy, "has_python_installer", side_effect=fake_has):
            row = deploy._resolve_python("3.13", "3.13.18", None)
        self.assertEqual(row["version"], "3.13.16")
        self.assertEqual(calls[:3], ["3.13.18", "3.13.17", "3.13.16"])

    def test_python_gives_up_after_limit(self):
        with mock.patch.object(deploy, "has_python_installer", return_value=False):
            self.assertIsNone(deploy._resolve_python("3.13", "3.13.18", None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
