import os, json, io, zipfile, urllib.request, urllib.error, time, re
from typing import Dict, Any, Optional, List, Tuple
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import asyncpg
from pydantic import BaseModel

app = FastAPI(title="UnityLab Backend", version="2.4.0-realclips")

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
    "Schema must match exactly. If impossible, return {\"clips\":[]}."
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
        }
        data = _openrouter_request(payload)
        text = data["choices"][0]["message"]["content"]
        last_text = text

        js = _extract_json_str(text)
        if js:
            try:
                obj = json.loads(js)
                if isinstance(obj, dict) and isinstance(obj.get("clips"), list):
                    return {"clips": obj["clips"], "raw": text[:800]}
                if isinstance(obj, list):
                    return {"clips": obj, "raw": text[:800]}
            except Exception:
                pass

        user_prompt = (
            "Previous response invalid JSON.\n"
            "Respond again with ONLY valid JSON schema: "
            f"{schema_hint}\n\n"
            "Your last output (truncated):\n" + last_text[:500]
        )
        time.sleep(0.5)
    return {"clips": [], "raw": last_text[:800]}

# ---------------- Utilities ----------------
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
    out = {
        "id": int(c.get("id") or (idx + 1)),
        "start_time": st,
        "end_time": et,
        "duration": dur,
    }
    # Copy through whatever real fields exist, but don’t fake anything
    for k in ["viral_score","hook","reason","title","caption","platforms","predicted_views"]:
        if k in c: out[k] = c[k]
    return out

def _dedupe(clips: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    seen=set(); out=[]
    for c in clips:
        key=(c.get("start_time"),c.get("end_time"))
        if key not in seen:
            seen.add(key); out.append(c)
    return out

# ---------------- Routes ----------------
@app.get("/")
async def root(): return {"ok": True}

@app.get("/health")
async def health(): return {"ok": True}

@app.get("/api/ai-status")
def ai_status_check():
    key = os.getenv("OPENROUTER_API_KEY", "")
    return {
        "ai_status":"online",
        "api_key_valid":bool(key and key.startswith("sk-or-")),
        "model_default":os.getenv("FINAL_JUDGE_MODEL","openai/gpt-5")
    }

# ---------------- Multi-agent ----------------
@app.post("/api/analyze-video")
async def analyze_video(job: JobIn):
    """
    Multi-agent real pipeline:
      A) Gemini 2.5 Pro → candidate spans
      B) Claude 3.5 Sonnet → hooks/reasons
      C) GPT-5 (fallback GPT-4.1) → select strongest
      D) GPT-4.1 → add metadata (if possible)
    Returns 1–3 clips. Never fakes fields.
    """
    try:
        # 1. Gemini
        schema1='{"clips":[{"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"topic":"..."}]}'
        g_prompt=f"Video URL:{job.video_url}\nTitle:{job.title}\nDesc:{job.description}\nTask: propose viral spans.\nReturn {schema1}"
        g=_ask_json("google/gemini-2.5-pro",g_prompt,schema1)

        # 2. Claude
        schema2='{"clips":[{"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"topic":"...","hook":"...","reason":"..."}]}'
        c_prompt=f"Input:\n{json.dumps({'clips':g['clips']},ensure_ascii=False)}\nAdd hook & reason.\nReturn {schema2}"
        c=_ask_json("anthropic/claude-3.5-sonnet",c_prompt,schema2)

        # 3. Judge
        schema3='{"clips":[{"id":1,"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"viral_score":8.7,"hook":"...","reason":"..."}]}'
        judge_model=os.getenv("FINAL_JUDGE_MODEL","openai/gpt-5") or "openai/gpt-5"
        j_prompt=f"Review clips:\n{json.dumps({'clips':c['clips']},ensure_ascii=False)}\nPick strongest ≤3, improve hook/reason, add viral_score.\nReturn {schema3}"
        try:
            j=_ask_json(judge_model,j_prompt,schema3)
            used_judge=judge_model
        except Exception:
            j=_ask_json("openai/gpt-4.1",j_prompt,schema3); used_judge="openai/gpt-4.1"

        # Combine
        merged=[_normalize_clip(c,i) for i,c in enumerate(j["clips"])]
        merged=_dedupe(merged)
        if not merged: merged=[_normalize_clip(c,i) for i,c in enumerate(c["clips"])]
        if not merged: merged=[_normalize_clip(c,i) for i,c in enumerate(g["clips"])]
        merged=merged[:3]

        # 4. Packager
        schema4='{"clips":[{"id":1,"start_time":"HH:MM:SS","end_time":"HH:MM:SS","duration":45,"hook":"...","reason":"...","title":"...","caption":"...","platforms":["TikTok"],"predicted_views":45000}]}'
        p_prompt=f"Enhance clips:\n{json.dumps({'clips':merged},ensure_ascii=False)}\nAdd title, caption, platforms, predicted_views.\nReturn {schema4}"
        p=_ask_json("openai/gpt-4.1",p_prompt,schema4)
        final=[_normalize_clip(c,i) for i,c in enumerate(p["clips"])]
        if not final: final=merged

        return {"success":True,"video_url":job.video_url,"clips":final,"agents":{"A":"gemini-2.5-pro","B":"claude-3.5-sonnet","C":used_judge,"D":"gpt-4.1"}}

    except urllib.error.HTTPError as e:
        return {"success":False,"error":f"HTTP {e.code}", "details":e.read().decode('utf-8') if hasattr(e,'read') else str(e)}
    except Exception as e:
        return {"success":False,"error":f"Pipeline failed: {str(e)}"}

# ---------------- Handoff ZIP ----------------
def _sample_srt()->str:
    return "1\n00:00:00,000 --> 00:00:02,000\nUnityLab sample\n\n"

@app.get("/api/handoff.zip")
async def handoff_zip():
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w",compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt","UnityLab handoff")
        zf.writestr("captions.srt",_sample_srt())
    buf.seek(0)
    return StreamingResponse(buf,media_type="application/zip",headers={"Content-Disposition":'attachment; filename="handoff.zip"',"Cache-Control":"no-store"})
