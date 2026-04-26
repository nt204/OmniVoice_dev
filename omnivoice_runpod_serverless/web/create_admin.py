import os
import sys
from pathlib import Path

# Add parent dir to path so we can import web
sys.path.append(str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session
from web.db import Base, engine, wait_for_database
from web.models import User
from web.auth import hash_password

def create_superadmin(email, password):
    wait_for_database()
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        # Check if user exists
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            print(f"User {email} already exists.")
            return

        # Hash password using app helper
        password_hash = hash_password(password)
        
        admin = User(
            email=email,
            password_hash=password_hash,
            role="admin",
            is_active=True
        )
        db.add(admin)
        db.commit()
        print(f"SuperAdmin {email} created successfully.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Create a SuperAdmin user.")
    parser.get_default("email")
    parser.add_argument("--email", default="admin@omnivoice.ai", help="Admin email")
    parser.add_argument("--password", default="admin123456", help="Admin password")
    
    args = parser.parse_args()
    create_superadmin(args.email, args.password)
