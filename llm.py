"""Model routing: FAST (bulk extraction) vs SMART (reasoning). Gemini free tier via REST. Set GEMINI_API_KEY."""
import os, json, hashlib, requests
from pathlib import Path
_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
from config import CACHE
FAST = os.getenv("FINN_FAST_MODEL", "gemini-2.0-flash")
SMART = os.getenv("FINN_SMART_MODEL", "gemini-2.5-flash")

def ask_json(prompt, model=FAST):
    """Cached JSON-mode call. Same prompt+model never costs twice."""
    key = CACHE / "llm" / (hashlib.sha1((model + prompt).encode()).hexdigest() + ".json")
    key.parent.mkdir(exist_ok=True)
    if key.exists(): return json.loads(key.read_text())
    k = os.getenv("GEMINI_API_KEY")
    if not k: raise RuntimeError("GEMINI_API_KEY not set")
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": k}, timeout=120,
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}})
    r.raise_for_status()
    out = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
    key.write_text(json.dumps(out)); return out

            