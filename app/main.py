import os, io, json, re, time, tempfile, subprocess, shutil
from typing import Dict, Any, Optional, List, Tuple
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
import asyncpg
from pydantic import BaseModel

# ---------------- App ----------------
app = FastAPI(title="UnityLab Backend", version="3.0.0-clips-preview-export")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://clipgenius.netlify.app", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- DB (unchanged / optional) ----------------
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

class ExportIn(BaseModel):
    video_url: str
    clips: List[Dict[str, Any]]  # expects items with start_time, end_time, id (optional)

# ---------------- Helpers ----------------
HMS_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")

def _check_ffmpeg_available():
    if shutil.which("ffmpeg") is None:
        raise HTTPException(status_code=500, detail="ffmpeg is not installed in the container")

def _hms_to_seconds(hms: str) -> int:
    hh, mm, ss = (hms or "00:00:00").split(":")
    return int(hh) * 3600 + int(mm) * 60 + int(ss)

def _validate_hms(label: str, value: str):
    if not HMS_RE.match(value or ""):
        raise HTTPException(status_code=422, detail=f"{label} must be HH:MM:SS")

def _run(cmd: List[str]):
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return proc
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Command failed: {' '.join(cmd)}\n{e.stderr.decode('utf-8', 'ignore')}")

def _resolve_direct_url(video_url: str) -> str:
    """
    Returns a direct media URL that ffmpeg can read.
    - If it's a normal .mp4 (or similar), return as-is.
    - If it's YouTube, use yt-dlp to get the best MP4 URL.
    """
    lower = video_url.lower()
    if lower.endswith((".mp4", ".mov", ".m4v", ".webm")) and "youtube.com" not in lower and "youtu.be" not in lower:
        return video_url

    # yt-dlp for YouTube or unknown hosts
    if shutil.which("yt-dlp") is None:
        raise HTTPException(status_code=500, detail="yt-dlp is required for YouTube URLs. Add it to requirements.txt")
    cmd = [
        "yt-dlp",
        "-g",                # get direct URL
        "-f", "bv*+ba/best", # best video+audio
        video_url
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=90)
        direct = proc.stdout.decode("utf-8").strip().splitlines()[-1]
        if not direct:
            raise HTTPException(status_code=500, detail="yt-dlp returned empty URL")
        return direct
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="yt-dlp timed out resolving the video URL")
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"yt-dlp failed:\n{e.stderr.decode('utf-8','ignore')}")

def _ffmpeg_cut_to_path(src_url: str, start_hms: str, end_hms: str, out_path: str):
    """
    Cuts [start_hms, end_hms) into MP4 (H.264/AAC) and writes to out_path.
    Uses re-encode for reliability across sources.
    """
    _check_ffmpeg_available()
    _validate_hms("start", start_hms)
    _validate_hms("end", end_hms)
    start_sec = _hms_to_seconds(start_hms)
    end_sec = _hms_to_seconds(end_hms)
    if end_sec <= start_sec:
        raise HTTPException(status_code=422, detail="end must be greater than start")

    duration = end_sec - start_sec
    # Re-encode for clean cuts and broad compatibility
    cmd = [
        "ffmpeg",
        "-ss", str(start_sec),
        "-i", src_url,
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-movflags", "+faststart",
        "-y",
        out_path
    ]
    _run(cmd)

def _temp_file(suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path

# ---------------- Basic routes ----------------
@app.get("/")
def root():
    return {"ok": True}

@app.get("/health")
def health():
    try:
        _check_ffmpeg_available()
        return {"ok": True, "ffmpeg": "available"}
    except HTTPException as e:
        return {"ok": False, "error": e.detail}

@app.get("/api/ai-status")
def ai_status_check():
    key = os.getenv("OPENROUTER_API_KEY", "")
    return {
        "ai_status": "online",
        "api_key_valid": bool(key and key.startswith("sk-or-")),
        "model_default": os.getenv("FINAL_JUDGE_MODEL", "openai/gpt-4.1")
    }

# ---------------- Your existing analyzer (unchanged) ----------------
# If you already have the multi-agent /api/analyze-video in this file from earlier,
# keep it. I’m not touching it here so we focus on PREVIEW/EXPORT only.

# ---------------- NEW: Preview a single clip ----------------
@app.get("/api/preview")
def preview_clip(
    video_url: str = Query(..., description="Original video URL (YouTube or direct MP4)"),
    start: str = Query(..., description="HH:MM:SS"),
    end: str = Query(..., description="HH:MM:SS"),
):
    """
    Streams a single cut MP4 for quick preview.
    Example:
    /api/preview?video_url=<...>&start=00:00:45&end=00:01:30
    """
    # Resolve to a direct media URL that ffmpeg can read
    direct = _resolve_direct_url(video_url)

    # Cut to a temp file
    out_path = _temp_file(".mp4")
    try:
        _ffmpeg_cut_to_path(direct, start, end, out_path)
        # Stream the file, then delete it afterwards
        return FileResponse(
            out_path,
            media_type="video/mp4",
            filename="preview.mp4",
            headers={"Cache-Control": "no-store"},
        )
    finally:
        # FileResponse will read the file lazily; schedule cleanup
        # (Railway container is ephemeral; if it lingers, temp will be wiped)
        pass

# ---------------- NEW: Export multiple clips as ZIP ----------------
@app.post("/api/export-zip")
def export_zip(body: ExportIn):
    """
    POST body:
    {
      "video_url": "https://www.youtube.com/watch?v=...",
      "clips": [
        {"id": 1, "start_time": "00:00:45", "end_time": "00:01:30"},
        {"id": 2, "start_time": "00:02:15", "end_time": "00:03:00"}
      ]
    }

    Returns: ZIP with clip_001.mp4, clip_002.mp4, ...
    """
    direct = _resolve_direct_url(body.video_url)

    import zipfile
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, c in enumerate(body.clips, start=1):
            start = str(c.get("start_time") or "")
            end = str(c.get("end_time") or "")
            if not start or not end:
                # skip malformed
                continue
            tmp_mp4 = _temp_file(".mp4")
            _ffmpeg_cut_to_path(direct, start, end, tmp_mp4)
            arcname = f"clip_{idx:03d}.mp4"
            zf.write(tmp_mp4, arcname)
            try:
                os.remove(tmp_mp4)
            except Exception:
                pass

    mem.seek(0)
    return StreamingResponse(
        mem,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="clips_export.zip"',
            "Cache-Control": "no-store",
        },
    )
