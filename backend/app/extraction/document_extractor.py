import os
import csv
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class DocumentExtractor:
    @staticmethod
    def extract_document_info(file_path: str, mime_type: str) -> Dict[str, Any]:
        """Extract structured metadata from downloaded document files."""
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {file_path}"}

        ext = path.suffix.lower()
        res = {
            "filename": path.name,
            "file_size": path.stat().st_size,
            "extension": ext,
            "mime_type": mime_type,
            "page_count": None,
            "columns": [],
            "row_count": None,
            "sample_rows": [],
            "extracted_text_preview": ""
        }

        try:
            if ext in [".csv", ".txt"]:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    if ext == ".csv":
                        reader = csv.reader(f)
                        rows = list(reader)
                        if rows:
                            res["columns"] = rows[0]
                            res["row_count"] = len(rows) - 1
                            res["sample_rows"] = rows[1:6]
                    else:
                        text = f.read()
                        res["extracted_text_preview"] = text[:500]
                        res["row_count"] = len(text.splitlines())

            elif ext == ".json":
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        res["row_count"] = len(data)
                        if data and isinstance(data[0], dict):
                            res["columns"] = list(data[0].keys())
                    elif isinstance(data, dict):
                        res["columns"] = list(data.keys())

        except Exception as e:
            logger.warning(f"Error parsing document {file_path}: {e}")

        return res

document_extractor = DocumentExtractor()
