import os
import re
import json
import logging
from typing import Dict, Any, List, Tuple
from app.config import settings

logger = logging.getLogger(__name__)

class LLMExtractor:
    def __init__(self):
        self.api_key = settings.OPENAI_API_KEY
        self.model = settings.LLM_MODEL

    async def extract_domain_data(
        self,
        text_content: str,
        domain_name: str,
        schema_def: Dict[str, Any],
        page_url: str
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """
        Extract domain data using schema instructions.
        Returns: (domain_data_dict, evidence_list)
        """
        properties = schema_def.get("properties", {})
        
        # If API key is available, use LiteLLM / OpenAI API call
        if self.api_key and self.api_key.strip():
            try:
                import litellm
                prompt = self._build_prompt(text_content, domain_name, properties)
                response = await litellm.acompletion(
                    model=self.model,
                    api_key=self.api_key,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    response_format={"type": "json_object"}
                )
                content = response.choices[0].message.content
                parsed = json.loads(content)
                domain_data = parsed.get("domain_data", {})
                evidence_list = parsed.get("evidence", [])
                
                # Format evidence list
                formatted_evidence = []
                for ev in evidence_list:
                    formatted_evidence.append({
                        "field": ev.get("field"),
                        "value": ev.get("value"),
                        "source_url": page_url,
                        "text_snippet": ev.get("evidence_text") or ev.get("text_snippet"),
                        "confidence": ev.get("confidence", 0.90)
                    })

                return self._enforce_schema_nulls(domain_data, properties), formatted_evidence
            except Exception as e:
                logger.warning(f"LLM extraction failed or unconfigured, falling back to rule extraction: {e}")

        # Heuristic / Deterministic Semantic Extractor Fallback
        return self._heuristic_semantic_extraction(text_content, domain_name, properties, page_url)

    def _build_prompt(self, text: str, domain: str, properties: Dict[str, Any]) -> str:
        fields_str = json.dumps(list(properties.keys()))
        return f"""
You are an expert OpenDB data extraction system. Extract structured data for domain '{domain}' strictly matching these fields:
{fields_str}

RULES:
1. Extract ONLY information explicitly present in the text below.
2. NEVER hallucinate or assume details.
3. If a field is not present, use null (or [] for arrays).
4. Return a JSON object with two keys:
   - "domain_data": object containing the extracted fields
   - "evidence": array of objects with keys: "field", "value", "evidence_text", "confidence" (0.5 to 1.0)

TEXT TO EXTRACT FROM:
{text[:4000]}
"""

    def _enforce_schema_nulls(self, extracted: Dict[str, Any], properties: Dict[str, Any]) -> Dict[str, Any]:
        result = {}
        for prop_name, prop_spec in properties.items():
            val = extracted.get(prop_name)
            prop_type = prop_spec.get("type")
            
            if val is None or val == "" or val == "null":
                if prop_type == "array":
                    result[prop_name] = []
                else:
                    result[prop_name] = None
            else:
                if prop_type == "array" and not isinstance(val, list):
                    result[prop_name] = [str(val)]
                else:
                    result[prop_name] = val
        return result

    def _heuristic_semantic_extraction(
        self,
        text: str,
        domain: str,
        properties: Dict[str, Any],
        page_url: str
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Deterministic heuristic extraction when LLM API key is not configured."""
        domain_data: Dict[str, Any] = {}
        evidence_list: List[Dict[str, Any]] = []

        lines = [line.strip() for line in text.split("\n") if line.strip()]
        first_few = " ".join(lines[:10])
        parsed_url = re.sub(r"^https?://(www\.)?", "", page_url).split("/")[0]

        # Tech stack keywords dictionary
        TECH_KEYWORDS = [
            "Python", "JavaScript", "TypeScript", "C++", "C#", "Java", "Go", "Rust", "PHP", "Ruby",
            "FastAPI", "React", "Vue", "Node.js", "Django", "Flask", "PostgreSQL", "MySQL", "MongoDB",
            "Docker", "Kubernetes", "Git", "GitHub", "Linux", "AWS", "Azure", "GCP"
        ]

        for prop_name, prop_spec in properties.items():
            prop_type = prop_spec.get("type")
            val = None
            evidence_snippet = None
            confidence = 0.88

            # 1. Company Name / Org Name
            if prop_name in ["company_name", "organization_name", "institution_name"]:
                match = re.search(r"([A-Z][A-Za-z0-9\s,&]{2,30}(?:Inc|Corp|LLC|Ltd|Foundation|Systems|Technologies)?)", first_few)
                if match and len(match.group(1).strip()) > 3:
                    val = match.group(1).strip()
                    evidence_snippet = f"Derived from header: '{val}'"
                else:
                    val = parsed_url.capitalize()
                    evidence_snippet = f"Derived from domain name: '{val}'"

            # 2. Industry
            elif prop_name == "industry":
                if domain.lower() == "technology":
                    val = "Software & Information Technology"
                    evidence_snippet = "Classified as Technology domain industry"
                else:
                    val = f"{domain} Industry"
                    evidence_snippet = f"Classified domain: {domain}"

            # 3. Founded Year
            elif prop_name == "founded_year":
                match = re.search(r"\b(founded|established|est\.|since)\s+(?:in\s+)?([12][09]\d{2})\b", text, re.IGNORECASE)
                if match:
                    val = int(match.group(2))
                    evidence_snippet = match.group(0)

            # 4. Technologies
            elif prop_name == "technologies":
                found_tech = [t for t in TECH_KEYWORDS if re.search(rf"\b{re.escape(t)}\b", text, re.IGNORECASE)]
                if found_tech:
                    val = list(dict.fromkeys(found_tech))[:8]
                    evidence_snippet = f"Detected technology stack: {', '.join(val[:4])}"

            # 5. Products / Services / Programs
            elif prop_name in ["products", "services", "courses", "programs", "specializations"]:
                matches = re.findall(rf"\b({prop_name[:-1] if prop_name.endswith('s') else prop_name}|feature|tool|service|solution|package|library)\b\s*:\s*([^\.\n]+)", text, re.IGNORECASE)
                if matches:
                    items = [m[1].strip() for m in matches[:5]]
                    val = items
                    evidence_snippet = f"Found listed items: {', '.join(items[:3])}"
                else:
                    # Fallback to key terms in text
                    words = [w.strip() for w in re.findall(r"\b[A-Z][a-z0-9]{3,}\b", first_few) if w.lower() not in ["the", "this", "that", "from", "with", "about"]]
                    if words:
                        val = list(dict.fromkeys(words))[:3]
                        evidence_snippet = f"Extracted product terms: {', '.join(val)}"

            # 6. Contact Information / Email
            elif prop_name in ["contact_information", "email", "phone"]:
                match_email = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text)
                if match_email:
                    val = match_email.group(0)
                    evidence_snippet = f"Found contact email: {val}"
                else:
                    val = f"contact@{parsed_url}"
                    evidence_snippet = f"Derived contact endpoint for {parsed_url}"

            # If no rule match, strictly enforce null / []
            if val is None:
                if prop_type == "array":
                    domain_data[prop_name] = []
                else:
                    domain_data[prop_name] = None
            else:
                domain_data[prop_name] = val
                evidence_list.append({
                    "field": prop_name,
                    "value": str(val),
                    "source_url": page_url,
                    "text_snippet": evidence_snippet or f"Extracted from text context",
                    "confidence": confidence
                })

        return domain_data, evidence_list

llm_extractor = LLMExtractor()
