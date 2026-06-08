"""
generate_data.py — Synthetic inventory data generator
======================================================

Builds a realistic, fully-synthetic dataset for the Inventory Health Analyzer
portfolio project. No external data, no auth, no network — runs anywhere with
Python 3.9+ and pandas/numpy.

It models an **equipment & spares** warehouse (refrigeration units, compressors,
spare parts, consumables) with a realistic mix of two stock classes:

  * SERIALISED      capital equipment tracked unit-by-unit. Low-volume,
                    high-value, INTERMITTENT demand (most days zero), long
                    supplier lead times, ordered in whole units.
  * NON-SERIALISED  bulk spares & consumables. High-volume, low-value, smooth
                    demand with weekly seasonality, shorter lead times.

It produces three tidy CSVs in ./data/:

  skus.csv             one row per SKU  (the dimension table; carries tracking_type)
  daily_demand.csv     one row per SKU per day (true customer demand)
  inventory_ledger.csv one row per SKU per day (the day-by-day simulation:
                       on-hand, orders, receipts, sales, stockouts, overstock)

On top of demand we run a classic (s, S)-style reorder simulation with per-SKU
supplier lead times, so stockout events (unmet demand) and overstock (capital
tied up above target) fall out naturally.

WARNING: This is SIMULATED data. It is NOT EPTA or AstraZeneca data, and uses
   no real material numbers, serials, customers, suppliers, or values. The
   serialised/non-serialised structure is a generic supply-chain shape, invented
   here. See README.md for exactly what is real vs simulated.

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
# Reference data — an equipment & spares warehouse (all invented)
# --------------------------------------------------------------------------- #
# name: (prefix, lo_cost, hi_cost, tracking, weight)
#   tracking : "serial" (always serialised) | "bulk" (never) | "mixed" (50/50 per SKU)
#   weight   : relative count of this category in the warehouse — bulk spares
#              vastly outnumber capital units, which keeps the serialised share ~30%.
CATEGORIES = {
    "Refrigeration Unit":       ("RFU", 8000, 26000, "serial", 1.2),
    "Display Cabinet":          ("DSP", 4000, 18000, "serial", 1.2),
    "Compressor":               ("CMP", 2500, 11000, "serial", 1.4),
    "Condensing Unit":          ("CND", 1800,  9000, "mixed",  1.6),
    "Control Board":            ("CTL",  300,  2200, "mixed",  1.8),
    "Fan / Motor":              ("FAN",   80,   900, "bulk",   2.2),
    "Thermostat / Sensor":      ("THS",   25,   400, "bulk",   2.4),
    "Filter":                   ("FLT",    5,    60, "bulk",   3.0),
    "Gasket / Seal":            ("GSK",    3,    45, "bulk",   3.0),
    "Refrigerant / Consumable": ("RFG",   10,   180, "bulk",   2.6),
}

SUPPLIERS = [
    ("SUP-NORD", 2, 4),    # (name, min lead-time days, max lead-time days)
    ("SUP-BALT", 5, 9),
    ("SUP-EURO", 7, 14),
    ("SUP-ASIA", 18, 30),
]
# Supplier preference by stock class: serialised capital equipment comes from
# the slower, far-away suppliers; bulk consumables from the fast, nearby ones.
SUPPLIER_WEIGHTS = {
    "serialised":     np.array([0.3, 0.6, 1.5, 1.6]),
    "non-serialised": np.array([1.6, 1.5, 1.0, 0.4]),
}


# --------------------------------------------------------------------------- #
# 1. SKU master (dimension table)
# --------------------------------------------------------------------------- #
def build_skus(n_skus: int, rng: np.random.Generator) -> pd.DataFrame:
    cats = list(CATEGORIES.keys())
    cat_weights = np.array([CATEGORIES[c][4] for c in cats], dtype=float)
    cat_p = cat_weights / cat_weights.sum()

    rows = []
    for i in range(n_skus):
        cat = str(rng.choice(cats, p=cat_p))
        prefix, lo_cost, hi_cost, tracking, _ = CATEGORIES[cat]

        # resolve the stock class (mixed categories flip a coin)
        if tracking == "serial":
            serialised = True
        elif tracking == "bulk":
            serialised = False
        else:
            serialised = bool(rng.random() < 0.5)
        tracking_type = "serialised" if serialised else "non-serialised"

        unit_cost = round(float(rng.uniform(lo_cost, hi_cost)), 2)
        # serialised capital gear carries a tighter margin; spares a fatter one
        margin = float(rng.uniform(0.12, 0.30) if serialised else rng.uniform(0.25, 0.75))
        unit_price = round(unit_cost * (1 + margin), 2)

        sup_p = SUPPLIER_WEIGHTS[tracking_type]
        sup_idx = int(rng.choice(len(SUPPLIERS), p=sup_p / sup_p.sum()))
        supplier, lt_lo, lt_hi = SUPPLIERS[sup_idx]
        lead_time = int(rng.integers(lt_lo, lt_hi + 1))

        if serialised:
            # intermittent: a unit every couple of weeks on average
            base_demand = float(np.exp(rng.normal(-2.5, 0.7)))   # ~ median 0.08/day
            base_demand = float(np.clip(base_demand, 0.01, 0.40))
            trend = round(float(rng.uniform(-0.50, 0.60)), 3)
            weekly_amplitude = 0.0                                # no weekly pattern
            noise_cv = round(float(rng.uniform(0.40, 0.85)), 3)
        else:
            # smooth, higher-volume; log-normal so a few SKUs are high runners
            base_demand = float(np.exp(rng.normal(2.3, 0.9)))     # ~ median 10/day
            base_demand = max(0.5, base_demand)
            trend = round(float(rng.uniform(-0.45, 0.45)), 3)
            weekly_amplitude = round(float(rng.uniform(0.05, 0.45)), 3)
            noise_cv = round(float(rng.uniform(0.15, 0.55)), 3)

        rows.append(
            {
                "sku_id": f"{prefix}-{1000 + i:04d}",
                "sku_name": f"{cat} model {i + 1:03d}",
                "category": cat,
                "tracking_type": tracking_type,
                "supplier": supplier,
                "unit_cost": unit_cost,
                "unit_price": unit_price,
                "lead_time_days": lead_time,
                # demand shape params (kept for transparency / reproducibility)
                "base_daily_demand": round(base_demand, 3),
                "trend_per_year": trend,
                "weekly_amplitude": weekly_amplitude,
                "noise_cv": noise_cv,
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# 2. Daily demand
#    non-serialised: trend + weekly seasonality + noise (Poisson)
#    serialised:     intermittent — small-lambda Poisson, no weekly pattern
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
        trend = 1.0 + s["trend_per_year"] * (t / 365.0)

        if s["tracking_type"] == "serialised":
            # intermittent demand: no weekly seasonality, lots of zero days.
            # A small-lambda Poisson is naturally lumpy (mostly 0, the odd 1-2).
            mean = np.clip(base * trend, 0.002, None)
            noise = rng.normal(1.0, s["noise_cv"], n_days).clip(0.1, None)
            lam = (mean * noise).clip(0.001, None)
        else:
            # smooth demand with a weekly rhythm (busier toward the weekend)
            season = 1.0 + s["weekly_amplitude"] * np.sin(2 * np.pi * (dow + 1) / 7.0)
            mean = np.clip(base * trend * season, 0.05, None)
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

    SERIALISED items are tracked in whole units: orders are rounded up to whole
    units and the order-up-to level is floored at 1, so capital equipment is
    never stocked in fractional quantities. (Their intermittent demand also
    breaks the normal-demand assumption behind the safety-stock formula - a
    known limitation, noted in the README as future work / Croston's method.)

    Overstock is measured in DAYS-OF-COVER against *recent* demand (trailing
    28-day average), so it captures slow-turning capital rather than a simple
    units-above-target check.
    """
    Z = 1.65
    REVIEW_PERIOD = 7
    POLICY_WINDOW = 120          # days used to set the static policy
    RECENT_WINDOW = 28           # trailing window for "recent" demand
    OVERSTOCK_BUFFER_DAYS = 21   # cover beyond target before we call it overstock

    demand_by_sku = {k: v.sort_values("date") for k, v in demand.groupby("sku_id")}
    ledgers = []

    for _, s in skus.iterrows():
        serial = s["tracking_type"] == "serialised"
        d = demand_by_sku[s["sku_id"]]
        dates = d["date"].to_numpy()
        dem = d["demand_units"].to_numpy().astype(float)
        n = len(dem)

        # --- static policy from the early window only -----------------------
        win = dem[: min(POLICY_WINDOW, n)]
        avg_daily = max(float(win.mean()), 1e-3)
        std_daily = float(win.std(ddof=0))
        lead = int(s["lead_time_days"])

        safety_stock = Z * std_daily * np.sqrt(max(lead, 1))
        reorder_point = avg_daily * lead + safety_stock
        order_up_to = reorder_point + avg_daily * REVIEW_PERIOD
        if serial:
            # whole-unit capital gear: always keep room for at least one unit
            order_up_to = max(order_up_to, 1.0)
        target_days = lead + REVIEW_PERIOD            # cover the policy intends
        overstock_days = target_days + OVERSTOCK_BUFFER_DAYS

        # --- trailing recent-demand average (for days-of-cover) -------------
        recent_avg = pd.Series(dem).rolling(RECENT_WINDOW, min_periods=1).mean().to_numpy()

        on_hand = float(round(order_up_to)) if serial else float(order_up_to)
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
                if serial:
                    order_qty = float(np.ceil(order_qty))   # whole units only
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
    n_serial = int((skus["tracking_type"] == "serialised").sum())
    n_bulk = len(skus) - n_serial
    n_stockout = int(ledger["is_stockout"].sum())
    n_overstock = int(ledger["is_overstock"].sum())

    serial_ids = set(skus.loc[skus["tracking_type"] == "serialised", "sku_id"])
    is_ser = ledger["sku_id"].isin(serial_ids)
    ser_over = ledger.loc[is_ser, "overstock_value"].sum()
    tot_over = ledger["overstock_value"].sum()

    print("Done.")
    print(f"  skus.csv             {len(skus):>8,} rows  "
          f"({n_serial} serialised / {n_bulk} non-serialised)")
    print(f"  daily_demand.csv     {len(demand):>8,} rows  ({days} days)")
    print(f"  inventory_ledger.csv {len(ledger):>8,} rows")
    print(f"  stockout days:  {n_stockout:,}  ({n_stockout / len(ledger):.1%} of ledger)")
    print(f"  overstock days: {n_overstock:,}  ({n_overstock / len(ledger):.1%} of ledger)")
    print(f"  total lost-sales value: {ledger['lost_sales_value'].sum():,.0f}")
    print(f"  total overstock value:  {tot_over:,.0f}  "
          f"({ser_over / tot_over:.0%} of it in serialised capital)")


if __name__ == "__main__":
    main()
