# Multi-tenant isolation suite

Placeholder. The real cross-tenant isolation suite (fixture with two
tenants; every repository must reject cross-tenant reads/writes) ships in
TASK-004, once `BaseRepository` and the tenant-scoped models exist
(TASK-003). This directory is scaffolded now so CI wiring (TASK-002) has a
stable path (`make isolation`) to target from day one.
