# Multi-tenant isolation suite

Mandated by the Constitution: every business query filters by `tenant_id` at
the repository layer, and that is verified here. **This suite is never
skipped, never marked xfail, and never removed from CI.** It runs as its own
GitHub Actions job so that disabling it shows up as a red required check
rather than as a quietly smaller test count inside `make test`.

Run it alone with `make isolation`.

## What is here

| File | Purpose |
|---|---|
| `conftest.py` | Two real tenants, each owning one product, knowledge source, chunk, conversation and message. Everything runs in a rolled-back transaction. |
| `test_tenant_isolation.py` | Tenant A must not read, rank, update or delete anything belonging to tenant B. |
| `test_isolation_suite_has_teeth.py` | The test of the test. |

## Why the meta-test exists

A passing isolation suite proves nothing by itself, because a suite whose
assertions can never fail also passes. `test_isolation_suite_has_teeth.py`
defines deliberately sabotaged repositories and asserts that the properties
this suite depends on really do break under them.

It covers two distinct failure modes:

* **No filter at all** — `select(Model)` without the tenant predicate.
* **Filtering after ranking** — the subtle one. The rows returned still all
  belong to the right tenant, so a naive test passes, but `ORDER BY` and
  `LIMIT` saw every tenant's rows, so a foreign row can push the tenant's
  real answer out of the result set. This is the shape a vector search
  regression would take.

If `BaseRepository` regresses, `test_tenant_isolation.py` fails. If someone
repairs the sabotaged classes, `test_isolation_suite_has_teeth.py` fails
instead, flagging that the meta-test has gone stale. Both directions are
covered on purpose.

## Adding a repository

Anything inheriting `BaseRepository` gets the tenant filter structurally, and
a model without a `tenant_id` column is rejected at class-definition time.
New repositories still need their own cross-tenant cases here — the base
class guarantees the filter, not that a hand-written query in a subclass
starts from `_scoped()`.
