#!/usr/bin/env python3
"""Offline tests for reconcile_openwebui_functions.py."""

from __future__ import annotations

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import reconcile_openwebui_functions as rec
import sync_openwebui_function as sync


class ReconcilerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "function.py"
        self.source.write_text("EMBEDDED_CAPABILITY_ROADMAP = None\nprint('hello')\n", encoding="utf-8")
        self.docs = self.root / "docs"
        self.docs.mkdir()
        (self.docs / "codex-local-capability-roadmap.json").write_text('{"version": 1}\n', encoding="utf-8")
        self.spec = rec.RequiredFunction("fn", str(self.source), "Fn")
        self.args = Namespace(
            base_url="http://owui.local",
            api_key_env="OWUI_API_KEY",
            api_key_file="missing",
            dry_run=False,
            check_only=False,
            timeout=1.0,
            attempts=1,
            initial_delay=0.0,
            max_delay=0.0,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def runtime(self) -> str:
        args = rec.sync_args(self.args, str(self.source))
        content = self.source.read_text(encoding="utf-8")
        return sync.runtime_content(args, content)[0]

    def test_noop_when_remote_matches(self) -> None:
        runtime = self.runtime()
        remote = {"id": "fn", "content": runtime, "is_active": True, "is_global": True}
        with patch.object(rec, "get_remote", return_value=(remote, "")):
            result = rec.reconcile_one(self.args, self.spec)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "no-op")
        self.assertEqual(result["issues"], [])

    def test_updates_stale_inactive_function(self) -> None:
        remote = {"id": "fn", "content": "old", "is_active": False, "is_global": False}
        updated = {"id": "fn", "content": self.runtime(), "is_active": False, "is_global": False}
        activated = {"id": "fn", "content": self.runtime(), "is_active": True, "is_global": True}
        with patch.object(rec, "get_remote", return_value=(remote, "")), patch.object(
            sync, "update_function_with_fallbacks", return_value=(updated, "test")
        ), patch.object(sync, "ensure_function_flags", return_value=(activated, ["toggle-active", "toggle-global"])):
            result = rec.reconcile_one(self.args, self.spec)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "updated")
        self.assertEqual(result["flag_actions"], ["toggle-active", "toggle-global"])
        self.assertEqual(result["issues"], [])

    def test_creates_missing_function(self) -> None:
        created = {"id": "fn", "content": self.runtime(), "is_active": True, "is_global": True}
        with patch.object(rec, "get_remote", return_value=(None, "not_found")), patch.object(
            rec, "create_function", return_value=(created, "test-create")
        ), patch.object(sync, "ensure_function_flags", return_value=(created, [])):
            result = rec.reconcile_one(self.args, self.spec)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "created")
        self.assertEqual(result["issues"], [])

    def test_check_only_reports_recovery(self) -> None:
        self.args.check_only = True
        remote = {"id": "fn", "content": "old", "is_active": False, "is_global": True}
        with patch.object(rec, "get_remote", return_value=(remote, "")):
            result = rec.reconcile_one(self.args, self.spec)
        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "check-failed")
        self.assertIn("CODEX_LOCAL_FILTER_INACTIVE", result["issues"])
        self.assertIn("CODEX_LOCAL_FILTER_STALE", result["issues"])
        self.assertIn("reconcile_openwebui_functions.py", result["recovery"])


if __name__ == "__main__":
    unittest.main()
