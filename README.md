# Inventory Health Analyzer

**A SKU-level inventory-health pipeline that finds where a warehouse loses money — *stockouts* (lost sales) on one side, *overstock* (tied-up capital) on the other — and points to the reorder points that fix it.**

> **Status:** Week 1 of 3 — *data + pipeline foundation*. The SQL analytics layer (Week 2) and the Streamlit dashboard (Week 3) build on top of what's here. See [Roadmap](#roadmap).

This repo is a portfolio piece: it shows a clean Python → SQL data pipeline on an inventory problem I know well from day-to-day warehouse work. It uses **only synthetic data** — see [What's real vs simulated](#whats-real-vs-simulated).

---

## The question it answers

For a multi-SKU warehouse: **which products are losing money, in which direction (stockout vs overstock), and what reorder point would fix each one?**

The pipeline lays the groundwork to answer, at a glance:

- What's the overall **service level** (% of demand met from stock)?
- Which 20% of SKUs drive 80% of value (**ABC analysis**)?
- For each SKU: days-of-cover vs target, recommended reorder point, stockout/overstock flag.
- The **action list** — top SKUs to fix first, ranked by money at stake.

---

## What this models (and why it's interesting)

The twist that makes the data realistic: the reorder policy is **set once** from an early "policy window" (first ~120 days) and then **never revised** — exactly how a lot of real warehouses actually run. Over the following ~1.5 years demand drifts, and the static policy falls out of sync:

- SKUs whose demand **grows** → the policy is too small → **stockouts** (lost sales).
- SKUs whose demand **shrinks** → the policy is too big → **overstock** (capital tied up in stock that turns too slowly).

That single, defensible mechanism is what creates the money-losing patterns the dashboard will surface — no forecasting or ML required.

### Sample results (50 SKUs, 2 years, `--seed 42`)

| Metric | Value |
|---|---|
| SKUs / days simulated | 50 / 731 |
| Ledger rows | 36,550 |
| **Overall service level** | **88.4%** of demand met from stock |
| Days with a stockout | 3,496 (9.6% of ledger) — across 47 SKUs |
| Days with overstock | 851 (2.3% of ledger) — across 21 SKUs |
| Total lost-sales value (margin) | ~1.04M |
| Total overstock value (cost) | ~0.88M |

*(Figures are deterministic for `seed=42`; change the seed for a different warehouse.)*

---

## What's real vs simulated

**100% synthetic — honest by design.** 
| Layer | Source |
|---|---|
| Demand backbone | **Simulated** — per-SKU `trend + weekly seasonality + noise` (Poisson draws). Realistic shape, invented numbers. |
| SKU master (cost, price, lead time, supplier) | **Simulated** — plausible ranges per product category. |
| Inventory ledger (on-hand, orders, stockouts, overstock) | **Simulated** — a day-by-day `(s, S)` reorder simulation on top of the demand. |

The pipeline is built so the demand backbone can later be swapped for a **real open dataset** (e.g. Kaggle "Store Item Demand Forecasting" — 5 yrs of daily sales) without touching the inventory-simulation or analytics layers. That's a flagged manual step below, not done here (it needs Kaggle auth).

---

## How to run it locally

Requires Python 3.9+. No database server, no credentials, no network.

```bash
# 1. (optional) isolate dependencies
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install pandas numpy

# 2. generate the synthetic CSVs into ./data/
python generate_data.py                # defaults: 50 SKUs, 2 years, seed 42
#   options: --skus 50 --years 2 --seed 42 --outdir data

# 3. clean + load into a local SQLite database (inventory.db)
python pipeline.py                     # options: --datadir data --db inventory.db
```

`pipeline.py` prints a sanity check at the end: row counts, the highest-value stockout and overstock rows, and the overall service level.

Inspect the database with any SQLite tool:

```bash
sqlite3 inventory.db "SELECT sku_id, ROUND(SUM(lost_sales_value)) AS lost
                      FROM fact_inventory GROUP BY sku_id
                      ORDER BY lost DESC LIMIT 5;"
```

---

## Files

```
inventory-health-analyzer/
├── generate_data.py   # synthetic data generator (demand + inventory simulation)
├── pipeline.py        # ingest, clean, validate, load CSVs -> SQLite
├── schema.sql         # Postgres-compatible DDL (+ notes on SQLite differences)
├── README.md          # this file
├── .gitignore         # excludes generated data/ and *.db
└── data/              # generated CSVs (git-ignored; reproducible)
    ├── skus.csv
    ├── daily_demand.csv
    └── inventory_ledger.csv
```

Generated artifacts (`data/`, `inventory.db`) are git-ignored on purpose — they're fully reproducible from the two scripts, which keeps the repo small and the build honest.

## Data model

A small star-ish schema (full DDL in [`schema.sql`](schema.sql)):

- **`dim_sku`** — one row per SKU: cost, price, lead time, supplier, demand-shape params.
- **`fact_daily_demand`** — one row per SKU per day: true customer demand.
- **`fact_inventory`** — one row per SKU per day: on-hand, in-transit, orders, receipts, stockout units, overstock units, days-of-cover, the policy's reorder point / order-up-to / safety stock, 0/1 flags, and the money columns (`lost_sales_value`, `on_hand_value`, `overstock_value`).

---

## Roadmap

| Week | Focus | Status |
|---|---|---|
| **1** | Data + pipeline (this repo) | **✅ done** |
| **2** | SQL analytics layer — service level, ABC class, reorder-point recommendations, stockout/overstock flags, action list | ⬜ next |
| **3** | Streamlit dashboard (overview KPIs · ABC · per-SKU detail · action list) + publish (GitHub + live URL) | ⬜ |

### Manual next steps (for David)

1. **Push to GitHub.** This session didn't have GitHub credentials. From the project folder:
   ```bash
   git init && git add . && git commit -m "Week 1: data + pipeline"
   git branch -M main
   git remote add origin https://github.com/<you>/inventory-health-analyzer.git
   git push -u origin main
   ```
2. **(Optional) swap in a real open dataset.** Download the Kaggle "Store Item Demand Forecasting" CSV (needs Kaggle auth) and adapt `build_demand()` to read it instead of generating demand. Keep the inventory simulation as-is. Update the "real vs simulated" table accordingly.
3. **Move SQLite → Supabase (Postgres)** when ready for the live dashboard:
   - Run `schema.sql` in the Supabase SQL editor (it's already Postgres-compatible).
   - Load the CSVs with `\copy` (see the load notes at the bottom of `schema.sql`).
   - Point the Week-3 Streamlit app at the Supabase connection string.
4. **Build Weeks 2–3** — the SQL analytics views and the Streamlit dashboard.

---

## Design guardrails (kept deliberately tight)

One warehouse, ~50 SKUs, one baseline inventory policy. No multi-echelon, no auth, no real-time. **No forecasting / ML** — reorder points use classic safety-stock math (`reorder_point = avg_daily × lead_time + z × σ × √lead_time`), which is the right level for this problem and easy to explain in an interview.
