-- Databricks seed script for the Daily Account Planner MVP
-- Enriched synthetic demo data with secure contract views

CREATE SCHEMA IF NOT EXISTS veeam_demo.ri;
CREATE SCHEMA IF NOT EXISTS veeam_demo.ri_secure;

CREATE OR REPLACE TABLE veeam_demo.ri.accounts (
  account_id STRING NOT NULL,
  name STRING NOT NULL,
  global_ultimate STRING NOT NULL,
  sales_team STRING NOT NULL,
  duns STRING,
  is_subsidiary BOOLEAN,
  industry STRING,
  sic_or_naics STRING,
  hq_country STRING,
  hq_region STRING,
  customer_or_prospect STRING,
  current_veeam_products STRING,
  renewal_date DATE,
  opportunity_stage STRING,
  last_seller_touch_date DATE
);

INSERT INTO veeam_demo.ri.accounts VALUES
  ('001GL0001', 'Ford Motor Company', 'Ford Motor Company', 'GreatLakes-ENT-Named-1', '001000001', false, 'Automotive', '336111', 'United States', 'NA', 'Customer', 'VBR,VRO', DATE '2026-06-30', 'Proposal', DATE '2026-02-10'),
  ('001GL0002', 'Latitude AI LLC', 'Ford Motor Company', 'GreatLakes-ENT-Named-1', '001000002', true, 'Automotive AI', '541715', 'United States', 'NA', 'Prospect', '', NULL, 'Prospecting', DATE '2025-11-02'),
  ('001GL0003', 'Bridgestone Americas, Inc.', 'BRIDGESTONE CORPORATION', 'GreatLakes-ENT-Named-1', '001000003', true, 'Manufacturing', '326211', 'United States', 'NA', 'Customer', 'VBM365', DATE '2026-05-15', 'Negotiation', DATE '2026-03-01'),
  ('001GL0004', 'Bridgestone Golf, Inc.', 'BRIDGESTONE CORPORATION', 'GreatLakes-ENT-Named-1', '001000004', true, 'Manufacturing', '339920', 'United States', 'NA', 'Customer', 'VBM365', DATE '2026-05-15', 'Negotiation', DATE '2026-01-12'),
  ('001GL0005', 'Ascension Health - IS, Inc.', 'Ascension Health Alliance', 'GreatLakes-ENT-Named-1', '001000005', true, 'Healthcare', '622110', 'United States', 'NA', 'Prospect', '', NULL, 'Discovery', DATE '2025-09-10'),
  ('001GL0006', 'Liberty Mutual', 'The Ohio Casualty Insurance Company', 'GreatLakes-ENT-Named-1', '001000006', false, 'Insurance', '524126', 'United States', 'NA', 'Prospect', '', NULL, 'None', DATE '2025-07-18'),
  ('001GE0001', 'adidas AG', 'adidas AG', 'Germany-ENT-Named-5', '001100001', false, 'Retail', '448210', 'Germany', 'EMEA', 'Customer', 'VDP,Vault', DATE '2026-04-20', 'Proposal', DATE '2026-03-05'),
  ('001GE0002', 'DATEV eG', 'DATEV eG', 'Germany-ENT-Named-5', '001100002', false, 'Software', '541511', 'Germany', 'EMEA', 'Prospect', '', NULL, 'Discovery', DATE '2025-10-15'),
  ('001GE0003', 'Porsche Digital GmbH', 'Dr. Ing. h.c. F. Porsche AG', 'Germany-ENT-Named-5', '001100003', true, 'Automotive Software', '541511', 'Germany', 'EMEA', 'Customer', 'VBM365', DATE '2026-09-30', 'Qualification', DATE '2026-02-25'),
  ('001UK0001', 'Tesco PLC', 'Tesco PLC', 'UK-COM-Named-3', '001200001', false, 'Retail', '445110', 'United Kingdom', 'EMEA', 'Customer', 'VBR', DATE '2026-04-30', 'Renewal', DATE '2026-02-20'),
  ('001UK0002', 'ARM Limited', 'ARM Holdings plc', 'UK-COM-Named-3', '001200002', true, 'Semiconductors', '334413', 'United Kingdom', 'EMEA', 'Prospect', '', NULL, 'Prospecting', DATE '2025-12-14'),
  ('001UK0003', 'British Airways Plc', 'International Consolidated Airlines Group, S.A.', 'UK-COM-Named-3', '001200003', true, 'Airlines', '481111', 'United Kingdom', 'EMEA', 'Customer', 'VBM365', DATE '2026-08-31', 'Discovery', DATE '2025-08-01'),
  ('001SC0001', 'ServiceTitan, Inc.', 'ServiceTitan, Inc.', 'SoCal-VEL-Named-2', '001300001', false, 'Software', '541511', 'United States', 'NA', 'Customer', 'VBM365', DATE '2026-07-31', 'Expansion', DATE '2026-03-02'),
  ('001SC0002', 'Snap Inc.', 'Snap Inc.', 'SoCal-VEL-Named-2', '001300002', false, 'Technology', '519290', 'United States', 'NA', 'Prospect', '', NULL, 'Prospecting', DATE '2025-10-05'),
  ('001SC0003', 'Riot Games, Inc.', 'Tencent Holdings Ltd.', 'SoCal-VEL-Named-2', '001300003', true, 'Gaming', '511210', 'United States', 'NA', 'Customer', 'VBR', DATE '2026-12-31', 'Qualification', DATE '2025-12-01'),
  ('001SC0004', 'Honey Science LLC', 'PayPal Holdings, Inc.', 'SoCal-VEL-Named-2', '001300004', true, 'Fintech', '522320', 'United States', 'NA', 'Prospect', '', NULL, 'Prospecting', DATE '2025-05-01'),
  ('001SC0005', 'StubHub, Inc.', 'StubHub Holdings, Inc.', 'SoCal-VEL-Named-2', '001300005', false, 'Marketplace', '519290', 'United States', 'NA', 'Prospect', '', NULL, 'None', DATE '2025-01-17'),
  ('001SC0006', 'Space Exploration Technologies Corp.', 'Space Exploration Technologies Corp.', 'SoCal-VEL-Named-2', '001300006', false, 'Aerospace', '336414', 'United States', 'NA', 'Prospect', '', NULL, 'Qualification', DATE '2026-02-01');

CREATE OR REPLACE TABLE veeam_demo.ri.reps (
  rep_key STRING NOT NULL,
  rep_name STRING NOT NULL,
  territory STRING NOT NULL
);

INSERT INTO veeam_demo.ri.reps VALUES
  ('scott', 'Scott Jackson', 'Germany-ENT-Named-5'),
  ('jon', 'Jon Test', 'GreatLakes-ENT-Named-1'),
  ('ukdemo', 'UK Demo', 'UK-COM-Named-3'),
  ('socalsdr', 'SoCal SDR', 'SoCal-VEL-Named-2');

CREATE OR REPLACE TABLE veeam_demo.ri.opportunities (
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

INSERT INTO veeam_demo.ri.opportunities VALUES
  ('001GL0001', 'Ford Motor Company', 'Ford Motor Company', 'GreatLakes-ENT-Named-1', 74.0, 8.0, 92.0, 3.0, 35.0, 60, 51, 'Headquartered account,Heavy VMware environment,10+ cloud workloads,Storage hardware over 3 years old', 'Has Kubernetes,Multiple distributions,OpenShift present', 'Microsoft 365 footprint with no premium protection', 'Salesforce present across business units', 'High cloud budget across AWS Azure and GCP', false, false, false, false, false, false, true, false, false),
  ('001GL0003', 'Bridgestone Americas, Inc.', 'BRIDGESTONE CORPORATION', 'GreatLakes-ENT-Named-1', 81.0, 11.0, 88.0, 4.0, 68.0, 77, 66, 'Existing customer with aging storage and strong fit for broader protection', 'Moderate Kubernetes footprint', 'M365 usage expanding', 'Salesforce critical workflows detected', 'Cloud workloads growing with weak multicloud backup coverage', false, false, false, false, true, true, false, true, false),
  ('001GL0005', 'Ascension Health - IS, Inc.', 'Ascension Health Alliance', 'GreatLakes-ENT-Named-1', 73.0, 6.0, 95.0, 0.0, 0.0, 69, 48, 'Top healthcare fit,Decision-making center,Hybrid infrastructure complexity', 'Kubernetes maturity present', 'Large Microsoft 365 footprint', 'Clinical application dependency suggests Salesforce relevance', 'Cloud growth with resilience gaps', false, false, true, false, true, false, false, false, false),
  ('001GL0006', 'Liberty Mutual', 'The Ohio Casualty Insurance Company', 'GreatLakes-ENT-Named-1', 78.0, 2.0, 94.0, 2.0, 0.0, 80, 50, 'Top insurance fit,Premium security offerings detected,Storage refresh window', 'Container presence detected', 'Office footprint with no modern protection', 'Salesforce footprint across field teams', 'High cloud budget with multiple providers', false, false, false, false, false, false, false, false, false),
  ('001GE0001', 'adidas AG', 'adidas AG', 'Germany-ENT-Named-5', 89.0, 12.0, 91.0, 5.0, 82.0, 85, 79, 'Global enterprise fit,Board-level resilience concern,Infrastructure modernization underway', 'Strong Kubernetes and OpenShift maturity', 'Large Microsoft 365 footprint', 'Salesforce used in regional operations', 'Significant multicloud presence with modernization pressure', false, true, true, false, true, true, false, true, false),
  ('001GE0002', 'DATEV eG', 'DATEV eG', 'Germany-ENT-Named-5', 83.0, 9.0, 96.0, 1.0, 79.0, 81, 74, 'High intent account,Decision power in core IT team,Strong data protection need', 'Moderate Kubernetes footprint', 'M365 protection gap', NULL, 'Cloud maturity increasing with no multicloud backup detected', true, false, true, false, true, false, false, false, false),
  ('001GE0003', 'Porsche Digital GmbH', 'Dr. Ing. h.c. F. Porsche AG', 'Germany-ENT-Named-5', 71.0, 7.0, 84.0, 3.0, 52.0, 68, 61, 'Digital subsidiary with product velocity and strong fit for expansion', 'Container engineering footprint', 'M365 footprint with opportunity to expand', NULL, 'Public cloud engineering teams growing', false, true, false, false, true, false, false, false, true),
  ('001UK0001', 'Tesco PLC', 'Tesco PLC', 'UK-COM-Named-3', 76.0, 6.0, 82.0, 1.0, 64.0, 72, 67, 'Existing customer with renewal approaching and cloud growth', NULL, 'M365 usage broad across business units', 'Salesforce marketing footprint', 'Hybrid retail workloads expanding across clouds', false, false, false, false, true, false, false, true, false),
  ('001UK0002', 'ARM Limited', 'ARM Holdings plc', 'UK-COM-Named-3', 71.0, 5.0, 86.0, 3.0, 0.0, 74, 62, 'High technical fit with infrastructure modernization', 'Kubernetes and engineering-led platform footprint', 'M365 footprint with protection gap', NULL, 'Cloud-native engineering workloads increasing', true, true, false, false, true, false, false, false, false),
  ('001UK0003', 'British Airways Plc', 'International Consolidated Airlines Group, S.A.', 'UK-COM-Named-3', 65.0, -3.0, 70.0, 2.0, 43.0, 63, 58, 'Customer environment is complex but timing is less urgent', NULL, 'M365 footprint exists', NULL, 'Cloud workloads present', false, false, false, false, false, false, false, false, false),
  ('001SC0001', 'ServiceTitan, Inc.', 'ServiceTitan, Inc.', 'SoCal-VEL-Named-2', 86.0, 10.0, 90.0, 1.0, 72.0, 82, 76, 'Fast-growing SaaS customer with room to expand resilience coverage', 'Containerized platform footprint', 'M365 footprint with expansion path', NULL, 'Heavy cloud workloads with strong timing signal', false, true, true, false, true, true, false, true, false),
  ('001SC0002', 'Snap Inc.', 'Snap Inc.', 'SoCal-VEL-Named-2', 82.0, 9.0, 88.0, 5.0, 0.0, 80, 71, 'High intent and strong fit with competitive pressure present', 'Platform engineering uses containers', 'Large Microsoft 365 footprint', NULL, 'Multicloud environment with resilience gap', true, true, false, false, true, false, false, false, false),
  ('001SC0003', 'Riot Games, Inc.', 'Tencent Holdings Ltd.', 'SoCal-VEL-Named-2', 79.0, 7.0, 84.0, 4.0, 55.0, 78, 69, 'Existing customer with game platform growth and expansion signal', 'Kubernetes present', 'M365 expansion opportunity', NULL, 'Cloud growth across live-service operations', false, true, true, false, true, false, false, true, false),
  ('001SC0004', 'Honey Science LLC', 'PayPal Holdings, Inc.', 'SoCal-VEL-Named-2', 74.0, 8.0, 81.0, 2.0, 0.0, 72, 64, 'Prospect with e-commerce data growth and modernization pressure', NULL, 'M365 footprint likely underprotected', NULL, 'Cloud-first workload profile', true, false, true, false, true, false, false, false, false),
  ('001SC0005', 'StubHub, Inc.', 'StubHub Holdings, Inc.', 'SoCal-VEL-Named-2', 68.0, 1.0, 77.0, 6.0, 0.0, 67, 59, 'Competitive pressure is the main signal here', NULL, 'M365 present', NULL, 'Cloud workloads present', false, false, false, false, false, false, false, false, false),
  ('001SC0006', 'Space Exploration Technologies Corp.', 'Space Exploration Technologies Corp.', 'SoCal-VEL-Named-2', 72.0, 4.0, 79.0, 2.0, 0.0, 70, 63, 'Strong technical fit but no strict play currently triggered', 'Engineering platform may support containers', 'M365 adoption present', NULL, 'Cloud and edge workloads expanding', false, false, false, false, false, false, false, false, false);

CREATE OR REPLACE TABLE veeam_demo.ri.contacts (
  account_id STRING NOT NULL,
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

INSERT INTO veeam_demo.ri.contacts VALUES
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
  ('001UK0001', 'Priya', 'Patel', 'Priya Patel', 'Director of IT Infrastructure', 'IT Leaders', 'priya.patel@tesco.example', '555-0301', 'Engaged', 'Marketing Qualified Contact', DATE '2026-03-06', false),
  ('001UK0002', 'Tom', 'Fowler', 'Tom Fowler', 'VP Security Engineering', 'Executive Leaders', 'tom.fowler@arm.example', '555-0310', 'Engaged', 'Sales Accepted Contact', DATE '2026-03-03', false),
  ('001UK0003', 'Megan', 'Ross', 'Megan Ross', 'IT Manager', 'IT Practitioner', 'megan.ross@ba.example', '555-0320', NULL, 'Marketing Qualified Contact', DATE '2025-06-01', true),
  ('001SC0001', 'Lena', 'Martinez', 'Lena Martinez', 'CIO', 'Executive Leaders', 'lena.martinez@servicetitan.example', '555-0401', 'Engaged', 'Sales Accepted Contact', DATE '2026-03-11', false),
  ('001SC0001', 'Evan', 'Brooks', 'Evan Brooks', 'Director of Infrastructure', 'IT Leaders', 'evan.brooks@servicetitan.example', '555-0402', 'Engaged', 'Marketing Qualified Contact', DATE '2026-02-26', false),
  ('001SC0002', 'Nora', 'Kim', 'Nora Kim', 'VP Engineering', 'Executive Leaders', 'nora.kim@snap.example', '555-0410', 'Engaged', 'Marketing Qualified Contact', DATE '2026-03-01', false),
  ('001SC0003', 'Alex', 'Diaz', 'Alex Diaz', 'Director of Platform Reliability', 'IT Leaders', 'alex.diaz@riot.example', '555-0420', NULL, 'Marketing Qualified Contact', DATE '2025-04-15', false),
  ('001SC0004', 'Jamie', 'Cole', 'Jamie Cole', 'Head of Security', 'Executive Leaders', 'jamie.cole@honey.example', '555-0430', 'Engaged', 'Marketing Qualified Contact', DATE '2026-02-18', false),
  ('001SC0004', 'Jamie', 'Cole', 'Jamie Cole', 'Head of Security', 'Executive Leaders', 'jcole@honey.example', '555-0430', NULL, 'Marketing Qualified Contact', DATE '2025-11-10', false),
  ('001SC0006', 'Riley', 'Ng', 'Riley Ng', 'Infrastructure Manager', 'IT Practitioner', 'riley.ng@spacex.example', '555-0440', NULL, 'Marketing Qualified Contact', DATE '2025-09-01', false);

CREATE OR REPLACE VIEW veeam_demo.ri_secure.accounts AS
SELECT * FROM veeam_demo.ri.accounts;

CREATE OR REPLACE VIEW veeam_demo.ri_secure.reps AS
SELECT * FROM veeam_demo.ri.reps;

CREATE OR REPLACE VIEW veeam_demo.ri_secure.opportunities AS
SELECT * FROM veeam_demo.ri.opportunities;

CREATE OR REPLACE VIEW veeam_demo.ri_secure.contacts AS
SELECT * FROM veeam_demo.ri.contacts;

SELECT 'accounts' AS object_name, COUNT(*) AS row_count FROM veeam_demo.ri.accounts
UNION ALL SELECT 'reps', COUNT(*) FROM veeam_demo.ri.reps
UNION ALL SELECT 'opportunities', COUNT(*) FROM veeam_demo.ri.opportunities
UNION ALL SELECT 'contacts', COUNT(*) FROM veeam_demo.ri.contacts
UNION ALL SELECT 'secure_accounts', COUNT(*) FROM veeam_demo.ri_secure.accounts
UNION ALL SELECT 'secure_reps', COUNT(*) FROM veeam_demo.ri_secure.reps
UNION ALL SELECT 'secure_opportunities', COUNT(*) FROM veeam_demo.ri_secure.opportunities
UNION ALL SELECT 'secure_contacts', COUNT(*) FROM veeam_demo.ri_secure.contacts;
