-- Revenue Intelligence POC seed script (expanded dataset)
-- This script targets non-UC warehouses as well by using hive_metastore.
-- Time range: Apr 2023 through Mar 2026 (36 monthly periods).

CREATE SCHEMA IF NOT EXISTS hive_metastore.ri_poc_test;

CREATE OR REPLACE TABLE hive_metastore.ri_poc_test.dim_region (
  region_id STRING,
  region_code STRING,
  region_name STRING,
  country STRING
);

CREATE OR REPLACE TABLE hive_metastore.ri_poc_test.dim_product (
  product_id STRING,
  product_family STRING,
  product_name STRING
);

CREATE OR REPLACE TABLE hive_metastore.ri_poc_test.dim_sales_channel (
  channel_id STRING,
  channel_name STRING
);

CREATE OR REPLACE TABLE hive_metastore.ri_poc_test.dim_customer_segment (
  segment_id STRING,
  segment_name STRING
);

CREATE OR REPLACE TABLE hive_metastore.ri_poc_test.dim_date (
  date_key DATE,
  month_index INT,
  fiscal_year INT,
  fiscal_quarter STRING,
  month_name STRING
);

CREATE OR REPLACE TABLE hive_metastore.ri_poc_test.fact_revenue (
  date_key DATE,
  region_id STRING,
  product_id STRING,
  segment_id STRING,
  channel_id STRING,
  gross_amount DECIMAL(18,2),
  discount_amount DECIMAL(18,2),
  net_amount DECIMAL(18,2),
  arr_amount DECIMAL(18,2),
  units INT
);

CREATE OR REPLACE TABLE hive_metastore.ri_poc_test.fact_quota (
  date_key DATE,
  region_id STRING,
  product_id STRING,
  quota_amount DECIMAL(18,2)
);

CREATE OR REPLACE TABLE hive_metastore.ri_poc_test.metric_catalog (
  metric_code STRING,
  metric_name STRING,
  metric_formula STRING,
  metric_grain STRING,
  metric_owner STRING
);

CREATE OR REPLACE TABLE hive_metastore.ri_poc_test.principal_region_access (
  principal STRING,
  region_code STRING
);

INSERT OVERWRITE hive_metastore.ri_poc_test.dim_region VALUES
  ('R1', 'NA', 'North America', 'United States'),
  ('R2', 'EMEA', 'Europe, Middle East and Africa', 'Germany'),
  ('R3', 'APAC', 'Asia Pacific', 'Singapore'),
  ('R4', 'LATAM', 'Latin America', 'Brazil'),
  ('R5', 'ANZ', 'Australia and New Zealand', 'Australia');

INSERT OVERWRITE hive_metastore.ri_poc_test.dim_product VALUES
  ('P1', 'Data Platform', 'Analytics Pro'),
  ('P2', 'Data Platform', 'Data Core'),
  ('P3', 'AI Add-on', 'Predictive Insights'),
  ('P4', 'AI Add-on', 'Forecast Studio'),
  ('P5', 'Security', 'Data Shield'),
  ('P6', 'Services', 'Advisory Pack');

INSERT OVERWRITE hive_metastore.ri_poc_test.dim_sales_channel VALUES
  ('C1', 'Direct'),
  ('C2', 'Partner'),
  ('C3', 'Online');

INSERT OVERWRITE hive_metastore.ri_poc_test.dim_customer_segment VALUES
  ('S1', 'Enterprise'),
  ('S2', 'Mid-Market'),
  ('S3', 'SMB');

INSERT OVERWRITE hive_metastore.ri_poc_test.dim_date
SELECT
  d AS date_key,
  ROW_NUMBER() OVER (ORDER BY d) - 1 AS month_index,
  year(add_months(d, 3)) AS fiscal_year,
  concat('Q', cast(quarter(add_months(d, 3)) AS STRING)) AS fiscal_quarter,
  date_format(d, 'MMMM') AS month_name
FROM (
  SELECT explode(sequence(to_date('2023-04-15'), to_date('2026-03-15'), interval 1 month)) AS d
) t;

INSERT OVERWRITE hive_metastore.ri_poc_test.metric_catalog VALUES
  ('gross_revenue', 'Gross Revenue', 'SUM(gross_amount)', 'region,product,segment,channel,time', 'RevenueOps'),
  ('net_revenue', 'Net Revenue', 'SUM(net_amount)', 'region,product,segment,channel,time', 'RevenueOps'),
  ('arr', 'Annual Recurring Revenue', 'SUM(arr_amount)', 'region,product,segment,channel,time', 'RevenueOps'),
  ('discount_pct', 'Discount Percent', 'SUM(discount_amount)/NULLIF(SUM(gross_amount),0)', 'region,product,time', 'RevenueOps'),
  ('attainment_pct', 'Attainment Percent', 'SUM(net_amount)/NULLIF(SUM(quota_amount),0)', 'region,product,time', 'RevenueOps'),
  ('yoy_growth_pct', 'Year-over-Year Growth Percent', '(rev_t - rev_t_1)/NULLIF(rev_t_1,0)', 'region,product,time', 'RevenueOps');

INSERT OVERWRITE hive_metastore.ri_poc_test.fact_revenue
WITH base AS (
  SELECT
    d.date_key,
    d.month_index,
    r.region_id,
    p.product_id,
    s.segment_id,
    c.channel_id,
    CAST(substr(r.region_id, 2, 1) AS INT) AS region_factor,
    CAST(substr(p.product_id, 2, 1) AS INT) AS product_factor,
    CAST(substr(s.segment_id, 2, 1) AS INT) AS segment_factor,
    CAST(substr(c.channel_id, 2, 1) AS INT) AS channel_factor,
    (pmod(abs(hash(concat(cast(d.date_key AS STRING), r.region_id, p.product_id, s.segment_id, c.channel_id))), 21) - 10) / 100.0 AS noise
  FROM hive_metastore.ri_poc_test.dim_date d
  CROSS JOIN hive_metastore.ri_poc_test.dim_region r
  CROSS JOIN hive_metastore.ri_poc_test.dim_product p
  CROSS JOIN hive_metastore.ri_poc_test.dim_customer_segment s
  CROSS JOIN hive_metastore.ri_poc_test.dim_sales_channel c
),
calc AS (
  SELECT
    date_key,
    region_id,
    product_id,
    segment_id,
    channel_id,
    ((18000 + region_factor * 2500 + product_factor * 1700 + segment_factor * 1200 + channel_factor * 900 + month_index * 350)
      * (CASE WHEN month(date_key) IN (11, 12) THEN 1.18 WHEN month(date_key) IN (6, 7) THEN 0.94 ELSE 1.00 END)
      * (1 + noise)) AS gross_base,
    LEAST(0.22, 0.06 + segment_factor * 0.012 + channel_factor * 0.006) AS discount_rate,
    CASE
      WHEN product_id IN ('P1', 'P2') THEN 0.92
      WHEN product_id IN ('P3', 'P4') THEN 0.88
      WHEN product_id = 'P5' THEN 0.84
      ELSE 0.72
    END AS arr_ratio
  FROM base
)
SELECT
  date_key,
  region_id,
  product_id,
  segment_id,
  channel_id,
  CAST(round(gross_base, 2) AS DECIMAL(18,2)) AS gross_amount,
  CAST(round(gross_base * discount_rate, 2) AS DECIMAL(18,2)) AS discount_amount,
  CAST(round(gross_base * (1 - discount_rate), 2) AS DECIMAL(18,2)) AS net_amount,
  CAST(round(gross_base * (1 - discount_rate) * arr_ratio, 2) AS DECIMAL(18,2)) AS arr_amount,
  CAST(greatest(1, round((gross_base * (1 - discount_rate)) / 950, 0)) AS INT) AS units
FROM calc;

INSERT OVERWRITE hive_metastore.ri_poc_test.fact_quota
SELECT
  f.date_key,
  f.region_id,
  f.product_id,
  CAST(round(SUM(f.net_amount) * 1.06, 2) AS DECIMAL(18,2)) AS quota_amount
FROM hive_metastore.ri_poc_test.fact_revenue f
GROUP BY f.date_key, f.region_id, f.product_id;

INSERT OVERWRITE hive_metastore.ri_poc_test.principal_region_access VALUES
  ('grp_revenue_na', 'NA'),
  ('grp_revenue_emea', 'EMEA'),
  ('grp_revenue_apac', 'APAC'),
  ('grp_revenue_latam', 'LATAM'),
  ('grp_revenue_anz', 'ANZ'),
  ('grp_revenue_global', 'NA'),
  ('grp_revenue_global', 'EMEA'),
  ('grp_revenue_global', 'APAC'),
  ('grp_revenue_global', 'LATAM'),
  ('grp_revenue_global', 'ANZ');

CREATE OR REPLACE VIEW hive_metastore.ri_poc_test.v_fact_revenue_secure AS
SELECT f.*
FROM hive_metastore.ri_poc_test.fact_revenue f;

-- NOTE:
-- In UC-enabled environments, replace the secure view definition above with
-- principal-aware row filters using is_account_group_member() and region mapping.

-- Quick validation queries:
-- SELECT min(date_key) AS min_date, max(date_key) AS max_date, count(*) AS months FROM hive_metastore.ri_poc_test.dim_date;
-- SELECT count(*) AS revenue_rows FROM hive_metastore.ri_poc_test.fact_revenue;
-- SELECT count(*) AS quota_rows FROM hive_metastore.ri_poc_test.fact_quota;
