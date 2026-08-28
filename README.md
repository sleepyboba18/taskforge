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
