"""DuckDB Trace Engine & Analytics Store for OpenEval Studio.

Ingests Inspect AI .eval archives via evals_df() and samples_df() into DuckDB
for sub-millisecond aggregate queries on pass rates, cost Pareto frontiers, and error distributions.
"""

import logging
from pathlib import Path
from typing import Any

import duckdb
from inspect_ai.analysis import evals_df, samples_df

logger = logging.getLogger("openeval.server.analytics")


class DuckDBTraceEngine:
    """In-memory and file-backed DuckDB engine for real-time evaluation analytics."""

    def __init__(self, logs_dir: Path, db_path: str = ":memory:") -> None:
        self.logs_dir = logs_dir
        self.con = duckdb.connect(database=db_path)
        self._init_tables()
        self.sync_logs()

    def _init_tables(self) -> None:
        """Create analytical views and tables."""
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS runs_summary (
                run_id VARCHAR PRIMARY KEY,
                task_id VARCHAR,
                model VARCHAR,
                provider VARCHAR,
                status VARCHAR,
                passed BOOLEAN,
                reward DOUBLE,
                total_duration_sec DOUBLE,
                total_tokens BIGINT,
                estimated_cost_usd DOUBLE,
                created_at TIMESTAMP
            );
            """
        )

    def sync_logs(self) -> int:
        """Read .eval logs with Inspect analysis dataframes and sync into DuckDB."""
        if not self.logs_dir.exists():
            return 0

        eval_files = list(self.logs_dir.glob("*.eval"))
        if not eval_files:
            return 0

        try:
            df_e = evals_df(str(self.logs_dir))
            df_s = samples_df(str(self.logs_dir))

            # Register temporary views in DuckDB
            self.con.register("inspect_evals_view", df_e)
            self.con.register("inspect_samples_view", df_s)
            logger.info("Synced %d evals and %d samples into DuckDB", len(df_e), len(df_s))
            return len(df_e)
        except Exception as exc:
            logger.warning("DuckDB sync error: %s", exc)
            return 0

    def get_model_leaderboard(self) -> list[dict[str, Any]]:
        """Calculate pass rate, average duration, tokens, and cost per model."""
        try:
            query_sql = (
                "SELECT model, COUNT(*) as total_runs, "
                "SUM(CASE WHEN score_headline_value IN ('C', '1.0', '1') THEN 1 ELSE 0 END) "
                "as passed_runs, "
                "AVG(CASE WHEN score_headline_value IN ('C', '1.0', '1') THEN 1.0 ELSE 0.0 END) "
                "as pass_rate, "
                "AVG(total_tokens) as avg_tokens, "
                "AVG(total_time) as avg_duration_sec "
                "FROM inspect_evals_view GROUP BY model "
                "ORDER BY pass_rate DESC, avg_duration_sec ASC"
            )
            res = self.con.execute(query_sql).fetchall()

            columns = [
                "model",
                "total_runs",
                "passed_runs",
                "pass_rate",
                "avg_tokens",
                "avg_duration_sec",
            ]
            return [
                {
                    col: round(row[i], 4) if isinstance(row[i], float) else row[i]
                    for i, col in enumerate(columns)
                }
                for row in res
            ]
        except Exception:
            return []

    def get_cost_pareto_frontier(self) -> list[dict[str, Any]]:
        """Compute the accuracy vs cost tradeoff points for the Pareto frontier."""
        try:
            pareto_sql = (
                "SELECT model, "
                "AVG(CASE WHEN score_headline_value IN ('C', '1.0', '1') THEN 1.0 ELSE 0.0 END) "
                "as accuracy, "
                "AVG(total_tokens) as avg_tokens "
                "FROM inspect_evals_view GROUP BY model ORDER BY avg_tokens ASC"
            )
            res = self.con.execute(pareto_sql).fetchall()

            return [
                {
                    "model": row[0],
                    "accuracy": round(float(row[1] or 0.0), 3),
                    "avg_tokens": int(row[2] or 0),
                }
                for row in res
            ]
        except Exception:
            return []

    def query(self, sql: str) -> list[dict[str, Any]]:
        """Execute arbitrary analytical SQL query."""
        try:
            df = self.con.execute(sql).df()
            return df.to_dict(orient="records")  # type: ignore[no-any-return]
        except Exception as exc:
            logger.error("SQL query error: %s", exc)
            return []
