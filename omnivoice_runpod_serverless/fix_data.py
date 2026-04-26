import os
import sys
import json
from decimal import Decimal
from datetime import timedelta, timezone
from sqlalchemy.orm import Session

# Add project root to path
sys.path.append(os.getcwd())

from web.db import SessionLocal
from web.models import SynthesisJob, JobCost, RunpodBillingBucket

def fix_data():
    db: Session = SessionLocal()
    try:
        # 1. Fix Timezone Shift (Subtract 7 hours from everything)
        print("Fixing timestamps (shifting -7 hours)...")
        jobs = db.query(SynthesisJob).all()
        for j in jobs:
            # Shift j.created_at
            if j.created_at:
                j.created_at = j.created_at - timedelta(hours=7)
            if j.finished_at:
                j.finished_at = j.finished_at - timedelta(hours=7)
        
        costs = db.query(JobCost).all()
        for c in costs:
            if c.created_at:
                c.created_at = c.created_at - timedelta(hours=7)
            
        buckets = db.query(RunpodBillingBucket).all()
        for b in buckets:
            if b.bucket_start:
                b.bucket_start = b.bucket_start - timedelta(hours=7)
            if b.synced_at:
                b.synced_at = b.synced_at - timedelta(hours=7)
            if b.created_at:
                b.created_at = b.created_at - timedelta(hours=7)
        
        # 2. Recalculate Costs (Model A: Audio Duration)
        RATE = Decimal("0.00019")
        FIXED = Decimal("0.0005")
        
        print("Recalculating costs based on Audio Duration (Model A)...")
        for c in costs:
            duration = c.audio_duration_sec or Decimal("0")
            new_service_fee = (duration * RATE) + FIXED
            c.service_fee_usd = new_service_fee.quantize(Decimal("0.000001"))
            c.total_cost_usd = c.service_fee_usd
            c.runpod_cost_usd = Decimal("0.0")
            c.pricing_version = "v2-audio-duration"
            
            breakdown = {
                "model": "audio_duration_v2_fixed",
                "rate_per_audio_second": float(RATE),
                "audio_duration_sec": float(duration),
                "fixed_fee_usd": float(FIXED),
                "total_cost_usd": float(c.total_cost_usd),
                "source": "manual_fix_option_a"
            }
            c.cost_breakdown_json = json.dumps(breakdown)

        db.commit()
        print("Data fix completed successfully.")
    except Exception as e:
        db.rollback()
        print(f"Error during data fix: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    fix_data()
