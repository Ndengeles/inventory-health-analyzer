SELECT
    sku_id,
    ROUND(SUM(units_sold) * 100.0 / SUM(demand_units) , 1) AS service_level_pct,
    SUM(demand_units) AS total_demand
FROM
    fact_inventory
GROUP BY sku_id
ORDER BY service_level_pct ASC
LIMIT 10;