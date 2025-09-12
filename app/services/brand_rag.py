import re
from typing import List, Dict, Tuple
from collections import Counter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models_brand import Brand, BrandDoc, CompliancePack, Platform

TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
def _tokens(text: str) -> List[str]: return [t.lower() for t in TOKEN_RE.findall(text or "")]
def _score(query: str, content: str) -> float:
    q = Counter(_tokens(query)); d = Counter(_tokens(content))
    if not q or not d: return 0.0
    dot = sum(q[t]*d[t] for t in q)
    nq = sum(v*v for v in q.values()) ** 0.5; nd = sum(v*v for v in d.values()) ** 0.5
    return float(dot) / float(nq*nd) if nq and nd else 0.0

async def rag_retrieve(session: AsyncSession, brand_id: str, query: str, k: int = 5) -> List[Dict]:
    docs = (await session.execute(select(BrandDoc).where(BrandDoc.brand_id==brand_id))).scalars().all()
    scored = sorted([{"docId": d.id, "title": d.title, "score": _score(query, d.content), "snippet": d.content[:400]} for d in docs], key=lambda x: x["score"], reverse=True)
    return scored[:k]

def _collect_text_blobs(payload: Dict):
    blobs = []; tr = (payload or {}).get("transcript") or ""
    if tr: blobs.append(("transcript", tr))
    for i, c in enumerate((payload or {}).get("captions") or []):
        if c: blobs.append((f"caption[{i}]", c))
    for i, o in enumerate((payload or {}).get("overlays") or []):
        if o: blobs.append((f"overlay[{i}]", o))
    return blobs

def _find_spans(text: str, needle: str):
    spans = []
    for m in re.finditer(re.escape(needle), text, flags=re.IGNORECASE):
        spans.append({"start": m.start(), "end": m.end(), "match": m.group(0)})
    return spans

async def run_compliance_scan(session: AsyncSession, platform: Platform, payload: Dict) -> Dict:
    violations, suggestions = [], []; blobs = _collect_text_blobs(payload)
    packs = (await session.execute(select(CompliancePack).where((CompliancePack.platform==platform) | (CompliancePack.platform==Platform.generic)))).scalars().all()
    for pack in packs:
        rules = pack.rules or {}
        regex_bans = rules.get("regexBans") or []
        phrase_bans = rules.get("phraseBans") or []
        claims = rules.get("claims") or {}
        disclosures = rules.get("disclosures") or []
        for kind, text in blobs:
            for rx in regex_bans:
                for m in re.finditer(rx, text, flags=re.IGNORECASE):
                    violations.append({"type":"RegexBan","severity":"high","pattern":rx,"where":kind,"spans":[{"start":m.start(),"end":m.end(),"match":m.group(0)}],"suggest":"Remove/alter."})
            for ph in phrase_bans:
                for sp in _find_spans(text, ph):
                    violations.append({"type":"PhraseBan","severity":"high","phrase":ph,"where":kind,"spans":[sp],"suggest":"Replace."})
            for ph in (claims.get("forbidden") or []):
                for sp in _find_spans(text, ph):
                    violations.append({"type":"ForbiddenClaim","severity":"high","phrase":ph,"where":kind,"spans":[sp],"suggest":"Qualify or remove."})
            need_kw = (disclosures and disclosures[0].get("keywords")) or []
            disclosure_text = (disclosures and disclosures[0].get("text")) or ""
            if need_kw and disclosure_text:
                text_all = " ".join(t for _, t in blobs).lower()
                if any(kw.lower() in text_all for kw in need_kw) and disclosure_text.lower() not in text_all:
                    violations.append({"type":"MissingDisclosure","severity":"medium","requires": disclosure_text,"where":"global","suggest": f"Add: “{disclosure_text}”.")})
    score = max(0, 100 - (len([v for v in violations if v["severity"]=="high"])*25 + len(violations)*5))
    return {"violations": violations, "suggestions": suggestions, "score": score}
