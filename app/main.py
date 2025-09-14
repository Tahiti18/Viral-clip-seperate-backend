import os, json, io, zipfile, urllib.request, urllib.error, time, re, subprocess
from typing import Dict, Any, Optional, List, Tuple
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import asyncpg
from pydantic import BaseModel

app = FastAPI(title="UnityLab Backend", version="2.3.1-multiagent-3clips")

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

# ---------------- OpenRouter JSON-forced helpers ----------------
JSON_ONLY_SYSTEM = (
    "Return ONLY valid JSON. No prose, no markdown, no code fences. "
    "Follow the schema exactly. If you cannot, return {\"clips\":[]}."
)

FENCE_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)

def _extract_json_str(text: str) -> Optional[str]:
    if not text:
        return None
    m = FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    a = text.find("{")
    b = text.rfind("}")
    if a != -1 and b != -1 and b > a:
        return text[a:b+1].strip()
    return None

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

def _ask_json(model: str, user_prompt: str, schema_hint: str, attempts: int = 2, max_tokens: int = 1200) -> Dict[str, Any]:
    last_text = ""
    for _ in range(attempts):
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": JSON_ONLY_SYSTEM + " Schema: " + schema_hint},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "seed": 1
        }
        data = _openrouter_request(payload)
        text = data["choices"][0]["message"]["content"]
        last_text = text

        js = _extract_json_str(text)
        if js:
            try:
                obj = json.loads(js)
                if isinstance(obj, dict) and isinstance(obj.get("clips"), list):
                    return {"clips": obj["clips"], "raw": text[:1000]}
                if isinstance(obj, list):
                    return {"clips": obj, "raw": text[:1000]}
            except Exception:
                pass

        user_prompt = (
            "The previous response was NOT valid JSON.\n"
            "Respond again with ONLY valid JSON that matches this schema: "
            f"{schema_hint}\n\n"
            "Your previous output (truncated):\n" + last_text[:1000]
        )
        time.sleep(0.4)
    return {"clips": [], "raw": last_text[:1000]}

# ---------------- Clip utilities ----------------
def _hms_to_seconds(hms: str) -> int:
    try:
        hh, mm, ss = (hms or "00:00:00").split(":")
        return int(hh) * 3600 + int(mm) * 60 + int(ss)
    except Exception:
        return 0

def _normalize_clip(c: Dict[str, Any], idx: int) -> Dict[str, Any]:
    st = c.get("start_time") or "00:00:00"
    et = c.get("end_time") or "00:00:10"
    dur = c.get("duration")
    if not isinstance(dur, int):
        dur = max(1, _hms_to_seconds(et) - _hms_to_seconds(st))
    return {
        "id": int(c.get("id") or (idx + 1)),
        "start_time": st,
        "end_time": et,
        "duration": dur,
        "viral_score": float(c.get("viral_score") or 0),
        "hook": c.get("hook") or "",
        "reason": c.get("reason") or "",
        "title": c.get("title") or "",
        "caption": c.get("caption") or "",
        "platforms": c.get("platforms") or [],
        "predicted_views": int(c.get("predicted_views") or 0),
    }

def _dedupe_by_times(clips: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[Tuple[str,str]] = set()
    out = []
    for c in clips:
        key = (c.get("start_time"), c.get("end_time"))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out

def _enforce_three(best: List[Dict[str,Any]], fallbacks: List[List[Dict[str,Any]]]) -> List[Dict[str,Any]]:
    merged: List[Dict[str,Any]] = [_normalize_clip(c,i) for i,c in enumerate(best)]
    for fb in fallbacks:
        for c in fb:
            merged.append(_normalize_clip(c, len(merged)))
    merged = _dedupe_by_times(merged)
    merged.sort(key=lambda c: float(c.get("viral_score") or 0), reverse=True)
    return merged[:3]

# ---------------- Routes ----------------
@app.get("/")
async def root():
    return {"ok": True}

@app.get("/health")
async def health():
    return {"ok": True}

# FFmpeg availability check
@app.get("/api/ffmpeg-check")
def ffmpeg_check():
    try:
        out = subprocess.check_output(["ffmpeg", "-version"], stderr=subprocess.STDOUT, timeout=5)
        return {"ok": True, "ffmpeg": out.decode("utf-8").splitlines()[0]}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# Railway health check
@app.get("/api/ai-status")
def ai_status_check():
    key = os.getenv("OPENROUTER_API_KEY", "")
    return {
        "ai_status": "online",
        "api_key_valid": bool(key and key.startswith("sk-or-")),
        "model_default": os.getenv("FINAL_JUDGE_MODEL", "openai/gpt-5")
    }

# ---------------- Multi-agent pipeline ----------------
@app.post("/api/analyze-video")
async def analyze_video(job: JobIn):
    """
    A -> Google Gemini 2.5 Pro: propose candidate spans
    B -> Claude 3.5 Sonnet: enrich hooks & reasons
    C -> OpenAI Judge (GPT-5, fallback GPT-4.1): select/refine top 3 & score
    D -> GPT-4.1: package with titles, captions, platforms, predicted views
    Always returns exactly 3 clips.
    """
    try:
        # 1) A: Candidate spans (Gemini)
        schema1 = '{"clips":[{"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"topic":"..."}]}'
        gemini_prompt = f"""Video URL: {job.video_url}
Title: {job.title}
Description: {job.description}

Task: propose candidate viral clip spans.
Return ONLY JSON matching: {schema1}
"""
        g = _ask_json("google/gemini-2.5-pro", gemini_prompt, schema1)

        # 2) B: Add hooks & reasons (Claude)
        schema2 = '{"clips":[{"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"topic":"...","hook":"...","reason":"..."}]}'
        claude_prompt = f"""Given this:
{json.dumps({"clips": g["clips"]}, ensure_ascii=False)}

For each clip, add "hook" and "reason".
Return ONLY JSON matching: {schema2}
"""
        c = _ask_json("anthropic/claude-3.5-sonnet", claude_prompt, schema2)

        # 3) C: Judge & refine top 3 (GPT-5 -> fallback GPT-4.1)
        schema3 = '{"clips":[{"id":1,"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"viral_score":8.7,"hook":"...","reason":"..."}]}'
        judge_model = os.getenv("FINAL_JUDGE_MODEL", "openai/gpt-5") or "openai/gpt-5"
        judge_prompt = f"""Review these clips and return the BEST three with improved hook/reason and a "viral_score" 0–10.
Input:
{json.dumps({"clips": c["clips"]}, ensure_ascii=False)}

Return ONLY JSON matching: {schema3}
"""
        try:
            j = _ask_json(judge_model, judge_prompt, schema3)
            used_judge = judge_model
            if not j["clips"]:
                raise RuntimeError("empty judge result")
        except Exception:
            j = _ask_json("openai/gpt-4.1", judge_prompt, schema3)
            used_judge = "openai/gpt-4.1"

        # Enforce exactly 3 BEFORE packaging
        final3 = _enforce_three(j["clips"], [c["clips"], g["clips"]])

        # 4) D: Package (GPT-4.1)
        schema4 = '{"clips":[{"id":1,"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"viral_score":8.7,"hook":"...","reason":"...","title":"...","caption":"...","platforms":["TikTok","Instagram"],"predicted_views":45000}]}'
        pack_prompt = f"""Enhance these with title, caption, platforms, predicted_views.
Input:
{json.dumps({"clips": final3}, ensure_ascii=False)}

Return ONLY JSON matching: {schema4}
"""
        p = _ask_json("openai/gpt-4.1", pack_prompt, schema4)
        packaged = _enforce_three(p["clips"], [final3])

        for i, clip in enumerate(packaged, start=1):
            clip["id"] = i

        return {
            "success": True,
            "video_url": job.video_url,
            "clips": packaged,
            "agents": {
                "A": "google/gemini-2.5-pro",
                "B": "anthropic/claude-3.5-sonnet",
                "C": used_judge,
                "D": "openai/gpt-4.1",
            },
        }

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
        return {"success": False, "error": f"HTTP Error {e.code}", "details": detail}
    except Exception as e:
        return {"success": False, "error": f"Pipeline failed: {str(e)}"}

# ---------------- Optional handoff ZIP ----------------
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
