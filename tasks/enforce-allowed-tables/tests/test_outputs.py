"""
Verifier for enforce-allowed-tables.

Behavioral, not source-pattern matching: it drives the real ``SQLToolset.query``
tool. Each test builds the toolset with an ``allowed_tables`` list, mocks the
``DbApiHook`` (so no live database is needed — execution returns canned rows), and
runs SQL through ``call_tool("query", ...)``. A query that reaches a table outside
the list — or uses a construct the list cannot vet — must be rejected *before
execution*; the toolset surfaces that as ``pydantic_ai.ModelRetry``.

Each requirement is checked as a PAIR that changes only one thing (usually the
allow-list): the same SQL is accepted when the table is listed and rejected when it
is not. ``ModelRetry`` is ``call_tool``'s catch-all wrapper, so the pair is what
proves a rejection is *caused by the table policy* rather than an incidental error —
and every reject also asserts ``get_records`` never ran (i.e. rejected before
execution). The accept side proves the guardrail isn't simply always-failing.

The SQL corpus is intentionally inline here (this file lives under /tests, which the
agent cannot read), so a solution cannot special-case the exact strings.

Requirements traced to instruction.md ("When allowed-tables list is active"):
  - reject before execution any table not on the list, reached via a direct query,
    a subquery, a CTE body, a JOIN, a set operation, DESCRIBE, information_schema,
    or a DML statement
  - CTE references are excluded by lexical scope, not by name
  - fail-secure: while a list is active, reject TABLE(...) / TABLE <name>, EXEC,
    SHOW, double-quoted table identifiers, and comments (a quoted column is allowed)
  - matching is case-insensitive; entries are `table` or `schema.table`, and a
    database/catalog-qualified (three-part) reference is rejected
  - no restriction when allowed_tables is unset
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, PropertyMock

import pytest
from pydantic_ai.exceptions import ModelRetry

from airflow.providers.common.ai.toolsets.sql import SQLToolset
from airflow.providers.common.sql.hooks.sql import DbApiHook


def _mock_hook():
    """A DbApiHook stand-in: no live DB; a successful query returns canned rows."""
    hook = MagicMock(spec=DbApiHook)
    hook.get_records.return_value = [(1, "alice"), (2, "bob")]
    type(hook).last_description = PropertyMock(return_value=[("id",), ("name",)])
    return hook


def _toolset(allowed=None, *, dialect=None, schema=None, allow_writes=False):
    """Construct the toolset with a mocked hook so call_tool needs no live database."""
    ts = SQLToolset("c", allowed_tables=allowed, schema=schema, allow_writes=allow_writes)
    ts._hook = _mock_hook()
    if dialect is not None:
        ts._hook.dialect_name = dialect
    return ts


def _query(ts, sql):
    return asyncio.run(ts.call_tool("query", {"sql": sql}, ctx=MagicMock(), tool=MagicMock()))


def _assert_accepted(ts, sql):
    """The query runs: the tool returns a rows payload and no ModelRetry is raised."""
    data = json.loads(_query(ts, sql))
    assert "rows" in data, f"expected the query to run, got: {data!r}"


def _assert_rejected(ts, sql):
    """The query is refused before execution: ModelRetry raised, get_records never ran."""
    with pytest.raises(ModelRetry):
        _query(ts, sql)
    ts._hook.get_records.assert_not_called()


# ---------------------------------------------------------------------------
# Tables must be checked wherever they appear in the query.
# ---------------------------------------------------------------------------

def test_direct_table_off_list_is_rejected():
    """A direct query to a table not on the list is rejected."""
    _assert_rejected(_toolset(allowed=["orders"]), "SELECT * FROM secrets")


def test_direct_table_on_list_is_accepted():
    """The same query runs once the table is listed — so the rejection was table-caused."""
    _assert_accepted(_toolset(allowed=["secrets"]), "SELECT * FROM secrets")


def test_subquery_table_off_list_is_rejected():
    """A table reached only through a subquery is still checked."""
    _assert_rejected(
        _toolset(allowed=["orders"]),
        "SELECT * FROM orders WHERE id IN (SELECT id FROM secrets)",
    )


def test_subquery_table_on_list_is_accepted():
    _assert_accepted(
        _toolset(allowed=["orders", "secrets"]),
        "SELECT * FROM orders WHERE id IN (SELECT id FROM secrets)",
    )


def test_cte_body_table_off_list_is_rejected():
    """A real table read inside a CTE body is checked (the CTE name is not that table)."""
    _assert_rejected(
        _toolset(allowed=["orders"]),
        "WITH t AS (SELECT id FROM secrets) SELECT * FROM t",
    )


def test_cte_body_table_on_list_is_accepted():
    _assert_accepted(
        _toolset(allowed=["secrets"]),
        "WITH t AS (SELECT id FROM secrets) SELECT * FROM t",
    )


def test_join_table_off_list_is_rejected():
    """A table joined in is checked."""
    _assert_rejected(
        _toolset(allowed=["orders"]),
        "SELECT * FROM orders o JOIN secrets s ON o.id = s.oid",
    )


def test_join_table_on_list_is_accepted():
    _assert_accepted(
        _toolset(allowed=["orders", "secrets"]),
        "SELECT * FROM orders o JOIN secrets s ON o.id = s.oid",
    )


def test_set_operation_table_off_list_is_rejected():
    """A table on one side of a UNION is checked."""
    _assert_rejected(
        _toolset(allowed=["orders"]),
        "SELECT id FROM orders UNION SELECT id FROM secrets",
    )


def test_set_operation_table_on_list_is_accepted():
    _assert_accepted(
        _toolset(allowed=["orders", "secrets"]),
        "SELECT id FROM orders UNION SELECT id FROM secrets",
    )


def test_describe_table_off_list_is_rejected():
    """DESCRIBE of an off-list table is rejected (metadata is still table-scoped)."""
    _assert_rejected(_toolset(allowed=["orders"]), "DESCRIBE secrets")


def test_describe_table_on_list_is_accepted():
    _assert_accepted(_toolset(allowed=["secrets"]), "DESCRIBE secrets")


def test_information_schema_off_list_is_rejected():
    """information_schema is a regular table: it needs explicit whitelisting."""
    _assert_rejected(
        _toolset(allowed=["orders"]),
        "SELECT table_name FROM information_schema.tables",
    )


def test_information_schema_on_list_is_accepted():
    _assert_accepted(
        _toolset(allowed=["information_schema.tables"]),
        "SELECT table_name FROM information_schema.tables",
    )


def test_dml_target_off_list_is_rejected():
    """With writes enabled, a DML targeting an off-list table is still rejected."""
    _assert_rejected(
        _toolset(allowed=["orders"], allow_writes=True),
        "DELETE FROM secrets WHERE id = 1",
    )


def test_dml_target_on_list_is_accepted():
    _assert_accepted(
        _toolset(allowed=["secrets"], allow_writes=True),
        "DELETE FROM secrets WHERE id = 1",
    )


# ---------------------------------------------------------------------------
# CTE exclusion is lexically scoped, not by name.
# ---------------------------------------------------------------------------

def test_same_named_cte_in_inner_scope_does_not_unlock_real_table():
    """A CTE named like a real table, defined in an *inner* scope, must not exclude the
    real top-level table of the same name. Here the outer `FROM secrets` is a real
    table; the inner `WITH secrets` is a different, unrelated scope."""
    sql = (
        "SELECT * FROM secrets "
        "WHERE id IN (WITH secrets AS (SELECT id FROM orders) SELECT id FROM secrets)"
    )
    _assert_rejected(_toolset(allowed=["orders"]), sql)


def test_in_scope_cte_is_a_temp_table_not_a_real_one():
    """A genuinely in-scope CTE named like an off-list table is allowed — it's a temp
    table, not the base table — and only the real tables in its body are checked."""
    sql = "WITH secrets AS (SELECT id FROM orders) SELECT * FROM secrets"
    _assert_accepted(_toolset(allowed=["orders"]), sql)


# ---------------------------------------------------------------------------
# Fail-secure: constructs an allow-list cannot vet are refused while it is active.
# ---------------------------------------------------------------------------

def test_double_quoted_table_identifier_is_rejected():
    """A double-quoted *table* identifier is case-sensitive and can't be matched soundly
    against the case-insensitive allow-list, so it's refused — even naming an allowed table."""
    _assert_rejected(_toolset(allowed=["orders"]), 'SELECT * FROM "orders"')


def test_unquoted_table_identifier_is_accepted():
    """The unquoted form of the same table is accepted — quoting the table is what triggers refusal."""
    _assert_accepted(_toolset(allowed=["orders"]), "SELECT * FROM orders")


def test_double_quoted_column_is_accepted():
    """A double-quoted *column* is not restricted: it can't reach a table outside the list,
    so a quoted column on an otherwise-allowed table runs."""
    _assert_accepted(_toolset(allowed=["orders"]), 'SELECT "id" FROM orders')


def test_inline_comment_is_rejected():
    """Inline comments (a parser-vs-engine differential) are refused while a list is active."""
    _assert_rejected(_toolset(allowed=["orders"]), "SELECT * FROM orders -- and secrets")


def test_query_without_comment_is_accepted():
    """The same query without the comment is accepted — the comment is what triggers refusal."""
    _assert_accepted(_toolset(allowed=["orders"]), "SELECT * FROM orders")


def test_block_comment_is_rejected():
    """Block /* */ comments are refused too — "block all comments" spans every comment style."""
    _assert_rejected(_toolset(allowed=["orders"]), "SELECT /* drop secrets */ * FROM orders")


def test_table_valued_row_source_is_rejected():
    """TABLE('name') names a table through a string the parser can't resolve — refused."""
    _assert_rejected(_toolset(allowed=["orders"]), "SELECT * FROM TABLE('secrets')")


def test_table_name_shorthand_is_rejected():
    """The TABLE <name> shorthand (which sqlglot mis-parses) is refused while a list is active."""
    _assert_rejected(_toolset(allowed=["orders"]), "TABLE secrets")


def test_exec_is_rejected():
    """EXEC / dynamic SQL reaches data through text the parser can't inspect — refused."""
    _assert_rejected(_toolset(allowed=["orders"]), "EXEC do_something")


def test_show_is_rejected_while_list_active():
    """SHOW enumerates objects outside a single table, so it's refused while a list is active."""
    _assert_rejected(_toolset(allowed=["orders"], dialect="snowflake"), "SHOW TABLES")


def test_show_is_accepted_without_a_list():
    """With no list, SHOW is allowed read-only metadata — so the refusal above is list-driven."""
    _assert_accepted(_toolset(allowed=None, dialect="snowflake"), "SHOW TABLES")


# ---------------------------------------------------------------------------
# Table matching: case-insensitive, schema qualification, single-database scope.
# ---------------------------------------------------------------------------

def test_matching_is_case_insensitive():
    """allowed_tables matches case-insensitively, so an upper-cased table name still runs."""
    _assert_accepted(_toolset(allowed=["orders"]), "SELECT * FROM ORDERS")


def test_schema_qualified_entry_matches_that_schema():
    """A schema-qualified entry matches a reference to that schema's table."""
    _assert_accepted(_toolset(allowed=["analytics.orders"]), "SELECT * FROM analytics.orders")


def test_unqualified_entry_does_not_match_other_schema():
    """An unqualified entry doesn't match a table referenced in a different schema."""
    _assert_rejected(_toolset(allowed=["orders"]), "SELECT * FROM analytics.orders")


def test_cross_database_reference_is_rejected():
    """A database/catalog-qualified (three-part) reference can't be matched to the single-database
    list, so it's rejected."""
    _assert_rejected(_toolset(allowed=["orders"]), "SELECT * FROM otherdb.public.orders")


def test_three_part_reference_is_rejected_even_if_listed():
    """The list is single-database: a three-part entry cannot authorize a cross-database reference."""
    _assert_rejected(_toolset(allowed=["otherdb.public.orders"]), "SELECT * FROM otherdb.public.orders")


# ---------------------------------------------------------------------------
# No allow-list -> no table restriction.
# ---------------------------------------------------------------------------

def test_no_allowed_tables_means_no_restriction():
    """When allowed_tables is unset, any table (even reached via JOIN) runs."""
    _assert_accepted(
        _toolset(allowed=None),
        "SELECT * FROM anything JOIN whatever ON anything.id = whatever.id",
    )


# ---------------------------------------------------------------------------
# Derived / edge constructs. These are NOT enumerated one-by-one in the
# instruction; they follow from the stated principle (verify every table a
# query reaches; fail-secure on anything you can't resolve). A checklist
# implementation that only handles the obvious forms tends to miss these.
# ---------------------------------------------------------------------------

def test_quoted_schema_identifier_is_rejected():
    """A double-quoted *schema* is as unmatchable as a quoted table, so it's rejected even when the
    table would otherwise be allowed — the quoting check must cover every identifier part, not just the table."""
    _assert_rejected(_toolset(allowed=["analytics.orders"]), 'SELECT * FROM "analytics".orders')


def test_multi_hop_cte_reaching_disallowed_is_rejected():
    """A real table reached two CTE hops deep is still checked."""
    _assert_rejected(
        _toolset(allowed=["orders"]),
        "WITH a AS (SELECT * FROM secrets), b AS (SELECT * FROM a) SELECT * FROM b",
    )


def test_multi_hop_cte_all_allowed_is_accepted():
    """The same two-hop shape runs when every real table in the chain is allowed."""
    _assert_accepted(
        _toolset(allowed=["orders"]),
        "WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM a) SELECT * FROM b",
    )


def test_derived_table_subquery_reaching_disallowed_is_rejected():
    """A table reached through a FROM-clause derived table is checked."""
    _assert_rejected(_toolset(allowed=["orders"]), "SELECT * FROM (SELECT * FROM secrets) x")


def test_correlated_subquery_reaching_disallowed_is_rejected():
    """A table reached only through a correlated EXISTS subquery is checked."""
    _assert_rejected(
        _toolset(allowed=["orders"]),
        "SELECT * FROM orders o WHERE EXISTS (SELECT 1 FROM secrets s WHERE s.id = o.id)",
    )


def test_nested_set_operation_reaching_disallowed_is_rejected():
    """A table nested inside a bracketed set operation is checked."""
    _assert_rejected(
        _toolset(allowed=["orders"]),
        "SELECT id FROM orders INTERSECT (SELECT id FROM orders UNION SELECT id FROM secrets)",
    )


def test_recursive_cte_self_reference_is_accepted():
    """A recursive CTE's self-reference is the CTE, not a base table, so a benign recursive query runs."""
    _assert_accepted(
        _toolset(allowed=["orders"]),
        "WITH RECURSIVE t AS (SELECT id FROM orders UNION ALL SELECT id FROM t) SELECT * FROM t",
    )


def test_recursive_cte_reaching_disallowed_is_rejected():
    """A real table read inside a recursive CTE's body is still checked."""
    _assert_rejected(
        _toolset(allowed=["orders"]),
        "WITH RECURSIVE t AS (SELECT id FROM secrets UNION ALL SELECT id FROM t) SELECT * FROM t",
    )


def test_merge_source_off_list_is_rejected():
    """With writes enabled, a MERGE whose source table is off the list is rejected."""
    _assert_rejected(
        _toolset(allowed=["orders"], allow_writes=True),
        "MERGE INTO orders o USING secrets s ON o.id = s.id WHEN MATCHED THEN UPDATE SET x = s.x",
    )


def test_lateral_subquery_reaching_disallowed_is_rejected():
    """A table reached through a LATERAL subquery is checked."""
    _assert_rejected(
        _toolset(allowed=["orders"]),
        "SELECT * FROM orders o, LATERAL (SELECT * FROM secrets s WHERE s.id = o.id) z",
    )


def test_show_columns_variant_is_rejected_while_list_active():
    """SHOW in any form (here SHOW COLUMNS) enumerates schema beyond a single table — refused while a list is active."""
    _assert_rejected(_toolset(allowed=["orders"], dialect="mysql"), "SHOW COLUMNS FROM orders")
