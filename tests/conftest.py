import os
import shutil
import tempfile

import pytest

_TEST_STORAGE = tempfile.mkdtemp(prefix="watcher_test_")
os.environ["WATCHER_STORAGE_DIR"] = _TEST_STORAGE
# Keep unit tests deterministic (no live LLM calls in PolicyGateway)
os.environ["OPENEVAL_WATCHER_USE_LLM"] = "0"


@pytest.fixture(autouse=True, scope="session")
def test_watcher_storage():
    yield _TEST_STORAGE
    shutil.rmtree(_TEST_STORAGE, ignore_errors=True)
