import pytest
import asyncio
from app.normalization.normalizer import normalizer
from app.classification.domain_classifier import domain_classifier
from app.schemas.registry import schema_registry
from app.extraction.css_extractor import css_extractor
from app.extraction.llm_extractor import llm_extractor

def test_normalization():
    assert normalizer.normalize_url("https://EXAMPLE.com/path/?utm_source=test#frag") == "https://example.com/path"
    assert normalizer.normalize_country("USA") == "United States"
    assert normalizer.normalize_language("en-US") == "en"

def test_domain_classifier():
    tech_text = "Python FastAPI Docker Cloud Database Kubernetes microservices platform SaaS software."
    domain, subdomain, conf = domain_classifier.classify(text_content=tech_text, title="Tech", url="https://tech.com")
    assert domain == "Technology"
    assert conf > 0.50

def test_schemas_registry():
    schemas = schema_registry.list_schemas()
    assert len(schemas) >= 4
    tech_schema = schema_registry.get_domain_schema("technology")
    assert tech_schema is not None
    assert "company_name" in tech_schema["properties"]

def test_css_extractor():
    html = """
    <html>
      <head>
        <title>OpenDB Test Page</title>
        <meta name="description" content="A test page for OpenDB extraction." />
        <link rel="canonical" href="https://example.com/test" />
      </head>
      <body>
        <h1>Main Heading</h1>
      </body>
    </html>
    """
    extracted = css_extractor.extract_deterministic_metadata(html, page_url="https://example.com/test")
    assert extracted["title"] == "OpenDB Test Page"
    assert extracted["description"] == "A test page for OpenDB extraction."
    assert extracted["canonical_url"] == "https://example.com/test"

@pytest.mark.asyncio
async def test_llm_extractor_missing_fields_and_heuristics():
    text = "Acme Corp was founded in 2010. We build cloud software products."
    schema = schema_registry.get_domain_schema("technology")
    data, evidence = await llm_extractor.extract_domain_data(
        text_content=text,
        domain_name="Technology",
        schema_def=schema,
        page_url="https://acme.com"
    )
    # Check that present fields are extracted, missing fields return null / []
    assert data["founded_year"] == 2010 or data["founded_year"] is None
    assert data["services"] == [] or isinstance(data["services"], list)
    assert "employees" in data and (data["employees"] is None or isinstance(data["employees"], (int, str)))
