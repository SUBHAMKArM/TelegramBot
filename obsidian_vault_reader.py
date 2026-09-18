"""
Obsidian Vault Read-Only Access Adapter
=======================================
HARD SECURITY INVARIANT:
This module provides STRICTLY READ-ONLY access to the user's local Obsidian Vault.
Zero file creation, modification, deletion, renaming, or writing methods are exposed.
All file reads validate path boundaries to strictly prohibit directory traversal attacks.
"""

import os
import re
from typing import List, Dict, Any, Optional

# Default vault location on this Windows PC
DEFAULT_VAULT_ROOT = os.environ.get(
    "OBSIDIAN_VAULT_PATH",
    r"C:\Users\sarmi\Documents\Obsidian Vault"
)

# Standard directories to exclude from indexing/searching
EXCLUDED_DIRS = {".git", ".obsidian", ".stfolder", "__pycache__", ".stignore"}

# Supported file extensions for reading
READABLE_EXTENSIONS = {".md", ".txt", ".canvas", ".json"}

# Stopwords to filter for accurate content search
STOP_WORDS = {
    "what", "is", "the", "for", "and", "a", "an", "in", "on", "at", "to", "from",
    "by", "with", "of", "about", "are", "do", "does", "my", "your", "can", "how",
    "tell", "me", "give", "show", "according", "notes", "note", "vault", "this",
    "who", "when", "where", "which", "there", "any", "some",
    "কী", "কি", "আছে", "কেমন", "বলো", "বল", "করো", "আমার", "নোট", "নোটস", "ভল্ট"
}


class SecurityPathViolationError(PermissionError):
    """Raised when an operation attempts to access files outside the Obsidian vault root."""
    pass


class ObsidianVaultReader:
    """
    Strict Read-Only Adapter for the Obsidian Vault.
    Guarantees no modifications, writes, or deletions can be performed.
    """

    def __init__(self, vault_root: Optional[str] = None):
        root = vault_root or DEFAULT_VAULT_ROOT
        self.vault_root = os.path.realpath(os.path.abspath(root))
        if not os.path.exists(self.vault_root):
            raise FileNotFoundError(f"Obsidian Vault directory does not exist: {self.vault_root}")

    def _resolve_safe_path(self, rel_or_abs_path: str) -> str:
        """
        Validates and canonicalizes the path to ensure it is strictly located
        within the vault root directory. Prevents path traversal (e.g., ../../).
        """
        # Strictly reject any path containing traversal patterns
        if ".." in rel_or_abs_path:
            raise SecurityPathViolationError(
                f"Security Violation: Directory traversal detected in path: '{rel_or_abs_path}'"
            )

        # Check absolute or root-prefixed paths immediately
        if os.path.isabs(rel_or_abs_path) or rel_or_abs_path.startswith("/") or rel_or_abs_path.startswith("\\"):
            full_path = os.path.realpath(os.path.abspath(rel_or_abs_path))
        else:
            full_path = os.path.realpath(os.path.abspath(os.path.join(self.vault_root, rel_or_abs_path)))
        
        # Check that the canonical path starts with the canonical vault root
        try:
            common = os.path.commonpath([self.vault_root, full_path])
        except ValueError:
            raise SecurityPathViolationError(f"Access denied: Path is on a different drive or invalid: {rel_or_abs_path}")
            
        if common != self.vault_root:
            raise SecurityPathViolationError(
                f"Security Violation: Target path '{rel_or_abs_path}' resolves outside the Obsidian Vault boundary."
            )
        return full_path

    def _get_relative_path(self, abs_path: str) -> str:
        """Returns relative path from the vault root with standardized forward slashes."""
        rel = os.path.relpath(abs_path, self.vault_root)
        return rel.replace("\\", "/")

    def read_file(self, rel_path: str) -> str:
        """
        Reads and returns the contents of a vault file strictly in read-only mode.
        """
        safe_path = self._resolve_safe_path(rel_path)
        if not os.path.isfile(safe_path):
            raise FileNotFoundError(f"File not found in vault: {rel_path}")

        # Explicit read-only mode
        with open(safe_path, mode="r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def search_files(self, query: str, folder_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Searches note filenames across the vault matching the query string.
        Optional folder_filter limits search to a subfolder (e.g., '01_College').
        """
        query_lower = query.strip().lower()
        results = []

        search_root = self.vault_root
        if folder_filter:
            search_root = self._resolve_safe_path(folder_filter)

        for root, dirs, files in os.walk(search_root):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext not in READABLE_EXTENSIONS:
                    continue
                file_lower = file.lower()
                if query_lower in file_lower:
                    abs_path = os.path.join(root, file)
                    rel_path = self._get_relative_path(abs_path)
                    results.append({
                        "filename": file,
                        "relative_path": rel_path,
                        "title": os.path.splitext(file)[0],
                        "extension": ext
                    })
        return results

    def search_content(
        self,
        query: str,
        folder_filter: Optional[str] = None,
        max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Scans note contents for text matching the query.
        Returns matching notes along with relevant text snippets.
        """
        raw_terms = [t.lower().strip("?,.!\"'`:;") for t in query.strip().split()]
        raw_terms = [t for t in raw_terms if len(t) > 1]
        query_terms = [t for t in raw_terms if t not in STOP_WORDS]
        if not query_terms:
            query_terms = raw_terms
        if not query_terms:
            return []

        search_root = self.vault_root
        if folder_filter:
            search_root = self._resolve_safe_path(folder_filter)

        matches = []
        for root, dirs, files in os.walk(search_root):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext not in READABLE_EXTENSIONS:
                    continue
                abs_path = os.path.join(root, file)
                try:
                    with open(abs_path, mode="r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except Exception:
                    continue

                content_lower = content.lower()
                score = 0
                snippets = []

                # Calculate relevance score based on term frequencies and positions
                for term in query_terms:
                    count = content_lower.count(term)
                    if count > 0:
                        score += count
                        # Extract a snippet around the match
                        idx = content_lower.find(term)
                        start = max(0, idx - 60)
                        end = min(len(content), idx + len(term) + 80)
                        snippet = content[start:end].replace("\n", " ").strip()
                        snippets.append(f"...{snippet}...")

                # Boost score if filename matches
                file_lower = file.lower()
                for term in query_terms:
                    if term in file_lower:
                        score += 5

                if score > 0:
                    rel_path = self._get_relative_path(abs_path)
                    matches.append({
                        "filename": file,
                        "relative_path": rel_path,
                        "title": os.path.splitext(file)[0],
                        "score": score,
                        "snippets": snippets[:3],
                        "length": len(content)
                    })

        matches.sort(key=lambda x: x["score"], reverse=True)
        return matches[:max_results]

    def get_metadata(self, rel_path: str) -> Dict[str, Any]:
        """
        Extracts YAML frontmatter metadata and file stats from a markdown note.
        """
        safe_path = self._resolve_safe_path(rel_path)
        content = self.read_file(rel_path)
        
        stat = os.stat(safe_path)
        metadata: Dict[str, Any] = {
            "relative_path": self._get_relative_path(safe_path),
            "filename": os.path.basename(safe_path),
            "size_bytes": stat.st_size,
            "modified_time": stat.st_mtime,
            "tags": [],
            "frontmatter": {}
        }

        # Check for YAML frontmatter block (--- ... ---)
        frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if frontmatter_match:
            raw_yaml = frontmatter_match.group(1)
            for line in raw_yaml.splitlines():
                if ":" in line:
                    key, val = line.split(":", 1)
                    key = key.strip()
                    val = val.strip().strip('"\'')
                    if val.startswith("-"):
                        val = val.lstrip("- ").strip()
                    metadata["frontmatter"][key] = val
                    if key.lower() == "tags" or key.lower() == "tag":
                        metadata["tags"].append(val)
                elif line.strip().startswith("- "):
                    metadata["tags"].append(line.strip().lstrip("- ").strip())

        # Also search for inline markdown tags (#tag)
        inline_tags = re.findall(r"(?:^|\s)#([a-zA-Z0-9_\-]+)", content)
        for t in inline_tags:
            if t not in metadata["tags"]:
                metadata["tags"].append(t)

        return metadata

    def get_links(self, rel_path: str) -> Dict[str, List[str]]:
        """
        Extracts wikilinks [[Note Title]] and markdown links from the note.
        """
        content = self.read_file(rel_path)
        
        # Match standard Obsidian wikilinks [[Target]] or [[Target|Alias]]
        wikilinks = re.findall(r"\[\[(.*?)\]\]", content)
        cleaned_links = []
        for wl in wikilinks:
            target = wl.split("|")[0].strip()
            if target and target not in cleaned_links:
                cleaned_links.append(target)

        return {
            "relative_path": rel_path,
            "outgoing_wikilinks": cleaned_links
        }

    def list_notes_in_domain(self, domain_folder: str) -> List[Dict[str, str]]:
        """
        Lists all notes under a specific domain folder (e.g., '01_College', '02_Projects', '03_Second_Brain/AI').
        """
        target_dir = self._resolve_safe_path(domain_folder)
        notes = []
        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
            for file in files:
                if file.endswith(".md"):
                    abs_path = os.path.join(root, file)
                    notes.append({
                        "filename": file,
                        "relative_path": self._get_relative_path(abs_path),
                        "title": os.path.splitext(file)[0]
                    })
        return notes

    def get_vault_stats(self) -> Dict[str, Any]:
        """Returns read-only summary statistics of the vault."""
        total_notes = 0
        domains = {}
        for item in os.listdir(self.vault_root):
            item_path = os.path.join(self.vault_root, item)
            if os.path.isdir(item_path) and item not in EXCLUDED_DIRS:
                count = sum(
                    len([f for f in files if f.endswith(".md")])
                    for _, _, files in os.walk(item_path)
                )
                domains[item] = count
                total_notes += count
        return {
            "vault_root": self.vault_root,
            "total_notes": total_notes,
            "domains": domains
        }

    def read_canvas_data(self, rel_path: str) -> Dict[str, Any]:
        """
        Reads an Obsidian .canvas file (JSON format) and extracts node labels and edges.
        """
        safe_path = self._resolve_safe_path(rel_path)
        if not safe_path.endswith(".canvas"):
            raise ValueError("File is not an Obsidian .canvas file")
        
        raw_text = self.read_file(rel_path)
        try:
            import json
            data = json.loads(raw_text)
            nodes = []
            for n in data.get("nodes", []):
                text = n.get("text", "") or n.get("file", "")
                nodes.append({
                    "id": n.get("id"),
                    "type": n.get("type"),
                    "text": text[:200]
                })
            return {
                "relative_path": rel_path,
                "node_count": len(nodes),
                "nodes": nodes,
                "edge_count": len(data.get("edges", []))
            }
        except Exception as e:
            return {"relative_path": rel_path, "error": f"Failed to parse canvas: {e}"}

    def get_file_history(self, rel_path: str, max_entries: int = 5) -> List[Dict[str, str]]:
        """
        Read-only inspection of git version history for a specific note.
        Forbidden: Cannot restore, checkout, or modify versions.
        """
        safe_path = self._resolve_safe_path(rel_path)
        import subprocess
        try:
            cmd = [
                "git", "log", f"-n{max_entries}",
                "--pretty=format:%h|%an|%ad|%s",
                "--date=short", "--", safe_path
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, cwd=self.vault_root
            )
            if result.returncode != 0:
                return []
            
            history = []
            for line in result.stdout.splitlines():
                parts = line.split("|", 3)
                if len(parts) == 4:
                    history.append({
                        "commit": parts[0],
                        "author": parts[1],
                        "date": parts[2],
                        "message": parts[3]
                    })
            return history
        except Exception:
            return []

    def get_file_diff(self, rel_path: str, commit_hash: str) -> str:
        """
        Read-only diff inspection between a commit and its parent for a note.
        Forbidden: Cannot revert or checkout files.
        """
        safe_path = self._resolve_safe_path(rel_path)
        import subprocess
        # Sanitize commit hash (hex only)
        if not re.match(r"^[0-9a-fA-F]{4,40}$", commit_hash):
            raise ValueError("Invalid git commit hash format")
        try:
            cmd = ["git", "diff", f"{commit_hash}~1", commit_hash, "--", safe_path]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, cwd=self.vault_root
            )
            if result.returncode == 0:
                return result.stdout[:3000]
            return "No diff available or root commit."
        except Exception as e:
            return f"Error fetching diff: {e}"
