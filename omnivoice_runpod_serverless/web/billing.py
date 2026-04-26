from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional


def _money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


@dataclass
class CostEstimate:
    pricing_version: str
    input_chars: int
    audio_duration_sec: Decimal
    compute_seconds: Decimal
    gpu_type: str
    runpod_cost_usd: Decimal
    storage_cost_usd: Decimal
    bandwidth_cost_usd: Decimal
    service_fee_usd: Decimal
    total_cost_usd: Decimal
    currency: str
    cost_breakdown_json: str


def estimate_cost(*, text: str, duration_sec: float | None, compute_seconds: float, gpu_type: Optional[str] = None) -> CostEstimate:
    # Model B: Pricing based on Actual Compute/GPU Seconds (Internal Usage Tracking)
    rate_per_compute_sec = float(os.getenv("PRICING_RATE_PER_COMPUTE_SEC", "0.00025")) # Example: $0.90/hr
    fixed_fee = float(os.getenv("PRICING_FIXED_FEE", "0.0001"))
    pricing_version = os.getenv("PRICING_VERSION", "v3-compute-seconds")
    
    actual_compute = max(float(compute_seconds or 0.0), 0.0)
    actual_audio_dur = max(float(duration_sec or 0.0), 0.0)
    
    # Calculate total cost based on GPU execution time
    base_cost = _money(actual_compute * rate_per_compute_sec)
    total = _money(float(base_cost) + fixed_fee)
    
    breakdown: Dict[str, Any] = {
        "model": "compute_seconds_v3",
        "rate_per_compute_second": rate_per_compute_sec,
        "compute_seconds": round(actual_compute, 4),
        "audio_duration_sec": round(actual_audio_dur, 4),
        "fixed_fee_usd": fixed_fee,
        "total_cost_usd": float(total),
    }
    
    return CostEstimate(
        pricing_version=pricing_version,
        input_chars=len(text or ""),
        audio_duration_sec=_money(actual_audio_dur),
        compute_seconds=_money(actual_compute),
        gpu_type=gpu_type or "standard",
        runpod_cost_usd=base_cost, # In internal model, compute cost is the runpod cost
        storage_cost_usd=Decimal("0.0"),
        bandwidth_cost_usd=Decimal("0.0"),
        service_fee_usd=Decimal("0.0"), # No markup for internal use
        total_cost_usd=total,
        currency="USD",
        cost_breakdown_json=json.dumps(breakdown, ensure_ascii=False),
    )
