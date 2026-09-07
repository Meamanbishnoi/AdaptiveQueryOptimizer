"""
Benchmark Data Generator & Seeder.
Populates realistic benchmark tables with sample rows for testing query execution plans.
"""

import sys
import random
from datetime import date, timedelta
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD, USE_MOCK_DB
import psycopg2


SEGMENTS = ["BUILDING", "AUTOMOBILE", "MACHINERY", "HOUSEHOLD", "FURNITURE"]
NATIONS = ["UNITED STATES", "GERMANY", "JAPAN", "INDIA", "BRAZIL", "CANADA", "FRANCE"]
BRANDS = ["Brand#11", "Brand#12", "Brand#23", "Brand#34", "Brand#45"]
TYPES = ["STANDARD BRASS", "PROMO POLISHED TIN", "LARGE BRUSHED STEEL", "ECONOMY COPPER"]


def seed_postgresql(num_customers=1000, num_orders=5000, num_items=20000):
    """Populates PostgreSQL with realistic benchmark rows."""
    print(f"[Seeder] Connecting to PostgreSQL {PG_HOST}:{PG_PORT}/{PG_DATABASE}...")
    try:
        conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname=PG_DATABASE,
            user=PG_USER,
            password=PG_PASSWORD,
            connect_timeout=3,
        )
        conn.autocommit = False
    except Exception as e:
        print(f"[Seeder] Cannot connect to PostgreSQL: {e}")
        return False

    cursor = conn.cursor()

    # Read and apply schema
    schema_path = PROJECT_ROOT / "data" / "schema.sql"
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    cursor.execute(schema_sql)
    conn.commit()
    print("[Seeder] Schema applied.")

    # 1. Customers
    print(f"[Seeder] Inserting {num_customers} customers...")
    cust_data = [
        (
            f"Customer#{i:06d}",
            random.choice(SEGMENTS),
            random.choice(NATIONS),
            round(random.uniform(-500.0, 10000.0), 2),
        )
        for i in range(1, num_customers + 1)
    ]
    cursor.executemany(
        "INSERT INTO customers (customer_name, market_segment, nation, account_balance) VALUES (%s, %s, %s, %s)",
        cust_data,
    )

    # 2. Suppliers
    print("[Seeder] Inserting 200 suppliers...")
    supp_data = [
        (
            f"Supplier#{i:05d}",
            random.choice(NATIONS),
            round(random.uniform(1000.0, 50000.0), 2),
        )
        for i in range(1, 201)
    ]
    cursor.executemany(
        "INSERT INTO suppliers (supplier_name, nation, account_balance) VALUES (%s, %s, %s)",
        supp_data,
    )

    # 3. Parts
    print("[Seeder] Inserting 1000 parts...")
    part_data = [
        (
            f"Part#{i:06d}",
            random.choice(BRANDS),
            random.choice(TYPES),
            round(random.uniform(10.0, 1000.0), 2),
        )
        for i in range(1, 1001)
    ]
    cursor.executemany(
        "INSERT INTO parts (part_name, part_brand, part_type, retail_price) VALUES (%s, %s, %s, %s)",
        part_data,
    )

    # 4. Orders
    print(f"[Seeder] Inserting {num_orders} orders...")
    start_dt = date(2022, 1, 1)
    orders_data = [
        (
            random.randint(1, num_customers),
            random.choice(["F", "O", "P"]),
            round(random.uniform(50.0, 15000.0), 2),
            start_dt + timedelta(days=random.randint(0, 700)),
        )
        for i in range(1, num_orders + 1)
    ]
    cursor.executemany(
        "INSERT INTO orders (customer_id, order_status, total_price, order_date) VALUES (%s, %s, %s, %s)",
        orders_data,
    )

    # 5. Order Items
    print(f"[Seeder] Inserting {num_items} order items...")
    items_data = [
        (
            random.randint(1, num_orders),
            random.randint(1, 1000),
            random.randint(1, 200),
            random.randint(1, 50),
            round(random.uniform(20.0, 5000.0), 2),
            round(random.choice([0.0, 0.02, 0.05, 0.08, 0.10]), 2),
            start_dt + timedelta(days=random.randint(1, 750)),
        )
        for i in range(1, num_items + 1)
    ]
    cursor.executemany(
        "INSERT INTO order_items (order_id, part_id, supplier_id, quantity, extended_price, discount, ship_date) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        items_data,
    )

    conn.commit()
    cursor.close()
    conn.close()
    print("[Seeder] Benchmark dataset populated successfully!")
    return True


if __name__ == "__main__":
    seed_postgresql()
