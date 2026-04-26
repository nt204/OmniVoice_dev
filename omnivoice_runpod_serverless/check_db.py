import os
import sys
import json
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

# Add project root to path
sys.path.append(os.getcwd())

from web.db import SessionLocal
from web.models import SynthesisJob, JobCost, RunpodBillingBucket, User

def check_and_fix_db():
    db: Session = SessionLocal()
    try:
        anomalies = []
        fixed_count = 0

        # 1. Check Users
        print("Checking users...")
        users = db.query(User).all()
        for u in users:
            if u.created_at and u.created_at.year < 2025:
                anomalies.append(f"User {u.id} has weird created_at: {u.created_at}")

        # 2. Check synthesis_jobs
        print("Checking synthesis_jobs...")
        jobs = db.query(SynthesisJob).all()
        for j in jobs:
            if j.created_at and j.created_at.year < 2025:
                anomalies.append(f"Job {j.id} has weird created_at: {j.created_at}")
            
        # 3. Check job_costs
        print("Checking job_costs...")
        RATE = Decimal("0.00019")
        FIXED = Decimal("0.0005")
        
        costs = db.query(JobCost).all()
        for c in costs:
            duration = c.audio_duration_sec or Decimal("0")
            expected_fee = (duration * RATE) + FIXED
            expected_fee = expected_fee.quantize(Decimal("0.000001"))
            
            # If there's a mismatch in cost
            if abs(c.service_fee_usd - expected_fee) > Decimal("0.000001") or abs(c.total_cost_usd - expected_fee) > Decimal("0.000001"):
                anomalies.append(f"Cost {c.id} mismatch. Expected {expected_fee}, got {c.service_fee_usd}")
                # Fix it
                c.service_fee_usd = expected_fee
                c.total_cost_usd = expected_fee
                if c.cost_breakdown_json:
                    try:
                        bd = json.loads(c.cost_breakdown_json)
                        bd["total_cost_usd"] = float(expected_fee)
                        c.cost_breakdown_json = json.dumps(bd)
                    except:
                        pass
                fixed_count += 1
                
            if c.created_at and c.created_at.year < 2025:
                anomalies.append(f"Cost {c.id} has weird created_at: {c.created_at}")

        # 4. Check runpod_billing_buckets
        print("Checking runpod_billing_buckets...")
        buckets = db.query(RunpodBillingBucket).all()
        for b in buckets:
            if b.raw_json:
                data = json.loads(b.raw_json)
                raw_time = data.get("time")
                if raw_time:
                    parsed_time = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    if b.bucket_start != parsed_time:
                        anomalies.append(f"Bucket {b.id} mismatch: {b.bucket_start} vs {parsed_time}")
                        b.bucket_start = parsed_time
                        fixed_count += 1

        db.commit()
        print(f"Check complete. Found {len(anomalies)} anomalies. Fixed {fixed_count} items.")
        if anomalies:
            print("Sample anomalies:")
            for a in anomalies[:10]:
                print(" -", a)
        else:
            print("All data is perfectly consistent.")
            
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    check_and_fix_db()
