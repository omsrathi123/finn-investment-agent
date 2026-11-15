"""Model routing: FAST (bulk extraction) vs SMART (reasoning). Gemini free tier via REST. Set GEMINI_API_KEY.

Every call is cached on disk by (model, prompt), so the same question never costs twice, and
usage is counted so the app can show what the session actually spent.
"""
import hashlib
import json
import os
import threading
from pathlib import Path

import requests

_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
from config import CACHE  # noqa: E402

FAST = os.getenv("FINN_FAST_MODEL", "gemini-3.5-flash-lite")
SMART = os.getenv("FINN_SMART_MODEL", "gemini-3.8-flash")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_lock = threading.Lock()


def has_key():
    return bool(os.getenv("GEMINI_API_KEY"))


def _usage_file():
    return CACHE / "llm_usage.json"


def _record(model, kind, tokens_in=0, tokens_out=0):
    with _lock:
        f = _usage_file()
        try:
            u = json.loads(f.read_text())
        except (OSError, ValueError):
            u = {}
        m = u.setdefault(model, {"calls": 0, "cache_hits": 0, "tokens_in": 0, "tokens_out": 0})
        m["cache_hits" if kind == "hit" else "calls"] += 1
        m["tokens_in"] += tokens_in
        m["tokens_out"] += tokens_out
        try:
            f.write_text(json.dumps(u))
        except OSError:
            pass


def usage():
    try:
        return json.loads(_usage_file().read_text())
    except (OSError, ValueError):
        return {}


def _post(model, body):
    k = os.getenv("GEMINI_API_KEY")
    if not k:
        raise RuntimeError("GEMINI_API_KEY not set — add it to enable filing/transcript reading")
    r = requests.post(URL.format(model=model), headers={"x-goog-api-key": k}, timeout=120, json=body)
    r.raise_for_status()
    data = r.json()
    meta = data.get("usageMetadata") or {}
    _record(model, "call", meta.get("promptTokenCount", 0) or 0, meta.get("candidatesTokenCount", 0) or 0)
    return data


def _cache_path(model, prompt, ns="llm"):
    key = CACHE / ns / (hashlib.sha1((model + prompt).encode()).hexdigest() + ".json")
    key.parent.mkdir(parents=True, exist_ok=True)
    return key


def ask_json(prompt, model=FAST):
    """Cached JSON-mode call. Same prompt+model never costs twice."""
    key = _cache_path(model, prompt)
    if key.exists():
        _record(model, "hit")
        return json.loads(key.read_text())
    data = _post(model, {"contents": [{"parts": [{"text": prompt}]}],
                         "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}})
    out = json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
    key.write_text(json.dumps(out))
    return out


def ask_grounded(prompt, model=FAST):
    """Web-grounded call (Google Search tool) for push-side enrichment such as a person's background.
    Never used on the pull/research side, which must stay on primary documents.
    Returns {"text": str, "sources": [{"title", "url"}]}."""
    key = _cache_path(model, prompt, "llm_grounded")
    if key.exists():
        _record(model, "hit")
        return json.loads(key.read_text())
    data = _post(model, {"contents": [{"parts": [{"text": prompt}]}],
                         "tools": [{"google_search": {}}],
                         "generationConfig": {"temperature": 0.2}})
    cand = (data.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
    sources = []
    for ch in (cand.get("groundingMetadata") or {}).get("groundingChunks", []) or []:
        web = ch.get("web") or {}
        if web.get("uri"):
            sources.append({"title": web.get("title") or web["uri"], "url": web["uri"]})
    out = {"text": text.strip(), "sources": sources[:5]}
    key.write_text(json.dumps(out))
    return out
