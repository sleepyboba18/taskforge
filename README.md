# TaskForge

TaskForge is a production-oriented foundation for a PostgreSQL-backed background job processing system. Modules 1 and 2 provide the Flask application factory, environment configuration, SQLAlchemy infrastructure, CORS, Socket.IO initialization, structured API errors, a health endpoint, and the persistent queue schema.

## Technologies

- Python 3.11+
- Flask and Flask-CORS
- Flask-SocketIO
- SQLAlchemy 2.x with psycopg
- PostgreSQL hosted by Supabase
- python-dotenv

## Architecture

`main.py` is the thin executable entry point. The `app` package owns configuration, database infrastructure, HTTP APIs, Socket.IO extensions, and reserved service and worker packages. Database sessions are created per operation so future worker processes can manage their own PostgreSQL transactions.

The database is Supabase-hosted PostgreSQL and remains the source of truth for durable job state. SQLAlchemy models define `Job`, `JobAttempt`, and `Worker`, including controlled statuses, JSONB payloads, UUID identifiers, retry metadata, execution history, ownership references, and queue-oriented indexes. A job progresses conceptually from pending or scheduled work through running, retrying, completed, failed, or cancelled states; worker processes and execution behavior are planned for later modules.

## Setup

Install Python 3.11 or newer and make sure PostgreSQL is available through Supabase. Create and activate a virtual environment, then install dependencies:

```text
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set `DATABASE_URL` to your Supabase PostgreSQL URL. Keep `.env` private; it is ignored by Git. The application requires this variable during startup.

## Run

```text
python main.py
```

The service listens on the configured host and port. `GET /health` returns the service status. Database connectivity can be checked through the reusable `check_database_connection` function; this foundation does not create tables automatically.

## Process Lifecycle

TaskForge manages a complete application lifecycle from startup through graceful shutdown:

### Startup

The application follows a deterministic startup sequence:
1. Load and validate environment configuration
2. Initialize logging
3. Initialize Flask application and database connection
4. Initialize worker pool
5. Initialize scheduler processes
6. Transition to RUNNING state

Startup errors are logged and the process exits with status 1. Partial initialization is cleaned up automatically.

### Graceful Shutdown

The application handles `SIGINT` (Ctrl+C) and `SIGTERM` signals by initiating graceful shutdown:

1. Transition to STOPPING state
2. Broadcast `server:shutdown` to Socket.IO clients
3. Stop accepting new job submissions (return HTTP 503)
4. Readiness probe becomes unavailable (return HTTP 503)
5. Scheduler stops creating new jobs
6. Workers drain current work without claiming new jobs
7. Wait up to `SHUTDOWN_TIMEOUT_SECONDS` for workers to finish
8. Force-terminate any remaining workers
9. Dispose SQLAlchemy database resources
10. Emit final metrics
11. Transition to STOPPED state and exit

### Shutdown Configuration

Configure graceful shutdown behavior in `.env`:

```text
SHUTDOWN_TIMEOUT_SECONDS=60
WORKER_SHUTDOWN_TIMEOUT=30
```

- `SHUTDOWN_TIMEOUT_SECONDS`: Total time allocated for graceful shutdown (default 60s, max 600s)
- `WORKER_SHUTDOWN_TIMEOUT`: Time to wait for individual workers to exit gracefully (default 30s, max 300s)

Workers that exceed the shutdown timeout are force-terminated. The total shutdown timeout must be greater than or equal to the worker shutdown timeout.

### Shutdown Behavior

During shutdown:

- New HTTP requests that create or schedule work receive `503 Service Unavailable`
- Read-only requests continue briefly before the HTTP server closes
- Connected Socket.IO clients receive a `server:shutdown` notification
- Active jobs are allowed to complete; jobs are not cancelled
- Long-running jobs that exceed the timeout remain in an appropriate state for recovery
- Workers exit cleanly, updating their status to STOPPED
- Database connections are properly disposed

Repeated shutdown signals (e.g., multiple Ctrl+C presses) are safe and idempotent.

### Lifecycle Monitoring

Query `GET /ready` for public readiness: returns `200 OK` only when the application is RUNNING and the database is reachable. Returns `503` during startup, shutdown, or failure.

Query `GET /health` for public liveness: returns `200 OK` as long as the process is alive.

Authenticated operators can query `GET /api/v1/health` for detailed lifecycle and component state.


User accounts are stored in PostgreSQL with unique usernames and emails. Passwords are stored only as Werkzeug password hashes. Login returns a short-lived JWT access token:

```text
POST /api/v1/auth/login
Authorization: Bearer <access_token>
```

Use `GET /api/v1/auth/me` to inspect the current account and `POST /api/v1/auth/change-password` to change its password. `ADMIN`, `OPERATOR`, and `VIEWER` roles are enforced from the current database user on every protected request. Viewers can inspect jobs, DLQ records, workers, and recurring schedules. Operators can submit/cancel jobs, retry or delete DLQ records, and modify recurring schedules. Administrators additionally manage users through `GET/POST /api/v1/users`, `GET /api/v1/users/<id>`, and `PATCH /api/v1/users/<id>`.

Configure `JWT_SECRET_KEY` and `JWT_ACCESS_TOKEN_EXPIRES_MINUTES` in `.env`; no JWT secret is embedded in source. An optional first administrator can be bootstrapped with `BOOTSTRAP_ADMIN_USERNAME`, `BOOTSTRAP_ADMIN_EMAIL`, and `BOOTSTRAP_ADMIN_PASSWORD`. These values must be supplied together, and no default account is created. Internal workers, schedulers, heartbeats, and recovery continue to use their database services directly and do not require JWTs.

## Observability

`GET /health` is public liveness and reports whether the Flask process is responding. `GET /ready` is public readiness and performs a lightweight PostgreSQL connectivity check, returning `503` when the database is unavailable. Neither endpoint exposes infrastructure details.

Authenticated users can query `GET /api/v1/health` for database, worker, scheduler, and queue state, and `GET /api/v1/metrics` for PostgreSQL-derived queue counts, workflow totals, dependency counts, audit totals, throughput, execution latency, success/failure rates, DLQ counts, and worker counts. Metrics supports the strict windows `1h`, `24h`, and `7d`. Viewers, operators, and administrators may read these endpoints.

Every HTTP response includes an `X-Request-ID`. A valid client-supplied request ID is reused; otherwise TaskForge generates a UUID. Request timing and slow requests are logged without request bodies, passwords, JWTs, authorization headers, database URLs, or other secrets. Configure `LOG_LEVEL`, `SLOW_REQUEST_THRESHOLD_MS`, and `METRICS_DEFAULT_WINDOW` in `.env`.

## Rate Limiting

TaskForge applies fixed-window, PostgreSQL-backed rate limiting to external API routes. Authenticated requests use a stable user ID bucket; unauthenticated requests use the direct Flask client address. Proxy headers are not trusted unless proxy handling is explicitly added to the deployment configuration. Public `/health` and `/ready` remain available without JWT authentication and are not subject to normal user API limits.

## Rate Limit Configuration

Rate limiting is enabled by default and can be configured in `.env`:

```text
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS=60
RATE_LIMIT_WINDOW_SECONDS=60
LOGIN_RATE_LIMIT_REQUESTS=10
LOGIN_RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_ADMIN=300
RATE_LIMIT_OPERATOR=120
RATE_LIMIT_VIEWER=60
RATE_LIMIT_RETENTION_SECONDS=3600
RATE_LIMIT_FAIL_OPEN=false
```

The login policy is separate and intentionally stricter. `RATE_LIMIT_FAIL_OPEN=false` rejects protected API requests with a controlled service error when PostgreSQL rate-limit state is unavailable. Set it to `true` only when temporary degraded operation is preferred. Rate-limit records are short-lived operational state and can be removed with `app.rate_limit.service.cleanup` from an existing maintenance process.

## Rate Limit Responses

Allowed responses include `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` headers. Exceeded requests return JSON with `error.code` set to `rate_limit_exceeded`, status `429 Too Many Requests`, and a non-negative `Retry-After` value. Rate-limit persistence failures return `rate_limit_unavailable` without exposing database details.

## Authentication Limits

`POST /api/v1/auth/login` is limited before password verification, so repeated invalid credentials cannot trigger unlimited password-hash work. Invalid JWT traffic is still assigned an unauthenticated client bucket by protected route authentication ordering.

## Role-Based Limits

Authenticated route buckets use the role loaded from PostgreSQL, never a request body field. `ADMIN`, `OPERATOR`, and `VIEWER` limits are configurable independently. Route categories distinguish `AUTH`, `READ`, `WRITE`, and `ADMIN`; authorization remains separate, so an authorized request receives `403 Forbidden` when permissions are insufficient and `429` only after exceeding its configured limit.

## Task Dependencies

Jobs may depend on other Jobs. An edge `A -> B` means B runs only after A succeeds. Jobs without dependencies retain the existing queue behavior. A job with dependencies waits until every direct dependency is `COMPLETED`; scheduled jobs must satisfy both their schedule and dependency conditions.

## Dependency Graphs

Use `GET /api/v1/jobs/<job_id>/dependencies` to inspect direct prerequisites and `GET /api/v1/jobs/<job_id>/dependents` to inspect direct downstream Jobs. Dependency edges are stored in PostgreSQL with indexed foreign keys and duplicate-edge protection. Graph traversal is bounded by `MAX_DEPENDENCY_GRAPH_DEPTH` and `MAX_DEPENDENCY_GRAPH_NODES` for future bounded graph operations.

## Dependency Rules

Dependencies can be supplied as a UUID array during Job creation or added and removed through the dependency endpoints while the Job is still `PENDING`. Self-dependencies, missing Jobs, duplicate input, and cycles are rejected. A terminally failed or cancelled dependency blocks downstream pending work and records a safe reason; retryable failures do not block dependents.

## Dependency APIs

```text
GET    /api/v1/jobs/<job_id>/dependencies
GET    /api/v1/jobs/<job_id>/dependents
POST   /api/v1/jobs/<job_id>/dependencies
DELETE /api/v1/jobs/<job_id>/dependencies/<dependency_job_id>
```

The creation request remains backwards compatible and accepts `"dependencies": []`. Dependency inspection uses authenticated read access; dependency mutation uses existing operator permissions and rate limits. Responses report each prerequisite Job status and whether it is satisfied.

## Workflow Execution

Workers continue using PostgreSQL row locks and now claim only pending Jobs whose direct dependencies have all completed successfully. Fan-out and fan-in workflows are supported without process-local graph state. Terminal dependency failure or cancellation propagates through pending downstream chains as `CANCELLED`; successful completion makes downstream Jobs naturally eligible on the next queue poll. Dependency counts for waiting Jobs, blocked Jobs, and edges are included in PostgreSQL-derived metrics.

## Workflows

A workflow is a logical collection and lifecycle boundary for related Jobs. A Job remains the executable unit, and a dependency remains the execution relationship between Jobs. Standalone Jobs remain fully supported.

## Workflow Lifecycle

Workflows start as `PENDING`, become `RUNNING` when a member Job starts, and become `SUCCEEDED` only when every member Job completes. Permanent failure or dependency blocking produces `FAILED`; explicit cancellation produces `CANCELLED`. Empty workflows remain pending.

## Workflow APIs

```text
POST   /api/v1/workflows
GET    /api/v1/workflows
GET    /api/v1/workflows/<workflow_id>
GET    /api/v1/workflows/<workflow_id>/jobs
GET    /api/v1/workflows/<workflow_id>/graph
POST   /api/v1/workflows/<workflow_id>/cancel
POST   /api/v1/workflows/<workflow_id>/retry
```

Create Jobs with `workflow_id` to associate them with an existing workflow. Workflow detail includes SQL-derived Job counts and progress; graph output is bounded and includes only Jobs in the requested workflow.

## Bulk Job Operations

Operators and administrators can submit bounded bulk cancellation or retry requests:

```text
POST /api/v1/jobs/bulk/cancel
POST /api/v1/jobs/bulk/retry
```

Each request accepts a unique `job_ids` array limited by `MAX_BULK_JOB_OPERATIONS` and returns per-Job results for partial success. Existing Job cancellation, retry, DLQ, authorization, and rate-limit rules remain authoritative.

## Workflow Dependencies

Workflow membership does not create dependencies automatically. Explicit Job dependencies continue to control execution, including cross-workflow dependencies. A workflow cannot be structurally changed by these APIs after execution begins, and dependency failure propagation can cause the workflow to fail without executing blocked Jobs.

## Audit Trail

Application logs remain technical runtime diagnostics. The append-only PostgreSQL audit trail records meaningful business and security actions without storing passwords, tokens, authorization headers, database credentials, or complete Job payloads. `AUDIT_RETENTION_DAYS=0` means unlimited retention; this module does not automatically delete audit history.

## Execution History

`JobAttempt` remains authoritative for detailed execution attempts. Audit events complement it with committed Job, attempt, workflow, dependency, worker, DLQ, scheduling, and bulk-operation history. Each API event preserves the existing request ID, while worker events identify the responsible worker.

## Audit Event Types

Events include Job and attempt lifecycle changes, workflow lifecycle changes, dependency changes, DLQ operations, worker registration/staleness, bulk actions, and authorization-sensitive denials where implemented. Audit details are server-generated bounded metadata, such as status transitions, retry source, actor, and counts.

## Audit APIs

```text
GET /api/v1/audit-events
GET /api/v1/audit-events/<audit_event_id>
GET /api/v1/jobs/<job_id>/history
GET /api/v1/workflows/<workflow_id>/history
```

The global audit API supports pagination and filters for event/entity type, IDs, actor, worker, Job, Workflow, and UTC creation bounds. History endpoints are paginated and rate-limited.

## Audit Security

Audit events are inserted only by services as part of the same transaction as the state change. There are no update or delete endpoints. Global audit access is limited to operators and administrators and uses the existing JWT, RBAC, request ID, and rate-limiting layers.

## Operational Monitoring

TaskForge monitoring is derived primarily from bounded PostgreSQL aggregates over Jobs, Attempts, Workers, Workflows, DLQ records, Dependencies, and AuditEvents. It provides operational telemetry without an external metrics database or monitoring platform. Values are an operational snapshot and may be slightly eventually consistent across worker processes.

## Monitoring APIs

Authenticated operators and administrators can use the overview and focused endpoints:

```text
GET /api/v1/monitoring/overview
GET /api/v1/monitoring/queue
GET /api/v1/monitoring/workers
GET /api/v1/monitoring/jobs
GET /api/v1/monitoring/workflows
GET /api/v1/monitoring/scheduler
GET /api/v1/monitoring/dlq
GET /api/v1/monitoring/database
GET /api/v1/monitoring/alerts
```

## Metrics

Monitoring accepts explicit windows: `1m`, `5m`, `15m`, `1h`, `6h`, `24h`, or `7d`; the default is `1h`. Metrics include queue depth and dependency blocking, worker utilization, Job throughput and rates, workflow totals, DLQ depth, database latency/pool values, audit totals, and bounded operational latency aggregates.

## Alert Conditions

The monitoring response derives non-persistent `INFO`, `WARNING`, and `CRITICAL` conditions such as high queue backlog, queue starvation, absent or stale workers, worker saturation, DLQ backlog, and failure/retry spikes. Alerts do not modify Jobs, workers, or scheduler state and do not send notifications.

## Monitoring Configuration

## Observability

TaskForge combines structured request logs with PostgreSQL-derived operational metrics. Request counters are bounded process-local supplemental telemetry; Jobs, Attempts, Workers, Workflows, DLQ records, Dependencies, and AuditEvents remain database-authoritative. No external monitoring stack is required.

## Metrics

Monitoring reports queue depth and backlog age, worker status and utilization, Job throughput/rates/latency, stale and long-running Jobs, workflow totals, dependency blocking, DLQ depth, scheduler schedule visibility, database latency/pool values, audit totals, and bounded API request/error/slow-request telemetry. Historical windows are explicitly limited to `1m`, `5m`, `15m`, `1h`, `6h`, `24h`, and `7d`.

## Monitoring API

Use the authenticated operator/admin endpoints under `/api/v1/monitoring`, including `/overview`, `/queue`, `/workers`, `/jobs`, `/workflows`, `/scheduler`, `/dlq`, `/database`, and `/alerts`. Monitoring is read-only and uses the existing request ID, RBAC, and PostgreSQL rate-limiting layers.

## Structured Logging

The existing logger records request ID, method, route, status, duration, actor context, and slow-request severity without request bodies. Worker and scheduler logs retain operational identifiers while excluding passwords, JWTs, authorization headers, database URLs, and complete Job payloads.

## Operational Health

`/health` remains lightweight liveness and `/ready` remains PostgreSQL readiness. Detailed monitoring can report degraded database state and deterministic queue, worker, DLQ, backlog, starvation, saturation, stale-worker, and failure/retry-spike conditions without modifying system state.

## Telemetry Retention

Monitoring does not create a row per observation or maintain a time-series database. Persistent audit history remains controlled by `AUDIT_RETENTION_DAYS=0` (unlimited) and is not automatically deleted. PostgreSQL operational records are queried with bounded windows and aggregate expressions.

Configure thresholds in `.env` with `LONG_RUNNING_JOB_THRESHOLD_SECONDS`, `QUEUE_BACKLOG_WARNING_THRESHOLD`, `DLQ_BACKLOG_WARNING_THRESHOLD`, and `WORKER_SATURATION_THRESHOLD_PERCENT`. Monitoring failures return a controlled `MONITORING_UNAVAILABLE` response and never expose SQLAlchemy errors, connection strings, secrets, or Job payloads.

## Administrative Operations

Operational controls are available under `/api/v1/admin` and require an authenticated `OPERATOR` or `ADMIN`. Every state-changing control is validated, rate-limited, logged, and persisted in the PostgreSQL audit trail. Administrative controls are explicit actions; monitoring never performs automatic remediation.

## Administrative Permissions

The existing RBAC model remains authoritative. Operators and administrators can control the queue, cancel or retry eligible Jobs, requeue DLQ Jobs, and use bounded bulk actions. Viewers cannot perform administrative operations.

## Queue Controls

```text
POST /api/v1/admin/queue/pause
POST /api/v1/admin/queue/resume
GET  /api/v1/admin/queue/status
```

Queue pause state is stored in PostgreSQL and checked before worker claims. Running Jobs continue; pending and scheduled Jobs remain persisted. Pause and resume are idempotent and return `already_paused` or `already_running` when appropriate.

## Job Controls

```text
POST /api/v1/admin/jobs/<job_id>/cancel
POST /api/v1/admin/jobs/<job_id>/retry
POST /api/v1/admin/jobs/cancel
POST /api/v1/admin/jobs/retry
```

Single-Job actions reuse the existing Job state machine and accept an optional sanitized `reason` of up to 500 characters. Bulk actions accept bounded unique UUID lists and return per-Job partial results; they do not bypass retry, cancellation, dependency, or DLQ rules.

## DLQ Controls

```text
POST /api/v1/admin/dlq/<job_id>/requeue
```

DLQ requeue locks the DLQ record and Job in one transaction, removes only the active DLQ entry, and returns the Job to the existing lifecycle. It does not create a duplicate Job.

## Worker Recovery

Worker recovery remains owned by the existing stale-worker recovery service. This module does not add a force-kill endpoint or automatically terminate healthy workers.

## Scheduler Controls

Scheduler polling remains owned by the existing scheduler processes. No second scheduler or unsafe refresh mechanism is introduced.

## Administrative Safety

Administrative status is available at `GET /api/v1/admin/status` and reuses monitoring data. State changes use short PostgreSQL transactions, row locks where needed, existing request IDs, and audit records. Secrets, tokens, credentials, and Job payloads are never accepted as audit metadata or returned by these APIs.

## API

Versioned JSON endpoints live under `/api/v1`. Routes validate input, apply the existing JWT, RBAC, and PostgreSQL rate-limit layers, call services, and serialize safe responses. Public `/health` and `/ready` retain their lightweight liveness/readiness roles.

## Authentication

Protected endpoints require the existing Bearer JWT and role checks. Invalid credentials return JSON errors without revealing resource existence or exposing tokens. Administrative and monitoring APIs require elevated roles.

## Error Responses

API errors use the existing `success: false` envelope with an error code/message, request ID, and optional details. Malformed JSON, unsupported methods, unknown routes, oversized bodies, database failures, and unexpected exceptions return JSON rather than HTML. Stack traces and infrastructure details remain internal.

## Pagination

Paginated collections use bounded `page` and `per_page` parameters with SQL-level pagination and metadata. Invalid or oversized values are rejected.

## Filtering

Filters and sorts use explicit allowlists. Job listing supports `status`, `task_type`, `priority`, and safe sort values such as `-created_at`, `priority`, and `status`; arbitrary SQL expressions and unknown query parameters are rejected. Accepted timestamps are timezone-aware and normalized to UTC.

## Rate Limiting

External API endpoints use the existing PostgreSQL-backed limiter where configured. `429` responses remain JSON with retry metadata, while health endpoints remain outside normal user API limits.

## Administrative API

Administrative controls live under `/api/v1/admin`, require `OPERATOR` or `ADMIN`, validate bounded inputs, use transactional state changes, and write audit events. Configure `MAX_REQUEST_BODY_MB` (default `2`) to bound request bodies. In production, use a strong `SECRET_KEY` and explicit `CORS_ORIGINS`; wildcard origins are development-only.

## Configuration

`Settings.from_environment()` is the single configuration loader. Process environment values take precedence over `.env`, followed by safe non-secret development defaults. It validates PostgreSQL `DATABASE_URL`, `APP_ENV` (`development`, `testing`, or `production`), `PORT` (`1` through `65535`), strict booleans, worker/scheduler/retry/rate-limit/monitoring thresholds, request limits, and JWT expiration.

Required deployment secrets are never logged or returned. Production requires explicit strong `SECRET_KEY` and `JWT_SECRET_KEY` values, `DEBUG=false`, and explicit `CORS_ORIGINS`; wildcard credentialed CORS is rejected. The sanitized startup log reports only environment, host, port, database configuration presence, worker count, and CORS configuration presence.

## Environment Variables

Use `.env.example` as the local template. Important settings include `DATABASE_URL`, `SECRET_KEY`, `JWT_SECRET_KEY`, `APP_ENV`, `HOST`, `PORT`, `WORKER_COUNT`, scheduler and retry intervals, `RATE_LIMIT_*`, `MAX_REQUEST_BODY_MB`, `SLOW_REQUEST_THRESHOLD_MS`, monitoring thresholds, `CORS_ORIGINS`, and `CORS_SUPPORTS_CREDENTIALS`. Do not place actual credentials in `.env.example` or commit `.env`.

## Concurrency Model

PostgreSQL is TaskForge's authoritative coordination layer. Independent worker processes use row-level locks and `FOR UPDATE SKIP LOCKED`; Python globals, files, and local locks are never used for distributed state.

## PostgreSQL Coordination

Queue pause state is persisted in the `system_settings` table. Worker admission locks the queue-control row and the worker row before selecting work, while the Job row lock prevents two workers from claiming the same Job. JobAttempt uniqueness remains enforced by `(job_id, attempt_number)`.

## Worker Job Claiming

Claiming follows a short transaction: lock an idle Worker, check operational state, select an eligible Job with `SKIP LOCKED`, create its JobAttempt, set `RUNNING`, record audit events, and commit. User task execution happens after commit. A `claimed_at` timestamp records the successful claim.

## Transaction Boundaries

Completion, failure, recovery, retries, cancellation, and DLQ requeue use short transactions and deterministic lock ordering. Locks are not held during task execution, retry backoff, scheduler sleeps, or Socket.IO emission. Late worker completion is ignored when the Job or Attempt is no longer owned and running.

## Concurrency Guarantees

The database prevents concurrent workers from receiving the same committed claim, and terminal Job states cannot transition back to execution. Administrative and DLQ operations lock their resources before validating and changing state. Monitoring remains read-only and may reflect a rapidly changing database snapshot.

## Recovery and Race Conditions

Recovery re-checks Worker, Job, and Attempt state after locking. A worker heartbeat or another transition that wins the database ordering prevents stale recovery from overwriting it. Task execution is generally at-least-once around process/database failures; handlers that produce external effects should be idempotent.

## Initialize the Schema

After configuring a valid Supabase `DATABASE_URL`, initialize missing ORM tables from a Python shell:

```text
python -c "from app.config.settings import Settings; from app.database.session import initialize_database, initialize_schema; initialize_database(Settings.from_environment().database_url); initialize_schema()"
```

Schema initialization creates missing tables only. It does not drop, reset, or modify existing tables. Queue claiming, workers, retries, and scheduling are not implemented yet.
Modules 1 through 3 provide the Flask application factory, environment configuration, SQLAlchemy infrastructure, CORS, Socket.IO initialization, structured API errors, a health endpoint, the persistent queue schema, and the job submission API.

## Job API

Jobs are stored directly in PostgreSQL, which is the durable queue. The API validates input and does not execute tasks or use an in-memory queue.

```text
POST /api/v1/jobs
GET  /api/v1/jobs/<job_id>
GET  /api/v1/jobs?page=1&per_page=20&status=PENDING
POST /api/v1/jobs/<job_id>/cancel
```

Submit an immediate job:

```json
{
	"name": "generate_report",
	"task_type": "report",
	"payload": {"report_id": 123},
	"priority": 10,
	"max_retries": 3
}
```

An immediate job is persisted as `PENDING`; a future timezone-aware `scheduled_at` is normalized to UTC and persisted as `SCHEDULED`. Jobs can be filtered by `status`, `task_type`, and `priority`, with database pagination and newest-first ordering. `PENDING`, `SCHEDULED`, and `RETRYING` jobs can be cancelled through a locked transaction. Successful submission and cancellation publish `job:created` and `job:cancelled` Socket.IO notifications after commit.

## Worker Engine

`python main.py` starts the configured number of independent worker processes in addition to Flask and Socket.IO. Each worker initializes its own SQLAlchemy resources, registers a persistent `Worker` row, and consumes PostgreSQL directly. Queue claiming uses SQLAlchemy `with_for_update(skip_locked=True)`, filters eligible `PENDING` jobs, and orders by priority descending then creation time ascending. The claim transaction updates the job and creates its `JobAttempt` before task execution begins.

The built-in task registry currently supports the safe `echo` task and a bounded `sleep` task. Only explicitly registered task types execute; unknown types fail the job without terminating the worker. Completion and failure use separate transactions after execution, and lifecycle events are published for workers and jobs. Configure `WORKER_COUNT`, `WORKER_POLL_INTERVAL`, and `WORKER_SHUTDOWN_TIMEOUT` in `.env`.

## Retry Engine

Failed executions use the persisted retry lifecycle `RUNNING -> RETRYING -> PENDING -> RUNNING`. `max_retries` counts retries after the initial attempt, while `retry_count` records retries already scheduled. For example, `max_retries=3` permits attempts 1 through 4. Retry delays use capped exponential backoff: `RETRY_BASE_DELAY * 2^(retry_number - 1)`, limited by `RETRY_MAX_DELAY`.

Retry timestamps are stored in PostgreSQL as `next_retry_at`. A dedicated scheduler process promotes due `RETRYING` jobs in bounded, row-locked batches; workers then create the next `JobAttempt` only when they claim the promoted `PENDING` job. `NonRetryableTaskError` skips retries and immediately marks the job failed. Configure `RETRY_BASE_DELAY`, `RETRY_MAX_DELAY`, `RETRY_POLL_INTERVAL`, and `RETRY_BATCH_SIZE` in `.env`. Retry events include `job:retrying`, `job:retry_ready`, and the existing `job:failed`. Advanced scheduling, recovery, and administrative retries are not implemented yet.

## One-Time Scheduling

Submit a job with a timezone-aware `scheduled_at` value to persist it as `SCHEDULED`:

```json
{
	"name": "future-task",
	"task_type": "echo",
	"payload": {"message": "hello"},
	"scheduled_at": "2026-09-01T10:30:00+05:30"
}
```

The scheduler polls PostgreSQL and promotes due jobs from `SCHEDULED` to `PENDING` using short `FOR UPDATE SKIP LOCKED` transactions. Workers continue to claim only `PENDING` jobs. The original `scheduled_at` is preserved; retries use `next_retry_at` separately. Past or current scheduled times become immediately eligible as `PENDING`, while cancelled scheduled jobs are never promoted. Scheduling is polling-based, so `scheduled_at` is the earliest intended eligibility time rather than an exact execution guarantee. Configure `SCHEDULER_POLL_INTERVAL` and `SCHEDULER_BATCH_SIZE` in `.env`. Recurring scheduling is not implemented.

## Recurring Jobs

Recurring definitions are persisted in PostgreSQL and use standard five-field cron expressions with an explicit IANA timezone:

```text
POST /api/v1/recurring-jobs
```

```json
{
	"name": "daily-report",
	"task_type": "echo",
	"payload": {"report": "daily"},
	"priority": 10,
	"max_retries": 2,
	"schedule": "0 9 * * *",
	"timezone": "Asia/Kolkata"
}
```

The recurring definition stores its next occurrence as UTC `next_run_at`. When due, the recurring scheduler locks the definition, creates one ordinary `PENDING` Job linked by `recurring_job_id`, records the occurrence in `last_run_at`, and advances `next_run_at` in the same transaction. It never executes task handlers. PostgreSQL locking prevents duplicate generation across scheduler processes, and overlap is allowed. Missed occurrences use the default `MISFIRE_POLICY=SKIP`: restart advances to the next future occurrence rather than generating a backlog. Use `GET /api/v1/recurring-jobs`, `GET /api/v1/recurring-jobs/<id>`, and the enable/disable endpoints to manage definitions. Disabling a definition does not delete generated execution history, and retries belong to each generated Job independently.

## Dead-Letter Queue

When a Job reaches terminal `FAILED` after retry exhaustion, TaskForge atomically preserves its final failed attempt and creates one `DeadLetterJob` record in PostgreSQL. The original Job remains `FAILED`; retryable failures remain `RETRYING` and do not enter the DLQ. Recurring schedules continue generating independent executions even when one execution is dead-lettered.

```text
GET    /api/v1/dead-letters?page=1&per_page=20&task_type=echo
GET    /api/v1/dead-letters/<id>
POST   /api/v1/dead-letters/<id>/retry
DELETE /api/v1/dead-letters/<id>
```

Manual retry locks the DLQ record and Job, removes only the active DLQ record, returns the Job to `PENDING`, and preserves every historical `JobAttempt`. Workers then claim it normally. Deleting a DLQ record removes only the management record and never deletes the original Job or its attempts. DLQ records retain bounded error information, attempt count, final attempt ID, task type, source, and recurring-job linkage without automatic reprocessing.

## Worker Heartbeats and Recovery

Each worker registers a unique UUID and process instance in PostgreSQL, then sends heartbeats independently of task execution. Heartbeats use separate short-lived SQLAlchemy sessions and refresh both the Worker and active JobAttempt timestamps. Worker health is derived from PostgreSQL through `GET /api/v1/workers`, `GET /api/v1/workers/<worker_id>`, and `GET /api/v1/workers/health`.

The recovery scheduler conservatively marks a worker `STALE` only after its heartbeat exceeds `WORKER_STALE_TIMEOUT`. It then locks and re-checks the Worker, Job, and running JobAttempt ownership before recovering an abandoned execution. Worker loss is recorded as `WORKER_LOST` and follows the existing retry policy or DLQ path. Graceful shutdown marks workers `STOPPED`, so they are not treated as crashed. Configure `WORKER_HEARTBEAT_INTERVAL`, `WORKER_STALE_TIMEOUT`, and `RECOVERY_POLL_INTERVAL`; defaults are 5, 30, and 10 seconds. Recovery emits `job:recovered` and does not broadcast every heartbeat.

## Worker Heartbeats and Recovery

Each worker registers a unique UUID and UUID-suffixed instance name in PostgreSQL, then updates `last_heartbeat_at` independently of task execution. Active attempts receive the same heartbeat timestamp through a separate SQLAlchemy session. Worker health is derived from PostgreSQL; `GET /api/v1/workers`, `GET /api/v1/workers/<worker_id>`, and `GET /api/v1/workers/health` expose safe status metadata.

When a worker heartbeat exceeds `WORKER_STALE_TIMEOUT`, the recovery scheduler locks and re-checks the Worker, Job, and running JobAttempt. Only a matching `STALE` worker with a still-running owned attempt is recovered. Worker loss is recorded as `WORKER_LOST`, then the existing retry policy sends the Job to `RETRYING` or the existing DLQ. Graceful shutdown marks workers `STOPPED`, and does not treat them as stale. Configure `WORKER_HEARTBEAT_INTERVAL`, `WORKER_STALE_TIMEOUT`, and `RECOVERY_POLL_INTERVAL`; the defaults are 5, 30, and 10 seconds.
