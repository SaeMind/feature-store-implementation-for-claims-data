"""Tests for the claims feature engineering pipeline."""

from pathlib import Path
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from feature_engineering import build_feature_store  # noqa: E402
from validator import validate_claims_schema  # noqa: E402


def test_build_feature_store_creates_expected_rows() -> None:
    """Validate feature generation row counts for the bundled sample data.

    Parameters:
        None.

    Returns:
        None.
    """

    claims = pd.read_csv(PROJECT_ROOT / "sample_data" / "claims.csv")
    validate_claims_schema(claims)
    features = build_feature_store(claims, "2026-06-09")
    expected_rows = (claims["member_id"].nunique() * 10) + (claims["provider_id"].nunique() * 8) + (claims["service_code"].nunique() * 5)
    assert len(features) == expected_rows
    assert set(features["entity_type"]) == {"member", "provider", "service_line"}


def test_member_risk_score_feature_exists() -> None:
    """Validate that member-level risk score features are generated.

    Parameters:
        None.

    Returns:
        None.
    """

    claims = pd.read_csv(PROJECT_ROOT / "sample_data" / "claims.csv")
    features = build_feature_store(claims, "2026-06-09")
    mask = (features["entity_type"] == "member") & (features["feature_name"] == "risk_score")
    assert mask.any()
