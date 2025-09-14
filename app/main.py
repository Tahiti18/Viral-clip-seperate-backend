import os, io, re, json, time, zipfile, tempfile, subprocess, urllib.request, urllib.error
from typing import Dict, Any, Optional, List, Tuple

import asyncpg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

# ---------- App ----------
app = FastAPI(title="UnityLab Backend", version="2.5.0-youtube-preview-export")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://clipgenius.netlify.app", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- DB helpers (kept, even if not used by media endpoints) ----------
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

# ---------- Schemas ----------
class JobIn(BaseModel):
    video_url: str
    title: str
    description: str

class PreviewIn(BaseModel):
    video_url: str
    start_time: str  # "HH:MM:SS"
    end_time: str    # "HH:MM:SS"

class ExportIn(BaseModel):
    video_url: str
    clips: List[Dict[str, Any]]  # needs start_time/end_time at minimum

# ---------- OpenRouter helpers ----------
JSON_ONLY_SYSTEM = (
    "Return ONLY valid JSON. No prose, no markdown, no code fences. "
    "If you cannot follow the schema, return {\"clips\":[]}."
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

def _ask_json(model: str, user_prompt: str, schema_hint: str, attempts: int = 2, max_tokens: int = 1200) -> Dict[str, Any]:
    last = ""
    for _ in range(attempts):
        payload = {
            "model": model,
            "messages": [
                {"role":"system","content":JSON_ONLY_SYSTEM + " Schema: " + schema_hint},
                {"role":"user","content":user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "response_format": {"type":"json_object"},
            "seed": 1
        }
        data = _openrouter_request(payload)
        text = data["choices"][0]["message"]["content"]
        last = text
        # lenient parse: first { ... } block
        a, b = text.find("{"), text.rfind("}")
        if a != -1 and b != -1 and b > a:
            try:
                obj = json.loads(text[a:b+1])
                if isinstance(obj, dict) and isinstance(obj.get("clips"), list):
                    return {"clips": obj["clips"], "raw": text[:1000]}
                if isinstance(obj, list):
                    return {"clips": obj, "raw": text[:1000]}
            except Exception:
                pass
        # retry with stricter instruction
        user_prompt = (
            "Your previous reply was not valid JSON that matches this schema. "
            f"Reply again with ONLY JSON: {schema_hint}\n\n"
            "Previous (truncated):\n" + last[:800]
        )
        time.sleep(0.3)
    return {"clips": [], "raw": last[:1000]}

# ---------- Clip utilities ----------
def _hms_to_seconds(hms: str) -> int:
    try:
        hh, mm, ss = (hms or "00:00:00").split(":")
        return int(hh)*3600 + int(mm)*60 + int(ss)
    except Exception:
        return 0

def _normalize_clip(c: Dict[str, Any], idx: int) -> Dict[str, Any]:
    st = c.get("start_time") or "00:00:00"
    et = c.get("end_time") or "00:00:10"
    dur = c.get("duration")
    if not isinstance(dur, int) or dur <= 0:
        dur = max(1, _hms_to_seconds(et) - _hms_to_seconds(st))
    return {
        "id": int(c.get("id") or idx+1),
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

def _enforce_three(best: List[Dict[str,Any]], fallback: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    merged: List[Dict[str,Any]] = []
    for i, c in enumerate(best or []):
        merged.append(_normalize_clip(c, i))
    for c in fallback or []:
        merged.append(_normalize_clip(c, len(merged)))
    # unique by (start,end)
    seen = set()
    uniq = []
    for c in merged:
        key = (c["start_time"], c["end_time"])
        if key in seen: continue
        seen.add(key); uniq.append(c)
    uniq.sort(key=lambda x: float(x.get("viral_score") or 0), reverse=True)
    return uniq[:3] if uniq else []

# ---------- YT / Media helpers ----------
YOUTUBE_RE = re.compile(r"(youtube\.com|youtu\.be)", re.IGNORECASE)

def _have_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg","-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False

def _yt_direct_url(youtube_url: str) -> str:
    """
    Resolve a YouTube watch/shorts URL to a direct media URL using yt-dlp (python lib).
    Requires `yt-dlp` in requirements and ffmpeg in image (already installed).
    """
    try:
        import yt_dlp
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "format": "bv*+ba/best",  # best available
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            # If it's a merged format, yt-dlp may give a 'url' directly; else pick best format URL
            if "url" in info:
                return info["url"]
            if "formats" in info and info["formats"]:
                # pick the last (best) http(s) format that ffmpeg can read
                for f in reversed(info["formats"]):
                    u = f.get("url")
                    if u and u.startswith("http"):
                        return u
    except Exception as e:
        raise RuntimeError(f"yt-dlp failed: {e}")
    raise RuntimeError("Could not resolve YouTube URL")

def _resolve_input_url(video_url: str) -> str:
    """Return a URL/path ffmpeg can read."""
    if YOUTUBE_RE.search(video_url):
        return _yt_direct_url(video_url)
    return video_url  # direct .mp4 or http source

def _cut_clip_to_file(input_url: str, start_hms: str, end_hms: str, out_path: str):
    """
    Use ffmpeg to cut a segment. Re-encode to h264/aac so every player can read it.
    """
    if not _have_ffmpeg():
        raise RuntimeError("ffmpeg is not installed in the container")
    # safer to re-encode instead of stream-copy across arbitrary sources
    cmd = [
        "ffmpeg",
        "-ss", start_hms,
        "-to", end_hms,
        "-i", input_url,
        "-vf", "scale=1280:-2",    # keep aspect, cap width at 1280 (good for preview/export)
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-y", out_path,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode('utf-8', errors='ignore')[:4000]}")

# ---------- Basic routes ----------
@app.get("/")
def root():
    return {"ok": True}

@app.get("/health")
def health():
    return {"ok": True, "ffmpeg": _have_ffmpeg()}

@app.get("/api/ai-status")
def ai_status_check():
    key = os.getenv("OPENROUTER_API_KEY", "")
    return {
        "ai_status": "online",
        "api_key_valid": bool(key and key.startswith("sk-or-")),
        "model_default": os.getenv("FINAL_JUDGE_MODEL", "openai/gpt-5")
    }

# ---------- Analyzer (multi-agent, 3 clips) ----------
@app.post("/api/analyze-video")
def analyze_video(job: JobIn):
    try:
        # 1) candidates (Gemini)
        schema1 = '{"clips":[{"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"topic":"..."}]}'
        g_prompt = f"""Video URL: {job.video_url}
Title: {job.title}
Description: {job.description}

Task: propose candidate viral clip spans.
Return ONLY JSON matching: {schema1}
"""
        g = _ask_json("google/gemini-2.5-pro", g_prompt, schema1)

        # 2) hooks (Claude)
        schema2 = '{"clips":[{"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"topic":"...","hook":"...","reason":"..."}]}'
        c_prompt = f"""Given this:
{json.dumps({"clips": g["clips"]}, ensure_ascii=False)}

For each clip add "hook" and "reason".
Return ONLY JSON matching: {schema2}
"""
        c = _ask_json("anthropic/claude-3.5-sonnet", c_prompt, schema2)

        # 3) judge top 3 (GPT-5 → fallback 4.1)
        schema3 = '{"clips":[{"id":1,"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"viral_score":8.7,"hook":"...","reason":"..."}]}'
        judge_model = os.getenv("FINAL_JUDGE_MODEL", "openai/gpt-5") or "openai/gpt-5"
        j_prompt = f"""Pick the BEST three clips, improve hook/reason, and add "viral_score" (0–10).
Input:
{json.dumps({"clips": c["clips"]}, ensure_ascii=False)}

Return ONLY JSON matching: {schema3}
"""
        try:
            j = _ask_json(judge_model, j_prompt, schema3)
            used_judge = judge_model
            if not j["clips"]:
                raise RuntimeError("empty judge result")
        except Exception:
            j = _ask_json("openai/gpt-4.1", j_prompt, schema3)
            used_judge = "openai/gpt-4.1"

        final3 = _enforce_three(j["clips"], g["clips"])

        # 4) package (titles/captions/platforms/views) with 4.1
        schema4 = '{"clips":[{"id":1,"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"viral_score":8.7,"hook":"...","reason":"...","title":"...","caption":"...","platforms":["TikTok","Instagram"],"predicted_views":45000}]}'
        p_prompt = f"""Enhance these 3 clips with:
- title, caption
- platforms (array)
- predicted_views (integer)

Return ONLY JSON matching: {schema4}
Input:
{json.dumps({"clips": final3}, ensure_ascii=False)}
"""
        p = _ask_json("openai/gpt-4.1", p_prompt, schema4)
        packaged = _enforce_three(p["clips"], final3)
        for i, clip in enumerate(packaged, 1):
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

# ---------- REAL MEDIA: Preview one clip ----------
@app.post("/api/preview")
def preview_clip(body: PreviewIn):
    """
    Returns a single MP4 clip for the given video_url/start/end.
    Works with YouTube and direct URLs. Uses ffmpeg server-side.
    """
    try:
        src = _resolve_input_url(body.video_url)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        tmp.close()
        _cut_clip_to_file(src, body.start_time, body.end_time, tmp.name)
        f = open(tmp.name, "rb")

        def _cleanup():
            try:
                f.close()
                os.unlink(tmp.name)
            except Exception:
                pass

        return StreamingResponse(
            f, media_type="video/mp4",
            headers={"Cache-Control": "no-store"},
            background=None  # Railway will cleanup after process; we close file ourselves
        )
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# ---------- REAL MEDIA: Export many clips as ZIP ----------
@app.post("/api/export")
def export_clips(body: ExportIn):
    """
    Returns a ZIP containing mp4 files for each clip in body.clips.
    """
    try:
        src = _resolve_input_url(body.video_url)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for i, c in enumerate(body.clips, 1):
                st = c.get("start_time") or "00:00:00"
                et = c.get("end_time") or "00:00:10"
                title = c.get("title") or f"clip_{i}"
                # sanitize filename
                safe = re.sub(r"[^a-zA-Z0-9_\- ]", "", title).strip() or f"clip_{i}"
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4"); tmp.close()
                try:
                    _cut_clip_to_file(src, st, et, tmp.name)
                    with open(tmp.name, "rb") as vf:
                        zf.writestr(f"{safe}.mp4", vf.read())
                finally:
                    try: os.unlink(tmp.name)
                    except Exception: pass

        buf.seek(0)
        return StreamingResponse(
            buf, media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="clips.zip"',
                "Cache-Control": "no-store"
            }
        )
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# ---------- Tiny sample ZIP kept ----------
def _sample_srt() -> str:
    return (
        "1\n00:00:00,000 --> 00:00:02,000\nWelcome to UnityLab!\n\n"
        "2\n00:00:02,000 --> 00:00:04,500\nThis is a sample SRT from the backend.\n\n"
    )

@app.get("/api/handoff.zip")
def handoff_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", "UnityLab editor handoff")
        zf.writestr("captions.srt", _sample_srt())
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="handoff.zip"'}
    )
