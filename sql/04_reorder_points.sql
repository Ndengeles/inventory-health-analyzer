WITH ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY sku_id ORDER BY date DESC) AS rn
    FROM fact_inventory
),
latest AS (
    SELECT * FROM ranked WHERE rn = 1
),
period_totals AS (
    SELECT
        sku_id,
        ROUND(SUM(lost_sales_value)) AS total_lost_sales
    FROM fact_inventory
    GROUP BY sku_id
)
SELECT
    l.sku_id,
    s.sku_name,
    s.category,
    l.on_hand_units,
    l.reorder_point,
    l.overstock_value,
    p.total_lost_sales,
    l.is_stockout,
    l.is_overstock, 
    CASE
        WHEN l.is_stockout = 1 THEN p.total_lost_sales
        WHEN l.is_overstock = 1 THEN l.overstock_value
        ELSE 0
    END AS impact_value
FROM latest l
JOIN period_totals p ON l.sku_id = p.sku_id
JOIN dim_sku s       ON l.sku_id = s.sku_id
WHERE l.is_stockout = 1 OR l.is_overstock = 1
ORDER BY impact_value DESC;