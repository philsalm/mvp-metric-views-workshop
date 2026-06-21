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
dbutils.widgets.dropdown("create_dashboard", "true", ["true", "false"], "Create AI/BI dashboard")
dbutils.widgets.text("warehouse_id", "", "Dashboard warehouse ID (blank = auto-pick)")
dbutils.widgets.text("n_members", "20000", "Number of synthetic members")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
CREATE_MV = dbutils.widgets.get("create_metric_view") == "true"
CREATE_DASHBOARD = dbutils.widgets.get("create_dashboard") == "true"
WAREHOUSE_ID = dbutils.widgets.get("warehouse_id").strip()
N_MEMBERS = int(dbutils.widgets.get("n_members"))

print(f"Target: {CATALOG}.{SCHEMA}  |  members={N_MEMBERS:,}  |  "
      f"create_metric_view={CREATE_MV}  |  create_dashboard={CREATE_DASHBOARD}")

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

# COMMAND ----------

# MAGIC %md
# MAGIC ## (Optional) Create the AI/BI dashboard — pointed at THIS catalog/schema
# MAGIC Builds the reference **MVP Health Care — Quality Measures (HEDIS)** dashboard and points every dataset at
# MAGIC `{CATALOG}.{SCHEMA}.quality_measures_mv` — the catalog/schema this run just loaded — so it works no matter
# MAGIC where the data lands. The dashboard JSON is embedded below (base64); the editable source is
# MAGIC `dashboards/mvp_quality_measures.lvdash.json` in the repo. Set `create_dashboard=false` to skip.

# COMMAND ----------

# Base64-encoded Lakeview dashboard. Editable source: dashboards/mvp_quality_measures.lvdash.json
# Regenerate after editing that file:  base64 -i dashboards/mvp_quality_measures.lvdash.json | tr -d "\n"
_DASHBOARD_TEMPLATE_B64 = (
    "ewogICJkYXRhc2V0cyI6IFsKICAgIHsKICAgICAgIm5hbWUiOiAiZHNfa3BpIiwKICAgICAgImRpc3BsYXlOYW1lIjogIktQSSBDb21wb25lbnRzIiwKICAgICAgInF1ZXJ5TGluZXMiOiBbCiAgICAgICAgIlNFTEVDVCBgbWVhc3VyZW1lbnRfeWVhcmAsIGBsaW5lX29mX2J1c2luZXNzYCwgTUVBU1VSRShgY29tcGxpYW50X21lbWJlcnNgKSBBUyBjb21wbGlhbnRfbWVtYmVycywgTUVBU1VSRShgZWxpZ2libGVfbWVtYmVyc2ApIEFTIGVsaWdpYmxlX21lbWJlcnMsIE1FQVNVUkUoYG9wZW5fY2FyZV9nYXBzYCkgQVMgb3Blbl9jYXJlX2dhcHMgRlJPTSBhaV9kZXZfa2l0X2RlbW9fM19jYXRhbG9nLm12cF9xdWFsaXR5X3dvcmtzaG9wLnF1YWxpdHlfbWVhc3VyZXNfbXYgR1JPVVAgQlkgYG1lYXN1cmVtZW50X3llYXJgLCBgbGluZV9vZl9idXNpbmVzc2AgIgogICAgICBdCiAgICB9LAogICAgewogICAgICAibmFtZSI6ICJkc19ieV9tZWFzdXJlIiwKICAgICAgImRpc3BsYXlOYW1lIjogIkNvbXBsaWFuY2UgYnkgTWVhc3VyZSIsCiAgICAgICJxdWVyeUxpbmVzIjogWwogICAgICAgICJTRUxFQ1QgYG1lYXN1cmVtZW50X3llYXJgLCBgbGluZV9vZl9idXNpbmVzc2AsIGBtZWFzdXJlX25hbWVgLCBNRUFTVVJFKGBjb21wbGlhbmNlX3JhdGVgKSoxMDAgQVMgY29tcGxpYW5jZV9yYXRlX3BjdCBGUk9NIGFpX2Rldl9raXRfZGVtb18zX2NhdGFsb2cubXZwX3F1YWxpdHlfd29ya3Nob3AucXVhbGl0eV9tZWFzdXJlc19tdiBHUk9VUCBCWSBgbWVhc3VyZW1lbnRfeWVhcmAsIGBsaW5lX29mX2J1c2luZXNzYCwgYG1lYXN1cmVfbmFtZWAgIgogICAgICBdCiAgICB9LAogICAgewogICAgICAibmFtZSI6ICJkc19ieV9sb2IiLAogICAgICAiZGlzcGxheU5hbWUiOiAiQ29tcGxpYW5jZSBieSBMaW5lIG9mIEJ1c2luZXNzIiwKICAgICAgInF1ZXJ5TGluZXMiOiBbCiAgICAgICAgIlNFTEVDVCBgbWVhc3VyZW1lbnRfeWVhcmAsIGBsaW5lX29mX2J1c2luZXNzYCwgTUVBU1VSRShgY29tcGxpYW5jZV9yYXRlYCkqMTAwIEFTIGNvbXBsaWFuY2VfcmF0ZV9wY3QgRlJPTSBhaV9kZXZfa2l0X2RlbW9fM19jYXRhbG9nLm12cF9xdWFsaXR5X3dvcmtzaG9wLnF1YWxpdHlfbWVhc3VyZXNfbXYgR1JPVVAgQlkgYG1lYXN1cmVtZW50X3llYXJgLCBgbGluZV9vZl9idXNpbmVzc2AgIgogICAgICBdCiAgICB9LAogICAgewogICAgICAibmFtZSI6ICJkc19ieV9yZWdpb24iLAogICAgICAiZGlzcGxheU5hbWUiOiAiT3BlbiBDYXJlIEdhcHMgYnkgUmVnaW9uIiwKICAgICAgInF1ZXJ5TGluZXMiOiBbCiAgICAgICAgIlNFTEVDVCBgbWVhc3VyZW1lbnRfeWVhcmAsIGBsaW5lX29mX2J1c2luZXNzYCwgYHJlZ2lvbmAsIE1FQVNVUkUoYG9wZW5fY2FyZV9nYXBzYCkgQVMgb3Blbl9jYXJlX2dhcHMgRlJPTSBhaV9kZXZfa2l0X2RlbW9fM19jYXRhbG9nLm12cF9xdWFsaXR5X3dvcmtzaG9wLnF1YWxpdHlfbWVhc3VyZXNfbXYgR1JPVVAgQlkgYG1lYXN1cmVtZW50X3llYXJgLCBgbGluZV9vZl9idXNpbmVzc2AsIGByZWdpb25gICIKICAgICAgXQogICAgfSwKICAgIHsKICAgICAgIm5hbWUiOiAiZHNfeW95IiwKICAgICAgImRpc3BsYXlOYW1lIjogIkNvbXBsaWFuY2UgWW9ZIGJ5IFF1YWxpdHkgRG9tYWluIiwKICAgICAgInF1ZXJ5TGluZXMiOiBbCiAgICAgICAgIlNFTEVDVCBgbWVhc3VyZW1lbnRfeWVhcmAsIGBsaW5lX29mX2J1c2luZXNzYCwgYHF1YWxpdHlfZG9tYWluYCwgTUVBU1VSRShgY29tcGxpYW5jZV9yYXRlYCkqMTAwIEFTIGNvbXBsaWFuY2VfcmF0ZV9wY3QgRlJPTSBhaV9kZXZfa2l0X2RlbW9fM19jYXRhbG9nLm12cF9xdWFsaXR5X3dvcmtzaG9wLnF1YWxpdHlfbWVhc3VyZXNfbXYgR1JPVVAgQlkgYG1lYXN1cmVtZW50X3llYXJgLCBgbGluZV9vZl9idXNpbmVzc2AsIGBxdWFsaXR5X2RvbWFpbmAgIgogICAgICBdCiAgICB9CiAgXSwKICAicGFnZXMiOiBbCiAgICB7CiAgICAgICJuYW1lIjogIm92ZXJ2aWV3IiwKICAgICAgImRpc3BsYXlOYW1lIjogIk92ZXJ2aWV3IiwKICAgICAgImxheW91dCI6IFsKICAgICAgICB7CiAgICAgICAgICAid2lkZ2V0IjogewogICAgICAgICAgICAibmFtZSI6ICJ0aXRsZSIsCiAgICAgICAgICAgICJtdWx0aWxpbmVUZXh0Ym94U3BlYyI6IHsKICAgICAgICAgICAgICAibGluZXMiOiBbCiAgICAgICAgICAgICAgICAiIyBNVlAgSGVhbHRoIENhcmUg4oCUIFF1YWxpdHkgTWVhc3VyZXMgKEhFRElTKSBbV29ya3Nob3BdIgogICAgICAgICAgICAgIF0KICAgICAgICAgICAgfQogICAgICAgICAgfSwKICAgICAgICAgICJwb3NpdGlvbiI6IHsKICAgICAgICAgICAgIngiOiAwLAogICAgICAgICAgICAieSI6IDAsCiAgICAgICAgICAgICJ3aWR0aCI6IDYsCiAgICAgICAgICAgICJoZWlnaHQiOiAxCiAgICAgICAgICB9CiAgICAgICAgfSwKICAgICAgICB7CiAgICAgICAgICAid2lkZ2V0IjogewogICAgICAgICAgICAibmFtZSI6ICJzdWJ0aXRsZSIsCiAgICAgICAgICAgICJtdWx0aWxpbmVUZXh0Ym94U3BlYyI6IHsKICAgICAgICAgICAgICAibGluZXMiOiBbCiAgICAgICAgICAgICAgICAiSEVESVMgcXVhbGl0eSBtZWFzdXJlIHBlcmZvcm1hbmNlIOKAlCBjb21wbGlhbmNlIHJhdGVzLCBvcGVuIGNhcmUgZ2FwcywgYW5kIGVsaWdpYmxlIG1lbWJlcnNoaXAuIFNvdXJjZTogcXVhbGl0eV9tZWFzdXJlc19tdiAoVW5pdHkgQ2F0YWxvZyBtZXRyaWMgdmlldykuIgogICAgICAgICAgICAgIF0KICAgICAgICAgICAgfQogICAgICAgICAgfSwKICAgICAgICAgICJwb3NpdGlvbiI6IHsKICAgICAgICAgICAgIngiOiAwLAogICAgICAgICAgICAieSI6IDEsCiAgICAgICAgICAgICJ3aWR0aCI6IDYsCiAgICAgICAgICAgICJoZWlnaHQiOiAxCiAgICAgICAgICB9CiAgICAgICAgfSwKICAgICAgICB7CiAgICAgICAgICAid2lkZ2V0IjogewogICAgICAgICAgICAibmFtZSI6ICJrcGktY29tcGxpYW5jZSIsCiAgICAgICAgICAgICJxdWVyaWVzIjogWwogICAgICAgICAgICAgIHsKICAgICAgICAgICAgICAgICJuYW1lIjogIm1haW5fcXVlcnkiLAogICAgICAgICAgICAgICAgInF1ZXJ5IjogewogICAgICAgICAgICAgICAgICAiZGF0YXNldE5hbWUiOiAiZHNfa3BpIiwKICAgICAgICAgICAgICAgICAgImZpZWxkcyI6IFsKICAgICAgICAgICAgICAgICAgICB7CiAgICAgICAgICAgICAgICAgICAgICAibmFtZSI6ICJjb21wbGlhbmNlX3JhdGVfcGN0IiwKICAgICAgICAgICAgICAgICAgICAgICJleHByZXNzaW9uIjogIlNVTShgY29tcGxpYW50X21lbWJlcnNgKS9TVU0oYGVsaWdpYmxlX21lbWJlcnNgKSoxMDAiCiAgICAgICAgICAgICAgICAgICAgfQogICAgICAgICAgICAgICAgICBdLAogICAgICAgICAgICAgICAgICAiZGlzYWdncmVnYXRlZCI6IGZhbHNlCiAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgfQogICAgICAgICAgICBdLAogICAgICAgICAgICAic3BlYyI6IHsKICAgICAgICAgICAgICAidmVyc2lvbiI6IDIsCiAgICAgICAgICAgICAgIndpZGdldFR5cGUiOiAiY291bnRlciIsCiAgICAgICAgICAgICAgImVuY29kaW5ncyI6IHsKICAgICAgICAgICAgICAgICJ2YWx1ZSI6IHsKICAgICAgICAgICAgICAgICAgImZpZWxkTmFtZSI6ICJjb21wbGlhbmNlX3JhdGVfcGN0IiwKICAgICAgICAgICAgICAgICAgImRpc3BsYXlOYW1lIjogIkNvbXBsaWFuY2UgUmF0ZSAoJSkiLAogICAgICAgICAgICAgICAgICAiZm9ybWF0IjogewogICAgICAgICAgICAgICAgICAgICJ0eXBlIjogIm51bWJlci1wbGFpbiIsCiAgICAgICAgICAgICAgICAgICAgImRlY2ltYWxQbGFjZXMiOiB7CiAgICAgICAgICAgICAgICAgICAgICAidHlwZSI6ICJleGFjdCIsCiAgICAgICAgICAgICAgICAgICAgICAicGxhY2VzIjogMQogICAgICAgICAgICAgICAgICAgIH0KICAgICAgICAgICAgICAgICAgfQogICAgICAgICAgICAgICAgfQogICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgImZyYW1lIjogewogICAgICAgICAgICAgICAgInNob3dUaXRsZSI6IHRydWUsCiAgICAgICAgICAgICAgICAidGl0bGUiOiAiQ29tcGxpYW5jZSBSYXRlICglKSIKICAgICAgICAgICAgICB9CiAgICAgICAgICAgIH0KICAgICAgICAgIH0sCiAgICAgICAgICAicG9zaXRpb24iOiB7CiAgICAgICAgICAgICJ4IjogMCwKICAgICAgICAgICAgInkiOiAyLAogICAgICAgICAgICAid2lkdGgiOiAyLAogICAgICAgICAgICAiaGVpZ2h0IjogMwogICAgICAgICAgfQogICAgICAgIH0sCiAgICAgICAgewogICAgICAgICAgIndpZGdldCI6IHsKICAgICAgICAgICAgIm5hbWUiOiAia3BpLWdhcHMiLAogICAgICAgICAgICAicXVlcmllcyI6IFsKICAgICAgICAgICAgICB7CiAgICAgICAgICAgICAgICAibmFtZSI6ICJtYWluX3F1ZXJ5IiwKICAgICAgICAgICAgICAgICJxdWVyeSI6IHsKICAgICAgICAgICAgICAgICAgImRhdGFzZXROYW1lIjogImRzX2twaSIsCiAgICAgICAgICAgICAgICAgICJmaWVsZHMiOiBbCiAgICAgICAgICAgICAgICAgICAgewogICAgICAgICAgICAgICAgICAgICAgIm5hbWUiOiAic3VtKG9wZW5fY2FyZV9nYXBzKSIsCiAgICAgICAgICAgICAgICAgICAgICAiZXhwcmVzc2lvbiI6ICJTVU0oYG9wZW5fY2FyZV9nYXBzYCkiCiAgICAgICAgICAgICAgICAgICAgfQogICAgICAgICAgICAgICAgICBdLAogICAgICAgICAgICAgICAgICAiZGlzYWdncmVnYXRlZCI6IGZhbHNlCiAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgfQogICAgICAgICAgICBdLAogICAgICAgICAgICAic3BlYyI6IHsKICAgICAgICAgICAgICAidmVyc2lvbiI6IDIsCiAgICAgICAgICAgICAgIndpZGdldFR5cGUiOiAiY291bnRlciIsCiAgICAgICAgICAgICAgImVuY29kaW5ncyI6IHsKICAgICAgICAgICAgICAgICJ2YWx1ZSI6IHsKICAgICAgICAgICAgICAgICAgImZpZWxkTmFtZSI6ICJzdW0ob3Blbl9jYXJlX2dhcHMpIiwKICAgICAgICAgICAgICAgICAgImRpc3BsYXlOYW1lIjogIlRvdGFsIE9wZW4gQ2FyZSBHYXBzIgogICAgICAgICAgICAgICAgfQogICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgImZyYW1lIjogewogICAgICAgICAgICAgICAgInNob3dUaXRsZSI6IHRydWUsCiAgICAgICAgICAgICAgICAidGl0bGUiOiAiVG90YWwgT3BlbiBDYXJlIEdhcHMiCiAgICAgICAgICAgICAgfQogICAgICAgICAgICB9CiAgICAgICAgICB9LAogICAgICAgICAgInBvc2l0aW9uIjogewogICAgICAgICAgICAieCI6IDIsCiAgICAgICAgICAgICJ5IjogMiwKICAgICAgICAgICAgIndpZHRoIjogMiwKICAgICAgICAgICAgImhlaWdodCI6IDMKICAgICAgICAgIH0KICAgICAgICB9LAogICAgICAgIHsKICAgICAgICAgICJ3aWRnZXQiOiB7CiAgICAgICAgICAgICJuYW1lIjogImtwaS1lbGlnaWJsZSIsCiAgICAgICAgICAgICJxdWVyaWVzIjogWwogICAgICAgICAgICAgIHsKICAgICAgICAgICAgICAgICJuYW1lIjogIm1haW5fcXVlcnkiLAogICAgICAgICAgICAgICAgInF1ZXJ5IjogewogICAgICAgICAgICAgICAgICAiZGF0YXNldE5hbWUiOiAiZHNfa3BpIiwKICAgICAgICAgICAgICAgICAgImZpZWxkcyI6IFsKICAgICAgICAgICAgICAgICAgICB7CiAgICAgICAgICAgICAgICAgICAgICAibmFtZSI6ICJzdW0oZWxpZ2libGVfbWVtYmVycykiLAogICAgICAgICAgICAgICAgICAgICAgImV4cHJlc3Npb24iOiAiU1VNKGBlbGlnaWJsZV9tZW1iZXJzYCkiCiAgICAgICAgICAgICAgICAgICAgfQogICAgICAgICAgICAgICAgICBdLAogICAgICAgICAgICAgICAgICAiZGlzYWdncmVnYXRlZCI6IGZhbHNlCiAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgfQogICAgICAgICAgICBdLAogICAgICAgICAgICAic3BlYyI6IHsKICAgICAgICAgICAgICAidmVyc2lvbiI6IDIsCiAgICAgICAgICAgICAgIndpZGdldFR5cGUiOiAiY291bnRlciIsCiAgICAgICAgICAgICAgImVuY29kaW5ncyI6IHsKICAgICAgICAgICAgICAgICJ2YWx1ZSI6IHsKICAgICAgICAgICAgICAgICAgImZpZWxkTmFtZSI6ICJzdW0oZWxpZ2libGVfbWVtYmVycykiLAogICAgICAgICAgICAgICAgICAiZGlzcGxheU5hbWUiOiAiVG90YWwgRWxpZ2libGUgTWVtYmVycyIKICAgICAgICAgICAgICAgIH0KICAgICAgICAgICAgICB9LAogICAgICAgICAgICAgICJmcmFtZSI6IHsKICAgICAgICAgICAgICAgICJzaG93VGl0bGUiOiB0cnVlLAogICAgICAgICAgICAgICAgInRpdGxlIjogIlRvdGFsIEVsaWdpYmxlIE1lbWJlcnMiCiAgICAgICAgICAgICAgfQogICAgICAgICAgICB9CiAgICAgICAgICB9LAogICAgICAgICAgInBvc2l0aW9uIjogewogICAgICAgICAgICAieCI6IDQsCiAgICAgICAgICAgICJ5IjogMiwKICAgICAgICAgICAgIndpZHRoIjogMiwKICAgICAgICAgICAgImhlaWdodCI6IDMKICAgICAgICAgIH0KICAgICAgICB9LAogICAgICAgIHsKICAgICAgICAgICJ3aWRnZXQiOiB7CiAgICAgICAgICAgICJuYW1lIjogImhkci1tZWFzdXJlIiwKICAgICAgICAgICAgIm11bHRpbGluZVRleHRib3hTcGVjIjogewogICAgICAgICAgICAgICJsaW5lcyI6IFsKICAgICAgICAgICAgICAgICIjIyBDb21wbGlhbmNlIFJhdGUgYnkgTWVhc3VyZSAmIExpbmUgb2YgQnVzaW5lc3MiCiAgICAgICAgICAgICAgXQogICAgICAgICAgICB9CiAgICAgICAgICB9LAogICAgICAgICAgInBvc2l0aW9uIjogewogICAgICAgICAgICAieCI6IDAsCiAgICAgICAgICAgICJ5IjogNSwKICAgICAgICAgICAgIndpZHRoIjogNiwKICAgICAgICAgICAgImhlaWdodCI6IDEKICAgICAgICAgIH0KICAgICAgICB9LAogICAgICAgIHsKICAgICAgICAgICJ3aWRnZXQiOiB7CiAgICAgICAgICAgICJuYW1lIjogImJhci1tZWFzdXJlIiwKICAgICAgICAgICAgInF1ZXJpZXMiOiBbCiAgICAgICAgICAgICAgewogICAgICAgICAgICAgICAgIm5hbWUiOiAibWFpbl9xdWVyeSIsCiAgICAgICAgICAgICAgICAicXVlcnkiOiB7CiAgICAgICAgICAgICAgICAgICJkYXRhc2V0TmFtZSI6ICJkc19ieV9tZWFzdXJlIiwKICAgICAgICAgICAgICAgICAgImZpZWxkcyI6IFsKICAgICAgICAgICAgICAgICAgICB7CiAgICAgICAgICAgICAgICAgICAgICAibmFtZSI6ICJtZWFzdXJlX25hbWUiLAogICAgICAgICAgICAgICAgICAgICAgImV4cHJlc3Npb24iOiAiYG1lYXN1cmVfbmFtZWAiCiAgICAgICAgICAgICAgICAgICAgfSwKICAgICAgICAgICAgICAgICAgICB7CiAgICAgICAgICAgICAgICAgICAgICAibmFtZSI6ICJzdW0oY29tcGxpYW5jZV9yYXRlX3BjdCkiLAogICAgICAgICAgICAgICAgICAgICAgImV4cHJlc3Npb24iOiAiU1VNKGBjb21wbGlhbmNlX3JhdGVfcGN0YCkiCiAgICAgICAgICAgICAgICAgICAgfQogICAgICAgICAgICAgICAgICBdLAogICAgICAgICAgICAgICAgICAiZGlzYWdncmVnYXRlZCI6IGZhbHNlCiAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgfQogICAgICAgICAgICBdLAogICAgICAgICAgICAic3BlYyI6IHsKICAgICAgICAgICAgICAidmVyc2lvbiI6IDMsCiAgICAgICAgICAgICAgImZyYW1lIjogewogICAgICAgICAgICAgICAgInNob3dUaXRsZSI6IHRydWUsCiAgICAgICAgICAgICAgICAidGl0bGUiOiAiQ29tcGxpYW5jZSBSYXRlICglKSBieSBNZWFzdXJlIgogICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgIm1hcmsiOiB7CiAgICAgICAgICAgICAgICAiY29sb3JzIjogWwogICAgICAgICAgICAgICAgICAiI0ZGQUIwMCIsCiAgICAgICAgICAgICAgICAgICIjMDBBOTcyIiwKICAgICAgICAgICAgICAgICAgIiNGRjM2MjEiLAogICAgICAgICAgICAgICAgICAiIzhCQ0FFNyIsCiAgICAgICAgICAgICAgICAgICIjQUI0MDU3IiwKICAgICAgICAgICAgICAgICAgIiM5OUREQjQiLAogICAgICAgICAgICAgICAgICAiI0ZDQTRBMSIsCiAgICAgICAgICAgICAgICAgICIjOTE5MTkxIiwKICAgICAgICAgICAgICAgICAgIiNCRjcwODAiCiAgICAgICAgICAgICAgICBdCiAgICAgICAgICAgICAgfSwKICAgICAgICAgICAgICAid2lkZ2V0VHlwZSI6ICJiYXIiLAogICAgICAgICAgICAgICJlbmNvZGluZ3MiOiB7CiAgICAgICAgICAgICAgICAieCI6IHsKICAgICAgICAgICAgICAgICAgImZpZWxkTmFtZSI6ICJtZWFzdXJlX25hbWUiLAogICAgICAgICAgICAgICAgICAiZGlzcGxheU5hbWUiOiAiTWVhc3VyZSIsCiAgICAgICAgICAgICAgICAgICJzY2FsZSI6IHsKICAgICAgICAgICAgICAgICAgICAidHlwZSI6ICJjYXRlZ29yaWNhbCIsCiAgICAgICAgICAgICAgICAgICAgInNvcnQiOiB7CiAgICAgICAgICAgICAgICAgICAgICAiYnkiOiAieSIKICAgICAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgICAgIH0KICAgICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgICAieSI6IHsKICAgICAgICAgICAgICAgICAgImZpZWxkTmFtZSI6ICJzdW0oY29tcGxpYW5jZV9yYXRlX3BjdCkiLAogICAgICAgICAgICAgICAgICAiZGlzcGxheU5hbWUiOiAiQ29tcGxpYW5jZSBSYXRlICglKSIsCiAgICAgICAgICAgICAgICAgICJzY2FsZSI6IHsKICAgICAgICAgICAgICAgICAgICAidHlwZSI6ICJxdWFudGl0YXRpdmUiCiAgICAgICAgICAgICAgICAgIH0KICAgICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgICAibGFiZWwiOiB7CiAgICAgICAgICAgICAgICAgICJzaG93IjogdHJ1ZQogICAgICAgICAgICAgICAgfQogICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgImRhdGEiOiB7CiAgICAgICAgICAgICAgICAicXVlcnlOYW1lIjogIm1haW5fcXVlcnkiCiAgICAgICAgICAgICAgfQogICAgICAgICAgICB9CiAgICAgICAgICB9LAogICAgICAgICAgInBvc2l0aW9uIjogewogICAgICAgICAgICAieCI6IDAsCiAgICAgICAgICAgICJ5IjogNiwKICAgICAgICAgICAgIndpZHRoIjogMywKICAgICAgICAgICAgImhlaWdodCI6IDYKICAgICAgICAgIH0KICAgICAgICB9LAogICAgICAgIHsKICAgICAgICAgICJ3aWRnZXQiOiB7CiAgICAgICAgICAgICJuYW1lIjogImJhci1sb2IiLAogICAgICAgICAgICAicXVlcmllcyI6IFsKICAgICAgICAgICAgICB7CiAgICAgICAgICAgICAgICAibmFtZSI6ICJtYWluX3F1ZXJ5IiwKICAgICAgICAgICAgICAgICJxdWVyeSI6IHsKICAgICAgICAgICAgICAgICAgImRhdGFzZXROYW1lIjogImRzX2J5X2xvYiIsCiAgICAgICAgICAgICAgICAgICJmaWVsZHMiOiBbCiAgICAgICAgICAgICAgICAgICAgewogICAgICAgICAgICAgICAgICAgICAgIm5hbWUiOiAibGluZV9vZl9idXNpbmVzcyIsCiAgICAgICAgICAgICAgICAgICAgICAiZXhwcmVzc2lvbiI6ICJgbGluZV9vZl9idXNpbmVzc2AiCiAgICAgICAgICAgICAgICAgICAgfSwKICAgICAgICAgICAgICAgICAgICB7CiAgICAgICAgICAgICAgICAgICAgICAibmFtZSI6ICJzdW0oY29tcGxpYW5jZV9yYXRlX3BjdCkiLAogICAgICAgICAgICAgICAgICAgICAgImV4cHJlc3Npb24iOiAiU1VNKGBjb21wbGlhbmNlX3JhdGVfcGN0YCkiCiAgICAgICAgICAgICAgICAgICAgfQogICAgICAgICAgICAgICAgICBdLAogICAgICAgICAgICAgICAgICAiZGlzYWdncmVnYXRlZCI6IGZhbHNlCiAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgfQogICAgICAgICAgICBdLAogICAgICAgICAgICAic3BlYyI6IHsKICAgICAgICAgICAgICAidmVyc2lvbiI6IDMsCiAgICAgICAgICAgICAgImZyYW1lIjogewogICAgICAgICAgICAgICAgInNob3dUaXRsZSI6IHRydWUsCiAgICAgICAgICAgICAgICAidGl0bGUiOiAiQ29tcGxpYW5jZSBSYXRlICglKSBieSBMaW5lIG9mIEJ1c2luZXNzIgogICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgIm1hcmsiOiB7CiAgICAgICAgICAgICAgICAiY29sb3JzIjogWwogICAgICAgICAgICAgICAgICAiI0ZGQUIwMCIsCiAgICAgICAgICAgICAgICAgICIjMDBBOTcyIiwKICAgICAgICAgICAgICAgICAgIiNGRjM2MjEiLAogICAgICAgICAgICAgICAgICAiIzhCQ0FFNyIsCiAgICAgICAgICAgICAgICAgICIjQUI0MDU3IiwKICAgICAgICAgICAgICAgICAgIiM5OUREQjQiLAogICAgICAgICAgICAgICAgICAiI0ZDQTRBMSIsCiAgICAgICAgICAgICAgICAgICIjOTE5MTkxIiwKICAgICAgICAgICAgICAgICAgIiNCRjcwODAiCiAgICAgICAgICAgICAgICBdCiAgICAgICAgICAgICAgfSwKICAgICAgICAgICAgICAid2lkZ2V0VHlwZSI6ICJiYXIiLAogICAgICAgICAgICAgICJlbmNvZGluZ3MiOiB7CiAgICAgICAgICAgICAgICAieCI6IHsKICAgICAgICAgICAgICAgICAgImZpZWxkTmFtZSI6ICJsaW5lX29mX2J1c2luZXNzIiwKICAgICAgICAgICAgICAgICAgImRpc3BsYXlOYW1lIjogIkxpbmUgb2YgQnVzaW5lc3MiLAogICAgICAgICAgICAgICAgICAic2NhbGUiOiB7CiAgICAgICAgICAgICAgICAgICAgInR5cGUiOiAiY2F0ZWdvcmljYWwiLAogICAgICAgICAgICAgICAgICAgICJzb3J0IjogewogICAgICAgICAgICAgICAgICAgICAgImJ5IjogInktcmV2ZXJzZWQiCiAgICAgICAgICAgICAgICAgICAgfQogICAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgICB9LAogICAgICAgICAgICAgICAgInkiOiB7CiAgICAgICAgICAgICAgICAgICJmaWVsZE5hbWUiOiAic3VtKGNvbXBsaWFuY2VfcmF0ZV9wY3QpIiwKICAgICAgICAgICAgICAgICAgImRpc3BsYXlOYW1lIjogIkNvbXBsaWFuY2UgUmF0ZSAoJSkiLAogICAgICAgICAgICAgICAgICAic2NhbGUiOiB7CiAgICAgICAgICAgICAgICAgICAgInR5cGUiOiAicXVhbnRpdGF0aXZlIgogICAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgICB9LAogICAgICAgICAgICAgICAgImxhYmVsIjogewogICAgICAgICAgICAgICAgICAic2hvdyI6IHRydWUKICAgICAgICAgICAgICAgIH0KICAgICAgICAgICAgICB9LAogICAgICAgICAgICAgICJkYXRhIjogewogICAgICAgICAgICAgICAgInF1ZXJ5TmFtZSI6ICJtYWluX3F1ZXJ5IgogICAgICAgICAgICAgIH0KICAgICAgICAgICAgfQogICAgICAgICAgfSwKICAgICAgICAgICJwb3NpdGlvbiI6IHsKICAgICAgICAgICAgIngiOiAzLAogICAgICAgICAgICAieSI6IDYsCiAgICAgICAgICAgICJ3aWR0aCI6IDMsCiAgICAgICAgICAgICJoZWlnaHQiOiA2CiAgICAgICAgICB9CiAgICAgICAgfSwKICAgICAgICB7CiAgICAgICAgICAid2lkZ2V0IjogewogICAgICAgICAgICAibmFtZSI6ICJoZHItcmVnaW9uIiwKICAgICAgICAgICAgIm11bHRpbGluZVRleHRib3hTcGVjIjogewogICAgICAgICAgICAgICJsaW5lcyI6IFsKICAgICAgICAgICAgICAgICIjIyBPcGVuIENhcmUgR2FwcyAmIFllYXItb3Zlci1ZZWFyIFRyZW5kIgogICAgICAgICAgICAgIF0KICAgICAgICAgICAgfQogICAgICAgICAgfSwKICAgICAgICAgICJwb3NpdGlvbiI6IHsKICAgICAgICAgICAgIngiOiAwLAogICAgICAgICAgICAieSI6IDEyLAogICAgICAgICAgICAid2lkdGgiOiA2LAogICAgICAgICAgICAiaGVpZ2h0IjogMQogICAgICAgICAgfQogICAgICAgIH0sCiAgICAgICAgewogICAgICAgICAgIndpZGdldCI6IHsKICAgICAgICAgICAgIm5hbWUiOiAiYmFyLXJlZ2lvbiIsCiAgICAgICAgICAgICJxdWVyaWVzIjogWwogICAgICAgICAgICAgIHsKICAgICAgICAgICAgICAgICJuYW1lIjogIm1haW5fcXVlcnkiLAogICAgICAgICAgICAgICAgInF1ZXJ5IjogewogICAgICAgICAgICAgICAgICAiZGF0YXNldE5hbWUiOiAiZHNfYnlfcmVnaW9uIiwKICAgICAgICAgICAgICAgICAgImZpZWxkcyI6IFsKICAgICAgICAgICAgICAgICAgICB7CiAgICAgICAgICAgICAgICAgICAgICAibmFtZSI6ICJSZWdpb24iLAogICAgICAgICAgICAgICAgICAgICAgImV4cHJlc3Npb24iOiAiYFJlZ2lvbmAiCiAgICAgICAgICAgICAgICAgICAgfSwKICAgICAgICAgICAgICAgICAgICB7CiAgICAgICAgICAgICAgICAgICAgICAibmFtZSI6ICJzdW0ob3Blbl9jYXJlX2dhcHMpIiwKICAgICAgICAgICAgICAgICAgICAgICJleHByZXNzaW9uIjogIlNVTShgb3Blbl9jYXJlX2dhcHNgKSIKICAgICAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgICAgIF0sCiAgICAgICAgICAgICAgICAgICJkaXNhZ2dyZWdhdGVkIjogZmFsc2UKICAgICAgICAgICAgICAgIH0KICAgICAgICAgICAgICB9CiAgICAgICAgICAgIF0sCiAgICAgICAgICAgICJzcGVjIjogewogICAgICAgICAgICAgICJ2ZXJzaW9uIjogMywKICAgICAgICAgICAgICAid2lkZ2V0VHlwZSI6ICJiYXIiLAogICAgICAgICAgICAgICJlbmNvZGluZ3MiOiB7CiAgICAgICAgICAgICAgICAieCI6IHsKICAgICAgICAgICAgICAgICAgImZpZWxkTmFtZSI6ICJSZWdpb24iLAogICAgICAgICAgICAgICAgICAic2NhbGUiOiB7CiAgICAgICAgICAgICAgICAgICAgInR5cGUiOiAiY2F0ZWdvcmljYWwiLAogICAgICAgICAgICAgICAgICAgICJzb3J0IjogewogICAgICAgICAgICAgICAgICAgICAgImJ5IjogInktcmV2ZXJzZWQiCiAgICAgICAgICAgICAgICAgICAgfQogICAgICAgICAgICAgICAgICB9LAogICAgICAgICAgICAgICAgICAiZGlzcGxheU5hbWUiOiAiUmVnaW9uIgogICAgICAgICAgICAgICAgfSwKICAgICAgICAgICAgICAgICJ5IjogewogICAgICAgICAgICAgICAgICAiZmllbGROYW1lIjogInN1bShvcGVuX2NhcmVfZ2FwcykiLAogICAgICAgICAgICAgICAgICAic2NhbGUiOiB7CiAgICAgICAgICAgICAgICAgICAgInR5cGUiOiAicXVhbnRpdGF0aXZlIgogICAgICAgICAgICAgICAgICB9LAogICAgICAgICAgICAgICAgICAiZGlzcGxheU5hbWUiOiAiT3BlbiBDYXJlIEdhcHMiCiAgICAgICAgICAgICAgICB9LAogICAgICAgICAgICAgICAgImxhYmVsIjogewogICAgICAgICAgICAgICAgICAic2hvdyI6IHRydWUKICAgICAgICAgICAgICAgIH0KICAgICAgICAgICAgICB9LAogICAgICAgICAgICAgICJmcmFtZSI6IHsKICAgICAgICAgICAgICAgICJzaG93VGl0bGUiOiB0cnVlLAogICAgICAgICAgICAgICAgInRpdGxlIjogIk9wZW4gQ2FyZSBHYXBzIGJ5IFJlZ2lvbiIKICAgICAgICAgICAgICB9LAogICAgICAgICAgICAgICJtYXJrIjogewogICAgICAgICAgICAgICAgImNvbG9ycyI6IFsKICAgICAgICAgICAgICAgICAgIiNGRkFCMDAiLAogICAgICAgICAgICAgICAgICAiIzAwQTk3MiIsCiAgICAgICAgICAgICAgICAgICIjRkYzNjIxIiwKICAgICAgICAgICAgICAgICAgIiM4QkNBRTciLAogICAgICAgICAgICAgICAgICAiI0FCNDA1NyIsCiAgICAgICAgICAgICAgICAgICIjOTlEREI0IiwKICAgICAgICAgICAgICAgICAgIiNGQ0E0QTEiLAogICAgICAgICAgICAgICAgICAiIzkxOTE5MSIsCiAgICAgICAgICAgICAgICAgICIjQkY3MDgwIgogICAgICAgICAgICAgICAgXQogICAgICAgICAgICAgIH0KICAgICAgICAgICAgfQogICAgICAgICAgfSwKICAgICAgICAgICJwb3NpdGlvbiI6IHsKICAgICAgICAgICAgIngiOiAwLAogICAgICAgICAgICAieSI6IDEzLAogICAgICAgICAgICAid2lkdGgiOiAzLAogICAgICAgICAgICAiaGVpZ2h0IjogNgogICAgICAgICAgfQogICAgICAgIH0sCiAgICAgICAgewogICAgICAgICAgIndpZGdldCI6IHsKICAgICAgICAgICAgIm5hbWUiOiAiYmFyLXlveSIsCiAgICAgICAgICAgICJxdWVyaWVzIjogWwogICAgICAgICAgICAgIHsKICAgICAgICAgICAgICAgICJuYW1lIjogIm1haW5fcXVlcnkiLAogICAgICAgICAgICAgICAgInF1ZXJ5IjogewogICAgICAgICAgICAgICAgICAiZGF0YXNldE5hbWUiOiAiZHNfeW95IiwKICAgICAgICAgICAgICAgICAgImZpZWxkcyI6IFsKICAgICAgICAgICAgICAgICAgICB7CiAgICAgICAgICAgICAgICAgICAgICAibmFtZSI6ICJxdWFsaXR5X2RvbWFpbiIsCiAgICAgICAgICAgICAgICAgICAgICAiZXhwcmVzc2lvbiI6ICJgcXVhbGl0eV9kb21haW5gIgogICAgICAgICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgICAgICAgewogICAgICAgICAgICAgICAgICAgICAgIm5hbWUiOiAibWVhc3VyZW1lbnRfeWVhciIsCiAgICAgICAgICAgICAgICAgICAgICAiZXhwcmVzc2lvbiI6ICJgbWVhc3VyZW1lbnRfeWVhcmAiCiAgICAgICAgICAgICAgICAgICAgfSwKICAgICAgICAgICAgICAgICAgICB7CiAgICAgICAgICAgICAgICAgICAgICAibmFtZSI6ICJzdW0oY29tcGxpYW5jZV9yYXRlX3BjdCkiLAogICAgICAgICAgICAgICAgICAgICAgImV4cHJlc3Npb24iOiAiU1VNKGBjb21wbGlhbmNlX3JhdGVfcGN0YCkiCiAgICAgICAgICAgICAgICAgICAgfQogICAgICAgICAgICAgICAgICBdLAogICAgICAgICAgICAgICAgICAiZGlzYWdncmVnYXRlZCI6IGZhbHNlCiAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgfQogICAgICAgICAgICBdLAogICAgICAgICAgICAic3BlYyI6IHsKICAgICAgICAgICAgICAidmVyc2lvbiI6IDMsCiAgICAgICAgICAgICAgImZyYW1lIjogewogICAgICAgICAgICAgICAgInNob3dUaXRsZSI6IHRydWUsCiAgICAgICAgICAgICAgICAidGl0bGUiOiAiQ29tcGxpYW5jZSBSYXRlICglKSBieSBZZWFyIChzcGxpdCBieSBRdWFsaXR5IERvbWFpbikiCiAgICAgICAgICAgICAgfSwKICAgICAgICAgICAgICAibWFyayI6IHsKICAgICAgICAgICAgICAgICJjb2xvcnMiOiBbCiAgICAgICAgICAgICAgICAgICIjRkZBQjAwIiwKICAgICAgICAgICAgICAgICAgIiMwMEE5NzIiLAogICAgICAgICAgICAgICAgICAiI0ZGMzYyMSIsCiAgICAgICAgICAgICAgICAgICIjOEJDQUU3IiwKICAgICAgICAgICAgICAgICAgIiNBQjQwNTciLAogICAgICAgICAgICAgICAgICAiIzk5RERCNCIsCiAgICAgICAgICAgICAgICAgICIjRkNBNEExIiwKICAgICAgICAgICAgICAgICAgIiM5MTkxOTEiLAogICAgICAgICAgICAgICAgICAiI0JGNzA4MCIKICAgICAgICAgICAgICAgIF0sCiAgICAgICAgICAgICAgICAibGF5b3V0IjogImdyb3VwIgogICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgIndpZGdldFR5cGUiOiAiYmFyIiwKICAgICAgICAgICAgICAiZW5jb2RpbmdzIjogewogICAgICAgICAgICAgICAgIngiOiB7CiAgICAgICAgICAgICAgICAgICJmaWVsZE5hbWUiOiAibWVhc3VyZW1lbnRfeWVhciIsCiAgICAgICAgICAgICAgICAgICJkaXNwbGF5TmFtZSI6ICJNZWFzdXJlbWVudCBZZWFyIiwKICAgICAgICAgICAgICAgICAgInNjYWxlIjogewogICAgICAgICAgICAgICAgICAgICJ0eXBlIjogImNhdGVnb3JpY2FsIgogICAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgICB9LAogICAgICAgICAgICAgICAgInkiOiB7CiAgICAgICAgICAgICAgICAgICJmaWVsZE5hbWUiOiAic3VtKGNvbXBsaWFuY2VfcmF0ZV9wY3QpIiwKICAgICAgICAgICAgICAgICAgImRpc3BsYXlOYW1lIjogIkNvbXBsaWFuY2UgUmF0ZSAoJSkiLAogICAgICAgICAgICAgICAgICAic2NhbGUiOiB7CiAgICAgICAgICAgICAgICAgICAgInR5cGUiOiAicXVhbnRpdGF0aXZlIgogICAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgICB9LAogICAgICAgICAgICAgICAgImNvbG9yIjogewogICAgICAgICAgICAgICAgICAiZmllbGROYW1lIjogInF1YWxpdHlfZG9tYWluIiwKICAgICAgICAgICAgICAgICAgImRpc3BsYXlOYW1lIjogIlF1YWxpdHkgRG9tYWluIiwKICAgICAgICAgICAgICAgICAgInNjYWxlIjogewogICAgICAgICAgICAgICAgICAgICJ0eXBlIjogImNhdGVnb3JpY2FsIgogICAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgICB9LAogICAgICAgICAgICAgICAgImxhYmVsIjogewogICAgICAgICAgICAgICAgICAic2hvdyI6IHRydWUKICAgICAgICAgICAgICAgIH0KICAgICAgICAgICAgICB9LAogICAgICAgICAgICAgICJkYXRhIjogewogICAgICAgICAgICAgICAgInF1ZXJ5TmFtZSI6ICJtYWluX3F1ZXJ5IgogICAgICAgICAgICAgIH0KICAgICAgICAgICAgfQogICAgICAgICAgfSwKICAgICAgICAgICJwb3NpdGlvbiI6IHsKICAgICAgICAgICAgIngiOiAzLAogICAgICAgICAgICAieSI6IDEzLAogICAgICAgICAgICAid2lkdGgiOiAzLAogICAgICAgICAgICAiaGVpZ2h0IjogNgogICAgICAgICAgfQogICAgICAgIH0KICAgICAgXSwKICAgICAgInBhZ2VUeXBlIjogIlBBR0VfVFlQRV9DQU5WQVMiCiAgICB9LAogICAgewogICAgICAibmFtZSI6ICJmaWx0ZXJzIiwKICAgICAgImRpc3BsYXlOYW1lIjogIkZpbHRlcnMiLAogICAgICAibGF5b3V0IjogWwogICAgICAgIHsKICAgICAgICAgICJ3aWRnZXQiOiB7CiAgICAgICAgICAgICJuYW1lIjogImZsdC15ZWFyIiwKICAgICAgICAgICAgInF1ZXJpZXMiOiBbCiAgICAgICAgICAgICAgewogICAgICAgICAgICAgICAgIm5hbWUiOiAiZGFzaGJvYXJkcy8wMWYxNjVmOWI5N2MxYWFlYWQ1ZWQ2NmZmMTM3OGY4Yy9kYXRhc2V0cy8wMWYxNjVmOWI5N2MxZDRkODAyNDQ2NDc5MTA0OGEyZF9tZWFzdXJlbWVudF95ZWFyIiwKICAgICAgICAgICAgICAgICJxdWVyeSI6IHsKICAgICAgICAgICAgICAgICAgImRhdGFzZXROYW1lIjogImRzX2twaSIsCiAgICAgICAgICAgICAgICAgICJmaWVsZHMiOiBbCiAgICAgICAgICAgICAgICAgICAgewogICAgICAgICAgICAgICAgICAgICAgIm5hbWUiOiAibWVhc3VyZW1lbnRfeWVhciIsCiAgICAgICAgICAgICAgICAgICAgICAiZXhwcmVzc2lvbiI6ICJgbWVhc3VyZW1lbnRfeWVhcmAiCiAgICAgICAgICAgICAgICAgICAgfSwKICAgICAgICAgICAgICAgICAgICB7CiAgICAgICAgICAgICAgICAgICAgICAibmFtZSI6ICJtZWFzdXJlbWVudF95ZWFyX2Fzc29jaWF0aXZpdHkiLAogICAgICAgICAgICAgICAgICAgICAgImV4cHJlc3Npb24iOiAiQ09VTlRfSUYoYGFzc29jaWF0aXZlX2ZpbHRlcl9wcmVkaWNhdGVfZ3JvdXBgKSIKICAgICAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgICAgIF0sCiAgICAgICAgICAgICAgICAgICJkaXNhZ2dyZWdhdGVkIjogZmFsc2UKICAgICAgICAgICAgICAgIH0KICAgICAgICAgICAgICB9CiAgICAgICAgICAgIF0sCiAgICAgICAgICAgICJzcGVjIjogewogICAgICAgICAgICAgICJ2ZXJzaW9uIjogMiwKICAgICAgICAgICAgICAiZnJhbWUiOiB7CiAgICAgICAgICAgICAgICAic2hvd1RpdGxlIjogdHJ1ZSwKICAgICAgICAgICAgICAgICJ0aXRsZSI6ICJNZWFzdXJlbWVudCBZZWFyIgogICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgIndpZGdldFR5cGUiOiAiZmlsdGVyLXNpbmdsZS1zZWxlY3QiLAogICAgICAgICAgICAgICJlbmNvZGluZ3MiOiB7CiAgICAgICAgICAgICAgICAiZmllbGRzIjogWwogICAgICAgICAgICAgICAgICB7CiAgICAgICAgICAgICAgICAgICAgImZpZWxkTmFtZSI6ICJtZWFzdXJlbWVudF95ZWFyIiwKICAgICAgICAgICAgICAgICAgICAicXVlcnlOYW1lIjogImRhc2hib2FyZHMvMDFmMTY1ZjliOTdjMWFhZWFkNWVkNjZmZjEzNzhmOGMvZGF0YXNldHMvMDFmMTY1ZjliOTdjMWQ0ZDgwMjQ0NjQ3OTEwNDhhMmRfbWVhc3VyZW1lbnRfeWVhciIKICAgICAgICAgICAgICAgICAgfQogICAgICAgICAgICAgICAgXQogICAgICAgICAgICAgIH0KICAgICAgICAgICAgfQogICAgICAgICAgfSwKICAgICAgICAgICJwb3NpdGlvbiI6IHsKICAgICAgICAgICAgIngiOiAwLAogICAgICAgICAgICAieSI6IDAsCiAgICAgICAgICAgICJ3aWR0aCI6IDIsCiAgICAgICAgICAgICJoZWlnaHQiOiAxCiAgICAgICAgICB9CiAgICAgICAgfSwKICAgICAgICB7CiAgICAgICAgICAid2lkZ2V0IjogewogICAgICAgICAgICAibmFtZSI6ICJmbHQtbG9iIiwKICAgICAgICAgICAgInF1ZXJpZXMiOiBbCiAgICAgICAgICAgICAgewogICAgICAgICAgICAgICAgIm5hbWUiOiAiZGFzaGJvYXJkcy8wMWYxNjVmOWI5N2MxYWFlYWQ1ZWQ2NmZmMTM3OGY4Yy9kYXRhc2V0cy8wMWYxNjVmOWI5N2MxZDRkODAyNDQ2NDc5MTA0OGEyZF9saW5lX29mX2J1c2luZXNzIiwKICAgICAgICAgICAgICAgICJxdWVyeSI6IHsKICAgICAgICAgICAgICAgICAgImRhdGFzZXROYW1lIjogImRzX2twaSIsCiAgICAgICAgICAgICAgICAgICJmaWVsZHMiOiBbCiAgICAgICAgICAgICAgICAgICAgewogICAgICAgICAgICAgICAgICAgICAgIm5hbWUiOiAibGluZV9vZl9idXNpbmVzcyIsCiAgICAgICAgICAgICAgICAgICAgICAiZXhwcmVzc2lvbiI6ICJgbGluZV9vZl9idXNpbmVzc2AiCiAgICAgICAgICAgICAgICAgICAgfSwKICAgICAgICAgICAgICAgICAgICB7CiAgICAgICAgICAgICAgICAgICAgICAibmFtZSI6ICJsaW5lX29mX2J1c2luZXNzX2Fzc29jaWF0aXZpdHkiLAogICAgICAgICAgICAgICAgICAgICAgImV4cHJlc3Npb24iOiAiQ09VTlRfSUYoYGFzc29jaWF0aXZlX2ZpbHRlcl9wcmVkaWNhdGVfZ3JvdXBgKSIKICAgICAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgICAgIF0sCiAgICAgICAgICAgICAgICAgICJkaXNhZ2dyZWdhdGVkIjogZmFsc2UKICAgICAgICAgICAgICAgIH0KICAgICAgICAgICAgICB9CiAgICAgICAgICAgIF0sCiAgICAgICAgICAgICJzcGVjIjogewogICAgICAgICAgICAgICJ2ZXJzaW9uIjogMiwKICAgICAgICAgICAgICAiZnJhbWUiOiB7CiAgICAgICAgICAgICAgICAic2hvd1RpdGxlIjogdHJ1ZSwKICAgICAgICAgICAgICAgICJ0aXRsZSI6ICJMaW5lIG9mIEJ1c2luZXNzIgogICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgIndpZGdldFR5cGUiOiAiZmlsdGVyLW11bHRpLXNlbGVjdCIsCiAgICAgICAgICAgICAgImVuY29kaW5ncyI6IHsKICAgICAgICAgICAgICAgICJmaWVsZHMiOiBbCiAgICAgICAgICAgICAgICAgIHsKICAgICAgICAgICAgICAgICAgICAiZmllbGROYW1lIjogImxpbmVfb2ZfYnVzaW5lc3MiLAogICAgICAgICAgICAgICAgICAgICJxdWVyeU5hbWUiOiAiZGFzaGJvYXJkcy8wMWYxNjVmOWI5N2MxYWFlYWQ1ZWQ2NmZmMTM3OGY4Yy9kYXRhc2V0cy8wMWYxNjVmOWI5N2MxZDRkODAyNDQ2NDc5MTA0OGEyZF9saW5lX29mX2J1c2luZXNzIgogICAgICAgICAgICAgICAgICB9CiAgICAgICAgICAgICAgICBdCiAgICAgICAgICAgICAgfQogICAgICAgICAgICB9CiAgICAgICAgICB9LAogICAgICAgICAgInBvc2l0aW9uIjogewogICAgICAgICAgICAieCI6IDIsCiAgICAgICAgICAgICJ5IjogMCwKICAgICAgICAgICAgIndpZHRoIjogMiwKICAgICAgICAgICAgImhlaWdodCI6IDEKICAgICAgICAgIH0KICAgICAgICB9CiAgICAgIF0sCiAgICAgICJwYWdlVHlwZSI6ICJQQUdFX1RZUEVfR0xPQkFMX0ZJTFRFUlMiCiAgICB9CiAgXSwKICAidWlTZXR0aW5ncyI6IHsKICAgICJ0aGVtZSI6IHsKICAgICAgIndpZGdldEhlYWRlckFsaWdubWVudCI6ICJBTElHTk1FTlRfVU5TUEVDSUZJRUQiCiAgICB9LAogICAgImFwcGx5TW9kZUVuYWJsZWQiOiBmYWxzZQogIH0KfQo="
)

if CREATE_DASHBOARD:
    import base64, requests
    if not CREATE_MV:
        print(f"WARNING: create_metric_view=false — the dashboard needs {CATALOG}.{SCHEMA}.quality_measures_mv "
              "to exist. Creating anyway; tiles stay empty until that view exists.")

    ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    host = ctx.apiUrl().get()
    token = ctx.apiToken().get()
    H = {"Authorization": f"Bearer {token}"}
    DISPLAY = "MVP Health Care \u2014 Quality Measures (HEDIS) [Workshop]"

    # Point the embedded template at this run's catalog.schema
    serialized = base64.b64decode(_DASHBOARD_TEMPLATE_B64).decode("utf-8")
    serialized = serialized.replace("ai_dev_kit_demo_3_catalog.mvp_quality_workshop", f"{CATALOG}.{SCHEMA}")

    # Resolve a SQL warehouse (widget, else first RUNNING, else first available)
    wid = WAREHOUSE_ID
    if not wid:
        whs = requests.get(f"{host}/api/2.0/sql/warehouses", headers=H).json().get("warehouses", [])
        running = [x for x in whs if x.get("state") == "RUNNING"]
        wid = ((running or whs or [{}])[0]).get("id")
    assert wid, "No SQL warehouse found. Set the warehouse_id widget to a warehouse in your workspace."

    # Place the dashboard in this notebook's parent folder (fallback: the user's home)
    nb = ctx.notebookPath().get()
    parts = [p for p in nb.split("/") if p]
    parent_path = "/Workspace/" + "/".join(parts[:-2]) if len(parts) >= 3 else "/Workspace/" + "/".join(parts[:-1])

    # Idempotent: trash any same-named dashboard already in that folder, then create fresh
    listing = requests.get(f"{host}/api/2.0/lakeview/dashboards",
                           headers=H, params={"page_size": 1000}).json().get("dashboards", [])
    target_dir = parent_path.replace("/Workspace", "", 1)
    for dsh in listing:
        if dsh.get("display_name") == DISPLAY and dsh.get("path", "").rsplit("/", 1)[0] == target_dir:
            requests.delete(f"{host}/api/2.0/lakeview/dashboards/{dsh['dashboard_id']}", headers=H)

    resp = requests.post(f"{host}/api/2.0/lakeview/dashboards", headers=H,
                         json={"display_name": DISPLAY, "warehouse_id": wid,
                               "serialized_dashboard": serialized, "parent_path": parent_path})
    resp.raise_for_status()
    did = resp.json()["dashboard_id"]
    print(f"Created dashboard {DISPLAY!r}")
    print(f"  id        : {did}")
    print(f"  folder    : {parent_path}")
    print(f"  warehouse : {wid}")
    print(f"  open it from the folder above, or: {host}/dashboardsv3/{did}/published")
else:
    print("Skipped dashboard creation (create_dashboard=false).")

# COMMAND ----------

print("Setup complete.")
