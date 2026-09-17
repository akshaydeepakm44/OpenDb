import os
import time
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
from app.audit.tracer import tracer, Checkpoint

logger = logging.getLogger(__name__)

class StorageManager:
    def __init__(self):
        self.use_local = settings.STORAGE_BACKEND == "local"
        self.local_dir = Path(settings.RAW_STORAGE_DIR)
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self.bucket_name = "opendb"
        self.client = None
        self.is_degraded = False
        
        if settings.STORAGE_BACKEND == "minio":
            if not HAS_MINIO:
                tracer.log_event(
                    level="CRITICAL",
                    checkpoint=Checkpoint.CP26_OBJECT_STORAGE,
                    event="MINIO_LIB_MISSING",
                    message="STORAGE_FAILED: minio python package not installed."
                )
                self.is_degraded = True
                return

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
                with socket.create_connection((h, pt), timeout=2.0):
                    pass
            except Exception as sock_err:
                tracer.log_event(
                    level="ERROR",
                    checkpoint=Checkpoint.CP26_OBJECT_STORAGE,
                    event="MINIO_ENDPOINT_UNREACHABLE",
                    message=f"STORAGE_FAILED: MinIO endpoint unreachable ({endpoint}): {sock_err}"
                )
                self.is_degraded = True
                self.client = None
                return

            import urllib3
            http_client = urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=2.0, read=5.0),
                retries=False
            )
            try:
                self.client = Minio(
                    endpoint,
                    access_key=access_key,
                    secret_key=secret_key,
                    secure=secure,
                    http_client=http_client
                )
                self._ensure_bucket()
                tracer.log_event(
                    level="INFO",
                    checkpoint=Checkpoint.CP26_OBJECT_STORAGE,
                    event="MINIO_CONNECTED",
                    message=f"MinIO object storage connected to {endpoint} bucket '{self.bucket_name}'"
                )
            except Exception as e:
                tracer.log_event(
                    level="ERROR",
                    checkpoint=Checkpoint.CP26_OBJECT_STORAGE,
                    event="MINIO_INIT_FAILED",
                    message=f"STORAGE_FAILED: MinIO initialization error: {e}",
                    exc_info=True
                )
                self.is_degraded = True
                self.client = None

    def _ensure_bucket(self):
        try:
            if self.client and not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)
        except Exception as e:
            tracer.log_event(
                level="ERROR",
                checkpoint=Checkpoint.CP26_OBJECT_STORAGE,
                event="MINIO_BUCKET_ERROR",
                message=f"STORAGE_FAILED: MinIO bucket check failed: {e}",
                exc_info=True
            )
            self.is_degraded = True
            raise RuntimeError(f"STORAGE_FAILED: MinIO bucket '{self.bucket_name}' error: {e}")

    @staticmethod
    def calculate_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _stage_to_outbox(self, object_name: str, content_bytes: bytes, content_type: str, domain: str = "") -> str:
        """Stage file locally and register in durable PostgreSQL ArtifactOutbox table."""
        staging_dir = Path(settings.RAW_STORAGE_DIR) / "staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        sha256 = self.calculate_hash(content_bytes)
        local_file = staging_dir / f"{sha256}.bin"
        local_file.write_bytes(content_bytes)

        try:
            from app.persistence.database import SessionLocal
            from app.persistence.models import ArtifactOutbox, utc_now
            with SessionLocal() as db:
                outbox_rec = ArtifactOutbox(
                    domain=domain,
                    object_name=object_name,
                    bucket_name=self.bucket_name,
                    content_type=content_type,
                    file_size_bytes=len(content_bytes),
                    sha256_hash=sha256,
                    local_staging_path=str(local_file),
                    status="PENDING",
                    retry_count=0,
                    created_at=utc_now(),
                )
                db.add(outbox_rec)
                db.commit()
                logger.info(f"[StorageManager] MinIO degraded: staged {object_name} ({len(content_bytes)} bytes) to durable ArtifactOutbox.")
        except Exception as db_err:
            logger.warning(f"[StorageManager] Failed to write to ArtifactOutbox: {db_err}")

        return f"s3://{self.bucket_name}/{object_name}"

    def process_artifact_outbox(self, db, limit: int = 20) -> int:
        """Flush durable pending artifacts to MinIO when connection is restored."""
        if self.client is None:
            return 0
        from app.persistence.models import ArtifactOutbox, utc_now
        pending = db.query(ArtifactOutbox).filter(
            ArtifactOutbox.status.in_(["PENDING", "RETRY"]),
            ArtifactOutbox.retry_count < ArtifactOutbox.max_retries
        ).limit(limit).all()

        uploaded = 0
        for item in pending:
            item.status = "UPLOADING"
            db.commit()
            try:
                local_p = Path(item.local_staging_path)
                if local_p.exists():
                    data = local_p.read_bytes()
                    self.client.put_object(
                        item.bucket_name,
                        item.object_name,
                        io.BytesIO(data),
                        len(data),
                        content_type=item.content_type
                    )
                    item.status = "COMPLETED"
                    item.uploaded_at = utc_now()
                    uploaded += 1
                    try:
                        local_p.unlink(missing_ok=True)
                    except Exception:
                        pass
                else:
                    item.status = "FAILED"
                    item.last_error = "Staging file missing on disk"
            except Exception as err:
                item.retry_count += 1
                item.status = "RETRY" if item.retry_count < item.max_retries else "FAILED"
                item.last_error = str(err)
            db.commit()
        return uploaded

    def _put_object(self, object_name: str, content_bytes: bytes, content_type: str = "application/octet-stream", domain: str = "") -> str:
        t0 = time.time()
        if settings.STORAGE_BACKEND == "minio":
            if self.client is None or self.is_degraded:
                return self._stage_to_outbox(object_name, content_bytes, content_type, domain=domain)
            try:
                self.client.put_object(
                    self.bucket_name,
                    object_name,
                    io.BytesIO(content_bytes),
                    len(content_bytes),
                    content_type=content_type
                )
                dur = time.time() - t0
                tracer.log_event(
                    level="DEBUG",
                    checkpoint=Checkpoint.CP26_OBJECT_STORAGE,
                    event="PUT_OBJECT_SUCCESS",
                    message=f"MinIO put object: s3://{self.bucket_name}/{object_name} ({len(content_bytes)} bytes)",
                    duration=dur,
                    status="SUCCESS",
                    extra={"bucket": self.bucket_name, "object": object_name, "size": len(content_bytes)}
                )
                return f"s3://{self.bucket_name}/{object_name}"
            except Exception as e:
                self.is_degraded = True
                logger.warning(f"[StorageManager] MinIO put error for {object_name}: {e}. Staging to durable ArtifactOutbox.")
                return self._stage_to_outbox(object_name, content_bytes, content_type, domain=domain)

        # Local storage mode (only when explicitly configured STORAGE_BACKEND=local)
        local_path = self.local_dir / object_name
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content_bytes)
        dur = time.time() - t0
        tracer.log_event(
            level="DEBUG",
            checkpoint=Checkpoint.CP26_OBJECT_STORAGE,
            event="LOCAL_FILE_SAVED",
            message=f"Local disk write: local://{object_name} ({len(content_bytes)} bytes)",
            duration=dur
        )
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
        except Exception as e:
            logger.debug(f"MinIO get_object for {clean_rel} not found/error: {e}")
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

    def save_company_page_artifact(
        self,
        domain: str,
        page_slug: str,
        content: str | bytes,
        ext: str = "md",
        metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, str]:
        """
        Store raw crawled page artifact in MinIO under companies/{domain}/pages/{page_slug}.{ext}
        along with sidecar companies/{domain}/pages/{page_slug}.meta.json retaining:
        source_url, object_key, page_type, content_hash, crawl_timestamp, crawl_job_id.
        Returns (content_hash, object_path).
        """
        import re
        clean_domain = domain.lower().strip().replace("www.", "")
        raw_slug = page_slug.strip("/").replace("/", "_")
        clean_slug = re.sub(r'[^a-z0-9_-]', '_', raw_slug.lower()) or "homepage"
        clean_ext = ext.lstrip(".").lower()
        content_bytes = content.encode("utf-8") if isinstance(content, str) else content
        content_hash = self.calculate_hash(content_bytes)

        object_path = f"companies/{clean_domain}/pages/{clean_slug}.{clean_ext}"
        mime_map = {
            "md": "text/markdown",
            "html": "text/html",
            "txt": "text/plain",
            "json": "application/json",
            "pdf": "application/pdf"
        }
        content_type = mime_map.get(clean_ext, "application/octet-stream")
        rel_path = self._put_object(object_path, content_bytes, content_type=content_type)

        # Save metadata sidecar
        meta_payload = {
            "source_url": (metadata or {}).get("source_url", ""),
            "object_key": object_path,
            "page_type": (metadata or {}).get("page_type", clean_slug),
            "content_hash": content_hash,
            "crawl_timestamp": (metadata or {}).get("crawl_timestamp", ""),
            "crawl_job_id": (metadata or {}).get("crawl_job_id", ""),
        }
        meta_path = f"companies/{clean_domain}/pages/{clean_slug}.meta.json"
        self._put_object(meta_path, json.dumps(meta_payload, indent=2).encode("utf-8"), content_type="application/json")

        return content_hash, rel_path

    def save_agent2_artifact(
        self,
        domain: str,
        page_slug: str,
        content: str | bytes,
        ext: str = "md",
        metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, str]:
        """
        Store Agent 2 targeted re-crawl evidence under companies/{domain}/agent2/pages/{page_slug}.{ext}
        along with companion sidecar {page_slug}.meta.json.
        Preserves Agent 1 artifacts as completely immutable.
        """
        import re
        clean_domain = domain.lower().strip().replace("www.", "")
        raw_slug = page_slug.strip("/").replace("/", "_")
        clean_slug = re.sub(r'[^a-z0-9_-]', '_', raw_slug.lower()) or "subpage"
        clean_ext = ext.lstrip(".").lower()
        content_bytes = content.encode("utf-8") if isinstance(content, str) else content
        content_hash = self.calculate_hash(content_bytes)

        object_path = f"companies/{clean_domain}/agent2/pages/{clean_slug}.{clean_ext}"
        mime_map = {
            "md": "text/markdown",
            "html": "text/html",
            "txt": "text/plain",
            "json": "application/json"
        }
        content_type = mime_map.get(clean_ext, "application/octet-stream")
        rel_path = self._put_object(object_path, content_bytes, content_type=content_type)

        meta_payload = {
            "source_url": (metadata or {}).get("source_url", ""),
            "object_key": object_path,
            "page_type": (metadata or {}).get("page_type", clean_slug),
            "content_hash": content_hash,
            "crawl_timestamp": (metadata or {}).get("crawl_timestamp", ""),
            "crawl_job_id": (metadata or {}).get("crawl_job_id", ""),
            "agent2_session_id": (metadata or {}).get("agent2_session_id", ""),
        }
        meta_path = f"companies/{clean_domain}/agent2/pages/{clean_slug}.meta.json"
        self._put_object(meta_path, json.dumps(meta_payload, indent=2).encode("utf-8"), content_type="application/json")

        return content_hash, rel_path

    def verify_artifact_exists(self, object_path: str) -> Dict[str, Any]:
        """
        System fact check: Does the referenced MinIO/S3 or local artifact actually exist?
        Returns dict with exists=bool, size_bytes, content_hash, error.
        Raises/returns explicit error when storage backend is down.
        """
        clean_path = object_path.replace(f"s3://{self.bucket_name}/", "").replace("local://", "")
        if self.use_local or object_path.startswith("local://") or (self.local_dir / clean_path).exists():
            target = self.local_dir / clean_path
            if target.exists() and target.is_file():
                data = target.read_bytes()
                return {
                    "exists": True,
                    "size_bytes": len(data),
                    "content_hash": self.calculate_hash(data),
                    "backend": "local",
                    "error": None
                }
            if self.use_local or object_path.startswith("local://"):
                return {"exists": False, "size_bytes": 0, "content_hash": None, "backend": "local", "error": "file_not_found"}

        if not self.client:
            target = self.local_dir / clean_path
            if target.exists() and target.is_file():
                data = target.read_bytes()
                return {
                    "exists": True,
                    "size_bytes": len(data),
                    "content_hash": self.calculate_hash(data),
                    "backend": "local",
                    "error": None
                }
            return {
                "exists": False,
                "size_bytes": 0,
                "content_hash": None,
                "backend": "minio",
                "error": "minio_client_uninitialized",
                "is_infra_error": False
            }

        try:
            stat = self.client.stat_object(self.bucket_name, clean_path)
            return {
                "exists": True,
                "size_bytes": stat.size,
                "content_hash": stat.etag,
                "backend": "minio",
                "error": None
            }
        except Exception as e:
            err_msg = str(e).lower()
            if "not found" in err_msg or "nosuchkey" in err_msg:
                return {"exists": False, "size_bytes": 0, "content_hash": None, "backend": "minio", "error": "object_not_found"}
            # Infrastructure failure
            return {"exists": False, "size_bytes": 0, "content_hash": None, "backend": "minio", "error": f"minio_error: {e}", "is_infra_error": True}


file_storage = StorageManager()
