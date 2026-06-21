"""Data loading utilities for the claims feature store."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pandas as pd

from config import ensure_directories, get_config
from validator import build_data_quality_metrics, validate_claims_schema


def read_claims(input_path: Path) -> pd.DataFrame:
    """Read raw claims data from CSV or Parquet.

    Parameters:
        input_path: Source file path ending in .csv, .parquet, or .pq.

    Returns:
        pd.DataFrame: Loaded raw claims dataframe.

    Raises:
        ValueError: If the file extension is unsupported.
    """

    suffix = input_path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(input_path)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(input_path)
    raise ValueError("Only CSV and Parquet inputs are supported")


def prepare_raw_claims(input_path: Path, output_path: Path) -> pd.DataFrame:
    """Validate and copy raw claims into the canonical raw-data location.

    Parameters:
        input_path: Source claims file path.
        output_path: Canonical output path used by the feature pipeline.

    Returns:
        pd.DataFrame: Validated raw claims dataframe.
    """

    claims = read_claims(input_path)
    validate_claims_schema(claims)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if input_path.resolve() != output_path.resolve():
        shutil.copyfile(input_path, output_path)
    return claims


def main() -> None:
    """Run the command-line raw claims preparation workflow.

    Parameters:
        None.

    Returns:
        None.
    """

    parser = argparse.ArgumentParser(description="Prepare raw claims data.")
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to input claims CSV or Parquet file.",
    )
    args = parser.parse_args()

    config = get_config()
    ensure_directories(config)
    claims = prepare_raw_claims(args.input, config.raw_data_path)
    metrics = build_data_quality_metrics(claims)
    metrics.to_csv(config.outputs_dir / "data_quality_metrics.csv", index=False)
    print(f"Prepared {len(claims):,} claims at {config.raw_data_path}")


if __name__ == "__main__":
    main()
