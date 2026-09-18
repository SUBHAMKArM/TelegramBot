"""
Comprehensive Test Suite for Iterative Verification & Self-Improvement RAG
==========================================================================
Tests:
1. Temporal Context Resolution (English and Banglish relative terms)
2. Answer Verifier (Claim support, entity overlap, contradiction detection)
3. Retrieval Memory (Record query, feedback, source boost/demote, persistence)
4. Iterative RAG Query Loop (Early stopping vs multi-iteration expansion)
5. Read-Only Vault Guard Invariant (Zero writes permitted)
6. Gateway API endpoints (Query, Feedback, Memory Stats)
"""

import os
import sys
import unittest
import tempfile
import json
from datetime import datetime, timedelta

from obsidian_vault_reader import ObsidianVaultReader, SecurityPathViolationError
from verifier import TemporalContextResolver, AnswerVerifier
from retrieval_memory import RetrievalMemory
from vault_rag import VaultRAG
from gateway_api import app, is_destructive_command, COMMAND_SAFETY_REFUSAL
from fastapi.testclient import TestClient

class TestTemporalResolver(unittest.TestCase):
    def setUp(self):
        self.resolver = TemporalContextResolver()

    def test_tomorrow_resolution(self):
        res = self.resolver.resolve_temporal_expression("College e class ache kal?")
        self.assertIsNotNone(res["target_date"])
        tomorrow_weekday = (datetime.now() + timedelta(days=1)).strftime("%A").lower()
        self.assertIn(tomorrow_weekday, [w.lower() for w in res["expanded_keywords"]])

    def test_today_resolution(self):
        res = self.resolver.resolve_temporal_expression("aaj ki class ache?")
        self.assertIsNotNone(res["target_date"])
        today_weekday = datetime.now().strftime("%A").lower()
        self.assertIn(today_weekday, [w.lower() for w in res["expanded_keywords"]])

    def test_named_weekday(self):
        res = self.resolver.resolve_temporal_expression("sombar ki routine?")
        self.assertIn("monday", [w.lower() for w in res["expanded_keywords"]])

    def test_non_temporal_query(self):
        res = self.resolver.resolve_temporal_expression("What is RAG in machine learning?")
        self.assertIsNone(res["target_date"])
        self.assertEqual(len(res["expanded_keywords"]), 0)


class TestAnswerVerifier(unittest.TestCase):
    def setUp(self):
        self.verifier = AnswerVerifier()

    def test_high_confidence_match(self):
        context = "On Monday, BCA has Python Lab at 10:00 AM with SRB in Room 401. Data Structures is at 11:40 AM."
        draft = "On Monday there is Python Lab at 10:00 AM in Room 401 with SRB."
        result = self.verifier.verify(draft, context, query="What class on Monday?")
        self.assertGreaterEqual(result["confidence_score"], 0.75)
        self.assertTrue(result["is_sufficient"])
        self.assertFalse(result["has_contradiction"])

    def test_contradiction_detection(self):
        context = "Classes are held from Monday to Friday. Sunday is always a holiday with no classes scheduled."
        draft = "Classes are held on Sunday at 10:00 AM in room 401."
        result = self.verifier.verify(draft, context, query="Is there class on Sunday?")
        self.assertTrue(result["has_contradiction"])
        self.assertIn("contradiction", result["issues"][0].lower())

    def test_empty_context_low_confidence(self):
        context = ""
        draft = "Quantum entanglement happens instantaneously."
        result = self.verifier.verify(draft, context, query="Explain quantum entanglement")
        self.assertLess(result["confidence_score"], 0.40)
        self.assertFalse(result["is_sufficient"])


class TestRetrievalMemory(unittest.TestCase):
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.temp_file.close()
        self.memory = RetrievalMemory(memory_path=self.temp_file.name)

    def tearDown(self):
        if os.path.exists(self.temp_file.name):
            try:
                os.remove(self.temp_file.name)
            except Exception:
                pass

    def test_record_and_boost_on_positive_feedback(self):
        query = "bca routine monday"
        sources = ["College/Routine.md", "College/Teachers.md"]
        self.memory.record_query(query, sources, confidence=0.85, iteration_count=1)
        self.memory.record_feedback(query, sources, is_positive=True)

        boosted = self.memory.get_boosted_sources(query)
        self.assertIn("College/Routine.md", boosted)
        self.assertGreater(boosted["College/Routine.md"], 0.0)

    def test_demote_on_negative_feedback(self):
        query = "artificial intelligence notes"
        bad_sources = ["College/FeeStructure.md"]
        self.memory.record_feedback(query, bad_sources, is_positive=False)

        demoted = self.memory.get_demoted_sources(query)
        self.assertIn("College/FeeStructure.md", demoted)

    def test_stats_reporting(self):
        self.memory.record_feedback("test query", ["NoteA.md"], is_positive=True)
        self.memory.record_feedback("test query 2", ["NoteB.md"], is_positive=False)
        stats = self.memory.get_stats()
        self.assertEqual(stats["positive_feedback"], 1)
        self.assertEqual(stats["negative_feedback"], 1)


class TestIterativeRAG(unittest.TestCase):
    def setUp(self):
        self.reader = ObsidianVaultReader()
        self.rag = VaultRAG(self.reader)

    def test_early_stopping_on_clear_query(self):
        # Mock LLM caller that provides a well-supported response directly
        def mock_ollama(prompt, model=None, timeout=60):
            return "RAG combines search retrieval with language model generation to produce grounded answers."

        result = self.rag.iterative_rag_query(
            query="What is RAG retrieval augmented generation?",
            ollama_caller=mock_ollama,
            max_iterations=5
        )
        self.assertTrue(result["success"])
        # Should early stop at iteration 1 or 2 because confidence is high
        self.assertLessEqual(result["iterations"], 2)
        self.assertGreaterEqual(result["confidence"], 0.70)
        self.assertIn("✓ Checked against local vault", result["answer"])

    def test_wikilink_and_domain_expansion(self):
        alts = self.rag.generate_alternative_queries("College routine monday", iteration=2)
        self.assertGreater(len(alts), 1)

    def test_read_only_vault_invariant(self):
        # Ensure that no vault files can be written to or read outside the vault boundary
        with self.assertRaises(SecurityPathViolationError):
            self.reader._resolve_safe_path("../outside_vault.txt")
        with self.assertRaises(SecurityPathViolationError):
            self.reader._resolve_safe_path("C:\\Windows\\System32\\calc.exe")


class TestGatewayEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_check(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["security_mode"], "STRICT_READ_ONLY")
        self.assertGreater(data["vault_total_notes"], 0)

    def test_destructive_command_refusal(self):
        res = self.client.post("/api/vault/query", json={"query": "Delete all notes in Obsidian vault"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["refused"])
        self.assertEqual(data["answer"], COMMAND_SAFETY_REFUSAL)

    def test_feedback_endpoint(self):
        res = self.client.post("/api/vault/feedback", json={
            "query": "test query feedback",
            "sources": ["AI/Test.md"],
            "is_positive": True
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])

    def test_memory_stats_endpoint(self):
        res = self.client.get("/api/vault/memory_stats")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("total_queries", data)
        self.assertIn("positive_feedback", data)


if __name__ == "__main__":
    unittest.main()
