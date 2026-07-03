#!/usr/bin/env python3
"""Offline tests for sync_openwebui_function.py."""

from __future__ import annotations

import unittest
from argparse import Namespace

import sync_openwebui_function as sync


class SyncOpenWebUiFunctionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.args = Namespace(no_activate=False, no_global=False)
        self.content = '''"""
title: Example Filter
author: Example Author
version: 1.2.3
description: Example description.
"""

print("hello")
'''
        self.remote = {
            "id": "example_filter",
            "name": "Example Filter",
            "type": "filter",
            "user_id": "user-1",
            "meta": {
                "description": "Old description",
                "manifest": {
                    "title": "Old Title",
                    "author": "Old Author",
                },
            },
        }

    def test_parse_manifest_extracts_header_fields(self) -> None:
        manifest = sync.parse_manifest(self.content)
        self.assertEqual(
            manifest,
            {
                "title": "Example Filter",
                "author": "Example Author",
                "version": "1.2.3",
                "description": "Example description.",
            },
        )

    def test_desired_meta_merges_remote_and_manifest(self) -> None:
        meta = sync.desired_meta(self.content, self.remote)
        self.assertEqual(meta["description"], "Old description")
        self.assertEqual(
            meta["manifest"],
            {
                "title": "Example Filter",
                "author": "Example Author",
                "version": "1.2.3",
                "description": "Example description.",
            },
        )

    def test_update_payload_variant_can_include_meta_flags_and_user(self) -> None:
        payload = sync.update_payload_variant(
            self.args,
            self.remote,
            self.content,
            include_meta=True,
            include_flags=True,
            include_user_id=True,
        )
        self.assertEqual(payload["id"], "example_filter")
        self.assertEqual(payload["user_id"], "user-1")
        self.assertTrue(payload["is_active"])
        self.assertTrue(payload["is_global"])
        self.assertEqual(payload["meta"]["manifest"]["title"], "Example Filter")


if __name__ == "__main__":
    unittest.main()
