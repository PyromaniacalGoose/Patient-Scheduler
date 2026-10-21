# Robustness test suite

Production code is unchanged. Tests assert intended behavior; known bugs are
ordinary failing tests, not xfails. `regression` is a selection label, not a
promise that a test fails forever. Remove the label after fixing a regression
if you want it to leave the repair queue.

## Run

Use Python 3.12+ and PostgreSQL (not SQLite: exclusion constraints, sequences,
and row-lock behavior are part of the contract).

```sh
python -m pip install -r requirements-test.txt
python -m pytest -ra
```

The test role must be able to create/drop databases and install `btree_gist`.
Use a disposable local PostgreSQL instance. Configuration is in
`tests/settings.py`; production settings are untouched. Set `TEST_DB_NAME`,
`TEST_DB_USER`, `TEST_DB_PASSWORD`, `TEST_DB_HOST`, and `TEST_DB_PORT` as needed.
Defaults match the existing development settings. pytest-django creates a
separate `test_patient_scheduler` database by default. The Actions workflow
provides an isolated PostgreSQL 17 service and runs all tests on pushes/PRs.

Useful selections:

```sh
# Calculations/domain tests: no PostgreSQL required
python -m pytest scheduling/tests tests/test_availability.py tests/test_domain.py tests/test_patient_service.py tests/test_planning.py -m 'not django_db'
# Known robustness expectations (some already pass)
python -m pytest -m regression
# Independent-connection race tests (PostgreSQL required)
python -m pytest -m concurrency
# HTTP behavior, including middleware and actual persistence
python -m pytest tests/test_views.py
```

## Coverage and repair guide

| Module | What it protects |
| --- | --- |
| `tests/test_availability.py` | Global/room override precedence, closures, room isolation, clipping, overlapping bookings, quarter-hour alignment, nonpositive durations |
| `tests/test_domain.py` | Positive, timezone-aware ranges; treatment lengths; immutable edits revalidate |
| `tests/test_planning.py` | Requested room selection, invalid counts/intervals, no-room search |
| `tests/test_patient_service.py` | Gender mapping, active patient reuse, reactivation, invalid gender without writes |
| `tests/test_repositories.py` | Missing/inactive patients; override round trips; real overlap exclusion and half-open boundaries; inactive history; course number uniqueness |
| `tests/test_booking_integrity.py` | Real rollback after collisions and persistence failures; reschedule metadata; closed dates/hours; duration; patient double booking; stale/forged cascades; empty courses; moving within the original slot |
| `tests/test_views.py` | Authentication on all protected routes; viewer denial; real permission provisioning; CSRF; invalid input; missing records; orphan calendar slots; UTC/local time; draft/confirm flow; retries; conflicts; spacing after manual edits; schedule deletion/atomicity |
| `tests/test_concurrency.py` | Two room bookings competing for one interval; concurrent reschedule lost updates; duplicate patient registrations |

Existing scheduling tests now freeze the clock (both `date.today` and
`datetime.now`) and use the domain classes in fakes. The course-count mismatch
assertion now names its exception. An existing course example was moved to an
actual 56-day interval, and the back-to-back example explicitly extends opening
hours so those fixtures remain valid when booking validation is strengthened.

## Expected failures and behavior choices

The tests intentionally expose defects identified in the review. Additional
expectations are explicit: an appointment may move within its own original
slot, a patient cannot be booked into two rooms at once, new courses must have
at least one appointment, and mutation-only selection endpoints reject GET.
These are proposed application contracts; change them deliberately if your
requirements differ. Quarter-hour alignment follows the scheduling function's
stated intention. No medical interval is invented: the spacing view test starts
a course requesting 56 days, then verifies that manual selection cannot bypass
that specific request.

Validation views may render a form (200), return 400, or return 422; tests check
that invalid state is not written. Service validation tests currently accept
`ValueError`, the existing slot conflict error, or the course conflict wrapper
as documented in each test. If you introduce a dedicated validation exception,
update those expectations without weakening the state assertions.

Concurrency tests use separate connections, synchronization barriers, and
bounded PostgreSQL lock/statement timeouts. Exceptions in worker threads are
propagated. The reschedule regression synchronizes the initial reads while
allowing an implementation using `select_for_update` to serialize them. It
inspects ORM queries only to coordinate the race, not to require a particular
locking implementation. If you substantially change persistence, adapt that
synchronization hook while retaining the no-orphan invariant.

The existing fake-based booking tests do not prove rollback. Use the new real
repository tests for that guarantee. Integration tests run real migrations;
fixtures do not manually create the missing booking permission, so a fresh
installation cannot silently appear healthier than it is.

## Verification in this review environment

The database-free selection ran: **56 passed, 8 failed, 4 deselected**. The eight
failures expose the ignored cascade `today` argument, global overrides (two
cases), quarter-hour alignment, ignored requested rooms, and invalid planning
inputs (three cases). Full collection succeeds: **174 tests**. PostgreSQL is
unavailable in this environment, so database/view/concurrency tests were not
executed; their results must be checked against the configured service locally
or in Actions. CI is intentionally red until the regression fixes are made.

## Remaining blind spots

This is not exhaustive coverage. Browser JavaScript/HTMX swapping, accessibility,
production deployment checks, large-data query budgets, audit-history policy,
DST-transition treatment durations, and backup/restore still need dedicated
work. The suite does not assume an undocumented minimum interval for all
courses; storing and enforcing each course's rules requires production changes.
