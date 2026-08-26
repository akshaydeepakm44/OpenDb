import re
import html
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

class DataNormalizer:
    @staticmethod
    def normalize_string(val: str | None) -> str | None:
        if not val:
            return None
        # Unescape HTML entities
        cleaned = html.unescape(str(val))
        # Collapse multi whitespace
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned if cleaned else None

    @staticmethod
    def normalize_url(url: str | None, base_url: str | None = None) -> str | None:
        if not url:
            return None
        try:
            url_str = url.strip()
            if base_url and not url_str.startswith(("http://", "https://")):
                from urllib.parse import urljoin
                url_str = urljoin(base_url, url_str)

            parsed = urlparse(url_str)
            scheme = parsed.scheme.lower()
            netloc = parsed.netloc.lower()
            
            # Filter tracking query parameters
            query_params = parse_qs(parsed.query)
            tracking_keys = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
            filtered_params = {k: v for k, v in query_params.items() if k.lower() not in tracking_keys}
            
            clean_query = urlencode(filtered_params, doseq=True)
            path = parsed.path
            if path and path != "/" and path.endswith("/"):
                path = path.rstrip("/")

            normalized = urlunparse((
                scheme,
                netloc,
                path,
                parsed.params,
                clean_query,
                "" # remove fragment
            ))
            return normalized
        except Exception:
            return url

    @staticmethod
    def normalize_language(lang: str | None) -> str | None:
        if not lang:
            return None
        clean = lang.strip().lower()
        if "-" in clean:
            clean = clean.split("-")[0]
        return clean[:10]

    @staticmethod
    def normalize_country(country: str | None) -> str | None:
        if not country:
            return None
        c_map = {
            "us": "United States", "usa": "United States", "united states of america": "United States",
            "uk": "United Kingdom", "gb": "United Kingdom", "great britain": "United Kingdom",
            "in": "India", "ind": "India"
        }
        clean = country.strip().lower()
        return c_map.get(clean, country.strip().title())

    @staticmethod
    def normalize_phone(phone: str | None) -> str | None:
        if not phone:
            return None
        digits = re.sub(r"[^\d+]", "", phone)
        return digits if len(digits) >= 7 else phone.strip()

    @staticmethod
    def normalize_email(email: str | None) -> str | None:
        if not email:
            return None
        email_clean = email.strip().lower()
        if re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email_clean):
            return email_clean
        return None

normalizer = DataNormalizer()
