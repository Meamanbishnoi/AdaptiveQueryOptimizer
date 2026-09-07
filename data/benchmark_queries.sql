-- ==============================================================================
-- OS-Aware Adaptive Query Optimization Benchmark Queries
-- Diverse analytical workload: Filter, 1-Join, 2-Join, 3-Join, 4-Join, Aggregations
-- ==============================================================================

-- Q01: Simple point and range filter on orders
SELECT * FROM orders WHERE total_price > 500 AND order_status = 'F' LIMIT 100;

-- Q02: Date-bounded order search with sorting
SELECT order_id, customer_id, order_date, total_price 
FROM orders 
WHERE order_date BETWEEN '2023-01-01' AND '2023-12-31' 
ORDER BY total_price DESC LIMIT 50;

-- Q03: Basic single join (Customer + Orders)
SELECT c.customer_name, c.market_segment, o.order_id, o.total_price
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
WHERE c.market_segment = 'BUILDING';

-- Q04: Filtered join with aggregation
SELECT c.market_segment, COUNT(o.order_id) AS total_orders, AVG(o.total_price) AS avg_spent
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
WHERE o.order_date >= '2023-01-01'
GROUP BY c.market_segment
ORDER BY avg_spent DESC;

-- Q05: High-value orders with customer profile
SELECT c.customer_name, c.account_balance, o.order_id, o.total_price
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
WHERE o.total_price > 2000.0 AND c.account_balance > 5000.0;

-- Q06: Two-table join with order item breakdown
SELECT o.order_id, o.order_date, oi.item_id, oi.quantity, oi.extended_price
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
WHERE oi.quantity > 30;

-- Q07: Aggregation on line items grouped by discount bracket
SELECT oi.discount, COUNT(*) AS item_count, SUM(oi.extended_price) AS revenue
FROM order_items oi
WHERE oi.ship_date BETWEEN '2023-01-01' AND '2023-06-30'
GROUP BY oi.discount
ORDER BY revenue DESC;

-- Q08: 3-Table Join: Customer + Order + LineItem (Classic Decision Support)
SELECT c.customer_name, o.order_id, oi.item_id, oi.extended_price, oi.discount
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
JOIN order_items oi ON o.order_id = oi.order_id
WHERE o.order_status = 'O' AND oi.discount > 0.05;

-- Q09: 3-Table Join Revenue Aggregation by Customer Nation
SELECT c.nation, SUM(oi.extended_price * (1 - oi.discount)) AS net_revenue
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
JOIN order_items oi ON o.order_id = oi.order_id
WHERE o.order_date >= '2023-01-01'
GROUP BY c.nation
HAVING SUM(oi.extended_price) > 50000
ORDER BY net_revenue DESC;

-- Q10: 4-Table Heavy Join: Supplier + Part + OrderItems + Orders
SELECT p.part_name, s.supplier_name, o.order_date, oi.quantity, oi.extended_price
FROM parts p
JOIN order_items oi ON p.part_id = oi.part_id
JOIN suppliers s ON oi.supplier_id = s.supplier_id
JOIN orders o ON oi.order_id = o.order_id
WHERE p.part_type LIKE '%BRASS%' AND s.nation = 'UNITED STATES'
ORDER BY oi.extended_price DESC LIMIT 100;

-- Q11: Heavy Multi-Join with Complex Aggregations and Subquery
SELECT p.part_brand, COUNT(DISTINCT o.order_id) AS distinct_orders, SUM(oi.quantity) AS total_qty
FROM parts p
JOIN order_items oi ON p.part_id = oi.part_id
JOIN orders o ON oi.order_id = o.order_id
WHERE p.retail_price > 100.0
GROUP BY p.part_brand
HAVING SUM(oi.quantity) > 500
ORDER BY total_qty DESC;

-- Q12: Nested Subquery Filter on Customer Spend
SELECT customer_id, customer_name, account_balance
FROM customers
WHERE customer_id IN (
    SELECT o.customer_id 
    FROM orders o 
    JOIN order_items oi ON o.order_id = oi.order_id 
    WHERE oi.extended_price > 5000
)
ORDER BY account_balance DESC;
