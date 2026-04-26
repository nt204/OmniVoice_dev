import os
import sys
from datetime import datetime, timedelta, timezone

# Add project root to path
sys.path.append(os.getcwd())

from web.fastapi_app import _sync_runpod_billing_buckets
from web.db import SessionLocal

db = SessionLocal()
try:
    print("Syncing RunPod billing buckets...")
    api_key = os.getenv("RUNPOD_API_KEY")
    endpoint_id = os.getenv("RUNPOD_ENDPOINT_ID")
    if api_key and endpoint_id:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=7)
        synced = _sync_runpod_billing_buckets(
            endpoint_id=endpoint_id,
            api_key=api_key,
            db=db,
            start_time=start_time,
            end_time=end_time
        )
        db.commit()
        print(f"Sync complete. Synced {synced} buckets.")
    else:
        print("Missing credentials.")
except Exception as e:
    print(f"Error: {e}")
finally:
    db.close()
