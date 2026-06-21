"""Validation checks for raw claims and engineered feature tables."""

from __future__ import annotations

from typing import Iterable

import pandas as pd

REQUIRED_CLAIMS_COLUMNS = {
    "claim_id",
    "member_id",
    "provider_id",
    "service_code",
    "service_date",
    "member_birth_date",
    "provider_specialty",
    "allowed_amount",
    "paid_amount",
    "days_supply",
    "place_of_service",
    "diagnosis_code",
    "procedure_code",
    "claim_status",
    "denial_flag",
    "readmission_30d",
    "adverse_event_flag",
    "referring_provider_id",
    "quality_score_cms",
    "risk_score",
    "clinical_appropriateness",
}

FEATURE_COLUMNS = {
    "feature_id",
    "entity_id",
    "entity_type",
    "feature_name",
    "feature_value",
    "as_of_date",
}


def validate_required_columns(
    dataframe: pd.DataFrame,
    required_columns: Iterable[str],
    dataset_name: str,
) -> None:
    """Validate that a dataframe contains required columns.

    Parameters:
        dataframe: Dataframe to validate.
        required_columns: Column names that must be present.
        dataset_name: Human-readable dataset name used in error messages.

    Returns:
        None.

    Raises:
        ValueError: If one or more required columns are missing.
    """

    missing_columns = sorted(set(required_columns) - set(dataframe.columns))
    if missing_columns:
        raise ValueError(
            f"{dataset_name} is missing required columns: {missing_columns}"
        )


def validate_claims_schema(claims: pd.DataFrame) -> None:
    """Validate raw claims schema and basic value sanity.

    Parameters:
        claims: Raw claims dataframe.

    Returns:
        None.

    Raises:
        ValueError: If required columns, dates, IDs, or numeric fields are invalid.
    """

    validate_required_columns(claims, REQUIRED_CLAIMS_COLUMNS, "claims")
    if claims.empty:
        raise ValueError("claims data is empty")

    id_columns = ["claim_id", "member_id", "provider_id", "service_code"]
    for column in id_columns:
        if claims[column].isna().any():
            raise ValueError(f"{column} contains null values")

    service_dates = pd.to_datetime(claims["service_date"], errors="coerce")
    birth_dates = pd.to_datetime(claims["member_birth_date"], errors="coerce")
    if service_dates.isna().any() or birth_dates.isna().any():
        raise ValueError("service_date or member_birth_date contains invalid dates")

    numeric_columns = [
        "allowed_amount",
        "paid_amount",
        "days_supply",
        "denial_flag",
        "readmission_30d",
        "adverse_event_flag",
        "quality_score_cms",
        "risk_score",
    ]
    for column in numeric_columns:
        numeric_values = pd.to_numeric(claims[column], errors="coerce")
        if numeric_values.isna().any():
            raise ValueError(f"{column} contains non-numeric values")

    if (pd.to_numeric(claims["allowed_amount"]) < 0).any():
        raise ValueError("allowed_amount cannot be negative")


def build_data_quality_metrics(claims: pd.DataFrame) -> pd.DataFrame:
    """Calculate data quality metrics for raw claims.

    Parameters:
        claims: Raw claims dataframe.

    Returns:
        pd.DataFrame: Data quality metrics with metric names and values.
    """

    metrics = [
        ("row_count", int(len(claims))),
        ("claim_id_unique_count", int(claims["claim_id"].nunique())),
        ("member_id_unique_count", int(claims["member_id"].nunique())),
        ("provider_id_unique_count", int(claims["provider_id"].nunique())),
        ("service_code_unique_count", int(claims["service_code"].nunique())),
        ("duplicate_claim_id_count", int(claims["claim_id"].duplicated().sum())),
        ("null_cell_count", int(claims.isna().sum().sum())),
        ("denial_rate", float(pd.to_numeric(claims["denial_flag"]).mean())),
        ("readmission_rate", float(pd.to_numeric(claims["readmission_30d"]).mean())),
    ]
    return pd.DataFrame(metrics, columns=["metric", "value"])


def validate_feature_store(features: pd.DataFrame) -> None:
    """Validate generated feature store rows.

    Parameters:
        features: Long-format feature dataframe.

    Returns:
        None.

    Raises:
        ValueError: If the feature dataframe is malformed.
    """

    validate_required_columns(features, FEATURE_COLUMNS, "features")
    if features.empty:
        raise ValueError("features table is empty")
    if features[["feature_id", "entity_id", "feature_name"]].isna().any().any():
        raise ValueError("feature identifiers cannot be null")
