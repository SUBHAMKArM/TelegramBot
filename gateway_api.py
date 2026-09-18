"""
Local AI Gateway for Obsidian Vault Knowledge Assistant
=======================================================
FastAPI service bound strictly to 127.0.0.1:8765.
Provides local RAG query and search endpoints backed by local Ollama.
Hard Invariants:
- Strictly bound to localhost (127.0.0.1)
- Zero external data transmission (100% local processing)
- Strictly read-only access to Obsidian Vault
- Rejects all file modification/deletion commands
- Structured local audit logging (gateway.log) with zero vault content logging
"""

import os
import sys
import re
import time
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
import requests
import uvicorn

from obsidian_vault_reader import ObsidianVaultReader, SecurityPathViolationError
from vault_rag import VaultRAG

# Configuration
GATEWAY_HOST = os.environ.get("GATEWAY_HOST", "127.0.0.1")
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "8765"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
DEFAULT_OLLAMA_MODEL = os.environ.get("FAST_LOCAL_MODEL", "qwen2.5:1.5b")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gateway.log")

# Setup Logging
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [User:%(message)s]"
)
logger = logging.getLogger("vault_gateway")

app = FastAPI(
    title="Obsidian Read-Only AI Gateway",
    description="Localhost gateway for querying Obsidian vault via Ollama",
    version="1.0.0"
)

# Initialize Read-Only Adapter and RAG Engine
vault_reader = ObsidianVaultReader()
vault_rag = VaultRAG(vault_reader)

# Regex patterns to detect destructive/write/modification attempts
DESTRUCTIVE_COMMAND_PATTERNS = [
    r"\b(delete|remove|erase|destroy|unlink|drop)\b",
    r"\b(edit|modify|alter|update|change|rewrite|overwrite)\b",
    r"\b(rename|move|relocate)\b",
    r"\b(create|make|write|add|new|append|insert)\s+(note|file|markdown|doc|folder|directory)",
    r"\b(restore|rollback|revert)\b",
    r"(ডিলিট|মুছে\s*ফেল|বদল\s*করো|এডিট\s*করো|পরিবর্তন\s*করো|নতুন\s*(নোট|ফাইল))"
]

COMMAND_SAFETY_REFUSAL = (
    "I can read and search the vault, but this Telegram AI has no permission to edit or delete files."
)


class QueryRequest(BaseModel):
    query: str
    telegram_user_id: Optional[int] = None
    model: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    folder_filter: Optional[str] = None
    max_results: Optional[int] = 10


def is_destructive_command(text: str) -> bool:
    """Checks if the user text asks to modify, edit, create, or delete vault files."""
    lower = text.strip().lower()
    for pattern in DESTRUCTIVE_COMMAND_PATTERNS:
        if re.search(pattern, lower):
            return True
    return False


def call_ollama(prompt: str, model: str = DEFAULT_OLLAMA_MODEL, timeout: int = 120) -> Optional[str]:
    """
    Queries local Ollama engine without passing any filesystem tools.
    """
    try:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "10m"
        }
        res = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        if res.status_code == 200:
            return res.json().get("response", "").strip()
        return None
    except Exception as e:
        logger.error(f"Ollama connection error: {e}")
        return None


@app.get("/health")
def health_check():
    """Checks gateway health, Ollama status, and Vault readiness."""
    ollama_ok = False
    try:
        res = requests.get("http://127.0.0.1:11434", timeout=2)
        ollama_ok = res.status_code == 200
    except Exception:
        pass

    stats = vault_reader.get_vault_stats()
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "ollama_online": ollama_ok,
        "ollama_url": OLLAMA_URL,
        "vault_total_notes": stats["total_notes"],
        "security_mode": "STRICT_READ_ONLY"
    }


class FeedbackRequest(BaseModel):
    query: str
    sources: List[str] = []
    is_positive: bool


@app.post("/api/vault/query")
def query_vault(req: QueryRequest):
    """
    Performs iterative targeted search, draft generation, evidence checking,
    contradiction detection, and early stopping via local Ollama.
    Firmly rejects destructive or modification commands.
    """
    start_time = time.time()
    user_id_str = str(req.telegram_user_id) if req.telegram_user_id else "ANONYMOUS"
    query_text = req.query.strip()

    # 1. Hard Command Safety Check
    if is_destructive_command(query_text):
        logger.info(f"{user_id_str} | Query: '{query_text[:50]}' | ACTION_REFUSED_WRITE_ATTEMPT")
        return {
            "success": True,
            "refused": True,
            "answer": COMMAND_SAFETY_REFUSAL,
            "sources": [],
            "domain": "SAFETY_GUARD",
            "has_context": False,
            "iterations": 0,
            "confidence": 0.0,
            "verification_status": "Refused (Write Attempt)"
        }

    # 2. Iterative RAG Pipeline (1 to 5 cycles with early stop)
    model_to_use = req.model or DEFAULT_OLLAMA_MODEL
    result = vault_rag.iterative_rag_query(
        query=query_text,
        ollama_caller=call_ollama,
        model=model_to_use,
        max_iterations=5
    )

    elapsed = result.get("duration_seconds", round(time.time() - start_time, 2))
    logger.info(
        f"{user_id_str} | Query: '{query_text[:50]}' | Domain: {result['domain']} | "
        f"Sources: {len(result['sources'])} | Iterations: {result['iterations']} | "
        f"Conf: {result['confidence']} | Duration: {elapsed}s"
    )

    result["refused"] = False
    return result


@app.post("/api/vault/feedback")
def record_feedback(req: FeedbackRequest):
    """
    Records user feedback (👍 / 👎) into local retrieval memory.
    Zero modification to Obsidian vault or Ollama weights.
    """
    updated = vault_rag.memory.record_feedback(
        query=req.query,
        sources=req.sources,
        is_positive=req.is_positive
    )
    return {
        "success": True,
        "updated": updated,
        "memory_stats": vault_rag.memory.get_stats()
    }


@app.get("/api/vault/memory_stats")
def retrieval_memory_stats():
    """Returns statistics from the local retrieval learning memory."""
    return vault_rag.memory.get_stats()


@app.post("/api/vault/search")
def search_vault(req: SearchRequest):
    """
    Safe read-only search endpoint returning matching notes and snippets.
    """
    try:
        results = vault_reader.search_content(
            query=req.query,
            folder_filter=req.folder_filter,
            max_results=req.max_results or 10
        )
        return {
            "success": True,
            "query": req.query,
            "count": len(results),
            "results": results
        }
    except SecurityPathViolationError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/vault/stats")
def vault_stats():
    """Returns read-only statistics about notes in the vault."""
    return vault_reader.get_vault_stats()


def start_server():
    """Entrypoint to run the gateway locally."""
    print(f"Starting Local Obsidian AI Gateway on http://{GATEWAY_HOST}:{GATEWAY_PORT} (Strict Read-Only)...")
    uvicorn.run(app, host=GATEWAY_HOST, port=GATEWAY_PORT, log_level="warning")


if __name__ == "__main__":
    start_server()
