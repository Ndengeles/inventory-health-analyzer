WITH sku_value AS (
    SELECT
        f.sku_id, ROUND(SUM(f.units_sold * d.unit_price)) AS revenue
    FROM fact_inventory f
    JOIN dim_sku d ON f.sku_id = d.sku_id
    GROUP BY f.sku_id
),
classified AS (
    SELECT
        sku_id,
        revenue,
        SUM(revenue) OVER (ORDER BY revenue DESC) AS running_total,
        SUM(revenue) OVER () AS grand_total,
        ROUND(SUM(revenue) OVER (ORDER BY revenue DESC) * 100.0 / SUM(revenue) OVER (), 1) AS cum_pct
    FROM sku_value
)
SELECT
    sku_id,
    revenue,
    running_total,
    grand_total,
    cum_pct,
    CASE
        WHEN cum_pct <= 80 THEN 'A'
        WHEN cum_pct <= 95 THEN 'B'
        ELSE 'C'
    END AS abc_class
FROM classified
ORDER BY revenue DESC;