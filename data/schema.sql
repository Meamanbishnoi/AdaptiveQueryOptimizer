-- ==============================================================================
-- OS-Aware Adaptive Query Optimization Schema & Seed Data
-- ==============================================================================

DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS parts CASCADE;
DROP TABLE IF EXISTS suppliers CASCADE;
DROP TABLE IF EXISTS customers CASCADE;

CREATE TABLE customers (
    customer_id SERIAL PRIMARY KEY,
    customer_name VARCHAR(100) NOT NULL,
    market_segment VARCHAR(50) NOT NULL,
    nation VARCHAR(50) NOT NULL,
    account_balance NUMERIC(12, 2) NOT NULL
);

CREATE TABLE suppliers (
    supplier_id SERIAL PRIMARY KEY,
    supplier_name VARCHAR(100) NOT NULL,
    nation VARCHAR(50) NOT NULL,
    account_balance NUMERIC(12, 2) NOT NULL
);

CREATE TABLE parts (
    part_id SERIAL PRIMARY KEY,
    part_name VARCHAR(100) NOT NULL,
    part_brand VARCHAR(50) NOT NULL,
    part_type VARCHAR(50) NOT NULL,
    retail_price NUMERIC(10, 2) NOT NULL
);

CREATE TABLE orders (
    order_id SERIAL PRIMARY KEY,
    customer_id INT REFERENCES customers(customer_id),
    order_status CHAR(1) NOT NULL,
    total_price NUMERIC(12, 2) NOT NULL,
    order_date DATE NOT NULL
);

CREATE TABLE order_items (
    item_id SERIAL PRIMARY KEY,
    order_id INT REFERENCES orders(order_id),
    part_id INT REFERENCES parts(part_id),
    supplier_id INT REFERENCES suppliers(supplier_id),
    quantity INT NOT NULL,
    extended_price NUMERIC(12, 2) NOT NULL,
    discount NUMERIC(4, 2) NOT NULL,
    ship_date DATE NOT NULL
);

-- Indices for realistic query plans (Index Scans vs Seq Scans)
CREATE INDEX idx_orders_cust ON orders(customer_id);
CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_orderitems_order ON order_items(order_id);
CREATE INDEX idx_orderitems_part ON order_items(part_id);
