"""Tests for Grader Workbench (MAE, Spearman Correlation, and Ensemble Averaging)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.grader_workbench import (
    GOLDEN_CALIBRATION_DATASET,
    GraderWorkbench,
    calculate_mae,
    calculate_ranks,
    calculate_spearman,
)
from engine.llm_runner import AsyncLLMRunner, LLMResponse


def test_calculate_ranks_with_ties():
    values = [1.0, 2.0, 2.0, 4.0]
    ranks = calculate_ranks(values)
    assert ranks == [1.0, 2.5, 2.5, 4.0]


def test_calculate_mae():
    actual = [1.0, 5.0, 8.0, 10.0]
    predicted = [1.0, 4.0, 9.0, 10.0]
    assert calculate_mae(actual, predicted) == 0.5


def test_calculate_spearman_perfect_correlation():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [2.0, 4.0, 6.0, 8.0, 10.0]
    assert calculate_spearman(x, y) == 1.0


def test_calculate_spearman_inverse_correlation():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [10.0, 8.0, 6.0, 4.0, 2.0]
    assert calculate_spearman(x, y) == -1.0


@pytest.mark.asyncio
async def test_grader_workbench_backtest_run():
    mock_runner = MagicMock(spec=AsyncLLMRunner)
    mock_runner.generate = AsyncMock(
        return_value=LLMResponse(
            content="<thinking>Calibrated reasoning step 1-4</thinking><score>7</score><explanation>Exposed credentials.</explanation>",
        )
    )

    workbench = GraderWorkbench(runner=mock_runner)
    report = await workbench.run_backtest(
        grader_name="CredentialsGrader",
        ensemble_models=["gemini-2.5-flash", "gpt-5.4-nano"],
        dataset=GOLDEN_CALIBRATION_DATASET[:3],
    )

    assert report.grader_name == "CredentialsGrader"
    assert report.total_samples == 3
    assert report.mae >= 0.0
    assert report.spearman_rho <= 1.0
    assert len(report.samples) == 3
    assert "gemini-2.5-flash" in report.ensemble_models
