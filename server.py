"""
Tastenote backend: accept audio upload, transcribe via engine API,
then get a structured tasting note (text summary, flavor wheel descriptors, radar dimensions).
Serves the web MVP.

Standalone deploy version: loads .env from same directory, honors PORT, accepts AI_BUILDER_TOKEN.
"""
import os
import sys
import json
import re
import logging
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

# Load .env from project root (same directory as server.py in standalone deploy)
BASE_DIR = Path(__file__).resolve().parent
_env_path = BASE_DIR / ".env"
if _env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_path)

from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi import Path as PathParam
from openai import OpenAI

from mcp_config import get_api_base_from_mcp, DEFAULT_API_BASE

DATA_DIR = BASE_DIR / "data"
NOTES_FILE = DATA_DIR / "notes.json"

# Tasting wheel: 8 main categories with subcategories (Whisky Magazine / Charles MacLean style).
# See https://www.whiskeymasters.org/whisky-tasting-wheel
WHEEL_CATEGORIES = [
    "Cereal",
    "Fruity",
    "Floral",
    "Peaty",
    "Feinty",
    "Sulphury",
    "Woody",
    "Winey",
]
WHEEL_SUBCATEGORIES = {
    "Cereal": ["Cooked Mash", "Cooked Veg", "Husky", "Malt Extract", "Yeasty"],
    "Fruity": ["Citric", "Fresh Fruit", "Cooked Fruit", "Dried Fruit", "Solvent"],
    "Floral": ["Fragrant", "Green House", "Leafy", "Hay"],
    "Peaty": ["Medicinal", "Smokey", "Kippery", "Mossy"],
    "Feinty": ["Honey", "Leathery", "Sweat & Plastic", "Tobacco"],
    "Sulphury": ["Coal Gas", "Rubbery", "Sandy", "Vegetative"],
    "Woody": ["Toasted", "Vanilla", "Old Wood", "New Wood"],
    "Winey": ["Sherried", "Nutty", "Chocolate", "Oily"],
}

# Radar: mouthfeel / evaluation dimensions (not flavor categories). Used for radar chart only.
# Inspired by WSET / evaluation frameworks (appearance, nose, palate, finish, balance).
RADAR_DIMENSIONS = [
    "Body",
    "Aroma",
    "Smoothness",
    "Finish",
    "Complexity",
    "Balance",
]

# Logging
app_logger = logging.getLogger("wine_notes")
app_logger.setLevel(logging.INFO)
app_logger.propagate = False
if not app_logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    app_logger.addHandler(handler)


def log(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}"
    try:
        with open(BASE_DIR / "app.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
    except Exception:
        pass
    app_logger.info(line)


def _trunc(s: str, max_len: int = 400) -> str:
    """Truncate string for logging; avoid huge dumps."""
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[:max_len] + "... [truncated, total " + str(len(s)) + " chars]"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Resolve API base URL from MCP at startup; fallback to default if unavailable."""
    base, mcp_ok = await get_api_base_from_mcp()
    app.state.api_base_url = base
    log("MCP coach server: up" if mcp_ok else "MCP coach server: down (using default base URL)")
    log(f"API base URL: {base}")
    yield


app = FastAPI(title="Tastenote", lifespan=lifespan)

log("=== Tastenote server started ===")
log(f"  BASE_DIR={BASE_DIR}, .env loaded={_env_path.exists()}")


def get_client(base_url: str):
    """Use SUPER_MIND_API_KEY or AI_BUILDER_TOKEN (injected by ai-builders.space deployment)."""
    api_key = os.getenv("SUPER_MIND_API_KEY") or os.getenv("AI_BUILDER_TOKEN")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="SUPER_MIND_API_KEY or AI_BUILDER_TOKEN is not set (add to .env or deployment env_vars)",
        )
    return OpenAI(api_key=api_key, base_url=base_url)


def _strip_trailing_json_artifacts(s: str) -> str:
    s = s.rstrip()
    while s and s[-1] in ('}', '"'):
        s = s[:-1].rstrip()
    return s


def _extract_transcription_text(transcript_response) -> str:
    if isinstance(transcript_response, str):
        raw = transcript_response.strip()
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                raw = data.get("text", raw)
            except json.JSONDecodeError:
                pass
        text = raw.replace("\\n", "\n").replace('\\"', '"').strip()
        return _strip_trailing_json_artifacts(text)
    if hasattr(transcript_response, "text"):
        raw = transcript_response.text or ""
    elif isinstance(transcript_response, dict):
        raw = transcript_response.get("text", "") or ""
    else:
        raw = str(transcript_response)
    text = raw.replace("\\n", "\n").replace('\\"', '"').strip()
    return _strip_trailing_json_artifacts(text)


def _parse_tasting_response(content: str) -> dict:
    content = (content or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if m:
        content = m.group(1).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def _build_wheel_prompt() -> str:
    lines = ["Use ONLY these categories and subcategories for wheel_flavors:"]
    for cat in WHEEL_CATEGORIES:
        subs = WHEEL_SUBCATEGORIES.get(cat, [])
        lines.append(f"- {cat}: {', '.join(subs)}")
    return "\n".join(lines)


def _first_line(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    return s.split("\n")[0].strip()


def _load_notes() -> list:
    """Load notes from JSON file; return list (newest first)."""
    if not NOTES_FILE.exists():
        return []
    try:
        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        notes = data if isinstance(data, list) else []
        notes.sort(key=lambda n: n.get("created_at", ""), reverse=True)
        return notes
    except (json.JSONDecodeError, OSError) as e:
        log(f"[Notes] Load failed: {e}")
        return []


def _save_notes(notes: list) -> None:
    """Persist notes to JSON file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)
    log(f"[Notes] Saved {len(notes)} notes to {NOTES_FILE}")


@app.get("/")
def index():
    log("[GET /] Serving index.html")
    return FileResponse(BASE_DIR / "index.html")


@app.get("/api/wheel-structure")
def wheel_structure():
    """Return the canonical wheel and radar structure for the frontend."""
    log("[GET /api/wheel-structure] Returning wheel_categories, wheel_subcategories, radar_dimensions")
    return {
        "wheel_categories": WHEEL_CATEGORIES,
        "wheel_subcategories": WHEEL_SUBCATEGORIES,
        "radar_dimensions": RADAR_DIMENSIONS,
    }


@app.get("/api/notes")
def list_notes():
    """Return all notes for repertoire list (id, created_at, product_name, snippet). Newest first."""
    notes = _load_notes()
    out = []
    for n in notes:
        first_line_en = _first_line(n.get("text_summary_en") or n.get("text_summary") or "")
        first_line_zh = _first_line(n.get("text_summary_zh") or n.get("text_summary") or "")
        snippet_en = (first_line_en[:80] + "…") if len(first_line_en) > 80 else first_line_en
        snippet_zh = (first_line_zh[:80] + "…") if len(first_line_zh) > 80 else first_line_zh
        out.append({
            "id": n.get("id", ""),
            "created_at": n.get("created_at", ""),
            "product_name": n.get("product_name"),
            "snippet_en": snippet_en or "(No summary)",
            "snippet_zh": snippet_zh or "(没有摘要)",
            "snippet": snippet_en or snippet_zh or "(No summary)",
        })
    log(f"[GET /api/notes] Returning {len(out)} notes")
    return {"notes": out}


@app.get("/api/notes/{note_id}")
def get_note(note_id: str = PathParam(..., description="Note ID")):
    """Return a single note by id (full payload for detail view)."""
    notes = _load_notes()
    for n in notes:
        if n.get("id") == note_id:
            log(f"[GET /api/notes/{note_id}] Found")
            return n
    log(f"[GET /api/notes/{note_id}] Not found")
    raise HTTPException(status_code=404, detail="Note not found")


@app.post("/api/capture")
async def capture_tasting(request: Request, audio: UploadFile = File(...)):
    """Accept audio, transcribe, then return text_summary, wheel_flavors (category/subcategory/descriptors), radar (mouthfeel dimensions)."""
    import traceback
    tmp_path = None
    base_url = request.app.state.api_base_url
    try:
        log("[Capture] ---------- New request ----------")
        log(f"[Capture] Upload: filename={getattr(audio, 'filename', '?')}, content_type={getattr(audio, 'content_type', '?')}")
        client = get_client(base_url)

        filename = getattr(audio, "filename", "") or "capture"
        suffix = Path(filename).suffix
        if not suffix or len(suffix) > 8:
            # Default to .webm because MediaRecorder commonly produces webm/opus; Whisper also accepts it.
            suffix = ".webm"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            content = await audio.read()
            tmp.write(content)
            tmp_path = tmp.name
        log(f"[Capture] Audio: size={len(content)} bytes, temp file={tmp_path}")
    except Exception as e:
        log(f"[Capture] Upload/read FAILED: {type(e).__name__}: {e}")
        log(f"[Capture] Traceback: {_trunc(traceback.format_exc(), 800)}")
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    try:
        log("[Capture] Calling transcription API (whisper-1)...")
        with open(tmp_path, "rb") as f:
            transcript_response = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="text",
            )
        raw_response = transcript_response
        if hasattr(transcript_response, "text"):
            raw_response = getattr(transcript_response, "text", transcript_response)
        log(f"[Capture] Transcription raw type={type(raw_response).__name__}, len={len(str(raw_response))}")
        transcription = _extract_transcription_text(transcript_response)
        log(f"[Capture] Transcript: length={len(transcription)} chars")
        log(f"[Capture] Transcript preview: {_trunc(transcription, 300)}")
    except Exception as e:
        import traceback
        log(f"[Capture] Transcription FAILED: {type(e).__name__}: {e}")
        log(f"[Capture] Traceback: {_trunc(traceback.format_exc(), 800)}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise HTTPException(status_code=502, detail=f"Transcription failed: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    wheel_guide = _build_wheel_prompt()
    system_prompt = f"""You are a wine and spirits tasting assistant. The user will provide a short voice transcript of someone describing a tasting (what they smell, taste, feel).

Your tasks:

1. If the speaker clearly mentions a specific wine, spirit, or bottle name (e.g. "Glenfiddich 12", "Barolo 2019"), set "product_name" to that name (a short string). Otherwise set "product_name" to null.

2. Write two summaries of the same content:
   - "text_summary_en": English (2–4 sentences, clear prose)
   - "text_summary_zh": Mandarin Chinese in Simplified Chinese characters (2–4 sentences, clear prose)
   The two summaries should convey the same meaning.

3. Extract every specific flavor or aroma descriptor the speaker mentioned (e.g. raisin, vanilla, smoke, honey).
   For each descriptor, assign it to ONE category and ONE subcategory from the list below.
   Return two descriptor languages per wheel entry:
   - "descriptors_en": descriptor values in English
   - "descriptors_zh": descriptor values in Simplified Chinese characters

   If the speaker said something that fits under "Fruity" → "Dried Fruit", add:
   {{"category": "Fruity", "subcategory": "Dried Fruit", "descriptors_en": ["Raisin"], "descriptors_zh": ["葡萄干"]}}

   You may have multiple entries for the same category/subcategory if they mentioned several things. Only include categories/subcategories that were actually mentioned.

{wheel_guide}

4. Score the following mouthfeel/evaluation dimensions from 0 to 5 (0 = not mentioned or not applicable, 5 = very strong/positive). Return "radar" as an array of {{"name": dimension, "score": 0-5}} for each: {', '.join(RADAR_DIMENSIONS)}.

Return ONLY a single JSON object with this exact shape (no markdown, no explanation):
{{
  "product_name": "Glenfiddich 12" or null,
  "text_summary_en": "...",
  "text_summary_zh": "...",
  "wheel_flavors": [
    {{"category": "Fruity", "subcategory": "Dried Fruit", "descriptors_en": ["Raisin", "Fig"], "descriptors_zh": ["葡萄干", "无花果"]}},
    ...
  ],
  "radar": [
    {{"name": "Body", "score": 4}},
    ...
  ]
}}

Use the category and subcategory names exactly as in the list. Include all radar dimensions. Scores must be 0–5 integers."""

    user_content = f"Tasting transcript:\n\n{transcription}"
    log(f"[Capture] Chat: system_prompt len={len(system_prompt)}, user_content len={len(user_content)}")
    log("[Capture] Calling chat API (gpt-5)...")
    try:
        completion = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        raw = (completion.choices[0].message.content or "").strip()
        log(f"[Capture] Chat response: len={len(raw)}, finish_reason={getattr(completion.choices[0], 'finish_reason', '?')}")
        log(f"[Capture] Chat response preview: {_trunc(raw, 350)}")
        parsed = _parse_tasting_response(raw)
        if not parsed:
            log("[Capture] WARNING: Chat response JSON parse failed or empty; using defaults")
        else:
            log(f"[Capture] Parsed keys: {list(parsed.keys())}")
    except Exception as e:
        import traceback
        log(f"[Capture] Chat FAILED: {type(e).__name__}: {e}")
        log(f"[Capture] Traceback: {_trunc(traceback.format_exc(), 800)}")
        raise HTTPException(status_code=502, detail=f"Tasting note failed: {e}")

    product_name = parsed.get("product_name")
    if product_name is not None and not isinstance(product_name, str):
        product_name = None
    if product_name is not None:
        product_name = (product_name or "").strip() or None
    text_summary_en = parsed.get("text_summary_en") or parsed.get("text_summary") or "(No English summary generated.)"
    text_summary_zh = parsed.get("text_summary_zh") or "(无摘要生成。)"
    if not isinstance(text_summary_en, str):
        text_summary_en = "(No English summary generated.)"
    if not isinstance(text_summary_zh, str):
        text_summary_zh = "(无摘要生成。)"
    wheel_flavors = parsed.get("wheel_flavors")
    if not isinstance(wheel_flavors, list):
        log(f"[Capture] wheel_flavors invalid (type={type(wheel_flavors).__name__}), using []")
        wheel_flavors = []
    radar = parsed.get("radar")
    if not isinstance(radar, list):
        log(f"[Capture] radar invalid (type={type(radar).__name__}), using []")
        radar = []
    # Normalize radar: ensure all RADAR_DIMENSIONS present, score 0-5
    radar_by_name = {r.get("name", ""): max(0, min(5, int(r.get("score", 0)))) for r in radar}
    radar = [{"name": d, "score": radar_by_name.get(d, 0)} for d in RADAR_DIMENSIONS]

    log(f"[Capture] Result: product_name={product_name!r}, wheel_flavors count={len(wheel_flavors)}, radar={[r['score'] for r in radar]}")
    if wheel_flavors:
        for i, w in enumerate(wheel_flavors[:10]):
            log(
                f"[Capture]   wheel_flavors[{i}] {w.get('category')} / {w.get('subcategory')} -> "
                f"en={w.get('descriptors_en', w.get('descriptors', []))}, zh={w.get('descriptors_zh', [])}"
            )
        if len(wheel_flavors) > 10:
            log(f"[Capture]   ... and {len(wheel_flavors) - 10} more")

    # Normalize wheel_flavors: ensure descriptor language arrays exist.
    normalized_wheel_flavors = []
    for w in wheel_flavors:
        if not isinstance(w, dict):
            continue
        descriptors_en = w.get("descriptors_en") or w.get("descriptors") or []
        descriptors_zh = w.get("descriptors_zh") or []
        if not isinstance(descriptors_en, list):
            descriptors_en = []
        if not isinstance(descriptors_zh, list):
            descriptors_zh = []

        entry = {
            "category": w.get("category"),
            "subcategory": w.get("subcategory"),
            "descriptors_en": descriptors_en,
            "descriptors_zh": descriptors_zh,
            # Backward-compatible alias used by older frontend code.
            "descriptors": descriptors_en,
        }
        normalized_wheel_flavors.append(entry)
    wheel_flavors = normalized_wheel_flavors

    note_id = str(uuid.uuid4())
    created_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    note = {
        "id": note_id,
        "created_at": created_at,
        "product_name": product_name,
        "transcription": transcription,
        "text_summary": text_summary_en,  # backward-compatible alias
        "text_summary_en": text_summary_en,
        "text_summary_zh": text_summary_zh,
        "wheel_flavors": wheel_flavors,
        "radar": radar,
    }
    notes = _load_notes()
    notes.insert(0, note)
    _save_notes(notes)

    log("[Capture] Returning response to client")
    return {
        "id": note_id,
        "created_at": created_at,
        "product_name": product_name,
        "transcription": transcription,
        "text_summary": text_summary_en,  # backward-compatible alias
        "text_summary_en": text_summary_en,
        "text_summary_zh": text_summary_zh,
        "wheel_flavors": wheel_flavors,
        "radar": radar,
        "wheel_categories": WHEEL_CATEGORIES,
        "radar_dimensions": RADAR_DIMENSIONS,
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
