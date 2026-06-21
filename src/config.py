"""Configuration utilities for the claims feature store."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class FeatureStoreConfig:
    """Runtime configuration for paths and feature-store persistence.

    Parameters:
        project_root: Root directory for the project.
        raw_data_path: Path to the canonical raw claims file.
        processed_dir: Directory where processed artifacts are written.
        outputs_dir: Directory where schemas, metrics, and SQL examples are saved.
        feature_db_path: SQLite database path for feature tables.
        as_of_date: Date stamp applied to generated feature rows.

    Returns:
        FeatureStoreConfig: Immutable configuration object.
    """

    project_root: Path
    raw_data_path: Path
    processed_dir: Path
    outputs_dir: Path
    feature_db_path: Path
    as_of_date: str


def get_config() -> FeatureStoreConfig:
    """Create a configuration object from environment variables.

    Parameters:
        None.

    Returns:
        FeatureStoreConfig: Resolved project configuration.
    """

    project_root = Path(os.getenv("PROJECT_ROOT", ".")).resolve()
    raw_data_path = project_root / os.getenv("RAW_DATA_PATH", "data/raw/claims.csv")
    processed_dir = project_root / os.getenv("PROCESSED_DIR", "data/processed")
    outputs_dir = project_root / os.getenv("OUTPUTS_DIR", "outputs")
    feature_db_path = project_root / os.getenv(
        "FEATURE_DB_PATH", "data/processed/feature_store.db"
    )
    as_of_date = os.getenv("AS_OF_DATE", "2026-06-09")

    return FeatureStoreConfig(
        project_root=project_root,
        raw_data_path=raw_data_path,
        processed_dir=processed_dir,
        outputs_dir=outputs_dir,
        feature_db_path=feature_db_path,
        as_of_date=as_of_date,
    )


def ensure_directories(config: FeatureStoreConfig) -> None:
    """Create required runtime directories when they do not exist.

    Parameters:
        config: Project configuration containing directory paths.

    Returns:
        None.
    """

    config.raw_data_path.parent.mkdir(parents=True, exist_ok=True)
    config.processed_dir.mkdir(parents=True, exist_ok=True)
    config.outputs_dir.mkdir(parents=True, exist_ok=True)
