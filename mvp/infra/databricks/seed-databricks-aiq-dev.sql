CREATE CATALOG IF NOT EXISTS __CATALOG__;
CREATE SCHEMA IF NOT EXISTS __CATALOG__.data_science_account_iq_gold;
CREATE SCHEMA IF NOT EXISTS __CATALOG__.account_iq_gold;

CREATE OR REPLACE TABLE __CATALOG__.data_science_account_iq_gold.account_iq_scores (
  account_id STRING NOT NULL,
  account_name STRING NOT NULL,
  company_name STRING NOT NULL,
  sales_team STRING NOT NULL,
  xf_score_previous_day DOUBLE,
  xf_score_diff_pct DOUBLE,
  intent DOUBLE,
  competitive DOUBLE,
  upsell DOUBLE,
  fit INT,
  need INT,
  vdp_why STRING,
  kasten_why STRING,
  o365_why STRING,
  vbsf_why STRING,
  cloud_why STRING,
  sales_play_sell_vdp BOOLEAN,
  sales_play_sell_kasten BOOLEAN,
  sales_play_sell_o365 BOOLEAN,
  sales_play_sell_vbsf BOOLEAN,
  sales_play_sell_cloud BOOLEAN,
  sales_play_sell_vault BOOLEAN,
  sales_play_vmware_migration BOOLEAN,
  sales_play_upsell_vdp BOOLEAN,
  sales_play_convert_to_vdc BOOLEAN
);

INSERT OVERWRITE __CATALOG__.data_science_account_iq_gold.account_iq_scores VALUES
  ('001GL0001', 'Ford Motor Company', 'Ford Motor Company', 'GreatLakes-ENT-Named-1', 74.0, 8.0, 92.0, 3.0, 35.0, 60, 51, 'Headquartered account,Heavy VMware environment,10+ cloud workloads,Storage hardware over 3 years old', 'Has Kubernetes,Multiple distributions,OpenShift present', 'Microsoft 365 footprint with no premium protection', 'Salesforce present across business units', 'High cloud budget across AWS Azure and GCP', false, false, false, false, false, false, true, false, false),
  ('001GL0003', 'Bridgestone Americas, Inc.', 'BRIDGESTONE CORPORATION', 'GreatLakes-ENT-Named-1', 81.0, 11.0, 88.0, 4.0, 68.0, 77, 66, 'Existing customer with aging storage and strong fit for broader protection', 'Moderate Kubernetes footprint', 'M365 usage expanding', 'Salesforce critical workflows detected', 'Cloud workloads growing with weak multicloud backup coverage', false, false, false, false, true, true, false, true, false),
  ('001GL0005', 'Ascension Health - IS, Inc.', 'Ascension Health Alliance', 'GreatLakes-ENT-Named-1', 73.0, 6.0, 95.0, 0.0, 0.0, 69, 48, 'Top healthcare fit,Decision-making center,Hybrid infrastructure complexity', 'Kubernetes maturity present', 'Large Microsoft 365 footprint', 'Clinical application dependency suggests Salesforce relevance', 'Cloud growth with resilience gaps', false, false, true, false, true, false, false, false, false),
  ('001GL0006', 'Liberty Mutual', 'The Ohio Casualty Insurance Company', 'GreatLakes-ENT-Named-1', 78.0, 2.0, 94.0, 2.0, 0.0, 80, 50, 'Top insurance fit,Premium security offerings detected,Storage refresh window', 'Container presence detected', 'Office footprint with no modern protection', 'Salesforce footprint across field teams', 'High cloud budget with multiple providers', false, false, false, false, false, false, false, false, false),
  ('001GE0001', 'adidas AG', 'adidas AG', 'Germany-ENT-Named-5', 89.0, 12.0, 91.0, 5.0, 82.0, 85, 79, 'Global enterprise fit,Board-level resilience concern,Infrastructure modernization underway', 'Strong Kubernetes and OpenShift maturity', 'Large Microsoft 365 footprint', 'Salesforce used in regional operations', 'Significant multicloud presence with modernization pressure', false, true, true, false, true, true, false, true, false),
  ('001GE0002', 'DATEV eG', 'DATEV eG', 'Germany-ENT-Named-5', 83.0, 9.0, 96.0, 1.0, 79.0, 81, 74, 'High intent account,Decision power in core IT team,Strong data protection need', 'Moderate Kubernetes footprint', 'M365 protection gap', NULL, 'Cloud maturity increasing with no multicloud backup detected', true, false, true, false, true, false, false, false, false),
  ('001GE0003', 'Porsche Digital GmbH', 'Dr. Ing. h.c. F. Porsche AG', 'Germany-ENT-Named-5', 71.0, 7.0, 84.0, 3.0, 52.0, 68, 61, 'Digital subsidiary with product velocity and strong fit for expansion', 'Container engineering footprint', 'M365 footprint with opportunity to expand', NULL, 'Public cloud engineering teams growing', false, true, false, false, true, false, false, false, true);

CREATE OR REPLACE TABLE __CATALOG__.account_iq_gold.aiq_contact (
  domain_account_id STRING NOT NULL,
  first_name STRING,
  last_name STRING,
  name STRING NOT NULL,
  title STRING,
  job_position STRING,
  email STRING,
  phone STRING,
  engagement_level STRING,
  contact_stage STRING,
  last_activity_date DATE,
  do_not_call BOOLEAN
);

INSERT OVERWRITE __CATALOG__.account_iq_gold.aiq_contact VALUES
  ('001GL0001', 'Todd', 'Wagner', 'Todd Wagner', 'Director of Infrastructure', 'IT Leaders', 'todd.wagner@ford.example', '555-0101', 'Engaged', 'Marketing Qualified Contact', DATE '2026-03-08', false),
  ('001GL0001', 'Brandon', 'Gentry', 'Brandon Gentry', 'CISO', 'Executive Leaders', 'brandon.gentry@ford.example', '555-0102', 'Engaged', 'Sales Accepted Contact', DATE '2026-02-22', false),
  ('001GL0003', 'Mina', 'Chen', 'Mina Chen', 'Vice President, Infrastructure', 'Executive Leaders', 'mina.chen@bridgestone.example', '555-0110', 'Engaged', 'Marketing Qualified Contact', DATE '2026-03-10', false),
  ('001GL0003', 'Sam', 'Lopez', 'Sam Lopez', 'Director of IT Operations', 'IT Leaders', 'sam.lopez@bridgestone.example', '555-0111', NULL, 'Marketing Qualified Contact', DATE '2025-07-10', false),
  ('001GL0005', 'Sean', 'Masterson', 'Sean Masterson', 'Director of Technology', 'IT Leaders', 'sean.masterson@ascension.example', '555-0120', 'Engaged', 'Marketing Qualified Contact', DATE '2026-03-04', false),
  ('001GL0005', 'Jeanne', 'Pilbeam', 'Jeanne Pilbeam', 'Information Technology Manager', 'IT Practitioner', 'jeanne.pilbeam@ascension.example', '555-0121', NULL, 'Marketing Qualified Contact', DATE '2025-08-15', false),
  ('001GE0001', 'Anika', 'Schmidt', 'Anika Schmidt', 'CIO', 'Executive Leaders', 'anika.schmidt@adidas.example', '555-0201', 'Engaged', 'Sales Accepted Contact', DATE '2026-03-09', false),
  ('001GE0001', 'Lars', 'Weber', 'Lars Weber', 'Director of Platform Engineering', 'IT Leaders', 'lars.weber@adidas.example', '555-0202', 'Engaged', 'Marketing Qualified Contact', DATE '2026-02-28', false),
  ('001GE0002', 'Helena', 'Bauer', 'Helena Bauer', 'Head of Infrastructure', 'Executive Leaders', 'helena.bauer@datev.example', '555-0210', 'Engaged', 'Marketing Qualified Contact', DATE '2026-03-07', false),
  ('001GE0003', 'Markus', 'Klein', 'Markus Klein', 'Cloud Architect', 'IT Practitioner', 'markus.klein@porsche-digital.example', '555-0220', NULL, 'Marketing Qualified Contact', DATE '2025-05-20', false),
  ('001GE0003', 'Markus', 'Klein', 'Markus Klein', 'Cloud Architect', 'IT Practitioner', 'markus.klein+alt@porsche-digital.example', '555-0220', NULL, 'Marketing Qualified Contact', DATE '2024-11-20', false),
  ('001GL0006', 'Terry', 'Miles', 'Terry Miles', 'Security Director', 'Executive Leaders', 'terry.miles@liberty.example', '555-0131', NULL, 'Marketing Qualified Contact', DATE '2024-10-01', true);

SELECT 'account_iq_scores' AS object_name, COUNT(*) AS row_count FROM __CATALOG__.data_science_account_iq_gold.account_iq_scores
UNION ALL
SELECT 'aiq_contact' AS object_name, COUNT(*) AS row_count FROM __CATALOG__.account_iq_gold.aiq_contact;
