-- ============================================================================
-- schema.sql  —  Inventory Health Analyzer
-- Postgres-compatible DDL (target: Supabase). See SQLite notes at the bottom.
-- ============================================================================
--
-- Star-ish model:
--   dim_sku            one row per SKU (the dimension)
--   fact_daily_demand  one row per SKU per day  — true customer demand
--   fact_inventory     one row per SKU per day  — the simulation ledger
--                      (on-hand, orders, receipts, stockouts, overstock, $)
--
-- This file is the source of truth for the schema. pipeline.py builds the
-- equivalent tables in SQLite for local, credential-free running; the few
-- type differences are noted at the bottom.
-- ============================================================================

DROP TABLE IF EXISTS fact_inventory;
DROP TABLE IF EXISTS fact_daily_demand;
DROP TABLE IF EXISTS dim_sku;

-- ---------------------------------------------------------------------------
-- Dimension: one row per SKU
-- ---------------------------------------------------------------------------
CREATE TABLE dim_sku (
    sku_id            TEXT          PRIMARY KEY,
    sku_name          TEXT,
    category          TEXT,
    tracking_type     TEXT,         -- 'serialised' | 'non-serialised'
    supplier          TEXT,
    unit_cost         NUMERIC(10,2),
    unit_price        NUMERIC(10,2),
    lead_time_days    INTEGER,
    -- demand-shape params kept for transparency / reproducibility
    base_daily_demand NUMERIC(10,3),
    trend_per_year    NUMERIC(10,3),
    weekly_amplitude  NUMERIC(10,3),
    noise_cv          NUMERIC(10,3)
);

-- ---------------------------------------------------------------------------
-- Fact: true daily demand per SKU
-- ---------------------------------------------------------------------------
CREATE TABLE fact_daily_demand (
    sku_id       TEXT    NOT NULL REFERENCES dim_sku(sku_id),
    date         DATE    NOT NULL,
    demand_units INTEGER NOT NULL,
    PRIMARY KEY (sku_id, date)
);

-- ---------------------------------------------------------------------------
-- Fact: daily inventory ledger from the simulation
-- ---------------------------------------------------------------------------
CREATE TABLE fact_inventory (
    sku_id             TEXT NOT NULL REFERENCES dim_sku(sku_id),
    date               DATE NOT NULL,
    demand_units       INTEGER,
    units_sold         NUMERIC(12,2),
    stockout_units     NUMERIC(12,2),   -- unmet demand (lost sales, in units)
    on_hand_units      NUMERIC(12,2),
    in_transit_units   NUMERIC(12,2),
    order_placed_units NUMERIC(12,2),
    received_units     NUMERIC(12,2),
    overstock_units    NUMERIC(12,2),   -- units above target days-of-cover
    days_of_cover      NUMERIC(12,1),   -- on-hand / recent avg daily demand
    reorder_point      NUMERIC(12,2),
    order_up_to_level  NUMERIC(12,2),
    safety_stock       NUMERIC(12,2),
    is_stockout        SMALLINT,        -- 0/1 flag
    is_overstock       SMALLINT,        -- 0/1 flag
    lost_sales_value   NUMERIC(14,2),   -- stockout_units * margin
    on_hand_value      NUMERIC(14,2),   -- on_hand_units * unit_cost
    overstock_value    NUMERIC(14,2),   -- overstock_units * unit_cost
    PRIMARY KEY (sku_id, date)
);

CREATE INDEX idx_demand_sku ON fact_daily_demand (sku_id);
CREATE INDEX idx_inv_sku    ON fact_inventory (sku_id);
CREATE INDEX idx_inv_date   ON fact_inventory (date);

-- ============================================================================
-- SQLite differences (what pipeline.py does locally)
-- ----------------------------------------------------------------------------
--  * Types: SQLite uses TEXT / REAL / INTEGER. NUMERIC(p,s) -> REAL,
--    SMALLINT -> INTEGER, DATE -> TEXT (ISO 'YYYY-MM-DD' strings). SQLite's
--    dynamic typing accepts the Postgres column types but does not enforce
--    precision, so pipeline.py uses the simpler affinities.
--  * Date functions: in SQLite use strftime()/date(); in Postgres use
--    date_trunc(), EXTRACT(), and native DATE arithmetic.
--  * Foreign keys: SQLite does not enforce FKs unless `PRAGMA foreign_keys=ON`.
--    pipeline.py enforces referential integrity in pandas before loading.
--
-- To move to Supabase/Postgres later:
--   1. Run this file as-is in the Supabase SQL editor (or `psql -f schema.sql`).
--   2. Load the CSVs with \copy, e.g.:
--        \copy dim_sku            FROM 'data/skus.csv'             CSV HEADER;
--        \copy fact_daily_demand  FROM 'data/daily_demand.csv'     CSV HEADER;
--        \copy fact_inventory     FROM 'data/inventory_ledger.csv' CSV HEADER;
--   3. The Week-2 analytics SQL (service level, ABC, reorder points, flags)
--      runs unchanged on both engines apart from the date-function note above.
-- ============================================================================
