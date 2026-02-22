"""One-time script to create Stripe products/prices and update the plans table.

Usage:
    python scripts/setup_stripe_products.py

Reads STRIPE_SECRET_KEY and DATABASE_URL directly from .env file.
"""

import sys
import os
from pathlib import Path

# Load .env manually to avoid Settings validation issues
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

import stripe
from sqlalchemy import create_engine, text


def main():
    stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")
    db_url = os.environ.get("DATABASE_URL", "")

    if not stripe_key:
        print("ERROR: STRIPE_SECRET_KEY is not set in .env")
        sys.exit(1)
    if not db_url:
        print("ERROR: DATABASE_URL is not set in .env")
        sys.exit(1)

    # Convert async URL to sync if needed
    sync_url = db_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    if sync_url.startswith("postgresql://"):
        sync_url = sync_url.replace("postgresql://", "postgresql+psycopg2://", 1)

    stripe.api_key = stripe_key
    engine = create_engine(sync_url)

    plans = [
        {
            "slug": "core",
            "name": "Core",
            "description": "10x more agent runs for growing teams",
            "price_cents": 2000,  # $20/mo
        },
        {
            "slug": "team",
            "name": "Team",
            "description": "High-volume usage for larger teams",
            "price_cents": 9900,  # $99/mo
        },
    ]

    for plan in plans:
        print(f"\nCreating Stripe product: {plan['name']}...")
        product = stripe.Product.create(
            name=plan["name"],
            description=plan["description"],
        )
        print(f"  Product ID: {product.id}")

        price = stripe.Price.create(
            product=product.id,
            unit_amount=plan["price_cents"],
            currency="usd",
            recurring={"interval": "month"},
        )
        print(f"  Price ID:   {price.id}")

        with engine.connect() as conn:
            conn.execute(
                text(
                    "UPDATE plans SET stripe_product_id = :product_id, "
                    "stripe_price_id = :price_id WHERE slug = :slug"
                ),
                {
                    "product_id": product.id,
                    "price_id": price.id,
                    "slug": plan["slug"],
                },
            )
            conn.commit()
        print(f"  Updated plans table for '{plan['slug']}'")

    print("\nDone! Stripe products created and plans table updated.")


if __name__ == "__main__":
    main()
