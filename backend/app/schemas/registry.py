import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class SchemaRegistry:
    def __init__(self, schemas_dir: Optional[str] = None):
        self.schemas_dir = Path(schemas_dir or "./schemas")
        self.universal_schema_path = self.schemas_dir / "universal" / "resource_schema.json"
        self.domains_dir = self.schemas_dir / "domains"
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.load_all_schemas()

    def load_all_schemas(self):
        """Load universal and domain schemas into memory cache."""
        self._cache = {}
        if self.universal_schema_path.exists():
            try:
                with open(self.universal_schema_path, "r", encoding="utf-8") as f:
                    self._cache["universal"] = json.load(f)
            except Exception as e:
                logger.error(f"Error loading universal schema: {e}")

        if self.domains_dir.exists():
            for schema_file in self.domains_dir.glob("*.json"):
                try:
                    with open(schema_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        domain_key = data.get("domain", schema_file.stem).lower()
                        self._cache[domain_key] = data
                except Exception as e:
                    logger.error(f"Error loading domain schema {schema_file}: {e}")

    def get_universal_schema(self) -> Dict[str, Any]:
        return self._cache.get("universal", {})

    def get_domain_schema(self, domain_name: str) -> Optional[Dict[str, Any]]:
        domain_key = domain_name.strip().lower()
        return self._cache.get(domain_key)

    def list_schemas(self) -> List[Dict[str, Any]]:
        result = []
        for key, schema_data in self._cache.items():
            result.append({
                "schema_id": key,
                "domain": schema_data.get("domain", key.capitalize()),
                "version": schema_data.get("version", "1.0.0"),
                "schema_definition": schema_data
            })
        return result

    def register_or_update_schema(self, domain_name: str, schema_def: Dict[str, Any]) -> Dict[str, Any]:
        domain_key = domain_name.strip().lower()
        schema_def["domain"] = domain_name.capitalize()
        if "version" not in schema_def:
            schema_def["version"] = "1.0.0"

        self.domains_dir.mkdir(parents=True, exist_ok=True)
        target_path = self.domains_dir / f"{domain_key}.json"
        
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(schema_def, f, indent=2)

        self._cache[domain_key] = schema_def
        return schema_def

schema_registry = SchemaRegistry()
