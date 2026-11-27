"""OpenEval Watcher Grader Workbench & Calibration Engine.

Compatibility shim re-exporting from unified engine.scorers.
"""

from engine.scorers import (
    GOLDEN_CALIBRATION_DATASET,
    PER_GRADER_CALIBRATION_DATASETS,
    BacktestReport,
    BacktestSample,
    GraderWorkbench,
    SamplePrediction,
    calculate_brier_score,
    calculate_continuous_mae,
    calculate_ece,
    calculate_mae,
    calculate_ranks,
    calculate_spearman,
    get_calibration_dataset_for_grader,
)

__all__ = [
    "GOLDEN_CALIBRATION_DATASET",
    "PER_GRADER_CALIBRATION_DATASETS",
    "BacktestReport",
    "BacktestSample",
    "GraderWorkbench",
    "SamplePrediction",
    "calculate_brier_score",
    "calculate_continuous_mae",
    "calculate_ece",
    "calculate_mae",
    "calculate_ranks",
    "calculate_spearman",
    "get_calibration_dataset_for_grader",
]
