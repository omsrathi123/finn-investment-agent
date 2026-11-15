"""PDF -> text, cached per URL. Page-level cache lets big annual reports be *chunked*: only the
pages that score on a keyword set are sent to the model, which keeps token spend small."""
import hashlib
import io
import json
import re

import requests
from pypdf import PdfReader

from config import CACHE, HEADERS


def _fetch_pages(url, max_pages=None):
    f = CACHE / "docs" / (hashlib.sha1(url.encode()).hexdigest() + ".pages.json")
    f.parent.mkdir(parents=True, exist_ok=True)
    if f.exists():
        pages = json.loads(f.read_text())
        if pages and (max_pages is None or len(pages) >= max_pages or pages[-1] == "<<END>>"):
            return [p for p in pages if p != "<<END>>"]
    ref = "https://www.nseindia.com/" if "nse" in url else "https://www.bseindia.com/"
    r = requests.get(url, headers={**HEADERS, "Referer": ref}, timeout=90)
    r.raise_for_status()
    reader = PdfReader(io.BytesIO(r.content))
    n = len(reader.pages) if max_pages is None else min(max_pages, len(reader.pages))
    pages = []
    for i in range(n):
        try:
            pages.append(reader.pages[i].extract_text() or "")
        except Exception:
            pages.append("")
    if n == len(reader.pages):
        pages.append("<<END>>")
    f.write_text(json.dumps(pages))
    return [p for p in pages if p != "<<END>>"]


def pdf_text(url, max_chars=14000):
    f = CACHE / "docs" / (hashlib.sha1(url.encode()).hexdigest() + ".txt")
    f.parent.mkdir(parents=True, exist_ok=True)
    if f.exists() and f.stat().st_size > 0:
        return f.read_text()[:max_chars]
    text = "\n".join(_fetch_pages(url))
    f.write_text(text)
    if not text.strip():
        raise RuntimeError("The PDF has no extractable text (likely a scanned image)")
    return text[:max_chars]


def relevant_pages(url, keywords, budget_chars=18000, max_pages=160):
    """Keyword-scored chunking: return the highest-scoring pages (in document order) within budget."""
    pages = _fetch_pages(url, max_pages=max_pages)
    pat = re.compile("|".join(keywords), re.I)
    scored = sorted(((len(pat.findall(p)), i) for i, p in enumerate(pages) if p.strip()), reverse=True)
    chosen, used = [], 0
    for s, i in scored:
        if s == 0 or used >= budget_chars:
            break
        chosen.append(i)
        used += len(pages[i])
    chosen.sort()
    return "\n".join(f"[page {i + 1}]\n{pages[i][:6000]}" for i in chosen)[:budget_chars], [i + 1 for i in chosen]
