"""Shared scoring status vocabulary.

Every automated score in OpenEval Studio carries an explicit status so that a
judge that never ran, or crashed, can never be mistaken for a pass.

- ``scored``: the scorer ran and produced a real ``score``/``passed`` value.
- ``not_run``: the scorer was intentionally skipped (e.g. empty trajectory).
- ``error``: the scorer was attempted and failed (LLM error, bad output, ...).
"""

from typing import Literal

ScoreStatus = Literal["scored", "not_run", "error"]
