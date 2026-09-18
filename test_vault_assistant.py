"""
Comprehensive Test Suite for Obsidian Read-Only AI Assistant
============================================================
Validates all READ and SECURITY specifications per system architecture requirements.
"""

import os
import sys
import unittest
from fastapi.testclient import TestClient

from obsidian_vault_reader import ObsidianVaultReader, SecurityPathViolationError
from vault_rag import VaultRAG
from gateway_api import app, is_destructive_command, COMMAND_SAFETY_REFUSAL, GATEWAY_HOST
import bot_server


class TestObsidianReadOnlyAssistant(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.reader = ObsidianVaultReader()
        cls.rag = VaultRAG(cls.reader)
        cls.client = TestClient(app)

    # ==================================================================
    # READ TESTS
    # ==================================================================

    def test_01_read_note(self):
        """[READ] Verify that an existing note can be read securely."""
        content = self.reader.read_file("01_College/Timetable/2026 Odd Semester - BCA 1B.md")
        self.assertIn("Narula Institute of Technology", content)
        self.assertIn("BCA 1B", content)

    def test_02_search_note_by_filename(self):
        """[READ] Verify searching notes by filename."""
        results = self.reader.search_files("BCA 1B")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["filename"], "2026 Odd Semester - BCA 1B.md")

    def test_03_search_content_multiple_notes(self):
        """[READ] Verify searching note contents across the vault."""
        results = self.reader.search_content("Mathematical", max_results=5)
        self.assertGreater(len(results), 0)
        self.assertTrue(any("2026 Odd Semester - BCA 1B.md" in r["filename"] or "Mathematical" in r["filename"] for r in results))

    def test_04_retrieve_relevant_context(self):
        """[READ] Verify targeted retrieval extracts minimal sufficient context."""
        retrieval = self.rag.retrieve_context("What classes do I have on Monday?")
        self.assertTrue(retrieval["has_context"])
        self.assertIn("2026 Odd Semester - BCA 1B.md", retrieval["source_notes"])
        self.assertLess(len(retrieval["context_text"]), 3000)

    def test_05_gateway_health(self):
        """[READ] Verify FastAPI Gateway health endpoint."""
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["security_mode"], "STRICT_READ_ONLY")
        self.assertGreater(data["vault_total_notes"], 50)

    def test_06_ask_ollama_vault_question(self):
        """[READ] Verify Ollama produces a grounded answer with sources."""
        res = self.client.post("/api/vault/query", json={
            "query": "What is RAG according to my notes?",
            "telegram_user_id": 8046833336
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertFalse(data["refused"])
        self.assertIn("RAG.md", data["sources"])
        self.assertIn("Retrieval", data["answer"])

    # ==================================================================
    # SECURITY & INTEGRITY TESTS
    # ==================================================================

    def test_07_absence_of_write_methods(self):
        """[SECURITY] Proves that zero write/create/delete/modify methods exist on ObsidianVaultReader."""
        reader_methods = [m for m in dir(self.reader) if not m.startswith("_")]
        forbidden_substrings = [
            "write", "create", "delete", "remove", "unlink",
            "rename", "move", "modify", "edit", "overwrite", "restore"
        ]
        for m in reader_methods:
            for forbidden in forbidden_substrings:
                self.assertNotIn(
                    forbidden, m.lower(),
                    f"CRITICAL SECURITY FAILURE: Exposed write method '{m}' on ObsidianVaultReader!"
                )

    def test_08_path_traversal_blocked(self):
        """[SECURITY] Proves that directory traversal attempts are strictly caught and blocked."""
        traversal_paths = [
            "../../Windows/System32/drivers/etc/hosts",
            "../bot_server.py",
            "....//....//Windows",
            "C:\\Windows\\System32\\cmd.exe",
            "/etc/passwd"
        ]
        for path in traversal_paths:
            with self.assertRaises(SecurityPathViolationError):
                self.reader.read_file(path)

    def test_09_destructive_command_refusal(self):
        """[SECURITY] Verify that all file deletion, modification, or creation commands are rejected."""
        test_commands = [
            "Delete Deep Learning.md",
            "delete this note please",
            "Remove note 01_College",
            "edit note RAG.md to add new text",
            "modify file Timetable",
            "rename note to something else",
            "create note My New Note",
            "create file test.txt",
            "restore the previous version",
            "মুছে ফেলো এই ফাইলটা",
            "নোট ডিলিট করো"
        ]
        for cmd in test_commands:
            # Test direct regex
            self.assertTrue(is_destructive_command(cmd), f"Failed to identify destructive command: {cmd}")
            # Test gateway endpoint
            res = self.client.post("/api/vault/query", json={"query": cmd, "telegram_user_id": 8046833336})
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data.get("refused"), f"Gateway failed to refuse: {cmd}")
            self.assertEqual(data.get("answer"), COMMAND_SAFETY_REFUSAL)

    def test_10_telegram_user_authorization(self):
        """[SECURITY] Verify unauthorized Telegram user IDs are rejected."""
        class DummyUser:
            def __init__(self, uid):
                self.id = uid

        class DummyUpdate:
            def __init__(self, uid):
                self.effective_user = DummyUser(uid)
                self.message = None

        # Authorized user
        auth_update = DummyUpdate(8046833336)
        self.assertTrue(bot_server.is_authorized(auth_update))

        # Unauthorized users
        unauth_update_1 = DummyUpdate(123456789)
        self.assertFalse(bot_server.is_authorized(unauth_update_1))

        unauth_update_2 = DummyUpdate(9999999999)
        self.assertFalse(bot_server.is_authorized(unauth_update_2))

    def test_11_gateway_bound_to_localhost_only(self):
        """[SECURITY] Verifies gateway host is strictly localhost (127.0.0.1)."""
        self.assertEqual(GATEWAY_HOST, "127.0.0.1")

    def test_12_answer_grounding_unknown_query(self):
        """[GROUNDING] Verify assistant does not hallucinate when notes do not exist."""
        res = self.client.post("/api/vault/query", json={
            "query": "What is the secret recipe for quantum flying cars?",
            "telegram_user_id": 8046833336
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("couldn't find enough information", data.get("answer", "").lower())

    def test_13_read_wikilinks(self):
        """[READ] Verify wikilink extraction from notes."""
        links = self.reader.get_links("01_College/Timetable/2026 Odd Semester - BCA 1B.md")
        self.assertIn("outgoing_wikilinks", links)
        self.assertGreater(len(links["outgoing_wikilinks"]), 0)
        self.assertIn("Mathematical Foundation to Computer Science - I", links["outgoing_wikilinks"])

    def test_14_read_canvas(self):
        """[READ] Verify reading .canvas files without modification."""
        canvas_path = "03_Second_Brain/AI/Untitled.canvas"
        data = self.reader.read_canvas_data(canvas_path)
        self.assertIn("node_count", data)
        self.assertIn("nodes", data)

    def test_15_read_version_history(self):
        """[READ] Verify read-only access to git version history."""
        note_path = "01_College/Timetable/2026 Odd Semester - BCA 1B.md"
        history = self.reader.get_file_history(note_path, max_entries=3)
        self.assertIsInstance(history, list)


if __name__ == "__main__":
    unittest.main()
