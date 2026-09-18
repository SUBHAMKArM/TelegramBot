"""
Local Retrieval Learning Memory
===============================
Provides persistent learning for the Obsidian RAG pipeline.
Remembers successful query-to-source mappings, failure patterns,
and user feedback (👍 / 👎) to continually optimize retrieval.

STRICT PRIVACY & READ-ONLY INVARIANTS:
- Stores ONLY non-sensitive query metadata and relative source paths.
- Never stores raw note contents, secrets, or authentication tokens.
- Completely isolated from Obsidian Vault (zero vault file modification).
- Zero model-weight modification (retrieval optimization only).
"""

import os
import json
import re
import time
from typing import List, Dict, Any, Optional

MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "retrieval_memory.json")

STOP_WORDS = {
    "what", "is", "the", "for", "and", "a", "an", "in", "on", "at", "to", "from",
    "by", "with", "of", "about", "are", "do", "does", "my", "your", "can", "how",
    "tell", "me", "give", "show", "according", "notes", "note", "vault", "this",
    "who", "when", "where", "which", "there", "any", "some",
    "কী", "কি", "আছে", "কেমন", "বলো", "বল", "করো", "আমার", "নোট", "নোটস", "ভল্ট"
}


def normalize_query(query: str) -> str:
    """Normalizes query string for consistent indexing."""
    cleaned = re.sub(r"[^\w\s\u0980-\u09FF]", " ", query.lower())
    words = [w.strip() for w in cleaned.split() if len(w.strip()) > 1]
    filtered = [w for w in words if w not in STOP_WORDS]
    return " ".join(filtered if filtered else words)


class RetrievalMemory:
    """
    Manages local retrieval memory to learn successful note associations
    and record user feedback.
    """

    def __init__(self, filepath: str = MEMORY_FILE, memory_path: str = None):
        self.filepath = memory_path or filepath
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(self.filepath):
            initial_data = {
                "version": "1.0.0",
                "last_updated": time.time(),
                "query_mappings": {},
                "failed_queries": {},
                "source_boosts": {}
            }
            self._save(initial_data)

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {
                "version": "1.0.0",
                "last_updated": time.time(),
                "query_mappings": {},
                "failed_queries": {},
                "source_boosts": {}
            }

    def _save(self, data: Dict[str, Any]):
        data["last_updated"] = time.time()
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_prioritized_sources(self, query: str) -> List[str]:
        """
        Returns list of source note filenames that previously answered
        identical or semantically similar queries with high confidence.
        """
        norm = normalize_query(query)
        if not norm:
            return []

        data = self._load()
        mappings = data.get("query_mappings", {})

        # Direct match
        if norm in mappings:
            entry = mappings[norm]
            if entry.get("positive_feedback", 0) >= entry.get("negative_feedback", 0):
                return entry.get("successful_sources", [])

        # Fuzzy word-set match
        query_words = set(norm.split())
        best_match = None
        best_score = 0.0

        for stored_norm, entry in mappings.items():
            stored_words = set(stored_norm.split())
            if not stored_words:
                continue
            common = query_words & stored_words
            score = len(common) / max(len(query_words), len(stored_words))
            if score > best_score and score >= 0.70:
                best_score = score
                best_match = entry

        if best_match and best_match.get("positive_feedback", 0) >= best_match.get("negative_feedback", 0):
            return best_match.get("successful_sources", [])

        return []

    def record_successful_retrieval(self, query: str, sources: List[str], iterations: int):
        """
        Records that a query was successfully answered using the specified sources.
        """
        if not sources:
            return
        norm = normalize_query(query)
        if not norm:
            return

        data = self._load()
        mappings = data.setdefault("query_mappings", {})
        boosts = data.setdefault("source_boosts", {})

        entry = mappings.get(norm, {
            "query_sample": query[:80],
            "successful_sources": [],
            "hits": 0,
            "positive_feedback": 0,
            "negative_feedback": 0,
            "avg_iterations": iterations
        })

        entry["hits"] += 1
        # Merge sources preserving order
        for s in sources:
            if s not in entry["successful_sources"]:
                entry["successful_sources"].append(s)
            boosts[s] = boosts.get(s, 0) + 1

        mappings[norm] = entry
        self._save(data)

    def record_retrieval_failure(self, query: str, attempted_queries: List[str]):
        """
        Records a retrieval failure so future searches expand to alternative strategies.
        """
        norm = normalize_query(query)
        if not norm:
            return

        data = self._load()
        failures = data.setdefault("failed_queries", {})
        failures[norm] = {
            "query_sample": query[:80],
            "attempts": attempted_queries[:5],
            "last_failed": time.time()
        }
        self._save(data)

    def record_feedback(self, query: str, sources: List[str], is_positive: bool) -> bool:
        """
        Updates memory based on explicit user feedback (👍 / 👎).
        """
        norm = normalize_query(query)
        data = self._load()
        mappings = data.setdefault("query_mappings", {})
        boosts = data.setdefault("source_boosts", {})

        if norm not in mappings:
            mappings[norm] = {
                "query_sample": query[:80],
                "successful_sources": [s for s in sources] if is_positive else [],
                "hits": 1,
                "positive_feedback": 1 if is_positive else 0,
                "negative_feedback": 0 if is_positive else 1,
                "avg_iterations": 1
            }
        else:
            entry = mappings[norm]
            if is_positive:
                entry["positive_feedback"] = entry.get("positive_feedback", 0) + 1
                for s in sources:
                    if s not in entry["successful_sources"]:
                        entry["successful_sources"].append(s)
            else:
                entry["negative_feedback"] = entry.get("negative_feedback", 0) + 1
                entry["successful_sources"] = [s for s in entry.get("successful_sources", []) if s not in sources]

        for s in sources:
            if is_positive:
                boosts[s] = boosts.get(s, 0) + 2
            else:
                boosts[s] = boosts.get(s, 0) - 2

        self._save(data)
        return True

    def record_query(self, query: str, sources: List[str], confidence: float = 0.0, iteration_count: int = 1):
        """Convenience alias to record_successful_retrieval."""
        self.record_successful_retrieval(query, sources, iteration_count)

    def get_boosted_sources(self, query: str) -> Dict[str, float]:
        """Returns dictionary of boosted sources and their boost weights."""
        data = self._load()
        boosts = data.get("source_boosts", {})
        prioritized = self.get_prioritized_sources(query)
        result = {}
        for s in prioritized:
            result[s] = float(boosts.get(s, 1))
        # Include all general positive boosts if prioritized was empty
        if not result:
            for s, b in boosts.items():
                if b > 0:
                    result[s] = float(b)
        return result

    def get_demoted_sources(self, query: str) -> List[str]:
        """Returns list of sources that have been demoted by negative feedback."""
        data = self._load()
        boosts = data.get("source_boosts", {})
        return [s for s, b in boosts.items() if b <= 0]

    def get_stats(self) -> Dict[str, Any]:
        """Returns comprehensive statistics on learned mappings and user feedback."""
        data = self._load()
        mappings = data.get("query_mappings", {})
        boosts = data.get("source_boosts", {})
        pos = sum(e.get("positive_feedback", 0) for e in mappings.values())
        neg = sum(e.get("negative_feedback", 0) for e in mappings.values())
        return {
            "total_queries": len(mappings),
            "total_mapped_queries": len(mappings),
            "total_failed_queries": len(data.get("failed_queries", {})),
            "positive_feedback": pos,
            "negative_feedback": neg,
            "boosted_sources_count": len([k for k, v in boosts.items() if v > 0]),
            "demoted_sources_count": len([k for k, v in boosts.items() if v <= 0]),
            "top_boosted_sources": sorted(
                boosts.items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]
        }
