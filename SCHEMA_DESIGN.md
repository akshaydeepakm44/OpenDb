# OpenDB Schema Design Specification

## Schema System Architecture

OpenDB separates universal attributes from domain-specific attributes.

### Universal Resource Schema (`schemas/universal/resource_schema.json`)

Applies across all domains:
- `resource_id`: Document UUID
- `canonical_name`: Standardized name of entity
- `title`: Page title
- `description`: Summary
- `url`: Canonical source URL
- `domain`: High-level domain (Technology, Healthcare, Education, Business)
- `subdomain`: Sub-category
- `entity_type`: Classification
- `language`: Code (e.g. `en`)
- `country`: Location country
- `location`: Address/headquarters
- `status`: Active status
- `confidence`: Extraction confidence

### Initial Domain Schemas (`schemas/domains/`)

1. **Technology**: `company_name`, `products`, `services`, `industry`, `founded_year`, `headquarters`, `employees`, `technologies`, `leadership`, `locations`, `contact_information`
2. **Healthcare**: `organization_name`, `facility_type`, `specializations`, `services`, `doctors`, `departments`, `locations`, `contact_information`, `accreditations`
3. **Education**: `institution_name`, `institution_type`, `courses`, `programs`, `departments`, `faculties`, `locations`, `admission_information`, `contact_information`
4. **Business**: `company_name`, `industry`, `products`, `services`, `founders`, `leadership`, `locations`, `contact_information`, `founded_year`

Domain schemas are dynamically registered and served via `GET /api/schemas`.
