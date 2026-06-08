"""
generate_data.py — Synthetic inventory data generator
======================================================

Builds a realistic, fully-synthetic dataset for the Inventory Health Analyzer
portfolio project. No external data, no auth, no network — runs anywhere with
Python 3.9+ and pandas/numpy.

It produces three tidy CSVs in ./data/:

  skus.csv             one row per SKU  (the dimension table)
  daily_demand.csv     one row per SKU per day (true customer demand)
  inventory_ledger.csv one row per SKU per day (the day-by-day simulation:
                       on-hand, orders, receipts, sales, stockouts, overstock)

The demand backbone is modelled as:  trend + weekly seasonality + noise,
varied per SKU, so the dataset behaves like a real multi-SKU warehouse.

On top of demand we run a classic (s, S)-style reorder simulation with
per-SKU supplier lead times, so stockout events (unmet demand) and overstock
(capital tied up above target) fall out naturally.

WARNING: This is SIMULATED data. It is NOT EPTA or AstraZeneca data. See
   README.md for exactly what is real vs simulated.

Run:
    python generate_data.py
    python generate_data.py --skus 50 --years 2 --seed 42
"""

from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Reference data for plausible-looking SKUs
# --------------------------------------------------------------------------- #
CATEGORIES = {
    "Beverages": ("BEV", 8, 60),       # (prefix, low unit cost, high unit cost)
    "Snacks": ("SNK", 3, 25),
    "Household": ("HSH", 5, 80),
    "Personal Care": ("PCR", 6, 90),
    "Pet": ("PET", 10, 70),
    "Frozen": ("FRZ", 12, 110),
    "Bakery": ("BAK", 2, 18),
    "Produce": ("PRD", 4, 30),
}

SUPPLIERS = [
    ("SUP-NORD", 2, 4),    # (name, min lead-time days, max lead-time days)
    ("SUP-BALT", 5, 9),
    ("SUP-EURO", 7, 14),
    ("SUP-ASIA", 18, 30),
]


# --------------------------------------------------------------------------- #
# 1. SKU master (dimension table)
# --------------------------------------------------------------------------- #
def build_skus(n_skus: int, rng: np.random.Generator) -> pd.DataFrame:
    cats = list(CATEGORIES.keys())
    rows = []
    for i in range(n_skus):
        cat = rng.choice(cats)
        prefix, lo_cost, hi_cost = CATEGORIES[cat]
        unit_cost = round(float(rng.uniform(lo_cost, hi_cost)), 2)
        # retail margin 20%-70%
        margin = float(rng.uniform(0.20, 0.70))
        unit_price = round(unit_cost * (1 + margin), 2)

        supplier, lt_lo, lt_hi = SUPPLIERS[rng.integers(0, len(SUPPLIERS))]
        lead_time = int(rng.integers(lt_lo, lt_hi + 1))

        # base daily demand level - log-normal so a few SKUs are high-volume
        base_demand = float(np.exp(rng.normal(2.3, 0.9)))  # ~ median 10 units/day
        base_demand = max(0.5, base_demand)

        rows.append(
            {
                "sku_id": f"{prefix}-{1000 + i:04d}",
                "sku_name": f"{cat} item {i + 1:02d}",
                "category": cat,
                "supplier": supplier,
                "unit_cost": unit_cost,
                "unit_price": unit_price,
                "lead_time_days": lead_time,
                # demand shape params (kept for transparency / reproducibility)
                "base_daily_demand": round(base_demand, 3),
                "trend_per_year": round(float(rng.uniform(-0.45, 0.45)), 3),
                "weekly_amplitude": round(float(rng.uniform(0.05, 0.45)), 3),
                "noise_cv": round(float(rng.uniform(0.15, 0.55)), 3),
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# 2. Daily demand (trend + weekly seasonality + noise)
# --------------------------------------------------------------------------- #
def build_demand(
    skus: pd.DataFrame, start: date, end: date, rng: np.random.Generator
) -> pd.DataFrame:
    days = pd.date_range(start, end, freq="D")
    n_days = len(days)
    dow = np.array([d.weekday() for d in days])  # 0=Mon ... 6=Sun
    t = np.arange(n_days)

    frames = []
    for _, s in skus.iterrows():
        base = s["base_daily_demand"]
        # linear trend over the horizon
        trend = 1.0 + s["trend_per_year"] * (t / 365.0)
        # weekly seasonality - busier toward the weekend
        season = 1.0 + s["weekly_amplitude"] * np.sin(2 * np.pi * (dow + 1) / 7.0)
        mean = np.clip(base * trend * season, 0.05, None)
        # Poisson-ish integer demand with extra dispersion from noise_cv
        noise = rng.normal(1.0, s["noise_cv"], n_days).clip(0.2, None)
        lam = (mean * noise).clip(0.01, None)
        demand = rng.poisson(lam)

        frames.append(
            pd.DataFrame(
                {
                    "sku_id": s["sku_id"],
                    "date": days,
                    "demand_units": demand.astype(int),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


# --------------------------------------------------------------------------- #
# 3. Inventory simulation  (baseline (s, S) reorder policy)
# --------------------------------------------------------------------------- #
def simulate_inventory(
    skus: pd.DataFrame, demand: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """
    Baseline (s, S) policy with a deliberately REALISTIC twist: the policy is
    set ONCE from an early "policy window" (first ~120 days) and then never
    revised - exactly how a lot of real warehouses run. As demand drifts over
    the following ~1.5 years, the static policy falls out of sync:

      * SKUs whose demand GROWS  -> the policy is too small -> STOCKOUTS.
      * SKUs whose demand SHRINKS -> the policy is too big  -> OVERSTOCK
        (capital tied up in stock that turns far too slowly).

    Policy (computed from the policy window only):
      avg_daily      = mean demand in window
      std_daily      = std of daily demand in window
      safety_stock   = z * std_daily * sqrt(lead_time)      (z=1.65 ~ 95% SL)
      reorder_point  = avg_daily * lead_time + safety_stock
      order_up_to S  = reorder_point + avg_daily * review_period (7 days)

    Overstock is measured in DAYS-OF-COVER against *recent* demand (trailing
    28-day average), so it captures slow-turning capital rather than a simple
    units-above-target check. A SKU is overstocked when on-hand covers more
    than `target_days + buffer` days of recent demand.

    Each day: receive due deliveries -> meet demand from on-hand (unmet =
    stockout) -> if nothing in transit and on_hand <= reorder_point, order up
    to S, arriving after lead_time days.
    """
    Z = 1.65
    REVIEW_PERIOD = 7
    POLICY_WINDOW = 120          # days used to set the static policy
    RECENT_WINDOW = 28           # trailing window for "recent" demand
    OVERSTOCK_BUFFER_DAYS = 21   # cover beyond target before we call it overstock

    demand_by_sku = {k: v.sort_values("date") for k, v in demand.groupby("sku_id")}
    ledgers = []

    for _, s in skus.iterrows():
        d = demand_by_sku[s["sku_id"]]
        dates = d["date"].to_numpy()
        dem = d["demand_units"].to_numpy().astype(float)
        n = len(dem)

        # --- static policy from the early window only -----------------------
        win = dem[: min(POLICY_WINDOW, n)]
        avg_daily = float(win.mean())
        std_daily = float(win.std(ddof=0))
        lead = int(s["lead_time_days"])

        safety_stock = Z * std_daily * np.sqrt(max(lead, 1))
        reorder_point = avg_daily * lead + safety_stock
        order_up_to = reorder_point + avg_daily * REVIEW_PERIOD
        target_days = lead + REVIEW_PERIOD            # cover the policy intends
        overstock_days = target_days + OVERSTOCK_BUFFER_DAYS

        # --- trailing recent-demand average (for days-of-cover) -------------
        recent_avg = pd.Series(dem).rolling(RECENT_WINDOW, min_periods=1).mean().to_numpy()

        on_hand = float(order_up_to)
        pipeline: dict[int, float] = {}  # arrival_index -> qty in transit

        for i in range(n):
            # 1. receive anything arriving today
            received = pipeline.pop(i, 0.0)
            on_hand += received

            # 2. meet demand
            want = float(dem[i])
            sold = min(on_hand, want)
            stockout_units = want - sold
            on_hand -= sold

            # 3. reorder decision (only if nothing already in transit)
            order_qty = 0.0
            if not pipeline and on_hand <= reorder_point:
                order_qty = max(0.0, order_up_to - on_hand)
                if order_qty > 0:
                    arrival = i + lead
                    pipeline[arrival] = pipeline.get(arrival, 0.0) + order_qty

            # 4. overstock = stock above `overstock_days` of *recent* demand
            r_avg = max(float(recent_avg[i]), 0.1)
            days_of_cover = on_hand / r_avg
            overstock_units = max(0.0, on_hand - overstock_days * r_avg)

            ledgers.append(
                {
                    "sku_id": s["sku_id"],
                    "date": dates[i],
                    "demand_units": int(want),
                    "units_sold": round(sold, 2),
                    "stockout_units": round(stockout_units, 2),
                    "on_hand_units": round(on_hand, 2),
                    "in_transit_units": round(sum(pipeline.values()), 2),
                    "order_placed_units": round(order_qty, 2),
                    "received_units": round(received, 2),
                    "overstock_units": round(overstock_units, 2),
                    "days_of_cover": round(days_of_cover, 1),
                    "reorder_point": round(reorder_point, 2),
                    "order_up_to_level": round(order_up_to, 2),
                    "safety_stock": round(safety_stock, 2),
                    "is_stockout": int(stockout_units > 0),
                    "is_overstock": int(overstock_units > 0),
                }
            )

    led = pd.DataFrame(ledgers)
    # money columns for downstream SQL (lost sales, holding value)
    cost = skus.set_index("sku_id")["unit_cost"]
    price = skus.set_index("sku_id")["unit_price"]
    led["lost_sales_value"] = (
        led["stockout_units"] * (led["sku_id"].map(price) - led["sku_id"].map(cost))
    ).round(2)
    led["on_hand_value"] = (led["on_hand_units"] * led["sku_id"].map(cost)).round(2)
    led["overstock_value"] = (led["overstock_units"] * led["sku_id"].map(cost)).round(2)
    return led


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic inventory data.")
    ap.add_argument("--skus", type=int, default=50, help="number of SKUs")
    ap.add_argument("--years", type=float, default=2.0, help="years of daily history")
    ap.add_argument("--seed", type=int, default=42, help="random seed")
    ap.add_argument("--outdir", default="data", help="output directory")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    end = date(2026, 6, 1)
    start = end - timedelta(days=int(round(args.years * 365)))

    os.makedirs(args.outdir, exist_ok=True)

    print(f"Generating {args.skus} SKUs over {start} -> {end} (seed={args.seed}) ...")
    skus = build_skus(args.skus, rng)
    demand = build_demand(skus, start, end, rng)
    ledger = simulate_inventory(skus, demand, rng)

    skus.to_csv(os.path.join(args.outdir, "skus.csv"), index=False)
    demand.to_csv(os.path.join(args.outdir, "daily_demand.csv"), index=False)
    ledger.to_csv(os.path.join(args.outdir, "inventory_ledger.csv"), index=False)

    # quick console summary
    days = demand["date"].nunique()
    n_stockout = int(ledger["is_stockout"].sum())
    n_overstock = int(ledger["is_overstock"].sum())
    print("Done.")
    print(f"  skus.csv             {len(skus):>8,} rows")
    print(f"  daily_demand.csv     {len(demand):>8,} rows  ({days} days)")
    print(f"  inventory_ledger.csv {len(ledger):>8,} rows")
    print(f"  stockout days:  {n_stockout:,}  ({n_stockout / len(ledger):.1%} of ledger)")
    print(f"  overstock days: {n_overstock:,}  ({n_overstock / len(ledger):.1%} of ledger)")
    print(f"  total lost-sales value: {ledger['lost_sales_value'].sum():,.0f}")
    print(f"  total overstock value:  {ledger['overstock_value'].sum():,.0f}")


if __name__ == "__main__":
    main()
