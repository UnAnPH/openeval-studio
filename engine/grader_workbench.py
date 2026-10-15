"""OpenEval Watcher Grader Workbench & Calibration Engine.

Compatibility shim re-exporting from unified engine.scorers.
"""

from engine.scorers import (
    GOLDEN_CALIBRATION_DATASET,
    BacktestReport,
    BacktestSample,
    GraderWorkbench,
    SamplePrediction,
    calculate_mae,
    calculate_ranks,
    calculate_spearman,
)

__all__ = [
    "GOLDEN_CALIBRATION_DATASET",
    "BacktestReport",
    "BacktestSample",
    "GraderWorkbench",
    "SamplePrediction",
    "calculate_mae",
    "calculate_ranks",
    "calculate_spearman",
]
