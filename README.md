# Unity Catalog Metric Views Workshop (HEDIS Quality)

A 90-minute, hands-on workshop for learning **Unity Catalog metric views** on a synthetic
health-plan quality dataset. Attendees build a HEDIS-style quality metric view, query it with
`MEASURE()`, consume it from AI/BI and Genie, and leave with a backlog of real metrics to implement.

> **Data is 100% synthetic** — no real members, providers, or claims. It is generated locally by the
> setup notebook.

## What's in here

| Path | What it is | Who runs it |
|---|---|---|
| `notebooks/00_setup_quality_data.py` | Generates the synthetic star schema (`dim_*`, `fact_measure_compliance`) and, optionally, the reference metric view. | **Admin / facilitator**, once |
| `notebooks/01_workshop_metric_views.sql` | The hands-on workshop. Five exercises building a metric view, plus consume + brainstorm sections. | **Each attendee** |
| `databricks.yml` | Optional Databricks Asset Bundle to deploy the setup notebook as a job. | Admin (optional) |

## Prerequisites

- A Databricks workspace with **Unity Catalog**.
- A **SQL warehouse or cluster on Databricks Runtime 17.3 or above** (metric views require 17.3+).
- A catalog where you have `CREATE SCHEMA` / `CREATE TABLE`. The data lands in a `mvp_quality_workshop`
  schema inside the catalog you choose.

## Setup

### 1. Get the repo into your workspace (Git folder)

In Databricks: **Workspace → (your folder) → Create → Git folder**, paste this repo's HTTPS URL, and clone.
(This is a public repo, so no Git credentials are required.)

### 2. Admin: load the workshop data (once)

Open `notebooks/00_setup_quality_data.py`, attach a cluster/warehouse on DBR 17.3+, set the widgets, and **Run All**:

- **`catalog`** — the catalog to create the workshop schema in (e.g. `main`, or your own catalog).
- **`schema`** — defaults to `mvp_quality_workshop`.
- **`create_metric_view`** — `true` creates the finished reference view `quality_measures_mv` (the "answer key");
  set `false` if you want attendees to build it from a blank slate.
- **`n_members`** — synthetic member count (default 20,000).

> Alternatively, deploy via the bundle:
> ```bash
> databricks bundle deploy --target dev
> databricks bundle run mvp_quality_workshop_setup --target dev
> ```
> Edit the `host` in `databricks.yml` first.

### 3. Attendees: run the workshop

Open `notebooks/01_workshop_metric_views.sql`, attach to a DBR 17.3+ warehouse, and run the first cell to
create the widgets. Then in the widget bar set:

- **`catalog`** — the catalog the admin loaded the data into.
- **`schema`** — `mvp_quality_workshop` unless the admin changed it.
- **`your_name`** — anything unique to you, so your metric view (`quality_mv_<your_name>`) doesn't collide
  with others'.

Work through the cells top to bottom.

## The data model

A small star schema of synthetic HEDIS-style quality data:

- `fact_measure_compliance` — one row per eligible (member, measure, year); `compliant_flag = 1` means the member met the measure.
- `dim_member`, `dim_plan`, `dim_provider`, `dim_measure` — the dimensions you slice by.

The central metric is **Compliance Rate** = `SUM(compliant_flag) / COUNT(1)` — the HEDIS rate — with **Open
Care Gaps** for the non-compliant eligible members.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `INVALID_EXTRACT_BASE_FIELD_TYPE … got STRING` | A join alias collides with a reserved word (e.g. `measure`, `plan`). Use `meas`, `pln`. |
| `MEASURE function can only be used with a metric view` | Only measures defined in a metric view go inside `MEASURE()`; fields are selected directly. |
| Metric view won't create | Warehouse must be on **DBR 17.3+**; you need `USE CATALOG`, `USE SCHEMA`, and `CREATE TABLE`. |
| `Table or view not found` | Check the `catalog` / `schema` widgets and confirm the setup notebook has been run. |
