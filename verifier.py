"""
Answer Verification, Contradiction Detection & Temporal Preprocessor
===================================================================
Performs evidence verification, factual grounding checks, contradiction
detection between notes, and relative temporal resolution (today/tomorrow/kal).
"""

import re
import datetime
from typing import List, Dict, Any, Tuple, Optional


class TemporalContextResolver:
    """
    Resolves relative dates and terms ('today', 'tomorrow', 'কাল', 'আগামীকাল')
    into concrete weekdays and dates before retrieval.
    """

    WEEKDAYS_BN = {
        0: "Monday (সোমবার)",
        1: "Tuesday (মঙ্গলবার)",
        2: "Wednesday (বুধবার)",
        3: "Thursday (বৃহস্পতিবার)",
        4: "Friday (শুক্রবার)",
        5: "Saturday (শনিবার)",
        6: "Sunday (রবিবার)"
    }

    WEEKDAYS_EN = {
        0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
        4: "Friday", 5: "Saturday", 6: "Sunday"
    }

    @classmethod
    def resolve_temporal_query(cls, query: str) -> Dict[str, Any]:
        """
        Detects relative temporal keywords and appends resolved day/date context.
        """
        now = datetime.datetime.now()
        q_lower = query.strip().lower()
        resolved_day = None
        resolved_date = None
        temporal_matched = False

        # Tomorrow matches
        tomorrow_patterns = ["tomorrow", "kal", "kalke", "agamikal", "কাল", "কালকে", "আগামীকাল", "next day"]
        if any(re.search(rf"\b{p}\b", q_lower) for p in tomorrow_patterns):
            target = now + datetime.timedelta(days=1)
            resolved_day = cls.WEEKDAYS_EN[target.weekday()]
            resolved_date = target.strftime("%Y-%m-%d")
            temporal_matched = True

        # Today matches
        today_patterns = ["today", "aaj", "aajke", "aj", "ajke", "আজ", "আজকে", "এখন", "tonight"]
        if not temporal_matched and any(re.search(rf"\b{p}\b", q_lower) for p in today_patterns):
            resolved_day = cls.WEEKDAYS_EN[now.weekday()]
            resolved_date = now.strftime("%Y-%m-%d")
            temporal_matched = True

        # Yesterday matches
        yesterday_patterns = ["yesterday", "gotokal", "গত", "গতকাল"]
        if not temporal_matched and any(re.search(rf"\b{p}\b", q_lower) for p in yesterday_patterns):
            target = now - datetime.timedelta(days=1)
            resolved_day = cls.WEEKDAYS_EN[target.weekday()]
            resolved_date = target.strftime("%Y-%m-%d")
            temporal_matched = True

        expanded_query = query
        if temporal_matched and resolved_day:
            expanded_query = f"{query} {resolved_day} {resolved_date}"

        return {
            "original_query": query,
            "expanded_query": expanded_query.strip(),
            "temporal_matched": temporal_matched,
            "resolved_day": resolved_day,
            "resolved_date": resolved_date
        }

    @classmethod
    def resolve_temporal_expression(cls, query: str) -> Dict[str, Any]:
        """Convenience wrapper returning structured temporal match dictionary."""
        q_lower = query.strip().lower()
        now = datetime.datetime.now()
        target_date = None
        expanded_keywords = []

        weekday_map = {
            "monday": 0, "sombar": 0, "সোমবার": 0,
            "tuesday": 1, "mongolbar": 1, "মঙ্গলবার": 1,
            "wednesday": 2, "budhbar": 2, "বুধবার": 2,
            "thursday": 3, "brihospotibar": 3, "বৃহস্পতিবার": 3,
            "friday": 4, "sukrobar": 4, "শুক্রবার": 4,
            "saturday": 5, "sonibar": 5, "শনিবার": 5,
            "sunday": 6, "robibar": 6, "রবিবার": 6
        }
        for name, idx in weekday_map.items():
            if re.search(rf"\b{name}\b", q_lower) or name in q_lower:
                days_ahead = (idx - now.weekday()) % 7
                target = now + datetime.timedelta(days=days_ahead)
                target_date = target.strftime("%Y-%m-%d")
                expanded_keywords.append(cls.WEEKDAYS_EN[idx])
                break

        res = cls.resolve_temporal_query(query)
        if res["temporal_matched"]:
            target_date = res["resolved_date"]
            if res["resolved_day"] and res["resolved_day"] not in expanded_keywords:
                expanded_keywords.append(res["resolved_day"])

        return {
            "target_date": target_date,
            "expanded_keywords": expanded_keywords,
            "original_query": query,
            "expanded_query": res["expanded_query"],
            "temporal_matched": res["temporal_matched"] or len(expanded_keywords) > 0
        }


class AnswerVerifier:
    """
    Audits a draft answer against the retrieved context to verify that:
    1. Key factual claims are supported by the vault evidence.
    2. Contradictions between multiple notes are detected.
    3. The answer is sufficiently complete to stop early.
    """

    @staticmethod
    def verify_draft(
        query: str,
        draft_answer: str,
        context_text: str,
        source_notes: List[str]
    ) -> Dict[str, Any]:
        """
        Evaluates the draft answer against evidence.
        Returns confidence score, sufficiency, contradictions, and suggestions.
        """
        if not draft_answer or not context_text or not source_notes:
            return {
                "confidence": 0.0,
                "is_sufficient": False,
                "unsupported_claims": ["No context available"],
                "contradictions": [],
                "suggestion": "Search for broader terms or related wikilinks."
            }

        # Check for standard "not found" admissions
        not_found_phrases = [
            "couldn't find enough information",
            "could not find enough information",
            "no information found",
            "তথ্য পাওয়া যায়নি",
            "খুঁজে পাওয়া যায়নি"
        ]
        if any(p in draft_answer.lower() for p in not_found_phrases):
            return {
                "confidence": 0.20,
                "is_sufficient": False,
                "unsupported_claims": ["Vault does not appear to contain answer"],
                "contradictions": [],
                "suggestion": "Expand search terms with aliases or check parent hub notes."
            }

        context_lower = context_text.lower()
        draft_lower = draft_answer.lower()

        # 1. Fact-check entities from draft against context
        # Extract potential named entities, timings, room numbers, subjects
        specific_tokens = re.findall(r"\b[A-Z][a-zA-Z0-9_\-]{2,}\b|\b\d{1,2}:\d{2}\b|\b\d{3}[a-zA-Z]?\b", draft_answer)
        
        supported_count = 0
        unsupported = []
        for token in set(specific_tokens):
            if token.lower() in context_lower:
                supported_count += 1
            else:
                unsupported.append(token)

        total_specific = len(set(specific_tokens))
        entity_support_ratio = (supported_count / total_specific) if total_specific > 0 else 0.85

        # 2. Contradiction Detection
        contradictions = []
        
        # Check direct negative vs positive statements (e.g. holiday / no classes vs classes held)
        if ("no class" in context_lower or "holiday" in context_lower or "off day" in context_lower or "no classes" in context_lower) and \
           ("classes are held" in draft_lower or "class is scheduled" in draft_lower or "there is class" in draft_lower):
            contradictions.append("Contradiction: Context specifies holiday or no classes, while draft states classes are held.")

        if len(source_notes) > 1:
            # Check for contrasting keywords like "updated", "deprecated", "revised", "old"
            if ("deprecated" in context_lower or "superseded" in context_lower) and "active" in context_lower:
                contradictions.append("Detected notes with conflicting lifecycle states (active vs superseded/deprecated).")

            # Check for multiple conflicting rooms for identical subject
            rooms_found = set(re.findall(r"room\s*(\d{3}[a-zA-Z]?)", context_lower))
            if len(rooms_found) > 2:
                contradictions.append(f"Multiple rooms referenced across notes: {', '.join(rooms_found)}.")

        # 3. Overall Confidence Calculation
        base_confidence = 0.50
        if len(context_text) > 50:
            base_confidence += 0.15
        if len(context_text) > 200:
            base_confidence += 0.10
        if entity_support_ratio >= 0.80:
            base_confidence += 0.25
        elif entity_support_ratio >= 0.50:
            base_confidence += 0.10
        else:
            base_confidence -= 0.15

        if contradictions:
            base_confidence -= 0.15

        confidence = max(0.0, min(1.0, base_confidence))
        
        # Stop condition: high confidence (>= 0.80) and zero severe contradictions
        is_sufficient = (confidence >= 0.80) and (len(contradictions) == 0)

        suggestion = None
        if not is_sufficient:
            if unsupported:
                suggestion = f"Verify unsupported claims: {', '.join(unsupported[:3])}"
            elif contradictions:
                suggestion = "Clarify contradiction between multiple retrieved sources."
            else:
                suggestion = "Deepen search into linked or backlinked notes."

        return {
            "confidence": round(confidence, 2),
            "is_sufficient": is_sufficient,
            "entity_support_ratio": round(entity_support_ratio, 2),
            "unsupported_claims": unsupported[:5],
            "contradictions": contradictions,
            "suggestion": suggestion
        }

    def verify(
        self,
        draft_answer: str,
        context_text: str,
        query: str = ""
    ) -> Dict[str, Any]:
        """Convenience wrapper mapping to verify_draft."""
        res = self.verify_draft(
            query=query,
            draft_answer=draft_answer,
            context_text=context_text,
            source_notes=["vault_context"] if context_text else []
        )
        return {
            "confidence_score": res["confidence"],
            "is_sufficient": res["is_sufficient"],
            "has_contradiction": len(res["contradictions"]) > 0,
            "issues": res["contradictions"] + res["unsupported_claims"],
            "suggestion": res["suggestion"]
        }
