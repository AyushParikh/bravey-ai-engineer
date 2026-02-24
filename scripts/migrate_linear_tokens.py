#!/usr/bin/env python3
"""
One-time script to migrate existing Linear long-lived tokens to the new
refresh-token flow.

For each org that has a linear_access_token but no linear_refresh_token,
calls Linear's migrate_old_token endpoint to get a refresh token.
Same for bot tokens.

Usage:
    python scripts/migrate_linear_tokens.py
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.database import SyncSessionLocal
from src.models.organization import Organization
from src.services.linear_service import migrate_old_token


def main() -> None:
    db = SyncSessionLocal()
    try:
        orgs = db.execute(select(Organization)).scalars().all()
        print(f"Found {len(orgs)} organization(s)")

        for org in orgs:
            # Migrate admin token
            if org.linear_access_token and not org.linear_refresh_token:
                print(f"\n[{org.slug}] Migrating admin token...")
                try:
                    token_data = migrate_old_token(org.linear_access_token)
                    org.linear_access_token = token_data["access_token"]
                    org.linear_refresh_token = token_data.get("refresh_token")
                    expires_in = token_data.get("expires_in", 0)
                    if expires_in:
                        org.linear_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                    db.commit()
                    print(f"  OK — refresh token stored, expires in {expires_in}s")
                except Exception as e:
                    db.rollback()
                    print(f"  FAILED — {e}")
            elif org.linear_access_token:
                print(f"\n[{org.slug}] Admin token already has refresh token, skipping")

            # Migrate bot token
            if org.linear_bot_token and not org.linear_bot_refresh_token:
                print(f"[{org.slug}] Migrating bot token...")
                try:
                    token_data = migrate_old_token(org.linear_bot_token)
                    org.linear_bot_token = token_data["access_token"]
                    org.linear_bot_refresh_token = token_data.get("refresh_token")
                    expires_in = token_data.get("expires_in", 0)
                    if expires_in:
                        org.linear_bot_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                    db.commit()
                    print(f"  OK — bot refresh token stored, expires in {expires_in}s")
                except Exception as e:
                    db.rollback()
                    print(f"  FAILED — {e}")
            elif org.linear_bot_token:
                print(f"[{org.slug}] Bot token already has refresh token, skipping")

        print("\nDone.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
