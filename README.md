# Feature Store Implementation for Claims Data

## Overview

This project is a portfolio-grade rapid scaffold for a clinical claims feature store. It ingests raw claims from CSV or Parquet, validates the schema, engineers member-, provider-, and service-line-level features, and writes a normalized feature store plus lineage metadata to SQLite.

The prototype uses SQLite for local development and is structured so the persistence layer can later migrate to PostgreSQL without rewriting the feature logic.

## Directory Structure

```text
feature-store-claims
├── data
│   ├── raw
│   └── processed
├── sample_data
├── src
│   ├── config.py
│   ├── data_loader.py
│   ├── feature_engineering.py
│   └── validator.py
├── outputs
├── tests
├── feature_store.ipynb
├── requirements.txt
├── .env.example
└── README.md
```

## Feature Definitions

The implementation includes all features explicitly requested in the task spec: 10 member features, 8 provider features, and 5 service-line features.

### Member Features (`member_id`)

| Feature | Definition |
|---|---|
| `age` | Member age as of the configured `AS_OF_DATE`. |
| `risk_score` | Mean member risk score across claims. |
| `chronic_condition_count` | Count of claims mapped to common chronic-condition diagnosis prefixes. |
| `days_supply_avg` | Average days supply across claims. |
| `cost_trend_12m` | Relative cost change between the most recent six months and the prior six months. |
| `readmission_flag` | Binary indicator for any 30-day readmission claim. |
| `er_visit_count` | Count of emergency-room claims. |
| `specialist_usage` | Share of claims associated with specialist specialties. |
| `medication_adherence` | Days supplied over 365 days, capped at 1.0. |
| `utilization_tier` | Low, medium, or high tier based on total allowed amount. |

### Provider Features (`provider_id`)

| Feature | Definition |
|---|---|
| `specialty` | Modal provider specialty. |
| `member_attribution_count` | Distinct members attributed to the provider. |
| `quality_score_cms` | Mean CMS-style quality score. |
| `cost_efficiency_percentile` | Inverted percentile rank of average allowed amount. |
| `readmission_rate` | Mean 30-day readmission flag. |
| `adverse_event_rate` | Mean adverse-event flag. |
| `referral_pattern` | `diverse` if provider has at least two unique referring providers; otherwise `concentrated`. |
| `claims_denial_rate` | Mean denial flag. |

### Service Line Features (`service_code`)

| Feature | Definition |
|---|---|
| `category` | Coarse clinical category derived from the service code. |
| `frequency_annual` | Annual count of claims for the service code. |
| `avg_cost` | Average allowed amount for the service code. |
| `outlier_flag` | Binary indicator for services at or above the 90th percentile of average cost. |
| `clinical_appropriateness` | Modal appropriateness label. |

## How to Run

### Install

```bash
pip install -r requirements.txt
```

### Prepare data

```bash
python src/data_loader.py --input data/raw/claims.csv
```

For the bundled sample dataset:

```bash
cp sample_data/claims.csv data/raw/claims.csv
python src/data_loader.py --input data/raw/claims.csv
```

### Build features

```bash
python src/feature_engineering.py
```

### Output

The SQLite feature store is written to:

```text
data/processed/feature_store.db
```

Primary tables:

```text
features(feature_id, entity_id, entity_type, feature_name, feature_value, as_of_date)
lineage(feature_id, data_source, calculation_sql, last_updated)
```

The required fields from the spec are preserved; `feature_id` is added to support row-level lineage.

## Sample Queries

### Top 10 high-risk members

```sql
SELECT entity_id AS member_id, CAST(feature_value AS REAL) AS risk_score
FROM features
WHERE entity_type = 'member' AND feature_name = 'risk_score'
ORDER BY risk_score DESC
LIMIT 10;
```

### Quality leaders by specialty

```sql
WITH specialty AS (
    SELECT entity_id AS provider_id, feature_value AS specialty
    FROM features
    WHERE entity_type = 'provider' AND feature_name = 'specialty'
), quality AS (
    SELECT entity_id AS provider_id, CAST(feature_value AS REAL) AS quality_score_cms
    FROM features
    WHERE entity_type = 'provider' AND feature_name = 'quality_score_cms'
)
SELECT specialty.specialty, quality.provider_id, quality.quality_score_cms
FROM quality
JOIN specialty USING (provider_id)
ORDER BY quality.quality_score_cms DESC;
```

### High-cost service-line outliers

```sql
SELECT entity_id AS service_code
FROM features
WHERE entity_type = 'service_line'
  AND feature_name = 'outlier_flag'
  AND feature_value = '1';
```

## Data Quality Metrics

The loader and ETL save `outputs/data_quality_metrics.csv` with:

- Row count
- Unique claim/member/provider/service counts
- Duplicate claim ID count
- Null cell count
- Denial rate
- Readmission rate

Schema metadata is saved to `outputs/feature_store_schema.json`. Sample SQL is saved to `outputs/sample_queries.sql`.

## Technologies Used

- Python 3.11+
- pandas
- SQLAlchemy
- DuckDB
- SQLite
- Jupyter Notebook
- pytest
- python-dotenv

## Portfolio Notes

This scaffold is intentionally normalized into an entity-attribute-value feature table for rapid prototyping. For production, the next upgrade should add point-in-time correctness, feature versioning, offline/online store separation, PHI-safe de-identification, access controls, and migration to PostgreSQL or a managed feature store.
