import os
import hashlib
import json
import logging
import io
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
try:
    from minio import Minio
    from minio.error import S3Error
    HAS_MINIO = True
except ImportError:
    Minio = None
    S3Error = Exception
    HAS_MINIO = False

from app.config import settings

logger = logging.getLogger(__name__)

class StorageManager:
    def __init__(self):
        self.use_local = settings.STORAGE_BACKEND == "local" or not HAS_MINIO
        self.local_dir = Path(settings.RAW_STORAGE_DIR)
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self.bucket_name = "opendb"
        self.client = None
        
        if not self.use_local:
            endpoint = os.getenv("MINIO_ENDPOINT", settings.MINIO_ENDPOINT)
            access_key = os.getenv("MINIO_ACCESS_KEY", settings.MINIO_ACCESS_KEY)
            secret_key = os.getenv("MINIO_SECRET_KEY", settings.MINIO_SECRET_KEY)
            secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
            
            import socket
            from urllib.parse import urlparse
            try:
                p = urlparse(f"http://{endpoint}" if "://" not in endpoint else endpoint)
                h = p.hostname or "127.0.0.1"
                pt = p.port or 9000
                with socket.create_connection((h, pt), timeout=0.3):
                    pass
            except Exception as sock_err:
                if settings.OPENDB_ENV.lower() == "production":
                    logger.error(f"MinIO endpoint unreachable in PRODUCTION mode: {sock_err}")
                    raise RuntimeError(f"MinIO endpoint unreachable in PRODUCTION mode: {sock_err}")
                logger.warning(f"MinIO endpoint unreachable ({sock_err}). Falling back to local filesystem storage.")
                self.use_local = True

            if not self.use_local:
                import urllib3, threading
                http_client = urllib3.PoolManager(
                    timeout=urllib3.Timeout(connect=0.2, read=0.2),
                    retries=False
                )
                
                def _init_minio():
                    try:
                        self.client = Minio(
                            endpoint,
                            access_key=access_key,
                            secret_key=secret_key,
                            secure=secure,
                            http_client=http_client
                        )
                        self._ensure_bucket()
                    except Exception as e:
                        if settings.OPENDB_ENV.lower() == "production":
                            logger.error(f"MinIO initialization failed in PRODUCTION mode: {e}")
                            raise RuntimeError(f"MinIO initialization failed in PRODUCTION mode: {e}")
                        logger.warning(f"MinIO initialization failed ({e}), using local storage.")
                        self.use_local = True

                minio_thread = threading.Thread(target=_init_minio, daemon=True)
                minio_thread.start()
                minio_thread.join(timeout=0.3)
                if minio_thread.is_alive() or self.client is None:
                    if settings.OPENDB_ENV.lower() == "production":
                        logger.error("MinIO connection timed out in PRODUCTION mode.")
                        raise RuntimeError("MinIO connection timed out in PRODUCTION mode.")
                    logger.warning("MinIO initialization timed out (>0.3s). Falling back to local storage.")
                    self.use_local = True

    def _ensure_bucket(self):
        try:
            if self.client and not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)
        except Exception as e:
            if settings.OPENDB_ENV.lower() == "production":
                logger.error(f"MinIO bucket check failed in PRODUCTION mode: {e}")
                raise RuntimeError(f"MinIO bucket check failed in PRODUCTION mode: {e}")
            logger.info(f"MinIO storage unready ({e.__class__.__name__}) — using local disk storage (OPENDB_ENV={settings.OPENDB_ENV})")
            self.use_local = True

    @staticmethod
    def calculate_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _put_object(self, object_name: str, content_bytes: bytes, content_type: str = "application/octet-stream") -> str:
        if self.use_local:
            local_path = self.local_dir / object_name
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(content_bytes)
            return f"local://{object_name}"
            
        try:
            self.client.put_object(
                self.bucket_name,
                object_name,
                io.BytesIO(content_bytes),
                len(content_bytes),
                content_type=content_type
            )
            return f"s3://{self.bucket_name}/{object_name}"
        except Exception as e:
            logger.error(f"MinIO put error for {object_name}, falling back to local: {e}")
            self.use_local = True
            local_path = self.local_dir / object_name
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(content_bytes)
            return f"local://{object_name}"

    def save_raw_page(self, content_str_or_bytes: str | bytes, ext: str = "html") -> Tuple[str, str]:
        """Save HTML or page content, returning (sha256_hash, relative_path)"""
        content_bytes = (
            content_str_or_bytes.encode("utf-8")
            if isinstance(content_str_or_bytes, str)
            else content_str_or_bytes
        )
        content_hash = self.calculate_hash(content_bytes)
        object_name = f"raw/pages/{content_hash}.{ext}"
        
        content_type = "text/html" if ext == "html" else "application/octet-stream"
        rel_path = self._put_object(object_name, content_bytes, content_type)
        return content_hash, rel_path

    def save_raw_document(self, content_bytes: bytes, ext: str) -> Tuple[str, str]:
        """Save PDF, DOC, CSV document returning (sha256_hash, relative_path)"""
        clean_ext = ext.lstrip(".").lower() or "bin"
        content_hash = self.calculate_hash(content_bytes)
        object_name = f"raw/documents/{content_hash}.{clean_ext}"
        
        content_type = "application/pdf" if clean_ext == "pdf" else "application/octet-stream"
        rel_path = self._put_object(object_name, content_bytes, content_type)
        return content_hash, rel_path

    def save_logo(self, logo_bytes: bytes, ext: str = "png") -> Tuple[str, str]:
        """Save logo or favicon image returning (sha256_hash, relative_path)"""
        clean_ext = ext.lstrip(".").lower() or "png"
        content_hash = self.calculate_hash(logo_bytes)
        object_name = f"raw/logos/{content_hash}.{clean_ext}"
        
        content_type = f"image/{clean_ext}" if clean_ext in ["png", "jpg", "jpeg", "gif", "svg", "webp"] else "image/x-icon"
        rel_path = self._put_object(object_name, logo_bytes, content_type)
        return content_hash, rel_path

    def save_processed_markdown(self, markdown_text: str, content_hash: str) -> str:
        object_name = f"processed/markdown/{content_hash}.md"
        content_bytes = (markdown_text or "").encode("utf-8")
        return self._put_object(object_name, content_bytes, "text/markdown")

    def save_processed_text(self, text: str, content_hash: str) -> str:
        object_name = f"processed/text/{content_hash}.txt"
        content_bytes = (text or "").encode("utf-8")
        return self._put_object(object_name, content_bytes, "text/plain")

    def save_extracted_json(self, document_id: str, extraction_payload: Dict[str, Any]) -> str:
        object_name = f"processed/extracted/{document_id}.json"
        content_bytes = json.dumps(extraction_payload, indent=2).encode("utf-8")
        return self._put_object(object_name, content_bytes, "application/json")

    def read_file_content(self, relative_path: str) -> Optional[str]:
        if not relative_path:
            return None

        clean_rel = relative_path
        if clean_rel.startswith("local://"):
            clean_rel = clean_rel.replace("local://", "")
        elif clean_rel.startswith(f"s3://{self.bucket_name}/"):
            clean_rel = clean_rel.replace(f"s3://{self.bucket_name}/", "")

        local_path = self.local_dir / clean_rel
        if local_path.exists():
            return local_path.read_text(encoding="utf-8", errors="ignore")

        if self.use_local or not self.client:
            return None

        try:
            response = self.client.get_object(self.bucket_name, clean_rel)
            return response.read().decode("utf-8", errors="ignore")
        except Exception:
            return None

    def read_file_bytes(self, relative_path: str) -> Tuple[Optional[bytes], str]:
        if not relative_path:
            return None, "application/octet-stream"

        clean_rel = relative_path
        if clean_rel.startswith("local://"):
            clean_rel = clean_rel.replace("local://", "")
        elif clean_rel.startswith(f"s3://{self.bucket_name}/"):
            clean_rel = clean_rel.replace(f"s3://{self.bucket_name}/", "")

        ext = clean_rel.split(".")[-1].lower() if "." in clean_rel else "bin"
        mime_types = {
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "svg": "image/svg+xml", "ico": "image/x-icon", "webp": "image/webp",
            "html": "text/html", "md": "text/markdown", "json": "application/json"
        }
        content_type = mime_types.get(ext, "application/octet-stream")

        local_path = self.local_dir / clean_rel
        if local_path.exists():
            return local_path.read_bytes(), content_type

        if self.use_local or not self.client:
            return None, content_type

        try:
            response = self.client.get_object(self.bucket_name, clean_rel)
            return response.read(), content_type
        except Exception:
            return None, content_type

    def save_brand_kit(self, domain: str, brand_kit_data: dict) -> str:
        """Store Brand Kit JSON: companies/{domain}/brand_kit.json in MinIO L3."""
        clean_domain = domain.lower().strip().replace("www.", "")
        path = f"companies/{clean_domain}/brand_kit.json"
        content = json.dumps(brand_kit_data, indent=2, default=str)
        return self._put_object(path, content.encode("utf-8"), content_type="application/json")

    def save_logo_asset(self, domain: str, logo_bytes: bytes, ext: str = "png") -> str:
        """Store Brand Logo: companies/{domain}/logo.{ext} in MinIO L3."""
        clean_domain = domain.lower().strip().replace("www.", "")
        path = f"companies/{clean_domain}/logo.{ext}"
        mime = "image/png" if ext == "png" else f"image/{ext}"
        return self._put_object(path, logo_bytes, content_type=mime)

    def save_markdown_dom(self, domain: str, url: str, markdown_content: str) -> str:
        """Store Crawled Subpage Markdown DOM: pages/{slug}.md in MinIO L3."""
        import re
        clean_domain = domain.lower().strip().replace("www.", "")
        slug = re.sub(r'[^a-z0-9_-]', '_', url.lower().replace("https://", "").replace("http://", "").strip("/"))[:80]
        path = f"pages/{clean_domain}_{slug}.md"
        return self._put_object(path, markdown_content.encode("utf-8"), content_type="text/markdown")


file_storage = StorageManager()
