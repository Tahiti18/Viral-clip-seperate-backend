import os, io, json, re, time, uuid, shutil, urllib.request, subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from pydantic import BaseModel

# --------------------------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------------------------
app = FastAPI(title="UnityLab Backend", version="3.0.0-multiagent-clips")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://clipgenius.netlify.app",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple in-memory job registry (ephemeral – survives while container is up)
app.state.jobs: Dict[str, Dict[str, Any]] = {}

# --------------------------------------------------------------------------------------
# Models / input schema
# --------------------------------------------------------------------------------------
class JobIn(BaseModel):
    video_url: str
    title: str
    description: str

# --------------------------------------------------------------------------------------
# OpenRouter helpers (JSON forced)
# --------------------------------------------------------------------------------------
JSON_ONLY_SYSTEM = (
    "Return ONLY valid JSON. No prose, no markdown, no code fences. "
    "If you cannot, return {\"clips\":[]}."
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

def _ask_json(model: str, user_prompt: str, schema_hint: str, attempts: int = 2, max_tokens: int = 1200) -> Dict[str, Any]:
    """
    Call a model and try hard to get a dict with a 'clips' list.
    """
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

# --------------------------------------------------------------------------------------
# Clip utilities + ffmpeg
# --------------------------------------------------------------------------------------
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
    if not isinstance(dur, int) or dur <= 0:
        dur = max(1, _hms_to_seconds(et) - _hms_to_seconds(st))
    return {
        "id": int(c.get("id") or (idx + 1)),
        "start_time": st,
        "end_time": et,
        "duration": dur,
        "viral_score": float(c.get("viral_score") or 0.0),
        "hook": c.get("hook") or "",
        "reason": c.get("reason") or "",
        "title": c.get("title") or "",
        "caption": c.get("caption") or "",
        "platforms": c.get("platforms") or [],
        "predicted_views": int(c.get("predicted_views") or 0),
    }

def _dedupe_by_times(clips: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
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
    return merged[:3] if len(merged) >= 3 else merged

def _ensure_tmp_dir(job_id: str) -> Path:
    p = Path("/tmp") / f"job_{job_id}"
    p.mkdir(parents=True, exist_ok=True)
    return p

def _download_video(job_id: str, url: str) -> Path:
    """
    Download the source video to /tmp/job_<id>/source.mp4
    """
    tmpdir = _ensure_tmp_dir(job_id)
    src = tmpdir / "source.mp4"
    # simple download – works for direct .mp4 links
    urllib.request.urlretrieve(url, src)
    return src

def _cut_clip_ffmpeg(src: Path, start_hms: str, duration: int, out_path: Path):
    """
    Use ffmpeg to cut segment to out_path.
    """
    # Example: ffmpeg -ss 00:00:10 -t 45 -i source.mp4 -c copy -y clip1.mp4
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-ss", start_hms,
        "-t", str(max(1, duration)),
        "-i", str(src),
        "-c", "copy",
        "-y",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)

# --------------------------------------------------------------------------------------
# Health + basic routes
# --------------------------------------------------------------------------------------
@app.get("/")
def root():
    return {"ok": True}

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/api/ffmpeg-check")
def ffmpeg_check():
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        return {"ok": True, "version": out.stdout.splitlines()[0] if out.returncode == 0 else "unknown"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/ai-status")
def ai_status_check():
    key = os.getenv("OPENROUTER_API_KEY", "")
    return {
        "ai_status": "online",
        "api_key_valid": bool(key and key.startswith("sk-or-")),
        "model_default": os.getenv("FINAL_JUDGE_MODEL", "openai/gpt-5")
    }

# --------------------------------------------------------------------------------------
# MAIN: multi-agent analysis + real previews & export
# --------------------------------------------------------------------------------------
@app.post("/api/analyze-video")
def analyze_video(job: JobIn):
    """
    A -> Google Gemini 2.5 Pro: propose candidate spans
    B -> Claude 3.5 Sonnet: enrich hooks & reasons
    C -> OpenAI Judge (GPT-5, fallback GPT-4.1): select/refine top 3 & score
    D -> GPT-4.1: package with titles, captions, platforms, predicted views

    Then:
      - Download source video to /tmp
      - ffmpeg-cut up to 3 clips
      - Return preview URLs and an export ZIP URL
    """
    try:
        # ---------- 1) A: candidate spans ----------
        schema1 = '{"clips":[{"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"topic":"..."}]}'
        gemini_prompt = f"""Video URL: {job.video_url}
Title: {job.title}
Description: {job.description}

Task: propose candidate viral clip spans (~30-45s).
Return ONLY JSON matching: {schema1}
"""
        g = _ask_json("google/gemini-2.5-pro", gemini_prompt, schema1)

        # ---------- 2) B: hooks & reasons ----------
        schema2 = '{"clips":[{"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"topic":"...","hook":"...","reason":"..."}]}'
        claude_prompt = f"""Given this:
{json.dumps({"clips": g["clips"]}, ensure_ascii=False)}

For each clip, add "hook" and "reason".
Return ONLY JSON matching: {schema2}
"""
        c = _ask_json("anthropic/claude-3.5-sonnet", claude_prompt, schema2)

        # ---------- 3) C: judge & refine ----------
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

        # Enforce up to 3 BEFORE packaging
        final3 = _enforce_three(j["clips"], [c["clips"], g["clips"]])
        if not final3:
            return {"success": True, "video_url": job.video_url, "clips": [], "message": "No viable clips suggested"}

        # ---------- 4) D: package ----------
        schema4 = '{"clips":[{"id":1,"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"viral_score":8.7,"hook":"...","reason":"...","title":"...","caption":"...","platforms":["TikTok","Instagram"],"predicted_views":45000}]}'
        pack_prompt = f"""Enhance these with title, caption, platforms, predicted_views.
Input:
{json.dumps({"clips": final3}, ensure_ascii=False)}

Return ONLY JSON matching: {schema4}
"""
        p = _ask_json("openai/gpt-4.1", pack_prompt, schema4)
        packaged = _enforce_three(p["clips"], [final3])

        # Reindex IDs cleanly
        for i, clip in enumerate(packaged, start=1):
            clip["id"] = i

        # ---------- Download + cut previews ----------
        job_id = uuid.uuid4().hex[:12]
        job_dir = _ensure_tmp_dir(job_id)
        source_path = _download_video(job_id, job.video_url)

        preview_clips = []
        for clip in packaged:
            start = clip["start_time"]
            dur = int(clip["duration"]) if clip.get("duration") else max(1, _hms_to_seconds(clip["end_time"]) - _hms_to_seconds(start))
            out_file = job_dir / f"clip_{clip['id']:02d}.mp4"
            try:
                _cut_clip_ffmpeg(source_path, start, dur, out_file)
                preview_url = f"/api/preview/{job_id}/{clip['id']:02d}.mp4"
            except subprocess.CalledProcessError as e:
                # If cutting fails, skip preview but keep the analysis
                preview_url = None

            preview_clips.append({**clip, "preview_url": preview_url})

        # Save job registry for preview/export endpoints
        app.state.jobs[job_id] = {
            "source": str(source_path),
            "dir": str(job_dir),
            "clips": [str(job_dir / f"clip_{c['id']:02d}.mp4") for c in packaged],
            "meta": preview_clips,
        }

        export_url = f"/api/export/{job_id}.zip"

        return {
            "success": True,
            "video_url": job.video_url,
            "job_id": job_id,
            "clips": preview_clips,
            "export_url": export_url,
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

# --------------------------------------------------------------------------------------
# Preview & Export endpoints
# --------------------------------------------------------------------------------------
@app.get("/api/preview/{job_id}/{index}.mp4")
def preview_clip(job_id: str, index: str):
    job = app.state.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    try:
        idx = int(index)
    except Exception:
        raise HTTPException(404, "Invalid index")
    clips = job.get("clips", [])
    if idx < 1 or idx > len(clips):
        raise HTTPException(404, "Clip not found")
    path = Path(clips[idx - 1])
    if not path.exists():
        raise HTTPException(404, "Clip file missing")
    return FileResponse(path, media_type="video/mp4", filename=path.name)

@app.get("/api/export/{job_id}.zip")
def export_zip(job_id: str):
    job = app.state.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job_dir = Path(job["dir"])
    zip_path = job_dir / "clips_export.zip"
    # build zip fresh each time
    if zip_path.exists():
        zip_path.unlink(missing_ok=True)
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", job_dir, )
    return FileResponse(zip_path, media_type="application/zip", filename=f"{job_id}_clips.zip")
