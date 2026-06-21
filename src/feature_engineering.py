"""Feature engineering and feature-store persistence for clinical claims."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import pandas as pd

try:
    import duckdb
except ImportError:  # pragma: no cover - environment fallback for rapid scaffold
    duckdb = None
from sqlalchemy import create_engine, text

from config import ensure_directories, get_config
from data_loader import read_claims
from validator import (
    build_data_quality_metrics,
    validate_claims_schema,
    validate_feature_store,
)

CHRONIC_PREFIXES = ("E11", "I10", "I25", "I50", "J45", "G43")
SPECIALIST_SPECIALTIES = {
    "Cardiology",
    "Endocrinology",
    "Neurology",
    "Orthopedics",
    "Radiology",
}

MEMBER_FEATURE_SQL = """
-- Representative SQL logic for member-level utilization and risk features.
SELECT
    member_id,
    AVG(days_supply) AS days_supply_avg,
    SUM(CASE WHEN place_of_service = 'ER' THEN 1 ELSE 0 END) AS er_visit_count,
    MAX(readmission_30d) AS readmission_flag,
    AVG(risk_score) AS risk_score
FROM claims
GROUP BY member_id;
"""

PROVIDER_FEATURE_SQL = """
-- Representative SQL logic for provider-level quality and cost features.
SELECT
    provider_id,
    COUNT(DISTINCT member_id) AS member_attribution_count,
    AVG(quality_score_cms) AS quality_score_cms,
    AVG(readmission_30d) AS readmission_rate,
    AVG(adverse_event_flag) AS adverse_event_rate,
    AVG(denial_flag) AS claims_denial_rate
FROM claims
GROUP BY provider_id;
"""

SERVICE_FEATURE_SQL = """
-- Representative SQL logic for service-line frequency and cost features.
SELECT
    service_code,
    COUNT(*) AS frequency_annual,
    AVG(allowed_amount) AS avg_cost
FROM claims
GROUP BY service_code;
"""


def _feature_id(entity_id: str, entity_type: str, feature_name: str, as_of_date: str) -> str:
    """Generate a stable identifier for a feature row.

    Parameters:
        entity_id: Entity identifier value.
        entity_type: Entity type, such as member, provider, or service_line.
        feature_name: Name of the engineered feature.
        as_of_date: Feature as-of date.

    Returns:
        str: SHA-256 feature identifier truncated to 32 characters.
    """

    raw_key = f"{entity_id}|{entity_type}|{feature_name}|{as_of_date}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]


def _to_long_feature_table(
    wide_features: pd.DataFrame,
    entity_id_column: str,
    entity_type: str,
    as_of_date: str,
) -> pd.DataFrame:
    """Convert a wide feature dataframe into long feature-store format.

    Parameters:
        wide_features: Wide dataframe with one row per entity.
        entity_id_column: Column containing entity identifiers.
        entity_type: Entity type label for the feature-store row.
        as_of_date: Date stamp applied to generated features.

    Returns:
        pd.DataFrame: Long-format feature dataframe.
    """

    value_columns = [col for col in wide_features.columns if col != entity_id_column]
    long_features = wide_features.melt(
        id_vars=[entity_id_column],
        value_vars=value_columns,
        var_name="feature_name",
        value_name="feature_value",
    )
    long_features = long_features.rename(columns={entity_id_column: "entity_id"})
    long_features["entity_type"] = entity_type
    long_features["as_of_date"] = as_of_date
    long_features["feature_value"] = long_features["feature_value"].astype(str)
    long_features["feature_id"] = long_features.apply(
        lambda row: _feature_id(
            row["entity_id"],
            row["entity_type"],
            row["feature_name"],
            row["as_of_date"],
        ),
        axis=1,
    )
    return long_features[
        [
            "feature_id",
            "entity_id",
            "entity_type",
            "feature_name",
            "feature_value",
            "as_of_date",
        ]
    ]


def _service_category(service_code: str) -> str:
    """Map a service code to a coarse clinical category.

    Parameters:
        service_code: CPT, HCPCS, or internal service code.

    Returns:
        str: Clinical service category.
    """

    code = str(service_code)
    if code.startswith("99"):
        return "evaluation_management"
    if code.startswith("8"):
        return "laboratory"
    if code.startswith("7"):
        return "radiology"
    if code.startswith("9"):
        return "therapy"
    if code.startswith("J"):
        return "pharmacy"
    return "procedure"


def calculate_member_features(claims: pd.DataFrame, as_of_date: str) -> pd.DataFrame:
    """Calculate member-level clinical and utilization features.

    Parameters:
        claims: Validated claims dataframe.
        as_of_date: Date stamp for generated features.

    Returns:
        pd.DataFrame: Long-format member feature rows.
    """

    claims = claims.copy()
    claims["service_date"] = pd.to_datetime(claims["service_date"])
    claims["member_birth_date"] = pd.to_datetime(claims["member_birth_date"])
    as_of_timestamp = pd.Timestamp(as_of_date)
    claims["age"] = (
        (as_of_timestamp - claims["member_birth_date"]).dt.days / 365.25
    ).astype(int)
    claims["is_chronic"] = claims["diagnosis_code"].astype(str).str.startswith(
        CHRONIC_PREFIXES
    )
    claims["is_specialist"] = claims["provider_specialty"].isin(SPECIALIST_SPECIALTIES)
    claims["is_er"] = claims["place_of_service"].eq("ER")

    member_group = claims.groupby("member_id", as_index=False)
    total_cost = member_group["allowed_amount"].sum().rename(
        columns={"allowed_amount": "total_allowed_amount"}
    )
    cost_quantiles = total_cost["total_allowed_amount"].quantile([0.33, 0.66]).to_dict()

    last_six_months = claims[claims["service_date"] >= as_of_timestamp - pd.DateOffset(months=6)]
    prior_six_months = claims[
        (claims["service_date"] < as_of_timestamp - pd.DateOffset(months=6))
        & (claims["service_date"] >= as_of_timestamp - pd.DateOffset(months=12))
    ]
    recent_cost = last_six_months.groupby("member_id")["allowed_amount"].sum()
    prior_cost = prior_six_months.groupby("member_id")["allowed_amount"].sum()
    cost_trend = ((recent_cost - prior_cost) / prior_cost.replace(0, pd.NA)).fillna(0)

    medication_days = claims.loc[claims["days_supply"] > 0].groupby("member_id")["days_supply"].sum()
    medication_adherence = (medication_days / 365).clip(upper=1).fillna(0)

    wide_features = member_group.agg(
        age=("age", "max"),
        risk_score=("risk_score", "mean"),
        chronic_condition_count=("is_chronic", "sum"),
        days_supply_avg=("days_supply", "mean"),
        readmission_flag=("readmission_30d", "max"),
        er_visit_count=("is_er", "sum"),
        specialist_usage=("is_specialist", "mean"),
    )
    wide_features = wide_features.merge(total_cost, on="member_id", how="left")
    wide_features["cost_trend_12m"] = wide_features["member_id"].map(cost_trend).fillna(0)
    wide_features["medication_adherence"] = (
        wide_features["member_id"].map(medication_adherence).fillna(0)
    )

    def assign_utilization_tier(cost: float) -> str:
        """Assign a utilization tier from total allowed amount.

        Parameters:
            cost: Total allowed amount for a member.

        Returns:
            str: low, medium, or high utilization tier.
        """

        if cost <= cost_quantiles.get(0.33, 0):
            return "low"
        if cost <= cost_quantiles.get(0.66, 0):
            return "medium"
        return "high"

    wide_features["utilization_tier"] = wide_features["total_allowed_amount"].apply(
        assign_utilization_tier
    )
    wide_features = wide_features.drop(columns=["total_allowed_amount"])
    wide_features = wide_features.round(4)
    return _to_long_feature_table(wide_features, "member_id", "member", as_of_date)


def calculate_provider_features(claims: pd.DataFrame, as_of_date: str) -> pd.DataFrame:
    """Calculate provider-level quality, cost, and pattern features.

    Parameters:
        claims: Validated claims dataframe.
        as_of_date: Date stamp for generated features.

    Returns:
        pd.DataFrame: Long-format provider feature rows.
    """

    claims = claims.copy()
    provider_group = claims.groupby("provider_id", as_index=False)
    wide_features = provider_group.agg(
        specialty=("provider_specialty", lambda values: values.mode().iat[0]),
        member_attribution_count=("member_id", "nunique"),
        quality_score_cms=("quality_score_cms", "mean"),
        readmission_rate=("readmission_30d", "mean"),
        adverse_event_rate=("adverse_event_flag", "mean"),
        claims_denial_rate=("denial_flag", "mean"),
        avg_allowed_amount=("allowed_amount", "mean"),
    )
    wide_features["cost_efficiency_percentile"] = (
        1 - wide_features["avg_allowed_amount"].rank(pct=True)
    ).round(4)

    referral_counts = (
        claims.groupby("provider_id")["referring_provider_id"]
        .nunique()
        .rename("unique_referring_provider_count")
    )
    wide_features["referral_pattern"] = (
        wide_features["provider_id"]
        .map(referral_counts)
        .fillna(0)
        .apply(lambda count: "diverse" if count >= 2 else "concentrated")
    )
    wide_features = wide_features.drop(columns=["avg_allowed_amount"])
    wide_features = wide_features.round(4)
    return _to_long_feature_table(wide_features, "provider_id", "provider", as_of_date)


def calculate_service_line_features(claims: pd.DataFrame, as_of_date: str) -> pd.DataFrame:
    """Calculate service-line frequency, cost, and appropriateness features.

    Parameters:
        claims: Validated claims dataframe.
        as_of_date: Date stamp for generated features.

    Returns:
        pd.DataFrame: Long-format service-line feature rows.
    """

    claims = claims.copy()
    service_group = claims.groupby("service_code", as_index=False)
    wide_features = service_group.agg(
        frequency_annual=("claim_id", "count"),
        avg_cost=("allowed_amount", "mean"),
        clinical_appropriateness=(
            "clinical_appropriateness",
            lambda values: values.mode().iat[0],
        ),
    )
    wide_features["category"] = wide_features["service_code"].apply(_service_category)
    cost_threshold = wide_features["avg_cost"].quantile(0.90)
    wide_features["outlier_flag"] = (wide_features["avg_cost"] >= cost_threshold).astype(int)
    wide_features = wide_features[
        [
            "service_code",
            "category",
            "frequency_annual",
            "avg_cost",
            "outlier_flag",
            "clinical_appropriateness",
        ]
    ].round(4)
    return _to_long_feature_table(
        wide_features,
        "service_code",
        "service_line",
        as_of_date,
    )


def build_feature_store(claims: pd.DataFrame, as_of_date: str) -> pd.DataFrame:
    """Build all configured feature-store rows from raw claims.

    Parameters:
        claims: Validated raw claims dataframe.
        as_of_date: Date stamp for generated features.

    Returns:
        pd.DataFrame: Long-format feature store containing all feature rows.
    """

    validate_claims_schema(claims)
    feature_tables = [
        calculate_member_features(claims, as_of_date),
        calculate_provider_features(claims, as_of_date),
        calculate_service_line_features(claims, as_of_date),
    ]
    features = pd.concat(feature_tables, ignore_index=True)
    validate_feature_store(features)
    return features


def build_lineage_table(features: pd.DataFrame) -> pd.DataFrame:
    """Build feature lineage rows for every generated feature.

    Parameters:
        features: Long-format feature store dataframe.

    Returns:
        pd.DataFrame: Lineage dataframe keyed by feature_id.
    """

    sql_map: Dict[str, str] = {
        "member": MEMBER_FEATURE_SQL,
        "provider": PROVIDER_FEATURE_SQL,
        "service_line": SERVICE_FEATURE_SQL,
    }
    lineage = features[["feature_id", "entity_type"]].copy()
    lineage["data_source"] = "raw_claims"
    lineage["calculation_sql"] = lineage["entity_type"].map(sql_map)
    lineage["last_updated"] = datetime.now(timezone.utc).isoformat()
    return lineage.drop(columns=["entity_type"])


def write_feature_store(
    features: pd.DataFrame,
    lineage: pd.DataFrame,
    database_path: Path,
) -> None:
    """Persist feature and lineage tables to a SQLite database.

    Parameters:
        features: Long-format feature-store dataframe.
        lineage: Feature lineage dataframe.
        database_path: SQLite database path.

    Returns:
        None.
    """

    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        features.to_sql("features", connection, if_exists="replace", index=False)
        lineage.to_sql("lineage", connection, if_exists="replace", index=False)
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_features_entity ON features(entity_type, entity_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_features_name ON features(feature_name)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_lineage_feature ON lineage(feature_id)"))


def save_outputs(
    claims: pd.DataFrame,
    features: pd.DataFrame,
    outputs_dir: Path,
) -> None:
    """Save schemas, quality metrics, and sample SQL query artifacts.

    Parameters:
        claims: Validated raw claims dataframe.
        features: Generated long-format feature store dataframe.
        outputs_dir: Directory for generated artifacts.

    Returns:
        None.
    """

    outputs_dir.mkdir(parents=True, exist_ok=True)
    schema = {
        "features": {column: str(dtype) for column, dtype in features.dtypes.items()},
        "raw_claims": {column: str(dtype) for column, dtype in claims.dtypes.items()},
    }
    (outputs_dir / "feature_store_schema.json").write_text(
        pd.Series(schema).to_json(indent=2)
    )
    build_data_quality_metrics(claims).to_csv(
        outputs_dir / "data_quality_metrics.csv",
        index=False,
    )
    sample_queries = """-- Top 10 high-risk members
SELECT entity_id AS member_id, CAST(feature_value AS REAL) AS risk_score
FROM features
WHERE entity_type = 'member' AND feature_name = 'risk_score'
ORDER BY risk_score DESC
LIMIT 10;

-- Quality leaders by specialty
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

-- High-cost service-line outliers
SELECT entity_id AS service_code
FROM features
WHERE entity_type = 'service_line'
  AND feature_name = 'outlier_flag'
  AND feature_value = '1';
"""
    (outputs_dir / "sample_queries.sql").write_text(sample_queries)


def run_duckdb_preview(claims: pd.DataFrame) -> List[tuple]:
    """Execute a DuckDB preview query to validate analytical SQL compatibility.

    Parameters:
        claims: Raw claims dataframe registered as a DuckDB relation.

    Returns:
        list[tuple]: First rows from a member risk/utilization preview query.
    """

    if duckdb is None:
        preview = claims.groupby("member_id", as_index=False).agg(
            days_supply_avg=("days_supply", "mean"),
            er_visit_count=("place_of_service", lambda values: (values == "ER").sum()),
            readmission_flag=("readmission_30d", "max"),
            risk_score=("risk_score", "mean"),
        )
        return list(preview.itertuples(index=False, name=None))

    connection = duckdb.connect(database=":memory:")
    connection.register("claims", claims)
    return connection.execute(MEMBER_FEATURE_SQL).fetchall()


def main() -> None:
    """Run end-to-end feature engineering and persistence workflow.

    Parameters:
        None.

    Returns:
        None.
    """

    config = get_config()
    ensure_directories(config)
    claims = read_claims(config.raw_data_path)
    features = build_feature_store(claims, config.as_of_date)
    lineage = build_lineage_table(features)
    write_feature_store(features, lineage, config.feature_db_path)
    save_outputs(claims, features, config.outputs_dir)
    print(f"Wrote {len(features):,} feature rows to {config.feature_db_path}")


if __name__ == "__main__":
    main()
