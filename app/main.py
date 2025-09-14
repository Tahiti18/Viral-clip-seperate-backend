import os, json, io, zipfile, urllib.request, urllib.error, time
from typing import Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import asyncpg
from pydantic import BaseModel, Field

app = FastAPI(title="UnityLab Backend", version="2.0.0-multiagent")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://clipgenius.netlify.app", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- DB helpers ----------
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

# ---------- Utility ----------
def _call_openrouter(model: str, prompt: str, api_key: str, max_tokens: int = 1000) -> str:
    """Call OpenRouter with given model and prompt, return content string."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "https://clipgenius.netlify.app"),
        "X-Title": os.getenv("OPENROUTER_APP_TITLE", "ClipGenius"),
    }
    data = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(data).encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]

# ---------- Endpoints ----------
@app.get("/")
async def root(): return {"ok": True}

@app.get("/health")
async def health(): return {"ok": True}

@app.post("/api/analyze-video")
async def analyze_video(job: JobIn):
    try:
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            return {"success": False, "error": "Missing OPENROUTER_API_KEY"}

        # --- 1. Gemini: propose candidate clips ---
        gemini_prompt = f"""
        Video URL: {job.video_url}
        Title: {job.title}
        Description: {job.description}

        Task: Break down this video into candidate viral clip spans.
        Return JSON with `clips` containing start_time, end_time, duration, and rough topic.
        """
        gemini_out = _call_openrouter("google/gemini-1.5-pro", gemini_prompt, api_key)
        
        # --- 2. Claude: extract emotional hooks ---
        claude_prompt = f"""
        Use this candidate clips JSON:
        {gemini_out}

        For each clip, add fields:
        - hook: a catchy one-liner
        - reason: why it will go viral (emotion, curiosity, humor)
        Return JSON again with updated clips.
        """
        claude_out = _call_openrouter("anthropic/claude-3.5-sonnet", claude_prompt, api_key)

        # --- 3. GPT-5: refine & select top 3 clips ---
        gpt5_prompt = f"""
        Review these annotated clips:
        {claude_out}

        Pick the 3 strongest viral candidates. Improve hooks & reasons for clarity and punch.
        Return JSON with exactly 3 clips, fields: id, start_time, end_time, duration, viral_score, hook, reason.
        """
        gpt5_out = _call_openrouter("openai/gpt-5", gpt5_prompt, api_key)

        # --- 4. GPT-4.1: add titles, captions, platform predictions ---
        gpt41_prompt = f"""
        Take the final 3 clips:
        {gpt5_out}

        For each clip, add:
        - title: optimized viral title
        - caption: short viral caption
        - platforms: best social platforms
        - predicted_views: number estimate
        Return JSON with enhanced clips.
        """
        gpt41_out = _call_openrouter("openai/gpt-4.1", gpt41_prompt, api_key)

        # Try to parse JSON
        try:
            start_idx = gpt41_out.find("{")
            end_idx = gpt41_out.rfind("}") + 1
            parsed = json.loads(gpt41_out[start_idx:end_idx])
            clips = parsed.get("clips", [])
        except Exception as e:
            clips = [{"id": 0, "hook": "Parsing error", "reason": str(e)}]

        return {
            "success": True,
            "video_url": job.video_url,
            "clips": clips,
            "agents": {
                "A": "openai/gpt-5",
                "B": "anthropic/claude-3.5-sonnet",
                "C": "google/gemini-1.5-pro",
                "D": "openai/gpt-4.1"
            }
        }

    except Exception as e:
        return {"success": False, "error": f"Pipeline failed: {str(e)}"}
