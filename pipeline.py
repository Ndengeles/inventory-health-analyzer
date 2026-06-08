"""
pipeline.py — Ingest, clean & load the inventory data into SQLite
=================================================================

Reads the three CSVs produced by generate_data.py, runs light cleaning &
validation, and loads them into a local SQLite database (inventory.db) using
a clean star-ish schema:

  dim_sku            dimension  (one row per SKU)
  fact_daily_demand  fact       (one row per SKU per day - true demand)
  fact_inventory     fact       (one row per SKU per day - simulation ledger)

No DB credentials needed - SQLite is a single local file. The same schema is
expressed in schema.sql in Postgres-compatible form so this can be moved to
Supabase later (see README.md).

Run (after generate_data.py):
    python pipeline.py
    python pipeline.py --datadir data --db inventory.db
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

import pandas as pd

EXPECTED = {
    "skus.csv": ["sku_id", "category", "tracking_type", "unit_cost", "unit_price", "lead_time_days"],
    "daily_demand.csv": ["sku_id", "date", "demand_units"],
    "inventory_ledger.csv": ["sku_id", "date", "on_hand_units", "stockout_units"],
}

SCHEMA = """
DROP TABLE IF EXISTS fact_inventory;
DROP TABLE IF EXISTS fact_daily_demand;
DROP TABLE IF EXISTS dim_sku;

CREATE TABLE dim_sku (
    sku_id            TEXT PRIMARY KEY,
    sku_name          TEXT,
    category          TEXT,
    tracking_type     TEXT,
    supplier          TEXT,
    unit_cost         REAL,
    unit_price        REAL,
    lead_time_days    INTEGER,
    base_daily_demand REAL,
    trend_per_year    REAL,
    weekly_amplitude  REAL,
    noise_cv          REAL
);

CREATE TABLE fact_daily_demand (
    sku_id       TEXT NOT NULL REFERENCES dim_sku(sku_id),
    date         TEXT NOT NULL,
    demand_units INTEGER NOT NULL,
    PRIMARY KEY (sku_id, date)
);

CREATE TABLE fact_inventory (
    sku_id             TEXT NOT NULL REFERENCES dim_sku(sku_id),
    date               TEXT NOT NULL,
    demand_units       INTEGER,
    units_sold         REAL,
    stockout_units     REAL,
    on_hand_units      REAL,
    in_transit_units   REAL,
    order_placed_units REAL,
    received_units     REAL,
    overstock_units    REAL,
    days_of_cover      REAL,
    reorder_point      REAL,
    order_up_to_level  REAL,
    safety_stock       REAL,
    is_stockout        INTEGER,
    is_overstock       INTEGER,
    lost_sales_value   REAL,
    on_hand_value      REAL,
    overstock_value    REAL,
    PRIMARY KEY (sku_id, date)
);

CREATE INDEX idx_demand_sku   ON fact_daily_demand(sku_id);
CREATE INDEX idx_inv_sku      ON fact_inventory(sku_id);
CREATE INDEX idx_inv_date     ON fact_inventory(date);
"""


def load_csv(datadir: str, name: str) -> pd.DataFrame:
    path = os.path.join(datadir, name)
    if not os.path.exists(path):
        sys.exit(f"ERROR: {path} not found. Run generate_data.py first.")
    df = pd.read_csv(path)
    missing = [c for c in EXPECTED[name] if c not in df.columns]
    if missing:
        sys.exit(f"ERROR: {name} missing expected columns: {missing}")
    return df


def clean(skus: pd.DataFrame, demand: pd.DataFrame, ledger: pd.DataFrame):
    """Light, defensive cleaning + validation."""
    # normalise dates to ISO strings (SQLite has no native date type)
    for df in (demand, ledger):
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

    # drop exact duplicate keys, keep first
    skus = skus.drop_duplicates("sku_id")
    demand = demand.drop_duplicates(["sku_id", "date"])
    ledger = ledger.drop_duplicates(["sku_id", "date"])

    # referential integrity: facts must reference a known SKU
    valid = set(skus["sku_id"])
    demand = demand[demand["sku_id"].isin(valid)]
    ledger = ledger[ledger["sku_id"].isin(valid)]

    # basic sanity: no negative demand / on-hand
    demand["demand_units"] = demand["demand_units"].clip(lower=0).astype(int)
    ledger["on_hand_units"] = ledger["on_hand_units"].clip(lower=0)
    ledger["stockout_units"] = ledger["stockout_units"].clip(lower=0)

    return skus, demand, ledger


def load_sqlite(db_path: str, skus, demand, ledger) -> None:
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA)
        skus.to_sql("dim_sku", con, if_exists="append", index=False)
        demand.to_sql("fact_daily_demand", con, if_exists="append", index=False)
        ledger.to_sql("fact_inventory", con, if_exists="append", index=False)
        con.commit()
    finally:
        con.close()


def sanity_check(db_path: str) -> None:
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        print("\nLoaded into", db_path)
        for t in ("dim_sku", "fact_daily_demand", "fact_inventory"):
            n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:<20} {n:>10,} rows")

        # a couple of sample stockout / overstock rows
        print("\nSample stockout rows (highest lost-sales value):")
        for r in cur.execute(
            """SELECT sku_id, date, stockout_units, lost_sales_value
               FROM fact_inventory WHERE is_stockout = 1
               ORDER BY lost_sales_value DESC LIMIT 3"""
        ):
            print("   ", r)

        print("\nSample overstock rows (highest overstock value):")
        for r in cur.execute(
            """SELECT sku_id, date, overstock_units, overstock_value
               FROM fact_inventory WHERE is_overstock = 1
               ORDER BY overstock_value DESC LIMIT 3"""
        ):
            print("   ", r)

        # quick service-level teaser (demand met from stock)
        total_demand, total_sold = cur.execute(
            "SELECT SUM(demand_units), SUM(units_sold) FROM fact_inventory"
        ).fetchone()
        if total_demand:
            print(f"\nOverall service level: {total_sold / total_demand:.1%} "
                  f"of demand met from stock")
    finally:
        con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Load inventory CSVs into SQLite.")
    ap.add_argument("--datadir", default="data", help="CSV input directory")
    ap.add_argument("--db", default="inventory.db", help="output SQLite db path")
    args = ap.parse_args()

    print("Ingesting CSVs ...")
    skus = load_csv(args.datadir, "skus.csv")
    demand = load_csv(args.datadir, "daily_demand.csv")
    ledger = load_csv(args.datadir, "inventory_ledger.csv")

    print("Cleaning & validating ...")
    skus, demand, ledger = clean(skus, demand, ledger)

    print(f"Loading into {args.db} ...")
    load_sqlite(args.db, skus, demand, ledger)

    sanity_check(args.db)
    print("\nPipeline complete.")


if __name__ == "__main__":
    main()
