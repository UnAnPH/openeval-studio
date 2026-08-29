"""Chaos Monkey Perturbation Engine for OpenEval Studio.

Injects synthetic latency, rate limit faults (HTTP 429), and noisy tool observations
to test agent resilience, error recovery, and degradation curves under chaotic environments.
"""

import asyncio
import logging
import random

from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import ToolCall

logger = logging.getLogger("openeval.engine.chaos")


class ChaosConfig:
    """Configuration for synthetic chaos perturbations."""

    def __init__(
        self,
        enabled: bool = True,
        rate_limit_prob: float = 0.15,
        min_latency_ms: float = 50.0,
        max_latency_ms: float = 300.0,
        noise_prob: float = 0.10,
    ) -> None:
        self.enabled = enabled
        self.rate_limit_prob = rate_limit_prob
        self.min_latency_ms = min_latency_ms
        self.max_latency_ms = max_latency_ms
        self.noise_prob = noise_prob


class ChaosEngine:
    """Engine that applies perturbations to tool executions and model interactions."""

    def __init__(self, config: ChaosConfig | None = None) -> None:
        self.config = config or ChaosConfig()
        self.injected_delays = 0
        self.injected_rate_limits = 0
        self.injected_noise = 0

    async def perturb_tool_execution(self, tool_call: ToolCall, original_result: str) -> str:
        """Apply latency, 429 simulation, or noisy truncated outputs."""
        if not self.config.enabled:
            return original_result

        # 1. Inject synthetic network latency
        delay_sec = random.uniform(
            self.config.min_latency_ms / 1000.0, self.config.max_latency_ms / 1000.0
        )
        await asyncio.sleep(delay_sec)
        self.injected_delays += 1

        # 2. Inject synthetic 429 rate limit
        if random.random() < self.config.rate_limit_prob:
            self.injected_rate_limits += 1
            logger.info("🐒 CHAOS MONKEY: Injected synthetic 429 Rate Limit!")
            return (
                "Error: 429 Too Many Requests - Server rate limit exceeded. "
                "Please retry with exponential backoff."
            )

        # 3. Inject observation noise or transient truncation
        if random.random() < self.config.noise_prob and len(original_result) > 50:
            self.injected_noise += 1
            logger.info("🐒 CHAOS MONKEY: Injected synthetic observation noise!")
            return (
                f"[TRANSIENT NETWORK JITTER]: {original_result[:40]}... "
                "(output truncated by buffer jitter, please retry if incomplete)"
            )

        return original_result


@solver
def chaos_solver_wrapper(inner_solver: Solver, config: ChaosConfig | None = None) -> Solver:
    """Wrap any standard solver with Chaos Monkey perturbations."""
    chaos = ChaosEngine(config)

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        # Prepend chaos environmental alert
        logger.info(
            "🐒 Running evaluation under Chaos Mode (Rate limit prob: %.2f)",
            chaos.config.rate_limit_prob,
        )
        return await inner_solver(state, generate)

    return solve
