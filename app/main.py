import os, json, io, zipfile, urllib.request, urllib.error, time, re, hashlib, subprocess, shlex, pathlib
from typing import Dict, Any, Optional, List, Tuple
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
import asyncpg
from pydantic import BaseModel

app = FastAPI(title="UnityLab Backend", version="2.5.0-preview-export")

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
                    return {"clips": obj["clips"], "raw": text[:1200]}
                if isinstance(obj, list):
                    return {"clips": obj, "raw": text[:1200]}
            except Exception:
                pass

        user_prompt = (
            "The previous response was NOT valid JSON or missed required keys.\n"
            "Respond again with ONLY valid JSON that matches this schema exactly: "
            f"{schema_hint}\n\n"
            "Your previous output (truncated):\n" + last_text[:800]
        )
        time.sleep(0.4)
    return {"clips": [], "raw": last_text[:1200]}

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
        "predicted_views": int(c.get("predicted_views") or c.get("views") or 0),
        "predicted_likes": int(c.get("predicted_likes") or c.get("likes") or 0),
        "predicted_shares": int(c.get("predicted_shares") or c.get("shares") or 0),
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

# ---------------- Simple status routes ----------------
@app.get("/")
async def root():
    return {"ok": True}

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/api/ai-status")
def ai_status_check():
    key = os.getenv("OPENROUTER_API_KEY", "")
    return {
        "ai_status": "online",
        "api_key_valid": bool(key and key.startswith("sk-or-")),
        "model_default": os.getenv("FINAL_JUDGE_MODEL", "openai/gpt-5")
    }

# ---------------- Multi-agent analysis ----------------
@app.post("/api/analyze-video")
async def analyze_video(job: JobIn):
    """
    A -> Google Gemini 2.5 Pro: propose candidate spans
    B -> Claude 3.5 Sonnet: add hooks & reasons
    C -> Judge (GPT-5 or fallback 4.1): select/refine top 3 & score
    D -> GPT-4.1: add title, caption, platforms, and required metrics
    """
    try:
        # 1) A
        schema1 = '{"clips":[{"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"topic":"..."}]}'
        gemini_prompt = f"""Video URL: {job.video_url}
Title: {job.title}
Description: {job.description}

Task: propose candidate viral clip spans (30–75 sec each).
Return ONLY JSON matching: {schema1}
"""
        g = _ask_json("google/gemini-2.5-pro", gemini_prompt, schema1)

        # 2) B
        schema2 = '{"clips":[{"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"topic":"...","hook":"...","reason":"..."}]}'
        claude_prompt = f"""Given this:
{json.dumps({"clips": g["clips"]}, ensure_ascii=False)}

For each clip, add "hook" and "reason".
Return ONLY JSON matching: {schema2}
"""
        c = _ask_json("anthropic/claude-3.5-sonnet", claude_prompt, schema2)

        # 3) C
        schema3 = '{"clips":[{"id":1,"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"viral_score":8.7,"hook":"...","reason":"..."}]}'
        judge_model = os.getenv("FINAL_JUDGE_MODEL", "openai/gpt-5") or "openai/gpt-5"
        judge_prompt = f"""Review these clips and return the BEST three with improved "hook" and "reason" plus a "viral_score" (0–10).
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

        final3 = _enforce_three(j["clips"], [c["clips"], g["clips"]])

        # 4) D (strict metrics)
        schema4 = (
            '{"clips":[{'
            '"id":1,'
            '"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,'
            '"viral_score":8.7,'
            '"hook":"...","reason":"...",'
            '"title":"...","caption":"...",'
            '"platforms":["TikTok","Instagram"],'
            '"predicted_views":45000,'
            '"predicted_likes":2200,'
            '"predicted_shares":300'
            '}]}'
        )
        pack_prompt = f"""Enhance these with:
- "title" (viral title)
- "caption" (short viral caption)
- "platforms" (array)
- REQUIRED integers: "predicted_views", "predicted_likes", "predicted_shares"
Return ONLY JSON matching EXACTLY this schema (keys & types must exist): {schema4}

Input:
{json.dumps({"clips": final3}, ensure_ascii=False)}
"""
        p = _ask_json("openai/gpt-4.1", pack_prompt, schema4)
        if not p["clips"] or not all(set(("predicted_views","predicted_likes","predicted_shares")).issubset(set(c.keys())) for c in p["clips"]):
            strict_prompt = pack_prompt + "\nALL THREE metrics are REQUIRED and must be integers on every clip."
            p = _ask_json("openai/gpt-4.1", strict_prompt, schema4)

        packaged = _enforce_three(p["clips"] or final3, [final3])
        for i, clip in enumerate(packaged, start=1): clip["id"] = i

        return {
            "success": True,
            "video_url": job.video_url,
            "clips": packaged,
            "agents": {"A":"google/gemini-2.5-pro","B":"anthropic/claude-3.5-sonnet","C":used_judge,"D":"openai/gpt-4.1"},
        }

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
        return {"success": False, "error": f"HTTP Error {e.code}", "details": detail}
    except Exception as e:
        return {"success": False, "error": f"Pipeline failed: {str(e)}"}

# ---------------- Real preview generation ----------------
TMP_DIR = "/tmp/clipgenius"
pathlib.Path(TMP_DIR).mkdir(parents=True, exist_ok=True)

def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]

def _ensure_binary(name: str) -> None:
    if not shutil.which(name):
        raise RuntimeError(f"Required binary not found: {name}")

def _download_source(video_url: str) -> str:
    """
    Returns a local mp4 path for the source video.
    - Direct .mp4: downloaded via urllib (once & cached)
    - YouTube/others: via yt-dlp (best mp4), cached by URL hash
    """
    os.makedirs(TMP_DIR, exist_ok=True)
    h = _hash(video_url)
    dst = os.path.join(TMP_DIR, f"{h}.mp4")
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return dst

    # If it's a direct mp4, just fetch
    if video_url.lower().endswith(".mp4"):
        req = urllib.request.Request(video_url, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as f:
            f.write(r.read())
        return dst

    # Otherwise, use yt-dlp
    import yt_dlp  # ensure in requirements.txt
    ydl_opts = {
        "outtmpl": os.path.join(TMP_DIR, f"{h}.%(ext)s"),
        "format": "mp4/best",
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        downloaded = ydl.prepare_filename(info)
    # Normalize extension to .mp4 if needed
    if downloaded.endswith(".mp4"):
        os.rename(downloaded, dst)
        return dst
    else:
        # Convert to mp4
        tmp_mp4 = os.path.join(TMP_DIR, f"{h}.mp4")
        _ffmpeg_copy(downloaded, tmp_mp4)
        return tmp_mp4

def _ffmpeg_trim(src: str, start_s: int, end_s: int, out_path: str) -> None:
    """
    Trim clip [start_s, end_s) with re-encode for browser safety.
    """
    duration = max(1, end_s - start_s)
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_s),
        "-i", src,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def _ffmpeg_copy(src: str, out_path: str) -> None:
    cmd = ["ffmpeg", "-y", "-i", src, "-c", "copy", out_path]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

@app.get("/api/preview")
def preview_clip(
    video_url: str = Query(..., description="YouTube URL or direct MP4"),
    start_time: str = Query(..., description="HH:MM:SS"),
    end_time: str = Query(..., description="HH:MM:SS"),
):
    """
    Stream a real clipped MP4 for instant preview in the UI.
    Example:
      /api/preview?video_url=...&start_time=00:01:15&end_time=00:02:00
    """
    try:
        start_s = _hms_to_seconds(start_time)
        end_s = _hms_to_seconds(end_time)
        if end_s <= start_s:
            return JSONResponse({"ok": False, "error": "end_time must be > start_time"}, status_code=400)

        src = _download_source(video_url)
        h = _hash(f"{src}-{start_s}-{end_s}")
        out_path = os.path.join(TMP_DIR, f"clip-{h}.mp4")
        if not os.path.exists(out_path):
            _ffmpeg_trim(src, start_s, end_s, out_path)

        headers = {
            "Cache-Control": "no-store",
            "Content-Disposition": 'inline; filename="preview.mp4"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        }
        return FileResponse(out_path, media_type="video/mp4", headers=headers)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
        return JSONResponse({"ok": False, "error": f"HTTP Error {e.code}", "details": detail}, status_code=502)
    except subprocess.CalledProcessError as e:
        return JSONResponse({"ok": False, "error": "ffmpeg failed", "details": e.stderr.decode("utf-8", "ignore")[:800]}, status_code=500)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

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
        headers={"Content-Disposition": 'attachment; filename="handoff.zip"', "Cache-Control": "no-store"},
    )
