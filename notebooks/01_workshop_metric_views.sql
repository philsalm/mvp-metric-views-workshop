-- Databricks notebook source
-- MAGIC %md
-- MAGIC # MVP Health Care — Unity Catalog Metric Views Workshop
-- MAGIC
-- MAGIC A 90-minute hands-on workshop. By the end you will understand what a metric view is, build one on
-- MAGIC synthetic health-plan quality data, query it, consume it from AI/BI and Genie, and identify real MVP
-- MAGIC metrics worth implementing.
-- MAGIC
-- MAGIC **How to use this notebook**
-- MAGIC 1. Attach to a SQL warehouse or cluster running **Databricks Runtime 17.3 or above** (metric views require 17.3+).
-- MAGIC 2. In the widget bar at the top, set **`catalog`** to the catalog your admin loaded the workshop data into,
-- MAGIC    set **`schema`** if it differs from the default, and set **`your_name`** so your objects are unique.
-- MAGIC 3. Work through the cells top to bottom. Run each query after you create or edit a metric view.
-- MAGIC
-- MAGIC > **Data:** 100% synthetic — no real members, providers, or claims. Loaded by `00_setup_quality_data` (see the README).

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## ⚙️ Set your parameters
-- MAGIC Run this cell once. It creates the widgets at the top of the notebook — then edit their values there.
-- MAGIC The whole notebook keys off `${catalog}`, `${schema}`, and `${your_name}`, so you only change them in one place.

-- COMMAND ----------

CREATE WIDGET TEXT catalog DEFAULT 'your_catalog_here';
CREATE WIDGET TEXT schema DEFAULT 'mvp_quality_workshop';
CREATE WIDGET TEXT your_name DEFAULT 'me';

-- COMMAND ----------

-- DBTITLE 1,Cell 4
-- Confirm the parameters resolve and the data is reachable.
SELECT '${catalog}.${schema}' AS workshop_location,
       COUNT(*)                AS eligible_member_measures
FROM ${catalog}.${schema}.fact_measure_compliance;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## Part 0 — Intro: what is a metric view? (facilitator talking points)
-- MAGIC
-- MAGIC **The problem.** Today, "the BCS rate" or "MLR" is calculated in many places — a SQL script here, a
-- MAGIC dashboard there, a spreadsheet on someone's laptop. The definitions drift. Two teams present two numbers
-- MAGIC for the same KPI in the same meeting. The logic is copied, pasted, and re-derived every time someone needs
-- MAGIC a new cut (by region, by plan, by quarter).
-- MAGIC
-- MAGIC **The idea.** A Unity Catalog metric view is a governed semantic layer. You define a metric once — for
-- MAGIC example, compliant members ÷ eligible members — and then anyone can group it by any available field at
-- MAGIC query time. The query engine generates the correct computation. **One definition, governed in Unity
-- MAGIC Catalog, consumed everywhere.**
-- MAGIC
-- MAGIC **Why it matters for MVP:**
-- MAGIC - **One source of truth** for quality rates, gaps, MLR, PMPM, network adequacy — defined and governed once.
-- MAGIC - **Consistency across tools** — SQL, AI/BI dashboards, Genie (natural language), and external BI (Power BI, Tableau) all read the same definition.
-- MAGIC - **Governance** — metric views are Unity Catalog objects, so access control, lineage, and auditing apply.
-- MAGIC - **Self-service** — analysts slice the metric without re-implementing the math, and Genie can answer plain-language questions because the metric is defined with business meaning.
-- MAGIC
-- MAGIC **The mental model:** a metric view separates **fields** (the things you group and filter by — Measure,
-- MAGIC Line of Business, Region, Year) from **measures** (the aggregations you compute — Eligible Members,
-- MAGIC Compliance Rate, Open Care Gaps). Measures are not pre-aggregated; the grain is decided at query time.

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## Part 1 — Orientation: the workshop data model
-- MAGIC
-- MAGIC The workshop uses a small star schema of synthetic HEDIS-style quality data in `${catalog}.${schema}`:
-- MAGIC
-- MAGIC | Table | Grain | Key columns |
-- MAGIC |---|---|---|
-- MAGIC | `fact_measure_compliance` | one row per eligible (member, measure, year) | `member_id`, `plan_id`, `pcp_provider_id`, `measure_id`, `measurement_year`, `eligible_flag`, `compliant_flag` |
-- MAGIC | `dim_member` | one row per member | `member_id`, `line_of_business`, `region`, `age_band`, `gender`, `plan_id`, `pcp_provider_id` |
-- MAGIC | `dim_plan` | one row per plan | `plan_id`, `plan_name`, `plan_type`, `line_of_business`, `metal_tier` |
-- MAGIC | `dim_provider` | one row per provider | `provider_id`, `specialty`, `region`, `network_status` |
-- MAGIC | `dim_measure` | one row per HEDIS measure | `measure_id`, `measure_name`, `domain`, `description` |
-- MAGIC
-- MAGIC The core HEDIS idea: every measure has an **eligible population** (the denominator) and a **numerator**
-- MAGIC (members who got the right care). Each row in `fact_measure_compliance` is an eligible member-measure;
-- MAGIC `compliant_flag = 1` means they met the measure. So:
-- MAGIC - **Compliance Rate** = `SUM(compliant_flag) / COUNT(1)` — the HEDIS rate.
-- MAGIC - **Open Care Gap** = an eligible member who is not compliant (`compliant_flag = 0`).
-- MAGIC
-- MAGIC Tip: open **Catalog Explorer → `${catalog}` → `${schema}`** and browse the five tables before starting.

-- COMMAND ----------

-- Quick peek at the dimension tables
SELECT * FROM ${catalog}.${schema}.dim_measure ORDER BY measure_id;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## Part 2 — Hands-on exercises
-- MAGIC
-- MAGIC Each exercise builds on the last. We use `CREATE OR REPLACE VIEW` so you can safely re-run any cell; the
-- MAGIC same edits can also be made incrementally with `ALTER VIEW`. Your view is named `quality_mv_${your_name}`
-- MAGIC so everyone's objects are unique.

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ### Exercise 1 — Explore the source (5 min)
-- MAGIC Get a feel for the raw data. Notice you have to **hand-write the rate math**, and you can only group by
-- MAGIC columns on this one table. That's the limitation a metric view removes.

-- COMMAND ----------

-- How many eligible member-measures, and the raw compliance, per measure (2025)?
SELECT measure_id,
       COUNT(*)            AS eligible,
       SUM(compliant_flag) AS compliant,
       ROUND(SUM(compliant_flag) / COUNT(*) * 100, 1) AS rate_pct
FROM ${catalog}.${schema}.fact_measure_compliance
WHERE measurement_year = 2025
GROUP BY measure_id
ORDER BY rate_pct;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ### Exercise 2 — Your first metric view (single source) (10 min)
-- MAGIC Create a metric view on the fact table alone. **Fields** are what you can group by; **measures** are the
-- MAGIC aggregations.

-- COMMAND ----------

CREATE OR REPLACE VIEW ${catalog}.${schema}.quality_mv_${your_name}
WITH METRICS LANGUAGE YAML AS
$$
version: 1.1
comment: "My first quality metric view"
source: ${catalog}.${schema}.fact_measure_compliance
fields:
  - name: measurement_year
    display_name: "Measurement Year"
    expr: measurement_year
  - name: measure_code
    display_name: "Measure Code"
    expr: measure_id
measures:
  - name: eligible_members
    display_name: "Eligible Members"
    expr: COUNT(1)
  - name: compliant_members
    display_name: "Compliant Members"
    expr: SUM(compliant_flag)
  - name: compliance_rate
    display_name: "Compliance Rate"
    expr: SUM(compliant_flag) / COUNT(1)
$$;

-- COMMAND ----------

-- Query it — note that measures must be wrapped in MEASURE()
SELECT measure_code,
       MEASURE(eligible_members)  AS eligible,
       MEASURE(compliant_members) AS compliant,
       ROUND(MEASURE(compliance_rate) * 100, 1) AS rate_pct
FROM ${catalog}.${schema}.quality_mv_${your_name}
WHERE measurement_year = 2025
GROUP BY measure_code
ORDER BY rate_pct;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC You got the same answer as Exercise 1 — but you didn't write the rate math in the query, and you can now
-- MAGIC group by any field without rewriting the measure. That is the whole point: **define once, group by anything.**

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ### Exercise 3 — Add star-schema joins and dimensions (10 min)
-- MAGIC The fact only has codes (`measure_id`, `plan_id`). Join the dimension tables so you can slice by friendly
-- MAGIC business attributes.
-- MAGIC
-- MAGIC > ⚠️ **Gotcha — don't name a join after a reserved word.** Join aliases like `measure` or `plan` collide
-- MAGIC > with SQL/metric-view keywords and fail with `INVALID_EXTRACT_BASE_FIELD_TYPE`. That's why we use `meas`
-- MAGIC > and `pln`. The `source.` prefix always refers to the fact table; a join's `name` refers to that joined table.

-- COMMAND ----------

CREATE OR REPLACE VIEW ${catalog}.${schema}.quality_mv_${your_name}
WITH METRICS LANGUAGE YAML AS
$$
version: 1.1
source: ${catalog}.${schema}.fact_measure_compliance
joins:
  - name: mbr
    source: ${catalog}.${schema}.dim_member
    on: source.member_id = mbr.member_id
  - name: pln
    source: ${catalog}.${schema}.dim_plan
    on: source.plan_id = pln.plan_id
  - name: meas
    source: ${catalog}.${schema}.dim_measure
    on: source.measure_id = meas.measure_id
fields:
  - name: measurement_year
    display_name: "Measurement Year"
    expr: measurement_year
  - name: measure_name
    display_name: "Measure"
    expr: meas.measure_name
  - name: quality_domain
    display_name: "Quality Domain"
    expr: meas.domain
  - name: line_of_business
    display_name: "Line of Business"
    expr: mbr.line_of_business
  - name: region
    display_name: "Region"
    expr: mbr.region
  - name: plan_type
    display_name: "Plan Type"
    expr: pln.plan_type
measures:
  - name: eligible_members
    display_name: "Eligible Members"
    expr: COUNT(1)
  - name: compliant_members
    display_name: "Compliant Members"
    expr: SUM(compliant_flag)
  - name: compliance_rate
    display_name: "Compliance Rate"
    expr: SUM(compliant_flag) / COUNT(1)
$$;

-- COMMAND ----------

-- Slice the same Compliance Rate measure by line of business — no measure rewrite needed
SELECT line_of_business,
       MEASURE(eligible_members) AS eligible,
       ROUND(MEASURE(compliance_rate) * 100, 1) AS rate_pct
FROM ${catalog}.${schema}.quality_mv_${your_name}
WHERE measurement_year = 2025
GROUP BY line_of_business
ORDER BY rate_pct DESC;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ### Exercise 4 — Build the HEDIS care-gap measures (10 min)
-- MAGIC Add care-gap measures (`Open Care Gaps`, `Gap Rate`, `Members With Open Gaps`), then answer real questions.
-- MAGIC The same three measures answer both questions below at different grains, with **zero changes to the definitions.**

-- COMMAND ----------

CREATE OR REPLACE VIEW ${catalog}.${schema}.quality_mv_${your_name}
WITH METRICS LANGUAGE YAML AS
$$
version: 1.1
source: ${catalog}.${schema}.fact_measure_compliance
joins:
  - name: mbr
    source: ${catalog}.${schema}.dim_member
    on: source.member_id = mbr.member_id
  - name: pln
    source: ${catalog}.${schema}.dim_plan
    on: source.plan_id = pln.plan_id
  - name: meas
    source: ${catalog}.${schema}.dim_measure
    on: source.measure_id = meas.measure_id
fields:
  - name: measurement_year
    display_name: "Measurement Year"
    expr: measurement_year
  - name: measure_name
    display_name: "Measure"
    expr: meas.measure_name
  - name: quality_domain
    display_name: "Quality Domain"
    expr: meas.domain
  - name: line_of_business
    display_name: "Line of Business"
    expr: mbr.line_of_business
  - name: region
    display_name: "Region"
    expr: mbr.region
  - name: plan_type
    display_name: "Plan Type"
    expr: pln.plan_type
measures:
  - name: eligible_members
    display_name: "Eligible Members"
    expr: COUNT(1)
  - name: compliant_members
    display_name: "Compliant Members"
    expr: SUM(compliant_flag)
  - name: compliance_rate
    display_name: "Compliance Rate"
    expr: SUM(compliant_flag) / COUNT(1)
  - name: open_care_gaps
    display_name: "Open Care Gaps"
    expr: SUM(1 - compliant_flag)
  - name: gap_rate
    display_name: "Gap Rate"
    expr: 1 - (SUM(compliant_flag) / COUNT(1))
  - name: members_with_open_gaps
    display_name: "Members With Open Gaps"
    expr: COUNT(DISTINCT CASE WHEN compliant_flag = 0 THEN member_id END)
$$;

-- COMMAND ----------

-- Worst-performing measures in 2025
SELECT measure_name,
       ROUND(MEASURE(compliance_rate) * 100, 1) AS rate_pct,
       MEASURE(open_care_gaps) AS open_gaps
FROM ${catalog}.${schema}.quality_mv_${your_name}
WHERE measurement_year = 2025
GROUP BY measure_name
ORDER BY rate_pct;

-- COMMAND ----------

-- Year-over-year movement by domain
SELECT quality_domain, measurement_year,
       ROUND(MEASURE(compliance_rate) * 100, 1) AS rate_pct
FROM ${catalog}.${schema}.quality_mv_${your_name}
GROUP BY quality_domain, measurement_year
ORDER BY quality_domain, measurement_year;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ### Exercise 5 — Add metadata for AI/BI and Genie (5 min)
-- MAGIC Metric views carry agent metadata — `synonyms` (and `display_name`, `format`) — that makes AI/BI and Genie
-- MAGIC smarter. Re-create your view with synonyms so Genie maps business language to the right metric: now when
-- MAGIC someone asks Genie *"what's our screening rate by region,"* it knows "screening rate" means **Compliance Rate**.
-- MAGIC
-- MAGIC The finished reference view — every field, measure, and synonym — is `quality_measures_mv` in the same
-- MAGIC schema, and the full YAML is in **Appendix A** below.

-- COMMAND ----------

CREATE OR REPLACE VIEW ${catalog}.${schema}.quality_mv_${your_name}
WITH METRICS LANGUAGE YAML AS
$$
version: 1.1
source: ${catalog}.${schema}.fact_measure_compliance
joins:
  - name: mbr
    source: ${catalog}.${schema}.dim_member
    on: source.member_id = mbr.member_id
  - name: pln
    source: ${catalog}.${schema}.dim_plan
    on: source.plan_id = pln.plan_id
  - name: meas
    source: ${catalog}.${schema}.dim_measure
    on: source.measure_id = meas.measure_id
fields:
  - name: measurement_year
    display_name: "Measurement Year"
    expr: measurement_year
  - name: measure_name
    display_name: "Measure"
    expr: meas.measure_name
    synonyms: ["quality measure", "HEDIS measure"]
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
  - name: plan_type
    display_name: "Plan Type"
    expr: pln.plan_type
measures:
  - name: eligible_members
    display_name: "Eligible Members"
    expr: COUNT(1)
    synonyms: ["denominator", "eligible population"]
  - name: compliant_members
    display_name: "Compliant Members"
    expr: SUM(compliant_flag)
    synonyms: ["numerator", "members meeting the measure"]
  - name: compliance_rate
    display_name: "Compliance Rate"
    expr: SUM(compliant_flag) / COUNT(1)
    synonyms: ["HEDIS rate", "quality rate", "screening rate", "measure rate"]
  - name: open_care_gaps
    display_name: "Open Care Gaps"
    expr: SUM(1 - compliant_flag)
    synonyms: ["gaps", "open gaps", "care gaps"]
  - name: gap_rate
    display_name: "Gap Rate"
    expr: 1 - (SUM(compliant_flag) / COUNT(1))
  - name: members_with_open_gaps
    display_name: "Members With Open Gaps"
    expr: COUNT(DISTINCT CASE WHEN compliant_flag = 0 THEN member_id END)
$$;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ### Exercise 6 — Filters: bake them into the metric (10 min)
-- MAGIC Metric views give you **three** places to filter, and they solve different problems:
-- MAGIC
-- MAGIC | Where | YAML / SQL | Use it for |
-- MAGIC |---|---|---|
-- MAGIC | **View-level** | `filter:` (top of the YAML) | An always-on guardrail applied to *every* query — e.g. "only ever count the eligible population." |
-- MAGIC | **Measure-level** | `expr: ... FILTER (WHERE ...)` | A measure scoped to a sub-population, so you can compare a segment against the all-up number **side by side in one query**. |
-- MAGIC | **Query-level** | `WHERE` in your `SELECT` | An ad-hoc slice for one question (what every cell above has used). |
-- MAGIC
-- MAGIC Below we add the first two. The view-level `filter: eligible_flag = 1` is a guardrail: our fact is already at the
-- MAGIC eligible grain so it changes no numbers today, but it **documents and enforces the denominator** — the moment a
-- MAGIC non-eligible row appears, the HEDIS rate stays correct. The measure-level `FILTER (WHERE ...)` lets us define a
-- MAGIC Medicare-Advantage-only numerator and rate that sit *next to* the all-LOB rate — something a single query `WHERE`
-- MAGIC can't do, because `WHERE` would scope the whole row set.

-- COMMAND ----------

CREATE OR REPLACE VIEW ${catalog}.${schema}.quality_mv_${your_name}
WITH METRICS LANGUAGE YAML AS
$$
version: 1.1
source: ${catalog}.${schema}.fact_measure_compliance
filter: eligible_flag = 1
joins:
  - name: mbr
    source: ${catalog}.${schema}.dim_member
    on: source.member_id = mbr.member_id
  - name: pln
    source: ${catalog}.${schema}.dim_plan
    on: source.plan_id = pln.plan_id
  - name: meas
    source: ${catalog}.${schema}.dim_measure
    on: source.measure_id = meas.measure_id
fields:
  - name: measurement_year
    display_name: "Measurement Year"
    expr: measurement_year
  - name: measure_name
    display_name: "Measure"
    expr: meas.measure_name
  - name: quality_domain
    display_name: "Quality Domain"
    expr: meas.domain
  - name: line_of_business
    display_name: "Line of Business"
    expr: mbr.line_of_business
  - name: region
    display_name: "Region"
    expr: mbr.region
  - name: plan_type
    display_name: "Plan Type"
    expr: pln.plan_type
measures:
  - name: eligible_members
    display_name: "Eligible Members"
    expr: COUNT(1)
  - name: compliant_members
    display_name: "Compliant Members"
    expr: SUM(compliant_flag)
  - name: compliance_rate
    display_name: "Compliance Rate"
    expr: SUM(compliant_flag) / COUNT(1)
  - name: open_care_gaps
    display_name: "Open Care Gaps"
    expr: SUM(1 - compliant_flag)
  # Measure-level filters — a numerator and rate scoped to Medicare Advantage only.
  - name: medicare_adv_eligible_members
    display_name: "Medicare Adv Eligible Members"
    expr: COUNT(1) FILTER (WHERE mbr.line_of_business = 'Medicare Advantage')
  - name: medicare_adv_compliance_rate
    display_name: "Medicare Adv Compliance Rate"
    expr: SUM(compliant_flag) FILTER (WHERE mbr.line_of_business = 'Medicare Advantage')
        / COUNT(1) FILTER (WHERE mbr.line_of_business = 'Medicare Advantage')
$$;

-- COMMAND ----------

-- Compare the all-LOB rate against the Medicare Advantage rate, side by side, in ONE query.
-- (A NULL Medicare Adv rate = no MA members are eligible for that measure, e.g. the pediatric measures.)
SELECT measure_name,
       ROUND(MEASURE(compliance_rate) * 100, 1)             AS all_lob_pct,
       ROUND(MEASURE(medicare_adv_compliance_rate) * 100, 1) AS medicare_adv_pct,
       MEASURE(medicare_adv_eligible_members)                AS medicare_adv_eligible
FROM ${catalog}.${schema}.quality_mv_${your_name}
WHERE measurement_year = 2025
GROUP BY measure_name
ORDER BY all_lob_pct;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC The view-level `filter` and the measure-level `FILTER (WHERE ...)` are now part of the governed definition —
-- MAGIC every tool that reads this metric view gets the same guardrail and the same segment logic, with no copy-paste.

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ### Exercise 7 — Window functions: trends, ranking, and running totals (10 min)
-- MAGIC "How did the rate move year over year?" and "rank our measures worst-to-best" are **window** questions. There
-- MAGIC are two ways to answer them, and you'll use both:
-- MAGIC
-- MAGIC **A. SQL window functions over the metric view (portable — runs on any DBR that supports metric views).**
-- MAGIC Aggregate with `MEASURE()` in a CTE, then apply `LAG`, `RANK`, `PERCENT_RANK`, `SUM() OVER (...)` in the outer
-- MAGIC query. The metric view still owns the *math*; the window function just arranges the already-correct results.

-- COMMAND ----------

-- Year-over-year change in compliance rate, per measure, using LAG()
WITH by_year AS (
  SELECT measure_name,
         measurement_year             AS yr,
         MEASURE(compliance_rate)     AS rate
  FROM ${catalog}.${schema}.quality_mv_${your_name}
  GROUP BY measure_name, measurement_year
)
SELECT measure_name, yr,
       ROUND(rate * 100, 1)                                                                       AS rate_pct,
       ROUND((rate - LAG(rate) OVER (PARTITION BY measure_name ORDER BY yr)) * 100, 1)               AS yoy_change_pts
FROM by_year
ORDER BY measure_name, yr;

-- COMMAND ----------

-- Rank the 2025 measures worst-to-best, and show each measure's percentile — classic gap-prioritization
WITH by_measure AS (
  SELECT measure_name,
         MEASURE(compliance_rate) AS rate,
         MEASURE(open_care_gaps)  AS open_gaps
  FROM ${catalog}.${schema}.quality_mv_${your_name}
  WHERE measurement_year = 2025
  GROUP BY measure_name
)
SELECT measure_name,
       ROUND(rate * 100, 1)                          AS rate_pct,
       open_gaps,
       RANK()         OVER (ORDER BY rate ASC)        AS worst_first_rank,
       ROUND(PERCENT_RANK() OVER (ORDER BY rate), 2)  AS pctile
FROM by_measure
ORDER BY worst_first_rank;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC **B. Native window measures (Experimental) — push the windowing *into* the semantic layer.**
-- MAGIC A measure can carry a `window` spec (`order`, `range`, `semiadditive`) so running totals and moving windows are
-- MAGIC defined once and reused everywhere — Genie and AI/BI get them for free. Below we add a **cumulative** running
-- MAGIC total of open care gaps, ordered by `Measurement Year`.
-- MAGIC
-- MAGIC > ⚠️ **Experimental + runtime note.** Window measures are an Experimental feature. Basic ranges (`cumulative`,
-- MAGIC > `current`) work on the workshop runtime; period-over-period via the `offset` modifier and `trailing N <unit>`
-- MAGIC > windows need **DBR 18.1+** *and* a date/timestamp `order` field (our `measurement_year` is an integer year).
-- MAGIC > For YoY on this data, prefer the portable `LAG()` query in Part A.

-- COMMAND ----------

CREATE OR REPLACE VIEW ${catalog}.${schema}.quality_mv_${your_name}
WITH METRICS LANGUAGE YAML AS
$$
version: 1.1
source: ${catalog}.${schema}.fact_measure_compliance
filter: eligible_flag = 1
joins:
  - name: mbr
    source: ${catalog}.${schema}.dim_member
    on: source.member_id = mbr.member_id
  - name: pln
    source: ${catalog}.${schema}.dim_plan
    on: source.plan_id = pln.plan_id
  - name: meas
    source: ${catalog}.${schema}.dim_measure
    on: source.measure_id = meas.measure_id
fields:
  - name: measurement_year
    display_name: "Measurement Year"
    expr: measurement_year
  - name: measure_name
    display_name: "Measure"
    expr: meas.measure_name
  - name: quality_domain
    display_name: "Quality Domain"
    expr: meas.domain
  - name: line_of_business
    display_name: "Line of Business"
    expr: mbr.line_of_business
  - name: region
    display_name: "Region"
    expr: mbr.region
  - name: plan_type
    display_name: "Plan Type"
    expr: pln.plan_type
measures:
  - name: eligible_members
    display_name: "Eligible Members"
    expr: COUNT(1)
  - name: compliant_members
    display_name: "Compliant Members"
    expr: SUM(compliant_flag)
  - name: compliance_rate
    display_name: "Compliance Rate"
    expr: SUM(compliant_flag) / COUNT(1)
  - name: open_care_gaps
    display_name: "Open Care Gaps"
    expr: SUM(1 - compliant_flag)
  - name: medicare_adv_eligible_members
    display_name: "Medicare Adv Eligible Members"
    expr: COUNT(1) FILTER (WHERE mbr.line_of_business = 'Medicare Advantage')
  - name: medicare_adv_compliance_rate
    display_name: "Medicare Adv Compliance Rate"
    expr: SUM(compliant_flag) FILTER (WHERE mbr.line_of_business = 'Medicare Advantage')
        / COUNT(1) FILTER (WHERE mbr.line_of_business = 'Medicare Advantage')
  # Window measure — cumulative running total of open care gaps across measurement years.
  - name: cumulative_open_care_gaps
    display_name: "Cumulative Open Care Gaps"
    expr: SUM(1 - compliant_flag)
    window:
      - order: measurement_year
        range: cumulative
        semiadditive: last
$$;

-- COMMAND ----------

-- Per-year gaps vs the running cumulative total — the window measure does the running sum for you.
SELECT measurement_year,
       MEASURE(open_care_gaps)            AS gaps_this_year,
       MEASURE(cumulative_open_care_gaps) AS cumulative_gaps
FROM ${catalog}.${schema}.quality_mv_${your_name}
GROUP BY measurement_year
ORDER BY measurement_year;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## Part 3 — Consume the metric view (10 min)
-- MAGIC
-- MAGIC ### A. Genie (natural language)
-- MAGIC In the workspace, go to **Genie → New** and add `quality_measures_mv` (and the dimension tables) as data. Ask:
-- MAGIC - *"What is the compliance rate by line of business for 2025?"*
-- MAGIC - *"Which quality measures have the most open care gaps?"*
-- MAGIC - *"Show breast cancer screening rate by region, 2024 vs 2025."*
-- MAGIC
-- MAGIC Notice Genie writes correct `MEASURE()` SQL because the metric defines the math and the synonyms map your
-- MAGIC words to the right measure.
-- MAGIC
-- MAGIC ### B. AI/BI dashboard
-- MAGIC Open the reference dashboard **"MVP Health Care — Quality Measures (HEDIS) [Workshop]"** (link from your
-- MAGIC facilitator), or build one: create a dataset on the metric view and add a bar chart of **Compliance Rate (%)
-- MAGIC by Measure**. The dashboard reads the same definitions — no duplicated logic.

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## Part 4 — Brainstorm + share-back (15 min)
-- MAGIC
-- MAGIC Now make it real for MVP. In small groups (10 minutes), use the **Metric Definition Worksheet** (separate
-- MAGIC Google Sheet) to capture 2–3 metrics MVP should implement as metric views. For each, sketch:
-- MAGIC - Metric name and the business question it answers
-- MAGIC - Source data (which tables/domains)
-- MAGIC - Grain / denominator (per member? per claim? per member-month?)
-- MAGIC - Numerator / measure expression (rough is fine)
-- MAGIC - Key dimensions to slice by (LOB, region, plan, provider, month…)
-- MAGIC - Owner / primary consumers and priority
-- MAGIC
-- MAGIC Then each group shares back one metric to the room (5 minutes). The worksheet is seeded with health-plan
-- MAGIC examples — Stars/HEDIS rates, Medical Loss Ratio (MLR), PMPM, network adequacy, prior-authorization
-- MAGIC turnaround time, 30-day readmissions, call-center service level.
-- MAGIC
-- MAGIC **Goal:** leave with a short, prioritized backlog of metric views MVP can stand up after the workshop.

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## Part 5 — Wrap-up (5 min)
-- MAGIC - **Governance:** metric views are Unity Catalog objects — grant SELECT to consumers; ownership controls who can edit; lineage and audit apply.
-- MAGIC - **One definition, many tools:** SQL, AI/BI, Genie, Power BI/Tableau all read the same metric.
-- MAGIC - **Next steps:** pick 1–2 metrics from the brainstorm, assign an owner, and implement them as metric views on certified Gold tables.

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## Appendix A — Reference metric view (the "answer key")
-- MAGIC This is the complete `quality_measures_mv` (also created by `00_setup_quality_data` when
-- MAGIC `create_metric_view = true`). Running the cell below recreates it in your catalog/schema.

-- COMMAND ----------

CREATE OR REPLACE VIEW ${catalog}.${schema}.quality_measures_mv
WITH METRICS LANGUAGE YAML AS
$$
version: 1.1
comment: "HEDIS-style quality measure compliance and open care gaps (synthetic workshop data)."
source: ${catalog}.${schema}.fact_measure_compliance
joins:
  - name: mbr
    source: ${catalog}.${schema}.dim_member
    on: source.member_id = mbr.member_id
  - name: pln
    source: ${catalog}.${schema}.dim_plan
    on: source.plan_id = pln.plan_id
  - name: meas
    source: ${catalog}.${schema}.dim_measure
    on: source.measure_id = meas.measure_id
  - name: prov
    source: ${catalog}.${schema}.dim_provider
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
$$;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## Appendix B — Troubleshooting
-- MAGIC
-- MAGIC | Symptom | Cause / fix |
-- MAGIC |---|---|
-- MAGIC | `INVALID_EXTRACT_BASE_FIELD_TYPE … got STRING` | A join alias collides with a reserved word (e.g. `measure`, `plan`). Rename the alias (`meas`, `pln`). |
-- MAGIC | `MEASURE function can only be used with a metric view` | You wrapped a regular column in `MEASURE()`, or queried a normal table. Only measures defined in a metric view go inside `MEASURE()`; fields are selected directly. |
-- MAGIC | Metric view won't create | Your SQL warehouse must be on **DBR 17.3+**. You also need `CREATE TABLE` + `USE SCHEMA` on the schema and `USE CATALOG` on the catalog. |
-- MAGIC | Genie gives odd answers | Add `synonyms` and `display_name` to fields/measures so Genie maps business language to the right metric. |
-- MAGIC | `Table or view not found` on the first cell | Check the `catalog` / `schema` widgets, and confirm an admin has run `00_setup_quality_data` to load the data. |
