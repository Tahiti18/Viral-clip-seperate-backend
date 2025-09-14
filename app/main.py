import os, json, io, zipfile, urllib.request, urllib.error, time, re
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import asyncpg
from pydantic import BaseModel

app = FastAPI(title="UnityLab Backend", version="2.1.0-multiagent-safe")

# ---------------- CORS ----------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://clipgenius.netlify.app", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- DB helpers ----------------
def _conn_strings():
    dpsql = os.getenv("DATABASE_URL_PSQL", "")
    dasync = os.getenv("DATABASE_URL", "")
    if not dpsql and dasync.startswith("postgresql+asyncpg://"):
        dpsql = "postgresql://" + dasync.split("postgresql+asyncpg://", 1)[1]
    return dpsql, dasync

async def _pool():
    if not hasattr(app.state, "pool"):
        dpsql, _ = _conn_strings()
        if not dpsql:
            raise HTTPException(status_code=500, detail="DATABASE_URL_PSQL or DATABASE_URL not set")
        app.state.pool = await asyncpg.create_pool(dpsql, min_size=1, max_size=5)
    return app.state.pool

# ---------------- Schemas ----------------
class JobIn(BaseModel):
    video_url: str
    title: str
    description: str

# ---------------- OpenRouter helpers ----------------
JSON_ONLY_SYSTEM = (
    "Return ONLY valid JSON. No prose, no markdown, no code fences. "
    "If unsure, return an object with a 'clips' array (possibly empty)."
)

def _openrouter_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "https://clipgenius.netlify.app"),
        "X-Title": os.getenv("OPENROUTER_APP_TITLE", "ClipGenius"),
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))

def _call_openrouter(model: str, user_prompt: str, max_tokens: int = 1200) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": JSON_ONLY_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    data = _openrouter_request(payload)
    return data["choices"][0]["message"]["content"]

# ---------------- JSON parsing hardening ----------------
FENCE_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)

def _extract_json_str(text: str) -> Optional[str]:
    if not text:
        return None
    # 1) Prefer fenced blocks ```json { ... } ```
    m = FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    # 2) Raw first '{' to last '}' approach
    a = text.find("{")
    b = text.rfind("}")
    if a != -1 and b != -1 and b > a:
        return text[a:b+1].strip()
    return None

def _safe_parse_clips(text: str, default: Optional[list] = None) -> Dict[str, Any]:
    """
    Try hard to parse a JSON object with a top-level 'clips' list.
    Returns {'clips': [...], 'raw': '<truncated>'}
    """
    js = _extract_json_str(text)
    clips = default or []
    if js:
        try:
            obj = json.loads(js)
            if isinstance(obj, dict) and isinstance(obj.get("clips"), list):
                clips = obj["clips"]
            else:
                # sometimes models return a list directly
                if isinstance(obj, list):
                    clips = obj
        except Exception:
            pass
    # limit raw echo to avoid huge payloads
    raw_preview = (text or "")[:5000]
    return {"clips": clips, "raw": raw_preview}

# ---------------- Routes ----------------
@app.get("/")
async def root():
    return {"ok": True}

@app.get("/health")
async def health():
    return {"ok": True}

# ---------------- Multi-agent pipeline (hardened) ----------------
@app.post("/api/analyze-video")
async def analyze_video(job: JobIn):
    """
    4-agent pipeline with robust JSON parsing at each stage.
    Models (OpenRouter slugs):
      - google/gemini-2.5-pro
      - anthropic/claude-3.5-sonnet
      - openai/gpt-5        (if not available to your key, swap to openai/gpt-4.1)
      - openai/gpt-4.1
    """
    try:
        # 1) GEMINI -> candidate spans
        gemini_prompt = f"""
        Video URL: {job.video_url}
      Title: {job.title}
      Description: {job.description}

      Task: Propose candidate viral clip spans from the video.
      Return JSON: {{ "clips": [ {{ "start_time": "HH:MM:SS", "end_time": "HH:MM:SS", "duration": <seconds>, "topic": "..." }} ] }}
        """.strip()
        g_out_text = _call_openrouter("google/gemini-2.5-pro", gemini_prompt)
        g_parsed = _safe_parse_clips(g_out_text)

        # 2) CLAUDE -> add hooks & reasons
        claude_prompt = f"""
        Given this JSON:
        {json.dumps({"clips": g_parsed["clips"]}, ensure_ascii=False)}

        For each clip, ADD:
          - "hook": catchy one-liner
          - "reason": why it will go viral (emotion, curiosity, novelty)
        Return JSON: {{ "clips": [ ...updated clips... ] }}
        """.strip()
        c_out_text = _call_openrouter("anthropic/claude-3.5-sonnet", claude_prompt)
        c_parsed = _safe_parse_clips(c_out_text, default=g_parsed["clips"])

        # 3) GPT-5 -> pick strongest 3 and refine (fallback to 4.1 if 5 is unavailable on your key)
        gpt5_model = os.getenv("FINAL_JUDGE_MODEL", "openai/gpt-5").strip() or "openai/gpt-5"
        gpt5_prompt = f"""
        Review these annotated clips:
        {json.dumps({"clips": c_parsed["clips"]}, ensure_ascii=False)}

        Pick the 3 strongest. Improve "hook" and "reason" for clarity and punch.
        Add "viral_score" (0-10). Keep times/duration.
        Return JSON strictly: {{ "clips": [ {{ "id": 1, "start_time": "...", "end_time": "...", "duration": 45, "viral_score": 8.7, "hook": "...", "reason": "..." }} ] }}
        """.strip()
        try:
            g5_out_text = _call_openrouter(gpt5_model, gpt5_prompt)
        except urllib.error.HTTPError as e:
            # if GPT-5 not available, retry on GPT-4.1 seamlessly
            if getattr(e, "code", None) in (400, 401, 403, 404):
                g5_out_text = _call_openrouter("openai/gpt-4.1", gpt5_prompt)
                gpt5_model = "openai/gpt-4.1"
            else:
                raise
        g5_parsed = _safe_parse_clips(g5_out_text, default=c_parsed["clips"])

        # Ensure exactly 3 clips if possible
        final3 = (g5_parsed["clips"] or c_parsed["clips"] or g_parsed["clips"])[:3]

        # 4) GPT-4.1 -> titles, captions, platforms, predicted views
        g41_prompt = f"""
        Enhance these 3 clips with:
          - "title": viral title
          - "caption": short viral caption
          - "platforms": best platforms (array)
          - "predicted_views": integer
        Return JSON: {{ "clips": [ ...enhanced... ] }}
        Input:
        {json.dumps({"clips": final3}, ensure_ascii=False)}
        """.strip()
        g41_text = _call_openrouter("openai/gpt-4.1", g41_prompt)
        g41_parsed = _safe_parse_clips(g41_text, default=final3)

        # If still empty, produce a soft fallback so frontend shows something
        clips = g41_parsed["clips"]
        if not clips:
            clips = [
                {
                    "id": i + 1,
                    "start_time": c.get("start_time") or "00:00:00",
                    "end_time": c.get("end_time") or "00:00:10",
                    "duration": c.get("duration") or 10,
                    "viral_score": c.get("viral_score") or 7.5,
                    "hook": c.get("hook") or "Potential high-engagement moment",
                    "reason": c.get("reason") or "Salient moment with novelty/emotion.",
                    "title": c.get("title") or "Viral Clip Candidate",
                    "caption": c.get("caption") or "🔥 Must-watch moment.",
                    "platforms": c.get("platforms") or ["TikTok", "Instagram"],
                    "predicted_views": c.get("predicted_views") or 25000,
                }
                for i, c in enumerate(final3)
            ]

        return {
            "success": True,
            "video_url": job.video_url,
            "clips": clips,
            "agents": {
                "A": "google/gemini-2.5-pro",
                "B": "anthropic/claude-3.5-sonnet",
                "C": gpt5_model,          # openai/gpt-5 or fallback
                "D": "openai/gpt-4.1",
            },
            "debug": {
                "gemini_raw_preview": g_parsed["raw"][:300],
                "claude_raw_preview": c_parsed["raw"][:300],
                "judge_raw_preview": g5_parsed["raw"][:300],
                "packager_raw_preview": g41_parsed["raw"][:300],
            },
        }

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
        return {"success": False, "error": f"HTTP Error {e.code}", "details": detail}
    except Exception as e:
        return {"success": False, "error": f"Pipeline failed: {str(e)}"}

# ---------------- Optional: tiny handoff ZIP (unchanged) ----------------
def _sample_srt() -> str:
    return (
        "1\n00:00:00,000 --> 00:00:02,000\nWelcome to UnityLab!\n\n"
        "2\n00:00:02,000 --> 00:00:04,500\nThis is a sample SRT from the backend.\n\n"
    )

@app.get("/api/handoff.zip")
async def handoff_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", "UnityLab editor handoff")
        zf.writestr("captions.srt", _sample_srt())
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="handoff.zip"',
            "Cache-Control": "no-store",
        },
    )
