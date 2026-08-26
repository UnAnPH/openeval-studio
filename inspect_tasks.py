"""Inspect AI Tasks Registry for OpenEval Studio.

Allows running benchmark tasks with UK AISI's Inspect AI framework:
    inspect eval inspect_tasks.py@cancel_async_tasks --model google/gemini-2.5-flash
    inspect eval inspect_tasks.py@regex_log --model google/gemini-2.5-flash
    inspect view
"""

from inspect_ai import Task, task

from engine.inspect_bridge import build_inspect_task_for_dir


@task
def cancel_async_tasks() -> Task:
    """Evaluate async Python concurrency and graceful cancellation handling."""
    return build_inspect_task_for_dir("tasks/cancel-async-tasks")


@task
def openssl_selfsigned_cert() -> Task:
    """Evaluate TLS certificate creation, fingerprinting, and OpenSSL scripting."""
    return build_inspect_task_for_dir("tasks/openssl-selfsigned-cert")


@task
def regex_log() -> Task:
    """Evaluate complex regular expressions on IPv4 log files."""
    return build_inspect_task_for_dir("tasks/regex-log")


@task
def feed_sync_platform() -> Task:
    """Evaluate backend optimistic concurrency, idempotency, and auth."""
    return build_inspect_task_for_dir("tasks/feed-sync-platform")


@task
def build_pov_ray() -> Task:
    """Evaluate legacy 1994 C89 POV-Ray raytracer compilation & rendering."""
    return build_inspect_task_for_dir("tasks/build-pov-ray")


TASKS_REGISTRY = {
    "cancel_async_tasks": cancel_async_tasks,
    "openssl_selfsigned_cert": openssl_selfsigned_cert,
    "regex_log": regex_log,
    "feed_sync_platform": feed_sync_platform,
    "build_pov_ray": build_pov_ray,
}
