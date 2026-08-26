import os
import hashlib
import json
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from app.config import settings

logger = logging.getLogger(__name__)

class StorageManager:
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = Path(base_dir or settings.RAW_STORAGE_DIR)
        self.raw_pages_dir = self.base_dir / "raw" / "pages"
        self.raw_docs_dir = self.base_dir / "raw" / "documents"
        self.raw_media_dir = self.base_dir / "raw" / "media"
        
        self.proc_markdown_dir = self.base_dir / "processed" / "markdown"
        self.proc_text_dir = self.base_dir / "processed" / "text"
        self.proc_extracted_dir = self.base_dir / "processed" / "extracted"
        self.manifests_dir = self.base_dir / "manifests"

        self._ensure_directories()

    def _ensure_directories(self):
        for path in [
            self.raw_pages_dir,
            self.raw_docs_dir,
            self.raw_media_dir,
            self.proc_markdown_dir,
            self.proc_text_dir,
            self.proc_extracted_dir,
            self.manifests_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def calculate_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def save_raw_page(self, content_str_or_bytes: str | bytes, ext: str = "html") -> Tuple[str, str]:
        """Save HTML or page content, returning (sha256_hash, relative_path)"""
        content_bytes = (
            content_str_or_bytes.encode("utf-8")
            if isinstance(content_str_or_bytes, str)
            else content_str_or_bytes
        )
        content_hash = self.calculate_hash(content_bytes)
        file_path = self.raw_pages_dir / f"{content_hash}.{ext}"
        
        if not file_path.exists():
            with open(file_path, "wb") as f:
                f.write(content_bytes)
                
        rel_path = str(file_path.relative_to(self.base_dir))
        return content_hash, rel_path

    def save_raw_document(self, content_bytes: bytes, ext: str) -> Tuple[str, str]:
        """Save PDF, DOC, CSV document returning (sha256_hash, relative_path)"""
        clean_ext = ext.lstrip(".").lower() or "bin"
        content_hash = self.calculate_hash(content_bytes)
        file_path = self.raw_docs_dir / f"{content_hash}.{clean_ext}"
        
        if not file_path.exists():
            with open(file_path, "wb") as f:
                f.write(content_bytes)
                
        rel_path = str(file_path.relative_to(self.base_dir))
        return content_hash, rel_path

    def save_processed_markdown(self, markdown_text: str, content_hash: str) -> str:
        file_path = self.proc_markdown_dir / f"{content_hash}.md"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(markdown_text or "")
        return str(file_path.relative_to(self.base_dir))

    def save_processed_text(self, text: str, content_hash: str) -> str:
        file_path = self.proc_text_dir / f"{content_hash}.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(text or "")
        return str(file_path.relative_to(self.base_dir))

    def save_extracted_json(self, document_id: str, extraction_payload: Dict[str, Any]) -> str:
        file_path = self.proc_extracted_dir / f"{document_id}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(extraction_payload, f, indent=2)
        return str(file_path.relative_to(self.base_dir))

    def read_file_content(self, relative_path: str) -> Optional[str]:
        full_path = self.base_dir / relative_path
        if full_path.exists():
            try:
                return full_path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                logger.error(f"Error reading file {full_path}: {e}")
                return None
        return None

file_storage = StorageManager()
