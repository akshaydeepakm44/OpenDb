"""
Haystack (deepset) pipelines for OpenDB.

Two pipelines:
  1. Extraction pipeline — uses an LLM to extract structured fields
     (company_name, headquarters, country, description, etc.) from
     crawled page text.  Runs as a Haystack Pipeline so it can be
     composed with other Haystack components.

  2. Retrieval pipeline — pgvector-backed semantic search.
     Embeds a query and retrieves the top-k most similar documents
     / entity descriptions from PostgreSQL (pgvector extension).

Both are lazily built singletons.  If any dependency (haystack-ai,
sentence-transformers, pgvector, pgvector-store) is missing the
build function logs a warning and returns None — callers must
handle the None case and fall back to the custom orchestrator.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── Availability check ──────────────────────────────────────────────────────

try:
    from haystack import Pipeline
    from haystack.components.builders import ChatMessageBuilder
    from haystack.components.generators import ChatGenerator
    from haystack.components.converters import TextFileToDocument, MarkdownConverter
    from haystack.components.preprocessors import DocumentSplitter
    from haystack.components.embedders import (
        SentenceTransformersTextEmbedder,
        SentenceTransformersDocumentEmbedder,
    )
    from haystack.dataclasses import ChatMessage, Document as HayDocument
    HAYSTACK_AVAILABLE = True
except ImportError as exc:
    HAYSTACK_AVAILABLE = False
    logger.warning(f"⚠️  Haystack framework not available ({exc}) — falling back to custom pipeline")

try:
    from haystack_pgvector import PGVectorDocumentStore
    PGVECTOR_STORE_AVAILABLE = True
except ImportError:
    PGVECTOR_STORE_AVAILABLE = False

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


# ─── 1. Extraction Pipeline ──────────────────────────────────────────────────

_EXTRACTION_PROMPT = """\
You are a data extraction agent.  Extract the following fields from the \
provided web page text about an organization.  Respond ONLY with a JSON \
object.  If a field is not present in the text, set it to null.  Do NOT \
invent or guess values.

Fields to extract:
{
  "company_name": string or null,
  "description": string or null,
  "headquarters": string or null,
  "country": string or null,
  "founded_year": int or null,
  "website": string or null,
  "industries": array of strings,
  "employee_count": string or null,
  "contact_email": string or null,
  "contact_phone": string or null,
  "social_media": object with keys twitter, linkedin, facebook (values string or null)
}

Page text:
{text}
"""


def build_extraction_pipeline() -> Optional["Pipeline"]:
    """
    Build a Haystack Pipeline that extracts structured fields from text
    using the configured LLM (via OpenAI-compatible API or Ollama).

    Returns None if Haystack or the LLM provider is unavailable.
    """
    if not HAYSTACK_AVAILABLE:
        return None

    try:
        from app.config import settings
        import os

        provider = (settings.LLM_PROVIDER or "ollama").lower()
        model = settings.LLM_MODEL

        if provider == "ollama":
            # Use the generic ChatGenerator with OpenAI-compatible endpoint
            generator = ChatGenerator(
                model=f"openai/{model}",
                api_base_url=settings.OLLAMA_BASE_URL,
                api_key=os.environ.get("OLLAMA_API_KEY", "ollama"),
                generation_kwargs={"temperature": 0.1, "max_tokens": 2048},
            )
        elif provider == "openai":
            api_key = settings.OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY")
            if not api_key:
                logger.warning("⚠️  OPENAI_API_KEY not set — extraction pipeline disabled")
                return None
            generator = ChatGenerator(
                model=model,
                api_key=api_key,
                generation_kwargs={"temperature": 0.1, "max_tokens": 2048},
            )
        else:
            # heuristics / qwen_local / unknown → not available via Haystack
            logger.info(f"ℹ️  LLM_PROVIDER={provider} — Haystack extraction pipeline skipped")
            return None

        prompt_builder = ChatMessageBuilder(
            template=_EXTRACTION_PROMPT,
            variables_mapping={"text": "$.text"},
        )

        pipeline = Pipeline()
        pipeline.add_component("prompt_builder", prompt_builder)
        pipeline.add_component("generator", generator)
        pipeline.connect("prompt_builder", "generator")

        logger.info(f"✅ Haystack extraction pipeline built (provider={provider}, model={model})")
        return pipeline

    except Exception as exc:
        logger.warning(f"⚠️  Failed to build Haystack extraction pipeline: {exc}")
        return None


def run_extraction_pipeline(
    pipeline: "Pipeline",
    text: str,
) -> Optional[Dict[str, Any]]:
    """
    Run the extraction pipeline on raw page text.

    Returns a dict of extracted fields (strict-null: only real values)
    or None if the pipeline fails / returns no parsable result.
    """
    import json as _json
    import re as _re

    if pipeline is None or not text or len(text.strip()) < 50:
        return None

    try:
        result = pipeline.run(
            {
                "prompt_builder": {
                    "text": text[:12000],  # cap input to ~12k chars
                },
            }
        )
        # Extract the LLM response
        raw = result.get("generator", {}).get("replies", [{}])
        reply_text = raw[0].text if raw else ""

        # Parse JSON from the reply (handle markdown code fences)
        reply_text = reply_text.strip()
        if reply_text.startswith("```"):
            reply_text = _re.sub(r"^```(?:json)?\s*", "", reply_text)
            reply_text = _re.sub(r"\s*```$", "", reply_text)

        data = _json.loads(reply_text)
        if not isinstance(data, dict):
            return None

        # Strict-null: drop keys whose value is None, empty string, or empty list
        clean = {
            k: v
            for k, v in data.items()
            if v is not None and v != "" and v != [] and v != {}
        }
        return clean if clean else None

    except Exception as exc:
        logger.warning(f"⚠️  Haystack extraction run failed: {exc}")
        return None


# ─── 2. Retrieval Pipeline ───────────────────────────────────────────────────


def build_retrieval_pipeline(
    db_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build a retrieval pipeline: query embedder → PGVectorDocumentStore
    similarity search.

    Returns a dict with keys:
      - "store": PGVectorDocumentStore instance
      - "embedder": SentenceTransformersTextEmbedder instance
      or None if unavailable.
    """
    if not HAYSTACK_AVAILABLE or not PGVECTOR_STORE_AVAILABLE:
        logger.info(
            f"ℹ️  Retrieval pipeline unavailable "
            f"(haystack={HAYSTACK_AVAILABLE}, pgvector_store={PGVECTOR_STORE_AVAILABLE})"
        )
        return None

    try:
        from app.config import settings
        from sqlalchemy import create_engine, text
        import sqlalchemy as sa

        # Build the pgvector connection URL
        pg_url = db_url or settings.DATABASE_URL
        # Convert postgresql:// → postgres:// for haystack-pgvector
        pg_url = pg_url.replace("postgresql://", "postgres://", 1)

        store = PGVectorDocumentStore(
            connection_string=pg_url,
            embedding_dim=EMBEDDING_DIM,
            table_name="haystack_documents",
        )

        # Verify connection
        store.get_all_documents()

        embedder = SentenceTransformersTextEmbedder(
            model=EMBEDDING_MODEL,
        )

        logger.info("✅ Haystack retrieval pipeline built (pgvector, 384-dim)")
        return {"store": store, "embedder": embedder}

    except Exception as exc:
        logger.warning(f"⚠️  Failed to build Haystack retrieval pipeline: {exc}")
        return None


def index_document(
    retrieval: Dict[str, Any],
    doc_id: str,
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Embed and store a single document in the pgvector store.
    Returns True on success.
    """
    if retrieval is None or not text or len(text.strip()) < 20:
        return False

    try:
        store = retrieval["store"]
        embedder = retrieval["embedder"]

        # Chunk long text
        chunks: List[str] = []
        if len(text) > 3000:
            # Simple sliding window
            for i in range(0, len(text), 3000):
                chunks.append(text[i : i + 3000])
        else:
            chunks = [text]

        hay_docs: List[HayDocument] = []
        for idx, chunk in enumerate(chunks):
            hay_doc = HayDocument(
                content=chunk,
                meta={
                    **(metadata or {}),
                    "chunk_index": idx,
                    "source_doc_id": doc_id,
                },
            )
            hay_docs.append(hay_doc)

        embeddings = embedder.embed_documents(hay_docs)
        for doc, emb in zip(hay_docs, embeddings):
            doc.embedding = emb
        store.write_documents(hay_docs)
        return True

    except Exception as exc:
        logger.warning(f"⚠️  Failed to index document {doc_id[:8]}…: {exc}")
        return False


def search_documents(
    retrieval: Dict[str, Any],
    query: str,
    top_k: int = 10,
    filter_meta: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Embed the query and retrieve top-k most similar documents.

    Returns a list of dicts: {content, score, metadata, document_id}.
    Returns empty list if the pipeline is unavailable or search fails.
    """
    if retrieval is None or not query or not query.strip():
        return []

    try:
        store = retrieval["store"]
        embedder = retrieval["embedder"]

        query_embedding = embedder.embed(query)
        results = store.search(
            query_embedding=query_embedding,
            top_k=top_k,
            filters=filter_meta,
        )

        out: List[Dict[str, Any]] = []
        for doc in results:
            out.append(
                {
                    "content": doc.content or "",
                    "score": getattr(doc, "score", None),
                    "metadata": doc.meta or {},
                    "document_id": (doc.meta or {}).get("source_doc_id"),
                }
            )
        return out

    except Exception as exc:
        logger.warning(f"⚠️  Haystack retrieval search failed: {exc}")
        return []


# ─── Lazy singletons ─────────────────────────────────────────────────────────


_extraction_pipeline: Optional["Pipeline"] = None
_retrieval: Optional[Dict[str, Any]] = None
_extraction_initialized = False
_retrieval_initialized = False


def get_extraction_pipeline() -> Optional["Pipeline"]:
    """Lazily build the extraction pipeline (once)."""
    global _extraction_pipeline, _extraction_initialized
    if _extraction_initialized:
        return _extraction_pipeline
    _extraction_initialized = True
    _extraction_pipeline = build_extraction_pipeline()
    return _extraction_pipeline


def get_retrieval_pipeline() -> Optional[Dict[str, Any]]:
    """Lazily build the retrieval pipeline (once)."""
    global _retrieval, _retrieval_initialized
    if _retrieval_initialized:
        return _retrieval
    _retrieval_initialized = True
    _retrieval = build_retrieval_pipeline()
    return _retrieval
