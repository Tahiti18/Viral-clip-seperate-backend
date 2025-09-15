import os, random
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
from fastapi.middleware.cors import CORSMiddleware

# ---------- Models ----------
class Engagement(BaseModel):
    views: int
    likes: int
    comments: int
    shares: int

class Clip(BaseModel):
    id: int
    start_time: str
    end_time: str
    duration: int
    viral_score: float
    hook: str
    reason: str
    platforms: List[str]
    emotional_trigger: Optional[str] = None
    target_audience: Optional[str] = None
    engagement_prediction: Optional[Engagement] = None

class AnalyzeRequest(BaseModel):
    video_url: HttpUrl
    title: Optional[str] = ""
    description: Optional[str] = ""

class AnalyzeResponse(BaseModel):
    success: bool = True
    source: str = "ai"
    video_url: HttpUrl
    clips: List[Clip]
    processing_time: str = "30-45 seconds"
    ai_powered: bool = True
    confidence_score: float = 0.9
    analysis: dict

# ---------- App ----------
app = FastAPI(title="VideoStudio Pro API", version="1.0.0")

# CORS: open for now (tighten later to your domain)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],   # GET, POST, OPTIONS, etc.
    allow_headers=["*"],   # Content-Type, Authorization, etc.
)

# ---------- Helpers ----------
def ts(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    return f"00:{m:02d}:{s:02d}"

def generate_mock_clips(kind: str = "general") -> List[Clip]:
    templates = {
        "podcast": [
            ("Mind-blowing revelation that changes everything", 9.2),
            ("The question everyone's afraid to ask", 8.7),
            ("Plot twist nobody saw coming", 8.9),
        ],
        "tutorial": [
            ("The secret technique pros don't want you to know", 9.1),
            ("Common mistake that costs thousands", 8.8),
            ("60-second hack that changes everything", 9.3),
        ],
        "business": [
            ("The strategy that built a $10M company", 8.9),
            ("Why 99% of businesses fail at this", 8.6),
            ("The investment that returns 10x", 8.8),
        ],
        "general": [
            ("This changes everything you thought you knew", 8.5),
            ("The truth they don't want you to discover", 8.7),
            ("60 seconds that will blow your mind", 9.0),
        ],
    }
    hooks = templates.get(kind, templates["general"])
    clips: List[Clip] = []
    for i, (hook, score) in enumerate(hooks, start=1):
        start = 90 + (i - 1) * 120
        end = start + 60
        clips.append(
            Clip(
                id=i,
                start_time=ts(start),
                end_time=ts(end),
                duration=60,
                viral_score=score,
                hook=hook,
                reason="Auto-selected moment with high engagement likelihood",
                platforms=["TikTok", "Instagram", "YouTube Shorts"],
                emotional_trigger="curiosity",
                target_audience="general",
                engagement_prediction=Engagement(
                    views=int(score * 5000) + random.randint(0, 10000),
                    likes=random.randint(600, 2400),
                    comments=random.randint(50, 300),
                    shares=random.randint(80, 400),
                ),
            )
        )
    return clips

def detect_kind(title: str, description: str) -> str:
    c = (title + " " + description).lower()
    if any(w in c for w in ["podcast", "interview"]): return "podcast"
    if any(w in c for w in ["tutorial", "how to"]):   return "tutorial"
    if any(w in c for w in ["business","entrepreneur"]): return "business"
    return "general"

# ---------- Routes (match the front end) ----------
@app.get("/health")
@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/ai-status")
def ai_status():
    # MUST be a simple 200 with no required params so your UI stays green
    return {"ai_status": "online"}

@app.post("/api/analyze-video", response_model=AnalyzeResponse)
def analyze_video(req: AnalyzeRequest):
    kind = detect_kind(req.title or "", req.description or "")
    clips = generate_mock_clips(kind)
    return AnalyzeResponse(
        video_url=req.video_url,
        clips=clips,
        analysis={
            "video_type": kind,
            "viral_potential": round(random.uniform(7.0, 9.8), 2),
            "recommended_platforms": ["TikTok", "Instagram", "YouTube Shorts"],
            "best_posting_times": ["6-9 PM EST", "9-11 AM EST"],
        },
    )

@app.get("/api/clips/{clip_id}/preview")
def clip_preview(clip_id: int):
    # Return a preview URL OR stream the bytes; UI supports both.
    return {"preview_url": "https://www.w3schools.com/html/mov_bbb.mp4"}

@app.get("/api/clips/{clip_id}/export")
def clip_export(clip_id: int):
    # Return a downloadable URL OR bytes; UI supports both.
    return {"download_url": "https://www.w3schools.com/html/mov_bbb.mp4"}

# Root helps quick sanity checks
@app.get("/")
def root():
    return {"ok": True, "service": "VideoStudio Pro API"}
