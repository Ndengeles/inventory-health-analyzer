# Inventory Health Analyzer

**A SKU-level pipeline that finds where a warehouse loses money — *stockouts* (lost sales) on one side, *overstock* (tied-up capital) on the other — and pinpoints the reorder points that fix it.**

Built on a fully reproducible simulation of a 50-SKU **equipment & spares** warehouse over two years — a realistic mix of serial-tracked capital equipment and bulk consumable spares — the project pairs a Python data pipeline with a SQL analytical layer to turn raw daily inventory movements into clear, money-ranked actions.

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

**Two stock classes, modelled differently:**

- **Serialised** capital equipment (refrigeration units, compressors, display cabinets) — high-value, **intermittent** demand (most days zero), long lead times, tracked and ordered in whole units.
- **Non-serialised** spares & consumables (filters, gaskets, fans, refrigerant) — high-volume, low-value, smooth demand with a weekly rhythm.

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
| SKUs (serialised / non-serialised) | 50 (14 / 36) |
| Days simulated · ledger rows | 731 · 36,550 |
| **Overall service level** | **89.9%** of demand met from stock |
| Days with a stockout | 1,818 (5.0% of ledger) |
| Days with overstock | 1,121 (3.1% of ledger) |
| Total lost-sales value (margin) | ~14.7M |
| Total overstock value (cost) | ~8.6M — **90% of it in serialised capital** |

The two stock classes fail in opposite directions: **serialised** equipment runs a lower service level (~80%) and holds the overwhelming majority of tied-up capital — the static safety-stock policy mis-sizes its lumpy demand — while **non-serialised** consumables drive most of the lost sales through frequent small stockouts.

*Results are deterministic for a given seed — change `--seed` to model a different warehouse.*

---

## The SQL analytics layer

Four queries turn the raw ledger into money-ranked decisions. Each is a self-contained `.sql` file in [`sql/`](sql/); run any of them with `python run_query.py sql/<file>.sql`. Outputs below are from the default dataset (50 SKUs · 2 years · seed 42).

### 1 · Overall service level — [`01_service_level.sql`](sql/01_service_level.sql)

The single headline number: what share of total demand was met from stock.

```
service_level_pct
-----------------
89.9
```

### 2 · Worst-served SKUs — [`02_service_level_by_sku.sql`](sql/02_service_level_by_sku.sql)

The same ratio per SKU, ranked worst-first — where the 89.9% is actually leaking. `GROUP BY` + a ratio aggregate.

| sku_id | service_level_pct | total_demand |
|---|--:|--:|
| CND-1003 | 59.1 | 406 |
| FAN-1046 | 65.1 | 64,797 |
| RFG-1048 | 70.2 | 6,402 |
| DSP-1002 | 73.9 | 211 |
| DSP-1035 | 74.6 | 67 |

*(top 5 of 10)* — note FAN-1046: a low service level **and** huge demand is the dangerous combination the action list flags first.

### 3 · ABC classification — [`03_abc_class.sql`](sql/03_abc_class.sql)

Pareto by revenue using window functions (`SUM() OVER (ORDER BY revenue DESC)` for a running cumulative %), then a `CASE` cut at 80% / 95%.

| Class | SKUs | Share of revenue |
|---|--:|--:|
| **A** | 5 (10%) | first 77.5% |
| **B** | 13 (26%) | next ~18% |
| **C** | 32 (64%) | last ~5% |

Five SKUs — led by FAN-1046 (30% of all revenue on its own) — carry most of the value; two-thirds of the catalogue is the long tail.

### 4 · Reorder points & action list — [`04_reorder_points.sql`](sql/04_reorder_points.sql)

The deliverable. `ROW_NUMBER()` isolates each SKU's latest ledger row for its current state, joined to period lost-sales totals, with a `CASE` that scores each flagged SKU by money at stake (lost sales if stocked out, tied-up capital if overstocked) — then ranks the whole action list by `impact_value`.

| SKU | Name | On hand | Reorder point | Direction | Impact |
|---|---|--:|--:|---|--:|
| FAN-1046 | Fan / Motor 047 | 0 | 1,983 | 🔴 stockout | 12,227,120 |
| CND-1034 | Condensing Unit 035 | 0 | 47 | 🔴 stockout | 655,491 |
| CND-1003 | Condensing Unit 004 | 0 | 12 | 🔴 stockout | 51,892 |
| FAN-1033 | Fan / Motor 034 | 73 | 48 | 🟡 overstock | 12,080 |
| RFG-1022 | Refrigerant 023 | 372 | 282 | 🟡 overstock | 4,951 |
| CMP-1031 | Compressor 032 | 4 | 4 | 🟡 overstock | 3,804 |
| CND-1024 | Condensing Unit 025 | 4 | 3 | 🟡 overstock | 1,479 |
| FAN-1018 | Fan / Motor 019 | 52 | 50 | 🟡 overstock | 1,375 |
| GSK-1030 | Gasket / Seal 031 | 471 | 267 | 🟡 overstock | 50 |

Nine SKUs need action — 3 stocked out, 6 overstocked. The list is dominated by **FAN-1046**: stocked out with a recommended reorder point of ~1,983 units, it alone accounts for 12.2M in lost sales. That is the one SKU to fix first.

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
| `dim_sku` | one row per SKU | cost, price, lead time, supplier, tracking type (serialised / non-serialised), demand-shape parameters |
| `fact_daily_demand` | SKU × day | true customer demand |
| `fact_inventory` | SKU × day | on-hand, in-transit, orders, receipts, stockout/overstock units, days-of-cover, reorder point / order-up-to / safety stock, status flags, and money columns (`lost_sales_value`, `on_hand_value`, `overstock_value`) |

Generated data (`data/`, `inventory.db`) is git-ignored — it's fully reproducible from the two scripts, which keeps the repository small.

---

## Data note

The dataset is **100% synthetic** — an invented equipment & spares warehouse with a realistic mix of serialised capital and non-serialised consumables. Non-serialised demand is smooth (trend + weekly seasonality + Poisson noise); serialised demand is intermittent (mostly-zero days). The SKU master and the day-by-day `(s, S)` inventory ledger are simulated on top. No real material numbers, serials, suppliers, customers, or values are used.

---

## Roadmap

- [x] **Data + pipeline** — synthetic generator, ingest/validate, SQLite load
- [x] **SQL analytics layer** — service level, ABC class, reorder-point recommendations, stockout/overstock flags, ranked action list
- [ ] **Interactive dashboard** — Streamlit (overview KPIs · ABC · per-SKU detail · action list), published to a live URL — *possible v2*

**Scope:** one warehouse, ~50 SKUs, a single baseline policy. No multi-echelon, no real-time, no ML — kept deliberately tight so the analysis stays clear and defensible. Serialised items deliberately reuse the same safety-stock policy as a baseline; fitting their intermittent demand properly (e.g. Croston's method) is noted as future work.
