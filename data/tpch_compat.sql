-- ==============================================================================
-- TPC-H compatibility layer over the QuerySync benchmark schema (data/schema.sql).
--
-- Lets the TPC-H-style queries in data/tpch_benchmark.sql run unchanged by exposing the
-- benchmark tables under TPC-H names. Captured from the live osaware_db so a freshly
-- seeded database is identical to the one used in the experiments.
--
-- IMPORTANT for reporting: this is NOT a real TPC-H database (no dbgen data). Several
-- TPC-H columns are constants (l_returnflag, l_linestatus, l_tax, p_size, p_container,
-- l_shipmode, ...), dates are shared (commit/receipt = ship date), and partsupp is a view
-- over order_items. Query shapes match TPC-H; selectivities and result sizes do not.
-- Describe results as "TPC-H-style queries on a synthetic schema", not as TPC-H results.
-- ==============================================================================

DROP VIEW IF EXISTS lineitem, customer, supplier, part, partsupp CASCADE;
DROP TABLE IF EXISTS nation CASCADE;
DROP TABLE IF EXISTS region CASCADE;

CREATE TABLE region (
    r_regionkey integer PRIMARY KEY,
    r_name      varchar(25) NOT NULL,
    r_comment   varchar(152)
);

CREATE TABLE nation (
    n_nationkey integer PRIMARY KEY,
    n_name      varchar(25) NOT NULL,
    n_regionkey integer REFERENCES region(r_regionkey),
    n_comment   varchar(152)
);

INSERT INTO region VALUES
    (0, 'AFRICA', 'African continent'),
    (1, 'AMERICA', 'American continent'),
    (2, 'ASIA', 'Asian continent'),
    (3, 'EUROPE', 'European continent'),
    (4, 'MIDDLE EAST', 'Middle Eastern continent');

INSERT INTO nation (n_nationkey, n_name, n_regionkey) VALUES
    (0, 'ALGERIA', 0), (1, 'ARGENTINA', 1), (2, 'BRAZIL', 1), (3, 'CANADA', 1),
    (4, 'EGYPT', 4), (5, 'ETHIOPIA', 0), (6, 'FRANCE', 3), (7, 'GERMANY', 3),
    (8, 'INDIA', 2), (9, 'INDONESIA', 2), (10, 'IRAN', 4), (11, 'IRAQ', 4),
    (12, 'JAPAN', 2), (13, 'JORDAN', 4), (14, 'KENYA', 0), (15, 'MOROCCO', 0),
    (16, 'MOZAMBIQUE', 0), (17, 'PERU', 1), (18, 'CHINA', 2), (19, 'ROMANIA', 3),
    (20, 'SAUDI ARABIA', 4), (21, 'VIETNAM', 2), (22, 'RUSSIA', 3),
    (23, 'UNITED KINGDOM', 3), (24, 'UNITED STATES', 1);

-- TPC-H column names on orders (stored generated columns + constant defaults)
ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS o_orderkey    integer      GENERATED ALWAYS AS (order_id) STORED,
    ADD COLUMN IF NOT EXISTS o_custkey     integer      GENERATED ALWAYS AS (customer_id) STORED,
    ADD COLUMN IF NOT EXISTS o_orderstatus character(1) GENERATED ALWAYS AS (order_status) STORED,
    ADD COLUMN IF NOT EXISTS o_totalprice  numeric(12,2) GENERATED ALWAYS AS (total_price) STORED,
    ADD COLUMN IF NOT EXISTS o_orderdate   date         GENERATED ALWAYS AS (order_date) STORED,
    ADD COLUMN IF NOT EXISTS o_orderpriority varchar(15) DEFAULT '1-URGENT',
    ADD COLUMN IF NOT EXISTS o_clerk       varchar(15)  DEFAULT 'Clerk#000000001',
    ADD COLUMN IF NOT EXISTS o_shippriority integer     DEFAULT 0,
    ADD COLUMN IF NOT EXISTS o_comment     varchar(79)  DEFAULT 'Standard order comment';

CREATE INDEX IF NOT EXISTS idx_orders_o_orderdate ON orders (o_orderdate);
CREATE INDEX IF NOT EXISTS idx_orders_o_custkey ON orders (o_custkey);

CREATE VIEW customer AS
 SELECT customer_id AS c_custkey,
    customer_name AS c_name,
    'address'::varchar(40) AS c_address,
    (customer_id % 25) AS c_nationkey,
    '123-456'::varchar(15) AS c_phone,
    account_balance AS c_acctbal,
    market_segment AS c_mktsegment,
    'comment'::varchar(117) AS c_comment
   FROM customers c;

CREATE VIEW lineitem AS
 SELECT order_id AS l_orderkey,
    part_id AS l_partkey,
    supplier_id AS l_suppkey,
    item_id AS l_linenumber,
    quantity AS l_quantity,
    extended_price AS l_extendedprice,
    discount AS l_discount,
    0.05::numeric(4,2) AS l_tax,
    'R'::character(1) AS l_returnflag,
    'O'::character(1) AS l_linestatus,
    ship_date AS l_shipdate,
    ship_date AS l_commitdate,
    ship_date AS l_receiptdate,
    'DELIVER IN PERSON'::varchar(25) AS l_shipinstruct,
    'AIR'::varchar(10) AS l_shipmode,
    'comment'::varchar(44) AS l_comment
   FROM order_items oi;

CREATE VIEW part AS
 SELECT part_id AS p_partkey,
    part_name AS p_name,
    part_brand AS p_mfgr,
    part_brand AS p_brand,
    part_type AS p_type,
    15 AS p_size,
    'JUMBO'::varchar(10) AS p_container,
    retail_price AS p_retailprice,
    'comment'::varchar(23) AS p_comment
   FROM parts p;

CREATE VIEW partsupp AS
 SELECT part_id AS ps_partkey,
    supplier_id AS ps_suppkey,
    quantity AS ps_availqty,
    extended_price AS ps_supplycost,
    'comment'::varchar(199) AS ps_comment
   FROM order_items oi;

CREATE VIEW supplier AS
 SELECT supplier_id AS s_suppkey,
    supplier_name AS s_name,
    'address'::varchar(40) AS s_address,
    (supplier_id % 25) AS s_nationkey,
    '123-456'::varchar(15) AS s_phone,
    account_balance AS s_acctbal,
    'comment'::varchar(101) AS s_comment
   FROM suppliers s;
