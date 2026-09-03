from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from app.schemas.registry import schema_registry
from app.persistence.database import get_db
from app.persistence.models import SchemaDefinition

router = APIRouter(prefix="/schemas", tags=["Schemas"])

@router.get("", response_model=List[Dict[str, Any]])
def list_schemas():
    return schema_registry.list_schemas()

@router.get("/active")
def get_active_schemas():
    """Return all active domain extraction schemas registered in OpenDB."""
    return schema_registry.list_schemas()

@router.get("/{domain}")
def get_domain_schema(domain: str):
    schema = schema_registry.get_domain_schema(domain)
    if not schema:
        raise HTTPException(status_code=404, detail=f"Schema for domain '{domain}' not found.")
    return schema

@router.post("")
def register_schema(payload: Dict[str, Any], db: Session = Depends(get_db)):
    domain = payload.get("domain")
    if not domain:
        raise HTTPException(status_code=400, detail="Schema definition must include 'domain' field.")

    updated_schema = schema_registry.register_or_update_schema(domain, payload)

    # Sync to Postgres schema_definitions table
    existing = db.query(SchemaDefinition).filter(SchemaDefinition.domain == domain.capitalize()).first()
    if existing:
        existing.schema_definition = updated_schema
        existing.version = updated_schema.get("version", "1.0.0")
    else:
        db.add(SchemaDefinition(
            domain=domain.capitalize(),
            version=updated_schema.get("version", "1.0.0"),
            schema_definition=updated_schema
        ))
    db.commit()

    return {"message": f"Schema for '{domain}' registered successfully.", "schema": updated_schema}
