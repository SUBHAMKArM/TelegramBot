"""
Obsidian Vault Targeted Retrieval / RAG Engine
==============================================
Provides architecture-aware targeted retrieval, query analysis, domain routing,
minimal-sufficient context extraction, and source attribution for Ollama.
All operations are local and strictly read-only.
"""

import os
import re
from typing import List, Dict, Any, Optional, Tuple
from obsidian_vault_reader import ObsidianVaultReader

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

    def __init__(self, reader: Optional[ObsidianVaultReader] = None):
        self.reader = reader or ObsidianVaultReader()

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
