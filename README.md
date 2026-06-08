# Inventory Health Analyzer

**A SKU-level pipeline that finds where a warehouse loses money — *stockouts* (lost sales) on one side, *overstock* (tied-up capital) on the other — and pinpoints the reorder points that fix it.**

Built on a fully reproducible simulation of a 50-SKU warehouse over two years, the project pairs a Python data pipeline with a SQL analytical layer to turn raw daily inventory movements into clear, money-ranked actions.

**Demonstrates:** SQL analytics · Python (pandas) data pipelines · dimensional data modelling · supply-chain / inventory domain knowledge.

---

## The problem it solves

For a multi-SKU warehouse: **which products are losing money, in which direction (stockout vs overstock), and what reorder point would fix each one?**

It surfaces, at a glance:

- Overall **service level** — % of demand met from stock.
- **ABC analysis** — which ~20% of SKUs drive ~80% of value.
- Per-SKU health — days-of-cover vs target, recommended reorder point, stockout/overstock flag.
- An **action list** — the SKUs to fix first, ranked by money at stake.

---

## How it works

The mechanism that makes the data realistic: the reorder policy is **set once** from an early policy window (the first ~120 days) and then **never revised** — the way a lot of real warehouses actually run. As demand drifts over the following ~18 months, the static policy falls out of sync:

- Demand **grows** → policy too small → **stockouts** (lost sales).
- Demand **shrinks** → policy too big → **overstock** (capital tied up in slow-moving stock).

That single, defensible mechanism produces the money-losing patterns the analytics surface — no forecasting or ML required. Reorder points use classic safety-stock math:

```
reorder_point = avg_daily_demand × lead_time + z × σ × √lead_time
```

### Sample results (50 SKUs · 2 years · seed 42)

| Metric | Value |
|---|---|
| SKUs / days simulated | 50 / 731 |
| Ledger rows | 36,550 |
| **Overall service level** | **88.4%** of demand met from stock |
| Days with a stockout | 3,496 (9.6%) — across 47 SKUs |
| Days with overstock | 851 (2.3%) — across 21 SKUs |
| Total lost-sales value (margin) | ~1.04M |
| Total overstock value (cost) | ~0.88M |

*Results are deterministic for a given seed — change `--seed` to model a different warehouse.*

---

## Tech stack

**Python** (pandas, numpy) for generation, ingest, and validation · **SQL** (SQLite locally, with a Postgres-compatible schema) for the analytical layer · designed to publish to **Supabase + Streamlit** for an interactive dashboard.

---

## Quick start

Requires Python 3.9+. No database server, credentials, or network needed.

```bash
pip install pandas numpy

# generate the synthetic dataset into ./data/
python generate_data.py            # defaults: 50 SKUs, 2 years, seed 42

# clean, validate, and load into a local SQLite database
python pipeline.py                 # creates inventory.db
```

`pipeline.py` prints a sanity check on completion — row counts, the highest-value stockout/overstock rows, and the overall service level.

Query the result with any SQLite tool:

```sql
SELECT sku_id, ROUND(SUM(lost_sales_value)) AS lost
FROM   fact_inventory
GROUP  BY sku_id
ORDER  BY lost DESC
LIMIT  5;
```

---

## Data model

A compact star schema (full DDL in [`schema.sql`](schema.sql)):

| Table | Grain | Holds |
|---|---|---|
| `dim_sku` | one row per SKU | cost, price, lead time, supplier, demand-shape parameters |
| `fact_daily_demand` | SKU × day | true customer demand |
| `fact_inventory` | SKU × day | on-hand, in-transit, orders, receipts, stockout/overstock units, days-of-cover, reorder point / order-up-to / safety stock, status flags, and money columns (`lost_sales_value`, `on_hand_value`, `overstock_value`) |

Generated data (`data/`, `inventory.db`) is git-ignored — it's fully reproducible from the two scripts, which keeps the repository small.

---

## Data note

The dataset is **100% synthetic**. The demand backbone is generated per SKU (trend + weekly seasonality + Poisson noise); the SKU master and the day-by-day `(s, S)` inventory ledger are simulated on top of it. The pipeline is structured so the demand layer can be swapped for a real open dataset (e.g. Kaggle's *Store Item Demand Forecasting*) without touching the simulation or analytics layers.

---

## Roadmap

- [x] **Data + pipeline** — synthetic generator, ingest/validate, SQLite load
- [ ] **SQL analytics layer** — service level, ABC class, reorder-point recommendations, stockout/overstock flags, ranked action list
- [ ] **Interactive dashboard** — Streamlit (overview KPIs · ABC · per-SKU detail · action list), published to a live URL

**Scope:** one warehouse, ~50 SKUs, a single baseline policy. No multi-echelon, no real-time, no ML — kept deliberately tight so the analysis stays clear and defensible.
