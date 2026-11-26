"""Scientific reference URL/DOI HTTP verifier for YouTube descriptions.

Extracts URLs and DOIs from Agent 3 / Trend Scout references, checks HTTP reachability
(HEAD with fallback GET), strips dead/404/hallucinated URLs, and retains verified
journal/institution + year metadata cleanly.
"""
from __future__ import annotations

import re
from typing import Iterable

import requests

USER_AGENT = "Mozilla/5.0 (compatible; KimiabillScienceBot/2.0)"

URL_RE = re.compile(r"https?://[^\s\)\]>\"']+", flags=re.IGNORECASE)
DOI_RE = re.compile(r"\b(?:doi:\s*)(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", flags=re.IGNORECASE)


def check_url_alive(url: str, timeout: float = 6.0) -> bool:
    """Return True if URL resolves to HTTP 200 (accepting clean 3xx->200 redirects, also treating 403/405 academic paywalls conservatively only when HEAD/GET succeeds with 200)."""
    clean = (url or "").strip().rstrip(".,;:")
    if not clean.startswith(("http://", "https://")):
        return False
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}
    try:
        r = requests.head(clean, headers=headers, allow_redirects=True, timeout=timeout)
        if r.status_code == 200:
            return True
        if r.status_code in (403, 405, 429):
            g = requests.get(clean, headers=headers, allow_redirects=True, timeout=timeout, stream=True)
            g.close()
            return g.status_code == 200
        return False
    except Exception:
        return False


def verify_and_clean_references(
    references: Iterable[str],
    *,
    check_http: bool = True,
) -> list[str]:
    """Validate URLs/DOIs in references; drop broken/hallucinated links while keeping clean text citations."""
    cleaned_out: list[str] = []
    seen: set[str] = set()

    for raw_ref in references:
        ref = " ".join((raw_ref or "").strip().split())
        if not ref:
            continue

        # Normalize explicit `doi:10.xxxx/...` tokens to https://doi.org/... for checking
        def _replace_doi(m: re.Match[str]) -> str:
            doi_id = m.group(1).rstrip(".,;:")
            return f"https://doi.org/{doi_id}"

        normalized = DOI_RE.sub(_replace_doi, ref)
        urls = URL_RE.findall(normalized)
        for u in urls:
            u_clean = u.rstrip(".,;:")
            if not check_http:
                continue
            if not check_url_alive(u_clean):
                # Strip broken/hallucinated URL & orphan parentheses cleanly
                normalized = normalized.replace(u, "").strip()

        normalized = re.sub(r"\(\s*\)", "", normalized)
        normalized = re.sub(r"\s{2,}", " ", normalized).strip(" -|;:,")
        # Drop boilerplate stock/generic lines or empty prefix left after stripping URL
        low = normalized.lower()
        if not normalized or low in {"sumber riset:", "referensi:", "doi:", "sumber:"}:
            continue
        if low.startswith("footage video:"):
            continue
        if low not in seen:
            seen.add(low)
            cleaned_out.append(normalized)

    return cleaned_out[:4]
