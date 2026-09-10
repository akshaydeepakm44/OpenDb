import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../")

from app.extraction.key_people_extractor import key_people_extractor

print("--- TEST 3: COMPANY LINKEDIN EXTRACTION ---")
sample_html = '<a href="https://www.linkedin.com/company/stripe/">LinkedIn</a>'
c_link = key_people_extractor.extract_company_linkedin_url(sample_html)
print(f"HTML extraction: {c_link}")

print("\n--- TEST 4: PERSONAL LINKEDIN ID EXTRACTION ---")
sample_snippets = [
    {
        "title": "Patrick Collison - Co-Founder & CEO - Stripe | LinkedIn",
        "snippet": "Patrick Collison is Co-Founder and CEO of Stripe.",
        "url": "https://www.linkedin.com/in/patrickcollison"
    },
    {
        "title": "John Doe | LinkedIn",
        "snippet": "Co-Founder at Stripe. View profile on https://www.linkedin.com/in/john-doe-12345",
        "url": "https://www.google.com/search?q=john"
    }
]
people = key_people_extractor.extract_from_linkedin_search_snippets(sample_snippets, "Stripe")
for p in people:
    print(f"  - Person: {p['name']} | Title: {p['title']} | LinkedIn: {p['linkedin_url']}")
