import os
import hashlib
import mimetypes
import logging
from urllib.parse import urlparse, unquote
from typing import List, Dict, Any, Optional, Tuple
import httpx
from app.normalization.normalizer import normalizer
from app.storage.file_storage import file_storage
from app.config import settings

logger = logging.getLogger(__name__)

DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".xml", ".json"}
MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".svg", ".gif", ".mp4", ".mp3"}
WEB_RESOURCE_NAMES = {"sitemap.xml", "robots.txt", "feed", "rss"}

class ResourceDiscoveryService:
    @staticmethod
    def classify_resource(url: str) -> Tuple[str, str, str]:
        """Returns (resource_type, mime_type, file_extension)"""
        parsed = urlparse(url)
        path = unquote(parsed.path.lower())
        filename = os.path.basename(path)
        _, ext = os.path.splitext(filename)

        if ext in DOCUMENT_EXTENSIONS:
            mime = mimetypes.types_map.get(ext, "application/octet-stream")
            return "document", mime, ext
        elif ext in MEDIA_EXTENSIONS:
            mime = mimetypes.types_map.get(ext, "image/jpeg" if ext in ['.jpg', '.jpeg'] else "application/octet-stream")
            return "media", mime, ext
        elif any(res in path for res in WEB_RESOURCE_NAMES) or ext in {".rss", ".xml"}:
            return "web_resource", "application/xml", ext or ".xml"
        
        return "web_resource", "text/html", ext or ".html"

    @classmethod
    async def process_discovered_resources(
        cls,
        page_url: str,
        links_and_media: List[Dict[str, Any]],
        document_id: str
    ) -> List[Dict[str, Any]]:
        discovered = []

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            for item in links_and_media:
                raw_url = item.get("href") or item.get("src")
                anchor = item.get("text") or item.get("alt") or ""
                if not raw_url:
                    continue

                res_url = normalizer.normalize_url(raw_url, base_url=page_url)
                if not res_url or not res_url.startswith(("http://", "https://")):
                    continue

                res_type, mime_type, ext = cls.classify_resource(res_url)
                
                # If it's just a standard HTML webpage link, skip putting it into raw resources table unless it's a doc/media
                if res_type == "web_resource" and ext not in {".xml", ".json", ".rss", ".txt"}:
                    continue

                parsed = urlparse(res_url)
                file_name = os.path.basename(unquote(parsed.path)) or f"resource_{hashlib.md5(res_url.encode()).hexdigest()[:8]}{ext}"

                res_metadata = {
                    "source_document_id": document_id,
                    "source_url": page_url,
                    "parent_page_url": page_url,
                    "resource_url": res_url,
                    "resource_type": res_type,
                    "mime_type": mime_type,
                    "file_extension": ext,
                    "file_name": file_name,
                    "anchor_text": anchor,
                    "http_status": None,
                    "content_length": None,
                    "hash": None,
                    "raw_path": None,
                    "downloaded": False
                }

                # Download text/pdf resources if within size limit
                if res_type == "document" or ext in {".pdf", ".txt", ".json", ".csv", ".xml"}:
                    try:
                        head_resp = await client.head(res_url)
                        content_len = int(head_resp.headers.get("content-length", 0))
                        res_metadata["http_status"] = head_resp.status_code
                        res_metadata["content_length"] = content_len

                        max_bytes = settings.RESOURCE_MAX_FILE_SIZE_MB * 1024 * 1024
                        if head_resp.status_code == 200 and (content_len == 0 or content_len <= max_bytes):
                            get_resp = await client.get(res_url)
                            if get_resp.status_code == 200:
                                file_bytes = get_resp.content
                                res_hash, rel_path = file_storage.save_raw_document(file_bytes, ext=ext)
                                res_metadata["hash"] = res_hash
                                res_metadata["raw_path"] = rel_path
                                res_metadata["downloaded"] = True
                                res_metadata["content_length"] = len(file_bytes)
                    except Exception as e:
                        logger.warning(f"Could not download resource {res_url}: {e}")

                discovered.append(res_metadata)

        return discovered

resource_discovery = ResourceDiscoveryService()
