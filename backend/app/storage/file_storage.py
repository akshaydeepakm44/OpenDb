import os
import hashlib
import json
import logging
import io
from typing import Optional, Tuple, Dict, Any
from minio import Minio
from minio.error import S3Error
from app.config import settings

logger = logging.getLogger(__name__)

class StorageManager:
    def __init__(self):
        # Fallback to localhost if not set (for local dev without docker)
        endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
        access_key = os.getenv("MINIO_ACCESS_KEY", "admin")
        secret_key = os.getenv("MINIO_SECRET_KEY", "password123")
        secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
        
        import urllib3
        http_client = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=1.0, read=1.0),
            retries=False
        )
        self.client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            http_client=http_client
        )
        self.bucket_name = "opendb"

    def _ensure_bucket(self):
        try:
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)
        except Exception as e:
            logger.warning(f"MinIO bucket check skipped/failed: {e}")

    @staticmethod
    def calculate_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _put_object(self, object_name: str, content_bytes: bytes, content_type: str = "application/octet-stream") -> str:
        try:
            self.client.put_object(
                self.bucket_name,
                object_name,
                io.BytesIO(content_bytes),
                len(content_bytes),
                content_type=content_type
            )
            return f"s3://{self.bucket_name}/{object_name}"
        except S3Error as e:
            logger.error(f"MinIO put error for {object_name}: {e}")
            raise e

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
        # Handle backward compatibility if someone passes a local path or an s3 uri
        object_name = relative_path
        if object_name.startswith(f"s3://{self.bucket_name}/"):
            object_name = object_name.replace(f"s3://{self.bucket_name}/", "")
        
        try:
            response = self.client.get_object(self.bucket_name, object_name)
            return response.read().decode("utf-8", errors="ignore")
        except S3Error as e:
            if e.code == 'NoSuchKey':
                return None
            logger.error(f"MinIO get error for {object_name}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error reading from MinIO {object_name}: {e}")
            return None

file_storage = StorageManager()
