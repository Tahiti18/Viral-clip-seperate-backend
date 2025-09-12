from dataclasses import dataclass
from typing import Literal, List, Dict

TargetMetric = Literal["CTR","Watch3s","Watch30s"]

@dataclass
class VariantPosterior:
    variant_id: str; impressions: int; successes: int; alpha: float; beta: float
    @property
    def mean(self) -> float:
        return (self.alpha) / (self.alpha + self.beta)

def recommend_allocations(posteriors: List[VariantPosterior], min_share: float = 0.10) -> Dict[str, float]:
    weights = [max(v.mean, 1e-6) for v in posteriors]; total = sum(weights)
    alloc = [w/total for w in weights]; alloc = [max(a, min_share) for a in alloc]
    norm = sum(alloc); alloc = [a/norm for a in alloc]
    return {p.variant_id: alloc[i] for i,p in enumerate(posteriors)}

def should_promote(posteriors: List[VariantPosterior], min_impressions: int, runtime_ok: bool):
    total_impr = sum(v.impressions for v in posteriors)
    if total_impr < min_impressions or not runtime_ok: return (False, None, None)
    winner = max(posteriors, key=lambda v: v.mean)
    return (True, winner.variant_id, winner.mean)
