SELECT
    ROUND(SUM(units_sold) /SUM(demand_units) * 100.0, 1) AS service_level_pct
FROM
    fact_inventory;