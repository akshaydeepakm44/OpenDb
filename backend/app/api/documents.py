from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.persistence.database import get_db
from app.persistence.models import Document, UniversalRecord, DomainRecord, Resource
from app.storage.file_storage import file_storage

router = APIRouter(prefix="/documents", tags=["Documents"])

@router.get("/{document_id}")
def get_document_metadata(document_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    resources = db.query(Resource).filter(Resource.source_document_id == document_id).all()
    
    return {
        "id": doc.id,
        "url": doc.url,
        "canonical_url": doc.canonical_url,
        "title": doc.title,
        "content_type": doc.content_type,
        "language": doc.language,
        "http_status": doc.http_status,
        "word_count": doc.word_count,
        "links_count": doc.links_count,
        "images_count": doc.images_count,
        "retrieved_at": doc.retrieved_at,
        "raw_paths": {
            "html": doc.raw_path,
            "markdown": doc.markdown_path,
            "text": doc.text_path
        },
        "resources_count": len(resources)
    }

@router.get("/{document_id}/raw")
def get_document_raw(document_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    html_content = file_storage.read_file_content(doc.raw_path) if doc.raw_path else None
    markdown_content = file_storage.read_file_content(doc.markdown_path) if doc.markdown_path else None
    text_content = file_storage.read_file_content(doc.text_path) if doc.text_path else None

    return {
        "document_id": doc.id,
        "url": doc.url,
        "html": html_content,
        "markdown": markdown_content,
        "text": text_content
    }

@router.get("/{document_id}/extraction")
def get_document_extraction(document_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    univ_rec = db.query(UniversalRecord).filter(UniversalRecord.document_id == document_id).first()
    if not univ_rec:
        raise HTTPException(status_code=404, detail="Extraction results not found for document.")

    dom_rec = db.query(DomainRecord).filter(DomainRecord.universal_record_id == univ_rec.id).first()
    extracted_json = file_storage.read_file_content(f"processed/extracted/{document_id}.json")

    return {
        "document_id": doc.id,
        "universal": {
            "canonical_name": univ_rec.canonical_name,
            "title": univ_rec.title,
            "description": univ_rec.description,
            "url": univ_rec.url,
            "entity_type": univ_rec.entity_type,
            "language": univ_rec.language,
            "country": univ_rec.country,
            "location": univ_rec.location,
            "status": univ_rec.status,
            "confidence": float(univ_rec.confidence) if univ_rec.confidence else None
        },
        "domain_data": dom_rec.data if dom_rec else {},
        "raw_extraction_payload": extracted_json
    }
