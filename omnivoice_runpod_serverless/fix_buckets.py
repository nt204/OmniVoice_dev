import os
import sys
import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session

# Add project root to path
sys.path.append(os.getcwd())

from web.db import SessionLocal
from web.models import RunpodBillingBucket

def fix_billing_buckets():
    db: Session = SessionLocal()
    try:
        print("Fixing runpod_billing_buckets bucket_start from raw_json...")
        buckets = db.query(RunpodBillingBucket).all()
        for b in buckets:
            if b.raw_json:
                data = json.loads(b.raw_json)
                raw_time = data.get("time")
                if raw_time:
                    # RunPod returns time like '2026-04-22 11:00:00'
                    # It's already in UTC.
                    parsed_time = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    b.bucket_start = parsed_time
        
        db.commit()
        print("Fixed runpod_billing_buckets successfully.")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    fix_billing_buckets()
