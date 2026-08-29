<!--
  This file IS the prompt your agent reads. Replace everything below this
  comment with your task. The example shown is the minimal valid shape.

  Key rules (full guidance: docs/task-anatomy.md → instruction.md section):
    - Use absolute paths (/app/output.txt, not output.txt) — relative paths fail CI.
    - State the goal, not the procedure. The agent picks the approach.
    - 2-3 paragraphs is the right length. Over-specification is penalized.
    - Write in your own voice. AI-generated instructions are flagged.
    - If you expect structured output, specify the schema explicitly.
-->

In Apache Airflow, the sql toolset in `providers.common.ai` has a flaw. It exposes
an `allowed_tables` argument to restrict which tables the AI models can have access 
to. But this feature is only used by discovery operations like `list tables` and 
`get schema`. If the AI directly runs a query by guessing the table name, it would 
have the ability to read the data.

## Expected behavior:
1. When user gives a list of tables to allow: this means the user wants to restrict 
access to data, therefore the program must fail risky sql execution as per the below
specifications
2. When no list of allow-tables is provided: there is no restriction to run any query
3. The program must not assume one SQL dialect:
    - parse with the dialect toolset resolves from db connection
    - parse dialect agnostically when dialect is set to None
4. Double-quotes denote SQL identifiers (ANSI) and not string literals

### When allowed-tables list is active:
1. The program must parse the SQL and **reject it before execution** if it reaches a table that is not on the list.
2. The toolset must check every table the given SQL statement can reach - anywhere in the statement, not just top-level FROM. That includes, not limited to:
    - Tables in direct SQL queries
    - Tables mentioned in subqueries
    - Tables mentioned in CTE body
    - Tables used in JOIN clauses
    - Tables used in set operations
    - Tables when using DESCRIBE, information_schema and DMLs
3. CTE usually acts as temporary tables, which the program should allow during runtime, as it cannot be allow-listed before hand. But a CTE with restricted-table-name can be created for namesake, and the real-table can be queried outside CTE scope, if CTE exclusions are global. Therefore CTE exclusion must be lexically scoped.
4. Some SQL statements can try to bypass static parsing analysis with advanced techniques such as real table reference hidden behind dynamic evaluation, metadata enumeration, string encoded names, or exploit parser-vs-engine ambiguity. Do not attempt enumerating the variations of these forms. Adopt a fail-secure rule: unless you can statically resolve a statement's complete set of referenced tables, and match against the list - reject it before execution. In particular, reject any double-quoted table or schema identifier outright; quoted name is case-sensite and case-insensitive allow-list cannot represent it; a double-quoted column reaches no new table, so it can be accepted.  


## Table matching:
1. Matching is case-insensitive: `orders` can match `Orders` and `ORDERS`
2. An unqualified entry (`orders`) can only match a table referenced without schema
3. A schema qualified entry (`schema.orders`) can only match table referenced with schema (`schema.orders`)
4. `information_schema` counts as schema, whitelist `information_schema.tables` explicitly, to allow it
5. The allowed-tables list is scoped to one database: A query referencing database/catalog qualifier (three parts, `db.schema.table`) must always be rejected


## Failure Expectation:
1. When a query fails the safety standards prescribed above, reject it by raising exception before executing it
2. Any exception is fine, the toolset surfaces it consistently to the caller
