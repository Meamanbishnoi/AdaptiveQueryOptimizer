"""
TPC-H workload: the 22 query templates with substitution parameters drawn by the rules of the
TPC-H specification (clause 2.4, the same rules the official qgen tool uses).

    instances(n_per_template, seed) -> list of {"id", "template", "sql"}
"""

import random
from datetime import date
from typing import Dict, List

REGIONS = ["AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"]
NATIONS = [  # (name, region index) in nationkey order
    ("ALGERIA", 0), ("ARGENTINA", 1), ("BRAZIL", 1), ("CANADA", 1), ("EGYPT", 4), ("ETHIOPIA", 0),
    ("FRANCE", 3), ("GERMANY", 3), ("INDIA", 2), ("INDONESIA", 2), ("IRAN", 4), ("IRAQ", 4),
    ("JAPAN", 2), ("JORDAN", 4), ("KENYA", 0), ("MOROCCO", 0), ("MOZAMBIQUE", 0), ("PERU", 1),
    ("CHINA", 2), ("ROMANIA", 3), ("SAUDI ARABIA", 4), ("VIETNAM", 2), ("RUSSIA", 3),
    ("UNITED KINGDOM", 3), ("UNITED STATES", 1),
]
SEGMENTS = ["AUTOMOBILE", "BUILDING", "FURNITURE", "MACHINERY", "HOUSEHOLD"]
TYPE_S1 = ["STANDARD", "SMALL", "MEDIUM", "LARGE", "ECONOMY", "PROMO"]
TYPE_S2 = ["ANODIZED", "BURNISHED", "PLATED", "POLISHED", "BRUSHED"]
TYPE_S3 = ["TIN", "NICKEL", "BRASS", "STEEL", "COPPER"]
CONT_S1 = ["SM", "LG", "MED", "JUMBO", "WRAP"]
CONT_S2 = ["CASE", "BOX", "BAG", "JAR", "PKG", "PACK", "CAN", "DRUM"]
SHIPMODES = ["REG AIR", "AIR", "RAIL", "SHIP", "TRUCK", "MAIL", "FOB"]
COLORS = (
    "almond antique aquamarine azure beige bisque black blanched blue blush brown burlywood burnished "
    "chartreuse chiffon chocolate coral cornflower cornsilk cream cyan dark deep dim dodger drab firebrick "
    "floral forest frosted gainsboro ghost goldenrod green grey honeydew hot indian ivory khaki lace "
    "lavender lawn lemon light lime linen magenta maroon medium metallic midnight mint misty moccasin "
    "navajo navy olive orange orchid pale papaya peach peru pink plum powder puff purple red rose rosy "
    "royal saddle salmon sandy seashell sienna sky slate smoke snow spring steel tan thistle tomato "
    "turquoise violet wheat white yellow"
).split()


def _month(r, y0, m0, y1, m1) -> str:
    months = (y1 - y0) * 12 + (m1 - m0)
    k = r.randint(0, months)
    return date(y0 + (m0 - 1 + k) // 12, (m0 - 1 + k) % 12 + 1, 1).isoformat()


def _brand(r) -> str:
    return f"Brand#{r.randint(1, 5)}{r.randint(1, 5)}"


def q01(r):
    return f"""select l_returnflag, l_linestatus, sum(l_quantity) as sum_qty, sum(l_extendedprice) as sum_base_price,
 sum(l_extendedprice * (1 - l_discount)) as sum_disc_price, sum(l_extendedprice * (1 - l_discount) * (1 + l_tax)) as sum_charge,
 avg(l_quantity) as avg_qty, avg(l_extendedprice) as avg_price, avg(l_discount) as avg_disc, count(*) as count_order
from lineitem where l_shipdate <= date '1998-12-01' - interval '{r.randint(60, 120)} days'
group by l_returnflag, l_linestatus order by l_returnflag, l_linestatus"""


def q02(r):
    size, t3, region = r.randint(1, 50), r.choice(TYPE_S3), r.choice(REGIONS)
    return f"""select s_acctbal, s_name, n_name, p_partkey, p_mfgr, s_address, s_phone, s_comment
from part, supplier, partsupp, nation, region
where p_partkey = ps_partkey and s_suppkey = ps_suppkey and p_size = {size} and p_type like '%{t3}'
 and s_nationkey = n_nationkey and n_regionkey = r_regionkey and r_name = '{region}'
 and ps_supplycost = (select min(ps_supplycost) from partsupp, supplier, nation, region
   where p_partkey = ps_partkey and s_suppkey = ps_suppkey and s_nationkey = n_nationkey
   and n_regionkey = r_regionkey and r_name = '{region}')
order by s_acctbal desc, n_name, s_name, p_partkey limit 100"""


def q03(r):
    d = date(1995, 3, r.randint(1, 31)).isoformat()
    return f"""select l_orderkey, sum(l_extendedprice * (1 - l_discount)) as revenue, o_orderdate, o_shippriority
from customer, orders, lineitem
where c_mktsegment = '{r.choice(SEGMENTS)}' and c_custkey = o_custkey and l_orderkey = o_orderkey
 and o_orderdate < date '{d}' and l_shipdate > date '{d}'
group by l_orderkey, o_orderdate, o_shippriority order by revenue desc, o_orderdate limit 10"""


def q04(r):
    d = _month(r, 1993, 1, 1997, 10)
    return f"""select o_orderpriority, count(*) as order_count from orders
where o_orderdate >= date '{d}' and o_orderdate < date '{d}' + interval '3 months'
 and exists (select * from lineitem where l_orderkey = o_orderkey and l_commitdate < l_receiptdate)
group by o_orderpriority order by o_orderpriority"""


def q05(r):
    d = f"{r.randint(1993, 1997)}-01-01"
    return f"""select n_name, sum(l_extendedprice * (1 - l_discount)) as revenue
from customer, orders, lineitem, supplier, nation, region
where c_custkey = o_custkey and l_orderkey = o_orderkey and l_suppkey = s_suppkey and c_nationkey = s_nationkey
 and s_nationkey = n_nationkey and n_regionkey = r_regionkey and r_name = '{r.choice(REGIONS)}'
 and o_orderdate >= date '{d}' and o_orderdate < date '{d}' + interval '1 year'
group by n_name order by revenue desc"""


def q06(r):
    d = f"{r.randint(1993, 1997)}-01-01"
    disc = r.randint(2, 9) / 100
    return f"""select sum(l_extendedprice * l_discount) as revenue from lineitem
where l_shipdate >= date '{d}' and l_shipdate < date '{d}' + interval '1 year'
 and l_discount between {disc - 0.01:.2f} and {disc + 0.01:.2f} and l_quantity < {r.randint(24, 25)}"""


def q07(r):
    n1, n2 = r.sample([n for n, _ in NATIONS], 2)
    return f"""select supp_nation, cust_nation, l_year, sum(volume) as revenue from (
 select n1.n_name as supp_nation, n2.n_name as cust_nation, extract(year from l_shipdate) as l_year,
  l_extendedprice * (1 - l_discount) as volume
 from supplier, lineitem, orders, customer, nation n1, nation n2
 where s_suppkey = l_suppkey and o_orderkey = l_orderkey and c_custkey = o_custkey
  and s_nationkey = n1.n_nationkey and c_nationkey = n2.n_nationkey
  and ((n1.n_name = '{n1}' and n2.n_name = '{n2}') or (n1.n_name = '{n2}' and n2.n_name = '{n1}'))
  and l_shipdate between date '1995-01-01' and date '1996-12-31') as shipping
group by supp_nation, cust_nation, l_year order by supp_nation, cust_nation, l_year"""


def q08(r):
    nation, reg = r.choice(NATIONS)
    ptype = f"{r.choice(TYPE_S1)} {r.choice(TYPE_S2)} {r.choice(TYPE_S3)}"
    return f"""select o_year, sum(case when nation = '{nation}' then volume else 0 end) / sum(volume) as mkt_share from (
 select extract(year from o_orderdate) as o_year, l_extendedprice * (1 - l_discount) as volume, n2.n_name as nation
 from part, supplier, lineitem, orders, customer, nation n1, nation n2, region
 where p_partkey = l_partkey and s_suppkey = l_suppkey and l_orderkey = o_orderkey and o_custkey = c_custkey
  and c_nationkey = n1.n_nationkey and n1.n_regionkey = r_regionkey and r_name = '{REGIONS[reg]}'
  and s_nationkey = n2.n_nationkey and o_orderdate between date '1995-01-01' and date '1996-12-31'
  and p_type = '{ptype}') as all_nations
group by o_year order by o_year"""


def q09(r):
    return f"""select nation, o_year, sum(amount) as sum_profit from (
 select n_name as nation, extract(year from o_orderdate) as o_year,
  l_extendedprice * (1 - l_discount) - ps_supplycost * l_quantity as amount
 from part, supplier, lineitem, partsupp, orders, nation
 where s_suppkey = l_suppkey and ps_suppkey = l_suppkey and ps_partkey = l_partkey and p_partkey = l_partkey
  and o_orderkey = l_orderkey and s_nationkey = n_nationkey and p_name like '%{r.choice(COLORS)}%') as profit
group by nation, o_year order by nation, o_year desc"""


def q10(r):
    d = _month(r, 1993, 2, 1995, 1)
    return f"""select c_custkey, c_name, sum(l_extendedprice * (1 - l_discount)) as revenue, c_acctbal, n_name,
 c_address, c_phone, c_comment
from customer, orders, lineitem, nation
where c_custkey = o_custkey and l_orderkey = o_orderkey and o_orderdate >= date '{d}'
 and o_orderdate < date '{d}' + interval '3 months' and l_returnflag = 'R' and c_nationkey = n_nationkey
group by c_custkey, c_name, c_acctbal, c_phone, n_name, c_address, c_comment order by revenue desc limit 20"""


def q11(r):
    nation = r.choice(NATIONS)[0]
    return f"""select ps_partkey, sum(ps_supplycost * ps_availqty) as value
from partsupp, supplier, nation
where ps_suppkey = s_suppkey and s_nationkey = n_nationkey and n_name = '{nation}'
group by ps_partkey having sum(ps_supplycost * ps_availqty) > (
 select sum(ps_supplycost * ps_availqty) * 0.0001 from partsupp, supplier, nation
 where ps_suppkey = s_suppkey and s_nationkey = n_nationkey and n_name = '{nation}')
order by value desc"""


def q12(r):
    m1, m2 = r.sample(SHIPMODES, 2)
    d = f"{r.randint(1993, 1997)}-01-01"
    return f"""select l_shipmode,
 sum(case when o_orderpriority = '1-URGENT' or o_orderpriority = '2-HIGH' then 1 else 0 end) as high_line_count,
 sum(case when o_orderpriority <> '1-URGENT' and o_orderpriority <> '2-HIGH' then 1 else 0 end) as low_line_count
from orders, lineitem
where o_orderkey = l_orderkey and l_shipmode in ('{m1}', '{m2}') and l_commitdate < l_receiptdate
 and l_shipdate < l_commitdate and l_receiptdate >= date '{d}' and l_receiptdate < date '{d}' + interval '1 year'
group by l_shipmode order by l_shipmode"""


def q13(r):
    w1 = r.choice(["special", "pending", "unusual", "express"])
    w2 = r.choice(["packages", "requests", "accounts", "deposits"])
    return f"""select c_count, count(*) as custdist from (
 select c_custkey, count(o_orderkey) as c_count
 from customer left outer join orders on c_custkey = o_custkey and o_comment not like '%{w1}%{w2}%'
 group by c_custkey) as c_orders
group by c_count order by custdist desc, c_count desc"""


def q14(r):
    d = _month(r, 1993, 1, 1997, 12)
    return f"""select 100.00 * sum(case when p_type like 'PROMO%' then l_extendedprice * (1 - l_discount) else 0 end)
 / sum(l_extendedprice * (1 - l_discount)) as promo_revenue
from lineitem, part
where l_partkey = p_partkey and l_shipdate >= date '{d}' and l_shipdate < date '{d}' + interval '1 month'"""


def q15(r):
    d = _month(r, 1993, 1, 1997, 10)
    return f"""with revenue as (
 select l_suppkey as supplier_no, sum(l_extendedprice * (1 - l_discount)) as total_revenue from lineitem
 where l_shipdate >= date '{d}' and l_shipdate < date '{d}' + interval '3 months' group by l_suppkey)
select s_suppkey, s_name, s_address, s_phone, total_revenue from supplier, revenue
where s_suppkey = supplier_no and total_revenue = (select max(total_revenue) from revenue) order by s_suppkey"""


def q16(r):
    sizes = ", ".join(str(s) for s in r.sample(range(1, 51), 8))
    return f"""select p_brand, p_type, p_size, count(distinct ps_suppkey) as supplier_cnt
from partsupp, part
where p_partkey = ps_partkey and p_brand <> '{_brand(r)}' and p_type not like '{r.choice(TYPE_S1)} {r.choice(TYPE_S2)}%'
 and p_size in ({sizes})
 and ps_suppkey not in (select s_suppkey from supplier where s_comment like '%Customer%Complaints%')
group by p_brand, p_type, p_size order by supplier_cnt desc, p_brand, p_type, p_size"""


def q17(r):
    return f"""select sum(l_extendedprice) / 7.0 as avg_yearly from lineitem, part
where p_partkey = l_partkey and p_brand = '{_brand(r)}' and p_container = '{r.choice(CONT_S1)} {r.choice(CONT_S2)}'
 and l_quantity < (select 0.2 * avg(l_quantity) from lineitem where l_partkey = p_partkey)"""


def q18(r):
    return f"""select c_name, c_custkey, o_orderkey, o_orderdate, o_totalprice, sum(l_quantity)
from customer, orders, lineitem
where o_orderkey in (select l_orderkey from lineitem group by l_orderkey having sum(l_quantity) > {r.randint(312, 315)})
 and c_custkey = o_custkey and o_orderkey = l_orderkey
group by c_name, c_custkey, o_orderkey, o_orderdate, o_totalprice order by o_totalprice desc, o_orderdate limit 100"""


def q19(r):
    q1, q2, q3 = r.randint(1, 10), r.randint(10, 20), r.randint(20, 30)
    b1, b2, b3 = _brand(r), _brand(r), _brand(r)
    return f"""select sum(l_extendedprice * (1 - l_discount)) as revenue from lineitem, part
where (p_partkey = l_partkey and p_brand = '{b1}' and p_container in ('SM CASE', 'SM BOX', 'SM PACK', 'SM PKG')
  and l_quantity >= {q1} and l_quantity <= {q1 + 10} and p_size between 1 and 5
  and l_shipmode in ('AIR', 'AIR REG') and l_shipinstruct = 'DELIVER IN PERSON')
 or (p_partkey = l_partkey and p_brand = '{b2}' and p_container in ('MED BAG', 'MED BOX', 'MED PKG', 'MED PACK')
  and l_quantity >= {q2} and l_quantity <= {q2 + 10} and p_size between 1 and 10
  and l_shipmode in ('AIR', 'AIR REG') and l_shipinstruct = 'DELIVER IN PERSON')
 or (p_partkey = l_partkey and p_brand = '{b3}' and p_container in ('LG CASE', 'LG BOX', 'LG PACK', 'LG PKG')
  and l_quantity >= {q3} and l_quantity <= {q3 + 10} and p_size between 1 and 15
  and l_shipmode in ('AIR', 'AIR REG') and l_shipinstruct = 'DELIVER IN PERSON')"""


def q20(r):
    d = f"{r.randint(1993, 1997)}-01-01"
    return f"""select s_name, s_address from supplier, nation
where s_suppkey in (
 select ps_suppkey from partsupp
 where ps_partkey in (select p_partkey from part where p_name like '{r.choice(COLORS)}%')
  and ps_availqty > (select 0.5 * sum(l_quantity) from lineitem where l_partkey = ps_partkey
   and l_suppkey = ps_suppkey and l_shipdate >= date '{d}' and l_shipdate < date '{d}' + interval '1 year'))
 and s_nationkey = n_nationkey and n_name = '{r.choice(NATIONS)[0]}'
order by s_name"""


def q21(r):
    return f"""select s_name, count(*) as numwait from supplier, lineitem l1, orders, nation
where s_suppkey = l1.l_suppkey and o_orderkey = l1.l_orderkey and o_orderstatus = 'F'
 and l1.l_receiptdate > l1.l_commitdate
 and exists (select * from lineitem l2 where l2.l_orderkey = l1.l_orderkey and l2.l_suppkey <> l1.l_suppkey)
 and not exists (select * from lineitem l3 where l3.l_orderkey = l1.l_orderkey and l3.l_suppkey <> l1.l_suppkey
  and l3.l_receiptdate > l3.l_commitdate)
 and s_nationkey = n_nationkey and n_name = '{r.choice(NATIONS)[0]}'
group by s_name order by numwait desc, s_name limit 100"""


def q22(r):
    codes = ", ".join(f"'{c}'" for c in r.sample(range(10, 35), 7))
    return f"""select cntrycode, count(*) as numcust, sum(c_acctbal) as totacctbal from (
 select substring(c_phone from 1 for 2) as cntrycode, c_acctbal from customer
 where substring(c_phone from 1 for 2) in ({codes})
  and c_acctbal > (select avg(c_acctbal) from customer where c_acctbal > 0.00
   and substring(c_phone from 1 for 2) in ({codes}))
  and not exists (select * from orders where o_custkey = c_custkey)) as custsale
group by cntrycode order by cntrycode"""


TEMPLATES = {f"Q{i:02d}": f for i, f in enumerate(
    [q01, q02, q03, q04, q05, q06, q07, q08, q09, q10, q11, q12, q13, q14, q15, q16, q17, q18, q19, q20, q21, q22], 1)}


def instances(n_per_template: int, seed: int = 42, templates=None) -> List[Dict[str, str]]:
    """n_per_template random instances of each template (deterministic for a given seed)."""
    out = []
    for name, fn in TEMPLATES.items():
        if templates and name not in templates:
            continue
        r = random.Random(f"{seed}-{name}")
        for k in range(n_per_template):
            out.append({"id": f"{name}-{k:02d}", "template": name, "sql": fn(r)})
    return out


# Query classes for the scheduling experiments: templates whose default plan runs < 0.5 s at idle
# (measured, results/runs.jsonl) are "interactive" (a user waits for them); the rest are "batch" reports.
INTERACTIVE = ("Q02", "Q04", "Q08", "Q11", "Q14", "Q15", "Q19", "Q20", "Q22")


def query_class(template: str) -> str:
    return "interactive" if template in INTERACTIVE else "batch"


def make_trace(n_jobs: int, rate_per_s: float, seed: int = 1, p_interactive: float = 0.6) -> List[Dict]:
    """
    A multi-user arrival trace: queries arrive as a Poisson process (exponential gaps, mean 1/rate);
    each is interactive with probability p_interactive, else batch; template uniform within its class;
    fresh random parameters (seed 1000+, never used for training).
    """
    r = random.Random(f"trace-{seed}")
    inter = [t for t in TEMPLATES if t in INTERACTIVE]
    batch = [t for t in TEMPLATES if t not in INTERACTIVE]
    t, out = 0.0, []
    for i in range(n_jobs):
        name = r.choice(inter if r.random() < p_interactive else batch)
        sql = TEMPLATES[name](random.Random(f"{1000 + seed}-{i}-{name}"))
        out.append({"at": round(t, 3), "template": name, "cls": query_class(name), "sql": sql})
        t += r.expovariate(rate_per_s)
    return out
