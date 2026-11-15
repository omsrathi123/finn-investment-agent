import hashlib, io, requests
from pypdf import PdfReader
from config import CACHE, HEADERS

def pdf_text(url, max_chars=14000):
    f = CACHE / "docs" / (hashlib.sha1(url.encode()).hexdigest() + ".txt")
    f.parent.mkdir(exist_ok=True)
    if not f.exists():
        r = requests.get(url, headers={**HEADERS, "Referer": "https://www.bseindia.com/"}, timeout=60)
        r.raise_for_status()
        f.write_text("\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(r.content)).pages))
    return f.read_text()[:max_chars]
