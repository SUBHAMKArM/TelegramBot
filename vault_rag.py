"""
Obsidian Vault Targeted Retrieval / RAG Engine
==============================================
Provides architecture-aware targeted retrieval, query analysis, domain routing,
minimal-sufficient context extraction, and source attribution for Ollama.
All operations are local and strictly read-only.
"""

import os
import re
import time
from typing import List, Dict, Any, Optional, Tuple, Callable

from obsidian_vault_reader import ObsidianVaultReader
from retrieval_memory import RetrievalMemory
from verifier import TemporalContextResolver, AnswerVerifier

# Domain Mapping based on Obsidian Vault Architecture
DOMAINS = {
    "COLLEGE": "01_College",
    "COLLEGE_TIMETABLE": "01_College/Timetable",
    "COLLEGE_SUBJECTS": "01_College/Subjects",
    "COLLEGE_TEACHERS": "01_College/Teachers",
    "COLLEGE_PROJECTS": "01_College/College_Projects",
    "PROJECTS": "02_Projects",
    "SECOND_BRAIN": "03_Second_Brain",
    "AI_KNOWLEDGE": "03_Second_Brain/AI",
    "RESOURCES": "04_Resources",
    "ARCHIVE": "05_Archive",
    "SYSTEM": "99_System",
    "PROJECT_MEMORY": "99_System/AI_Memory/Projects"
}

# Teacher initials and keyword map for instant resolution in 01_College
COLLEGE_KEYWORDS = {
    "timetable", "class", "classes", "routine", "period", "slot", "room",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "সোম", "মঙ্গল", "বুধ", "বৃহস্পতি", "শুক্র", "শনিবার",
    "ক্লাস", "রুটিন", "টিচার", "মাস্টার", "কলেজ", "পরীক্ষা", "exam", "syllabus",
    "srb", "rjr", "ans", "sdm", "nrp", "spm", "mrb", "dms", "brd",
    "bca", "narula", "nit"
}

AI_KEYWORDS = {
    "ai", "artificial intelligence", "ml", "machine learning", "deep learning",
    "neural network", "neural networks", "llm", "large language model",
    "transformer", "transformers", "embedding", "embeddings", "rag",
    "retrieval-augmented generation", "vector database", "computer vision",
    "agent", "ai agents", "qwen", "ollama", "gpt"
}

PROJECT_KEYWORDS = {
    "nit bca attendance", "attendance app", "aerointel", "victus", "tts",
    "cu report", "excel architect", "civic roi", "aroma brew", "gravity brew",
    "sudomax", "patent", "project"
}


class VaultRAG:
    """
    RAG & Targeted Retrieval Engine for the Obsidian Vault.
    Performs query analysis, domain routing, and context extraction.
    """

    def __init__(self, reader: Optional[ObsidianVaultReader] = None, memory: Optional[RetrievalMemory] = None):
        self.reader = reader or ObsidianVaultReader()
        self.memory = memory or RetrievalMemory()

    def analyze_query(self, query: str) -> Dict[str, Any]:
        """
        Analyzes the user question to determine intent, domain, and key entities.
        """
        q_lower = query.strip().lower()

        # Check for College Domain triggers
        college_score = sum(1 for kw in COLLEGE_KEYWORDS if kw in q_lower)
        # Check for AI Knowledge triggers
        ai_score = sum(1 for kw in AI_KEYWORDS if kw in q_lower)
        # Check for Projects triggers
        project_score = sum(1 for kw in PROJECT_KEYWORDS if kw in q_lower)

        primary_domain = None
        folder_filter = None

        if "timetable" in q_lower or "routine" in q_lower or "ক্লাস" in q_lower or "রুটিন" in q_lower:
            primary_domain = "COLLEGE_TIMETABLE"
            folder_filter = DOMAINS["COLLEGE_TIMETABLE"]
        elif college_score > 0 and college_score >= ai_score and college_score >= project_score:
            primary_domain = "COLLEGE"
            folder_filter = DOMAINS["COLLEGE"]
        elif ai_score > 0 and ai_score >= project_score:
            primary_domain = "AI_KNOWLEDGE"
            folder_filter = DOMAINS["AI_KNOWLEDGE"]
        elif project_score > 0:
            primary_domain = "PROJECTS"
            folder_filter = None  # Search both 02_Projects and 99_System/AI_Memory/Projects
        else:
            primary_domain = "GENERAL"
            folder_filter = None

        return {
            "query": query,
            "primary_domain": primary_domain,
            "folder_filter": folder_filter,
            "scores": {
                "college": college_score,
                "ai": ai_score,
                "project": project_score
            }
        }

    def classify_vault_domain(self, query: str) -> str:
        """Returns the detected vault domain name for the given query."""
        return self.analyze_query(query)["primary_domain"]

    def _extract_relevant_section(self, content: str, query: str, max_chars: int = 1200) -> str:
        """
        Extracts the most relevant heading, table, or section from the note
        matching the query to minimize context size sent to Ollama.
        """
        query_terms = [t.lower() for t in query.split() if len(t) > 1]
        if not query_terms:
            return content[:max_chars]

        # Break content into sections by markdown headers (# or ## or ###)
        sections = re.split(r"\n(?=#{1,4}\s)", content)
        if len(sections) <= 1:
            # Fallback to paragraph splitting
            sections = content.split("\n\n")

        scored_sections = []
        for sec in sections:
            sec_lower = sec.lower()
            score = sum(sec_lower.count(term) for term in query_terms)
            if score > 0:
                scored_sections.append((score, sec))

        if scored_sections:
            # Sort by highest relevance score
            scored_sections.sort(key=lambda x: x[0], reverse=True)
            # Pick best sections within max_chars
            selected = []
            cur_len = 0
            for _, sec in scored_sections:
                if cur_len + len(sec) <= max_chars:
                    selected.append(sec.strip())
                    cur_len += len(sec)
                else:
                    remaining = max_chars - cur_len
                    if remaining > 150:
                        selected.append(sec[:remaining].strip() + "...")
                    break
            return "\n\n---\n\n".join(selected)

        # Fallback: return start of content up to max_chars
        return content[:max_chars].strip()

    def retrieve_context(
        self,
        query: str,
        max_notes: int = 3,
        max_total_chars: int = 2500
    ) -> Dict[str, Any]:
        """
        Retrieves targeted context from the Obsidian vault for answering the query.
        Returns:
            {
                "has_context": bool,
                "context_text": str,
                "source_notes": List[str],
                "retrieval_count": int,
                "domain": str
            }
        """
        analysis = self.analyze_query(query)
        domain = analysis["primary_domain"]
        folder_filter = analysis["folder_filter"]

        # Step 1: Content search in target domain
        matches = self.reader.search_content(query, folder_filter=folder_filter, max_results=max_notes + 2)

        # Step 2: If few or no matches in specific subfolder, broaden search to full vault
        if len(matches) < 2 and folder_filter:
            broad_matches = self.reader.search_content(query, folder_filter=None, max_results=max_notes)
            existing_paths = {m["relative_path"] for m in matches}
            for bm in broad_matches:
                if bm["relative_path"] not in existing_paths:
                    matches.append(bm)
                    existing_paths.add(bm["relative_path"])

        # Step 3: Check filename matches
        filename_matches = self.reader.search_files(query, folder_filter=folder_filter)
        for fm in filename_matches:
            if not any(m["relative_path"] == fm["relative_path"] for m in matches):
                try:
                    meta = self.reader.get_metadata(fm["relative_path"])
                    matches.insert(0, {
                        "filename": fm["filename"],
                        "relative_path": fm["relative_path"],
                        "title": fm["title"],
                        "score": 10,
                        "snippets": [],
                        "length": meta.get("size_bytes", 0)
                    })
                except Exception:
                    pass

        # Sort top matches
        matches = matches[:max_notes]

        if not matches:
            return {
                "has_context": False,
                "context_text": "",
                "source_notes": [],
                "retrieval_count": 0,
                "domain": domain
            }

        context_chunks = []
        source_notes = []
        chars_used = 0
        budget_per_note = max_total_chars // max(1, len(matches))

        for m in matches:
            rel_path = m["relative_path"]
            try:
                raw_content = self.reader.read_file(rel_path)
                section = self._extract_relevant_section(raw_content, query, max_chars=budget_per_note)
                chunk_header = f"### [Source: {m['filename']}] ({rel_path})\n{section}"
                
                if chars_used + len(chunk_header) <= max_total_chars:
                    context_chunks.append(chunk_header)
                    source_notes.append(m["filename"])
                    chars_used += len(chunk_header)
                else:
                    break
            except Exception:
                continue

        final_context = "\n\n".join(context_chunks).strip()
        return {
            "has_context": bool(final_context),
            "context_text": final_context,
            "source_notes": source_notes,
            "retrieval_count": len(source_notes),
            "domain": domain
        }

    def build_ollama_prompt(self, user_query: str, retrieval_result: Dict[str, Any]) -> str:
        """
        Builds the grounded prompt for local Ollama inference.
        Enforces answer grounding, source attribution, and anti-hallucination.
        """
        if not retrieval_result.get("has_context"):
            return (
                "You are an assistant for a personal Obsidian knowledge vault.\n"
                f"The user asked: \"{user_query}\"\n\n"
                "No relevant notes were found in the Obsidian vault for this query.\n"
                "Strictly reply:\n"
                "\"I couldn't find enough information in the Obsidian vault.\"\n"
                "Do not invent facts."
            )

        context_text = retrieval_result["context_text"]
        sources_list = "\n".join(f"- {s}" for s in retrieval_result["source_notes"])

        prompt = (
            "You are a helpful knowledge assistant with READ-ONLY access to the user's personal Obsidian vault.\n"
            "Your task is to answer the user's question accurately and concisely, strictly grounded in the provided Vault Context.\n\n"
            "STRICT RULES:\n"
            "1. Answer ONLY using information explicitly stated in the Vault Context below.\n"
            "2. If the answer cannot be found in the Vault Context, clearly say: \"I couldn't find enough information in the Obsidian vault.\"\n"
            "3. Do NOT make up, invent, or hallucinate facts.\n"
            "4. Respond in the same language as the user's query (e.g. Bengali, English, or Banglish).\n"
            "5. At the very end of your response, you MUST include the sources in this exact format:\n"
            "Sources:\n"
            f"{sources_list}\n\n"
            f"--- VAULT CONTEXT ---\n{context_text}\n--- END CONTEXT ---\n\n"
            f"User Question: \"{user_query}\"\n"
            "Assistant Answer:"
        )
        return prompt

    def generate_alternative_queries(
        self,
        original_query: str,
        domain: str = None,
        retrieved_source_notes: List[str] = None,
        temporal_info: Dict[str, Any] = None,
        iteration: int = 1,
        **kwargs
    ) -> List[str]:
        """
        Generates alternative queries using temporal context, vault domain hierarchy,
        synonyms, and linked notes traversal.
        """
        alternatives = []
        q_lower = original_query.lower()

        if domain is None:
            domain = self.classify_vault_domain(original_query)
        if retrieved_source_notes is None:
            retrieved_source_notes = []
        if temporal_info is None:
            temporal_info = TemporalContextResolver.resolve_temporal_query(original_query)

        # 1. Temporal alternative queries
        resolved_day = temporal_info.get("resolved_day")
        if resolved_day:
            alternatives.append(f"{resolved_day} timetable BCA 1B")
            alternatives.append(f"{resolved_day} class routine")
            alternatives.append(f"Detailed Daily Breakdown {resolved_day}")

        # 2. Domain-specific expansion
        if domain in ("COLLEGE", "COLLEGE_TIMETABLE"):
            alternatives.extend([
                "2026 Odd Semester BCA 1B",
                "Class Timetable BCA 1B",
                "Master Timetable Matrix",
                "College Map"
            ])
        elif domain == "AI_KNOWLEDGE":
            # Expand AI terms with related concept notes
            for topic in ["rag", "llm", "embeddings", "vector database", "transformer", "neural network", "deep learning"]:
                if topic in q_lower:
                    alternatives.append(f"{topic.upper()} concept")
                    alternatives.append(f"Related {topic}")
        elif domain == "PROJECTS":
            alternatives.append("Antigravity Projects Memory")
            alternatives.append("Projects Map")

        # 3. Traversal of linked notes ([[wikilinks]]) from discovered sources
        for note_file in retrieved_source_notes[:2]:
            note_matches = self.reader.search_files(note_file)
            if note_matches:
                rel = note_matches[0]["relative_path"]
                try:
                    links = self.reader.get_links(rel)
                    for wl in links.get("outgoing_wikilinks", [])[:3]:
                        if len(wl) > 3 and wl.lower() not in [a.lower() for a in alternatives]:
                            alternatives.append(wl)
                except Exception:
                    pass

        # Return unique alternatives excluding the exact original query
        seen = {original_query.strip().lower()}
        clean_alts = []
        for a in alternatives:
            clean_a = a.strip()
            if clean_a.lower() not in seen:
                seen.add(clean_a.lower())
                clean_alts.append(clean_a)
        return clean_alts[:5]

    def iterative_rag_query(
        self,
        query: str,
        ollama_caller: Callable[[str, str], Optional[str]],
        model: str = "qwen2.5:1.5b",
        max_iterations: int = 5
    ) -> Dict[str, Any]:
        """
        Executes the iterative local verification loop:
        Search -> Draft -> Evidence Check -> Contradiction Check -> Decision -> (Query Expansion & Re-verify)
        Stops early (1-2 iterations) if sufficient evidence is established (NO BLIND LOOPING).
        """
        start_time = time.time()

        # Step 0: Resolve temporal context
        temp_info = TemporalContextResolver.resolve_temporal_query(query)
        effective_query = temp_info["expanded_query"]

        # Step 1: Check retrieval memory for previously verified sources
        prioritized_sources = self.memory.get_prioritized_sources(query)

        attempted_queries = [effective_query]
        accumulated_sources: List[str] = []
        best_draft = ""
        best_retrieval: Optional[Dict[str, Any]] = None
        best_confidence = 0.0
        final_contradictions: List[str] = []
        iteration_count = 0
        seen_contexts = set()

        for it in range(1, max_iterations + 1):
            iteration_count = it
            curr_q = attempted_queries[-1]

            # 1. Targeted Search
            retrieval = self.retrieve_context(curr_q)

            # Memory Boost: inject previously confirmed high-confidence sources
            if prioritized_sources and it == 1:
                for ps in prioritized_sources:
                    if ps not in retrieval["source_notes"]:
                        try:
                            found = self.reader.search_files(ps)
                            if found:
                                rel_p = found[0]["relative_path"]
                                raw_c = self.reader.read_file(rel_p)
                                sec = self._extract_relevant_section(raw_c, effective_query, max_chars=800)
                                chunk = f"### [Source (Memory Boosted): {ps}] ({rel_p})\n{sec}"
                                retrieval["context_text"] = f"{chunk}\n\n{retrieval['context_text']}".strip()
                                retrieval["source_notes"].insert(0, ps)
                                retrieval["has_context"] = True
                        except Exception:
                            pass

            if not retrieval["has_context"]:
                # If first search returned no notes, attempt query expansion before giving up
                if it < max_iterations:
                    alts = self.generate_alternative_queries(
                        query, retrieval.get("domain", "GENERAL"), [], temp_info
                    )
                    next_q = next((a for a in alts if a not in attempted_queries), None)
                    if next_q:
                        attempted_queries.append(next_q)
                        continue

                # Truly nothing found
                self.memory.record_retrieval_failure(query, attempted_queries)
                return {
                    "success": True,
                    "answer": "I couldn't find enough information in the Obsidian vault.",
                    "sources": [],
                    "iterations": iteration_count,
                    "confidence": 0.0,
                    "verification_status": "⚠️ Could not verify (no relevant notes in vault)",
                    "has_context": False,
                    "domain": retrieval.get("domain", "GENERAL"),
                    "contradictions": [],
                    "duration_seconds": round(time.time() - start_time, 2)
                }

            # Avoid re-processing identical context (no blind looping)
            context_hash = hash(retrieval["context_text"])
            if context_hash in seen_contexts and it > 1:
                break
            seen_contexts.add(context_hash)

            best_retrieval = retrieval
            for s in retrieval["source_notes"]:
                if s not in accumulated_sources:
                    accumulated_sources.append(s)

            # 2. Draft Answer Generation via Local Ollama
            prompt = self.build_ollama_prompt(query, retrieval)
            draft = ollama_caller(prompt, model=model) or ""

            # 3. Evidence & Contradiction Check
            v = AnswerVerifier.verify_draft(
                effective_query, draft, retrieval["context_text"], retrieval["source_notes"]
            )
            confidence = v["confidence"]
            contradictions = v["contradictions"]
            if contradictions:
                for c in contradictions:
                    if c not in final_contradictions:
                        final_contradictions.append(c)

            best_draft = draft
            best_confidence = max(best_confidence, confidence)

            # 4. DECISION: Stop early if sufficient (NO BLIND LOOPING)
            if v["is_sufficient"] or confidence >= 0.80:
                break

            # 5. Query Expansion for Next Iteration
            if it < max_iterations:
                alts = self.generate_alternative_queries(
                    query, retrieval.get("domain", "GENERAL"), retrieval["source_notes"], temp_info
                )
                next_q = next((a for a in alts if a not in attempted_queries), None)
                if not next_q:
                    # No new query expansion available, stop cleanly
                    break
                attempted_queries.append(next_q)

        # 6. Formulate Verified Final Answer
        final_answer = best_draft or "I couldn't find enough information in the Obsidian vault."

        # Add explicit contradiction warnings if detected across notes
        if final_contradictions:
            conflict_msg = f"\n\n⚠️ **Note Discrepancy:** {final_contradictions[0]}"
            if conflict_msg not in final_answer:
                final_answer += conflict_msg

        # Ensure Sources are cleanly cited
        if accumulated_sources and "Sources:" not in final_answer:
            sources_str = "\n".join(f"- {s}" for s in accumulated_sources)
            final_answer += f"\n\nSources:\n{sources_str}"

        # Attach Verification Badge
        if best_confidence >= 0.75 and accumulated_sources:
            verif_badge = "Verification: ✓ Checked against local vault"
        else:
            verif_badge = "Verification: ⚠️ Could not fully verify from the vault."

        if verif_badge not in final_answer:
            final_answer += f"\n\n{verif_badge}"

        # 7. Update Local Retrieval Memory (zero vault modification)
        if best_confidence >= 0.70 and accumulated_sources:
            self.memory.record_successful_retrieval(query, accumulated_sources, iteration_count)
        else:
            self.memory.record_retrieval_failure(query, attempted_queries)

        elapsed = round(time.time() - start_time, 2)
        return {
            "success": True,
            "answer": final_answer,
            "sources": accumulated_sources,
            "iterations": iteration_count,
            "confidence": best_confidence,
            "verification_status": verif_badge,
            "has_context": True,
            "domain": best_retrieval["domain"] if best_retrieval else "GENERAL",
            "contradictions": final_contradictions,
            "duration_seconds": elapsed
        }
