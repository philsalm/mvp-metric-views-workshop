# Databricks notebook source
# MAGIC %md
# MAGIC # MVP Health Care — Metric Views Workshop: Data Setup
# MAGIC
# MAGIC This notebook provisions everything the **Metric Views hands-on workshop** needs:
# MAGIC
# MAGIC 1. A catalog + schema for the workshop
# MAGIC 2. Synthetic, **HEDIS-style quality** tables (no real PHI):
# MAGIC    `dim_member`, `dim_plan`, `dim_provider`, `dim_measure`, `fact_measure_compliance`
# MAGIC 3. (Optional) the finished reference metric view `quality_measures_mv` (the "answer key")
# MAGIC
# MAGIC **Admins:** set the `catalog` and `schema` widgets to a location in your own workspace and Run All.
# MAGIC All data is randomly generated — it contains no real members, providers, or claims.

# COMMAND ----------

dbutils.widgets.text("catalog", "your_catalog_here", "Target catalog")
dbutils.widgets.text("schema", "mvp_quality_workshop", "Target schema")
dbutils.widgets.dropdown("create_metric_view", "true", ["true", "false"], "Create reference metric view")
dbutils.widgets.text("n_members", "20000", "Number of synthetic members")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
CREATE_MV = dbutils.widgets.get("create_metric_view") == "true"
N_MEMBERS = int(dbutils.widgets.get("n_members"))

print(f"Target: {CATALOG}.{SCHEMA}  |  members={N_MEMBERS:,}  |  create_metric_view={CREATE_MV}")

# COMMAND ----------

# The target catalog should already exist. Point the `catalog` widget at a catalog
# you have CREATE SCHEMA / CREATE TABLE on. If an admin points this at a brand-new
# catalog, we attempt to create it (requires Default Storage or a managed location).
try:
    spark.sql(f"USE CATALOG {CATALOG}")
except Exception:
    print(f"Catalog {CATALOG} not found — attempting to create it...")
    spark.sql(f"CREATE CATALOG IF NOT EXISTS {CATALOG}")
    spark.sql(f"USE CATALOG {CATALOG}")

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(f"USE SCHEMA {SCHEMA}")
print("Catalog + schema ready.")

# COMMAND ----------

# MAGIC %md ## Generate synthetic dimensions + fact

# COMMAND ----------

import numpy as np
import pandas as pd
from datetime import date, timedelta

SEED = 42
rng = np.random.default_rng(SEED)

# --- Reference lists (MVP operates in NY / VT) ---
REGIONS = ["Capital Region", "Central NY", "Hudson Valley", "Western NY", "Vermont"]
LOBS = ["Commercial", "Medicaid", "Medicare Advantage"]
LOB_WEIGHTS = [0.5, 0.3, 0.2]
PLAN_TYPES = ["HMO", "PPO", "EPO", "HDHP"]
PCP_SPECIALTIES = ["Family Medicine", "Internal Medicine", "Pediatrics"]
OTHER_SPECIALTIES = ["OB/GYN", "Cardiology", "Endocrinology", "Behavioral Health"]
MEASURE_YEARS = [2024, 2025]

# --- dim_plan ---
plans = []
pid = 1
for lob in LOBS:
    for pt in (["HMO", "PPO", "EPO", "HDHP"] if lob == "Commercial" else ["HMO", "PPO"]):
        metal = rng.choice(["Bronze", "Silver", "Gold", "Platinum"]) if lob == "Commercial" else "N/A"
        plans.append({
            "plan_id": f"PLN{pid:03d}",
            "plan_name": f"MVP {lob} {pt}",
            "plan_type": pt,
            "line_of_business": lob,
            "metal_tier": metal,
        })
        pid += 1
dim_plan = pd.DataFrame(plans)

# --- dim_provider ---
N_PROV = 300
prov = []
for i in range(1, N_PROV + 1):
    is_pcp = rng.random() < 0.6
    spec = rng.choice(PCP_SPECIALTIES) if is_pcp else rng.choice(OTHER_SPECIALTIES)
    prov.append({
        "provider_id": f"PRV{i:04d}",
        "provider_name": f"Provider {i:04d}",
        "specialty": spec,
        "region": rng.choice(REGIONS),
        "network_status": rng.choice(["In-Network", "Out-of-Network"], p=[0.9, 0.1]),
        "is_pcp": bool(is_pcp),
    })
dim_provider = pd.DataFrame(prov)
pcp_ids = dim_provider.loc[dim_provider.is_pcp, "provider_id"].to_numpy()

# --- dim_measure (HEDIS-style) ---
dim_measure = pd.DataFrame([
    {"measure_id": "BCS", "measure_name": "Breast Cancer Screening",            "domain": "Prevention",        "description": "Women 50-74 with a mammogram in the measurement period."},
    {"measure_id": "COL", "measure_name": "Colorectal Cancer Screening",        "domain": "Prevention",        "description": "Adults 45-75 screened for colorectal cancer."},
    {"measure_id": "CBP", "measure_name": "Controlling High Blood Pressure",    "domain": "Chronic Care",      "description": "Members 18-85 with hypertension whose BP was adequately controlled."},
    {"measure_id": "CDC", "measure_name": "Diabetes HbA1c Control",             "domain": "Chronic Care",      "description": "Members 18-75 with diabetes whose HbA1c was in control."},
    {"measure_id": "CIS", "measure_name": "Childhood Immunization Status",      "domain": "Pediatric",         "description": "Children who turned 2 with the recommended immunizations."},
    {"measure_id": "WCV", "measure_name": "Well-Care Visits (3-21)",            "domain": "Pediatric",         "description": "Members 3-21 with at least one well-care visit."},
    {"measure_id": "FUH", "measure_name": "Follow-Up After Hospitalization (MH)","domain": "Behavioral Health", "description": "Follow-up visit within 7 days of a mental-health hospitalization."},
])

# --- dim_member ---
def age_to_band(a):
    if a <= 2:  return "0-2"
    if a <= 12: return "3-12"
    if a <= 21: return "13-21"
    if a <= 39: return "22-39"
    if a <= 49: return "40-49"
    if a <= 64: return "50-64"
    return "65+"

lob_choices = rng.choice(LOBS, size=N_MEMBERS, p=LOB_WEIGHTS)
members = []
plans_by_lob = {lob: dim_plan.loc[dim_plan.line_of_business == lob, "plan_id"].to_numpy() for lob in LOBS}
for i in range(1, N_MEMBERS + 1):
    lob = lob_choices[i - 1]
    # Age distribution skews by LOB (Medicare older, Medicaid younger)
    if lob == "Medicare Advantage":
        age = int(np.clip(rng.normal(72, 7), 65, 95))
    elif lob == "Medicaid":
        age = int(np.clip(rng.normal(28, 18), 0, 80))
    else:
        age = int(np.clip(rng.normal(42, 16), 0, 80))
    members.append({
        "member_id": f"M{i:06d}",
        "gender": rng.choice(["F", "M"]),
        "age": age,
        "age_band": age_to_band(age),
        "region": rng.choice(REGIONS),
        "line_of_business": lob,
        "plan_id": rng.choice(plans_by_lob[lob]),
        "pcp_provider_id": rng.choice(pcp_ids),
        "has_hypertension": bool(rng.random() < (0.35 if age >= 50 else 0.12)),
        "has_diabetes": bool(rng.random() < (0.20 if age >= 50 else 0.06)),
        "had_mh_admit": bool(rng.random() < 0.02),
    })
dim_member = pd.DataFrame(members)

# --- fact_measure_compliance ---
# Base compliance rate per measure, then adjusted by LOB, region, and year.
BASE_RATE = {"BCS": 0.72, "COL": 0.60, "CBP": 0.65, "CDC": 0.58, "CIS": 0.70, "WCV": 0.55, "FUH": 0.45}
LOB_ADJ = {"Commercial": 0.05, "Medicare Advantage": 0.08, "Medicaid": -0.07}
REGION_ADJ = {r: a for r, a in zip(REGIONS, [0.03, -0.02, 0.01, -0.04, 0.02])}
YEAR_ADJ = {2024: 0.0, 2025: 0.03}  # slight YoY improvement

def eligible(m, measure_id):
    a = m["age"]
    if measure_id == "BCS": return m["gender"] == "F" and 50 <= a <= 74
    if measure_id == "COL": return 45 <= a <= 75
    if measure_id == "CBP": return m["has_hypertension"] and 18 <= a <= 85
    if measure_id == "CDC": return m["has_diabetes"] and 18 <= a <= 75
    if measure_id == "CIS": return a == 2
    if measure_id == "WCV": return 3 <= a <= 21
    if measure_id == "FUH": return m["had_mh_admit"]
    return False

rows = []
measure_ids = dim_measure["measure_id"].tolist()
member_recs = dim_member.to_dict("records")
for m in member_recs:
    for yr in MEASURE_YEARS:
        for mid in measure_ids:
            if not eligible(m, mid):
                continue
            p = BASE_RATE[mid] + LOB_ADJ[m["line_of_business"]] + REGION_ADJ[m["region"]] + YEAR_ADJ[yr]
            p = float(np.clip(p, 0.05, 0.97))
            compliant = int(rng.random() < p)
            last_service = None
            if compliant:
                start = date(yr, 1, 1)
                last_service = start + timedelta(days=int(rng.integers(0, 364)))
            rows.append({
                "member_id": m["member_id"],
                "plan_id": m["plan_id"],
                "pcp_provider_id": m["pcp_provider_id"],
                "measure_id": mid,
                "measurement_year": yr,
                "eligible_flag": 1,
                "compliant_flag": compliant,
                "last_service_date": last_service,
            })
fact = pd.DataFrame(rows)
print(f"dim_plan={len(dim_plan)}  dim_provider={len(dim_provider)}  dim_measure={len(dim_measure)}  "
      f"dim_member={len(dim_member):,}  fact_measure_compliance={len(fact):,}")

# COMMAND ----------

# MAGIC %md ## Write tables to Unity Catalog

# COMMAND ----------

def write(df, name, drop_cols=None):
    pdf = df.drop(columns=drop_cols) if drop_cols else df
    sdf = spark.createDataFrame(pdf)
    full = f"{CATALOG}.{SCHEMA}.{name}"
    sdf.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(full)
    print(f"  wrote {full}: {sdf.count():,} rows")

write(dim_plan, "dim_plan")
write(dim_provider, "dim_provider", drop_cols=["is_pcp"])
write(dim_measure, "dim_measure")
write(dim_member, "dim_member", drop_cols=["has_hypertension", "has_diabetes", "had_mh_admit"])
write(fact, "fact_measure_compliance")

# Helpful table comments
spark.sql(f"COMMENT ON TABLE {CATALOG}.{SCHEMA}.fact_measure_compliance IS "
          f"'Synthetic HEDIS-style measure eligibility/compliance. One row per eligible (member, measure, year). NO real PHI.'")
print("All tables written.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## (Optional) Create the reference metric view
# MAGIC This is the finished "answer key." During the workshop, attendees build their own; admins can
# MAGIC leave `create_metric_view=false` so attendees start from a blank slate.

# COMMAND ----------

if CREATE_MV:
    mv_sql = f"""
CREATE OR REPLACE VIEW {CATALOG}.{SCHEMA}.quality_measures_mv WITH METRICS LANGUAGE YAML AS
$$
version: 1.1
comment: "HEDIS-style quality measure compliance and open care gaps (synthetic data for workshop)."
source: {CATALOG}.{SCHEMA}.fact_measure_compliance

joins:
  - name: mbr
    source: {CATALOG}.{SCHEMA}.dim_member
    on: source.member_id = mbr.member_id
  - name: pln
    source: {CATALOG}.{SCHEMA}.dim_plan
    on: source.plan_id = pln.plan_id
  - name: meas
    source: {CATALOG}.{SCHEMA}.dim_measure
    on: source.measure_id = meas.measure_id
  - name: prov
    source: {CATALOG}.{SCHEMA}.dim_provider
    on: source.pcp_provider_id = prov.provider_id

fields:
  - name: measurement_year
    display_name: "Measurement Year"
    expr: measurement_year
  - name: measure_name
    display_name: "Measure"
    expr: meas.measure_name
    synonyms: ["quality measure", "HEDIS measure"]
  - name: measure_code
    display_name: "Measure Code"
    expr: meas.measure_id
  - name: quality_domain
    display_name: "Quality Domain"
    expr: meas.domain
    synonyms: ["domain", "measure category"]
  - name: line_of_business
    display_name: "Line of Business"
    expr: mbr.line_of_business
    synonyms: ["LOB", "product line"]
  - name: region
    display_name: "Region"
    expr: mbr.region
  - name: age_band
    display_name: "Age Band"
    expr: mbr.age_band
  - name: plan_name
    display_name: "Plan"
    expr: pln.plan_name
  - name: plan_type
    display_name: "Plan Type"
    expr: pln.plan_type
  - name: pcp_specialty
    display_name: "PCP Specialty"
    expr: prov.specialty
  - name: network_status
    display_name: "Network Status"
    expr: prov.network_status

measures:
  - name: eligible_members
    display_name: "Eligible Members"
    expr: COUNT(1)
    synonyms: ["denominator", "eligible population"]
  - name: compliant_members
    display_name: "Compliant Members"
    expr: SUM(compliant_flag)
    synonyms: ["numerator", "members meeting the measure"]
  - name: open_care_gaps
    display_name: "Open Care Gaps"
    expr: SUM(1 - compliant_flag)
    synonyms: ["gaps", "open gaps", "care gaps"]
  - name: compliance_rate
    display_name: "Compliance Rate"
    expr: SUM(compliant_flag) / COUNT(1)
    synonyms: ["HEDIS rate", "quality rate", "screening rate", "measure rate"]
  - name: gap_rate
    display_name: "Gap Rate"
    expr: 1 - (SUM(compliant_flag) / COUNT(1))
  - name: members_with_open_gaps
    display_name: "Members With Open Gaps"
    expr: COUNT(DISTINCT CASE WHEN compliant_flag = 0 THEN member_id END)
$$
"""
    spark.sql(mv_sql)
    print(f"Created metric view {CATALOG}.{SCHEMA}.quality_measures_mv")
else:
    print("Skipped metric view creation (create_metric_view=false).")

# COMMAND ----------

# MAGIC %md ## Verify

# COMMAND ----------

if CREATE_MV:
    display(spark.sql(f"""
        SELECT line_of_business,
               MEASURE(eligible_members)  AS eligible,
               MEASURE(compliant_members) AS compliant,
               MEASURE(open_care_gaps)    AS open_gaps,
               ROUND(MEASURE(compliance_rate) * 100, 1) AS compliance_rate_pct
        FROM {CATALOG}.{SCHEMA}.quality_measures_mv
        WHERE measurement_year = 2025
        GROUP BY line_of_business
        ORDER BY compliance_rate_pct DESC
    """))
else:
    display(spark.sql(f"SELECT measure_id, COUNT(*) eligible, SUM(compliant_flag) compliant "
                      f"FROM {CATALOG}.{SCHEMA}.fact_measure_compliance WHERE measurement_year=2025 GROUP BY measure_id"))

print("Setup complete.")
