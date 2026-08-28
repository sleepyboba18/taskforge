"""Workflow lifecycle and inspection operations."""

from __future__ import annotations

import uuid
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.session import session_scope
from app.models import Job, JobDependency, JobStatus, Workflow, WorkflowStatus
from app.services.dependency_service import propagate_dependency_failure
from app.sockets import publish_event


class WorkflowError(RuntimeError):
    pass


class WorkflowNotFoundError(WorkflowError):
    pass


class WorkflowConflictError(WorkflowError):
    pass


class WorkflowDatabaseError(WorkflowError):
    pass


def create_workflow(*, name: str, description: str | None, created_by: uuid.UUID) -> Workflow:
    try:
        with session_scope() as session:
            workflow = Workflow(name=name, description=description, created_by=created_by)
            session.add(workflow)
            session.commit()
            session.refresh(workflow)
            session.expunge(workflow)
    except SQLAlchemyError as exc:
        raise WorkflowDatabaseError from exc
    publish_event("workflow_created", {"workflow_id": str(workflow.id)})
    return workflow


def get_workflow(workflow_id: uuid.UUID) -> Workflow:
    try:
        with session_scope() as session:
            workflow = session.get(Workflow, workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError
            session.expunge(workflow)
            return workflow
    except WorkflowNotFoundError:
        raise
    except SQLAlchemyError as exc:
        raise WorkflowDatabaseError from exc


def list_workflows(*, page: int, per_page: int, status: WorkflowStatus | None = None) -> tuple[list[Workflow], int]:
    try:
        with session_scope() as session:
            filters = [Workflow.status == status] if status else []
            total = session.scalar(select(func.count()).select_from(Workflow).where(*filters)) or 0
            workflows = list(session.scalars(select(Workflow).where(*filters).order_by(Workflow.created_at.desc(), Workflow.id).offset((page - 1) * per_page).limit(per_page)))
            for workflow in workflows:
                session.expunge(workflow)
            return workflows, total
    except SQLAlchemyError as exc:
        raise WorkflowDatabaseError from exc


def workflow_summary(workflow_id: uuid.UUID) -> dict[str, Any]:
    try:
        with session_scope() as session:
            if session.get(Workflow, workflow_id) is None:
                raise WorkflowNotFoundError
            counts = dict(session.execute(select(Job.status, func.count()).where(Job.workflow_id == workflow_id).group_by(Job.status)).all())
            total = sum(counts.values())
            summary = {"total": total, "pending": counts.get(JobStatus.PENDING, 0) + counts.get(JobStatus.SCHEDULED, 0), "running": counts.get(JobStatus.RUNNING, 0), "succeeded": counts.get(JobStatus.COMPLETED, 0), "failed": counts.get(JobStatus.FAILED, 0), "cancelled": counts.get(JobStatus.CANCELLED, 0)}
            summary["progress_percentage"] = round(summary["succeeded"] / total * 100, 2) if total else 0
            return summary
    except WorkflowNotFoundError:
        raise
    except SQLAlchemyError as exc:
        raise WorkflowDatabaseError from exc


def list_workflow_jobs(workflow_id: uuid.UUID, *, page: int, per_page: int) -> tuple[list[Job], int]:
    try:
        with session_scope() as session:
            if session.get(Workflow, workflow_id) is None:
                raise WorkflowNotFoundError
            total = session.scalar(select(func.count()).select_from(Job).where(Job.workflow_id == workflow_id)) or 0
            jobs = list(session.scalars(select(Job).where(Job.workflow_id == workflow_id).order_by(Job.created_at.desc(), Job.id).offset((page - 1) * per_page).limit(per_page)))
            for job in jobs:
                session.expunge(job)
            return jobs, total
    except WorkflowNotFoundError:
        raise
    except SQLAlchemyError as exc:
        raise WorkflowDatabaseError from exc


def workflow_graph(workflow_id: uuid.UUID, *, max_depth: int, max_nodes: int) -> dict[str, Any]:
    try:
        with session_scope() as session:
            if session.get(Workflow, workflow_id) is None:
                raise WorkflowNotFoundError
            jobs = list(session.scalars(select(Job).where(Job.workflow_id == workflow_id)))
            job_ids = {job.id for job in jobs}
            edges = list(session.scalars(select(JobDependency).where(JobDependency.job_id.in_(job_ids)))) if job_ids else []
            nodes = [{"job_id": str(job.id), "workflow_id": str(workflow_id), "status": job.status.value} for job in jobs[:max_nodes]]
            node_ids = {job["job_id"] for job in nodes}
            output_edges = []
            for edge in edges:
                if str(edge.job_id) not in node_ids or len(output_edges) >= max_nodes:
                    continue
                output_edges.append({"job_id": str(edge.job_id), "depends_on_job_id": str(edge.depends_on_job_id), "workflow_id": str(workflow_id) if edge.depends_on_job_id in job_ids else None})
            return {"workflow_id": str(workflow_id), "nodes": nodes, "edges": output_edges, "truncated": len(jobs) > max_nodes or len(edges) > len(output_edges), "max_depth": max_depth}
    except WorkflowNotFoundError:
        raise
    except SQLAlchemyError as exc:
        raise WorkflowDatabaseError from exc


def update_workflow_status(session: Session, workflow_id: uuid.UUID | None) -> WorkflowStatus | None:
    if workflow_id is None:
        return None
    workflow = session.scalar(select(Workflow).where(Workflow.id == workflow_id).with_for_update())
    if workflow is None or workflow.status == WorkflowStatus.CANCELLED:
        return workflow.status if workflow else None
    counts = dict(session.execute(select(Job.status, func.count()).where(Job.workflow_id == workflow_id).group_by(Job.status)).all())
    total = sum(counts.values())
    now = datetime.now(timezone.utc)
    if counts.get(JobStatus.FAILED, 0) or counts.get(JobStatus.CANCELLED, 0):
        new_status = WorkflowStatus.FAILED
    elif total and counts.get(JobStatus.COMPLETED, 0) == total:
        new_status = WorkflowStatus.SUCCEEDED
    elif counts.get(JobStatus.RUNNING, 0) or counts.get(JobStatus.RETRYING, 0):
        new_status = WorkflowStatus.RUNNING
    else:
        new_status = WorkflowStatus.PENDING
    if workflow.status != new_status:
        workflow.status = new_status
        workflow.updated_at = now
        if new_status == WorkflowStatus.RUNNING and workflow.started_at is None:
            workflow.started_at = now
            publish_event("workflow_started", {"workflow_id": str(workflow_id)})
        if new_status in {WorkflowStatus.SUCCEEDED, WorkflowStatus.FAILED}:
            workflow.completed_at = now
            publish_event(f"workflow_{new_status.value.lower()}", {"workflow_id": str(workflow_id)})
    return new_status


def cancel_workflow(workflow_id: uuid.UUID) -> Workflow:
    try:
        with session_scope() as session:
            workflow = session.scalar(select(Workflow).where(Workflow.id == workflow_id).with_for_update())
            if workflow is None:
                raise WorkflowNotFoundError
            if workflow.status in {WorkflowStatus.SUCCEEDED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}:
                raise WorkflowConflictError
            workflow.status = WorkflowStatus.CANCELLED
            workflow.completed_at = datetime.now(timezone.utc)
            jobs = list(session.scalars(select(Job).where(Job.workflow_id == workflow_id).with_for_update()))
            for job in jobs:
                if job.status in {JobStatus.PENDING, JobStatus.SCHEDULED, JobStatus.RETRYING}:
                    job.status = JobStatus.CANCELLED
                    job.completed_at = workflow.completed_at
                    job.last_error = "Workflow was cancelled."
                    propagate_dependency_failure(session, job.id, max_depth=50)
            session.commit()
            session.refresh(workflow)
            session.expunge(workflow)
    except (WorkflowNotFoundError, WorkflowConflictError):
        raise
    except SQLAlchemyError as exc:
        raise WorkflowDatabaseError from exc
    publish_event("workflow_cancelled", {"workflow_id": str(workflow_id)})
    return workflow


def retry_workflow(workflow_id: uuid.UUID) -> Workflow:
    try:
        with session_scope() as session:
            workflow = session.scalar(select(Workflow).where(Workflow.id == workflow_id).with_for_update())
            if workflow is None:
                raise WorkflowNotFoundError
            if workflow.status != WorkflowStatus.FAILED:
                raise WorkflowConflictError
            jobs = list(session.scalars(select(Job).where(Job.workflow_id == workflow_id).with_for_update()))
            retryable = [job for job in jobs if job.status in {JobStatus.FAILED, JobStatus.CANCELLED} and job.retry_count < job.max_retries]
            if not retryable:
                raise WorkflowConflictError
            for job in retryable:
                job.status = JobStatus.PENDING
                job.completed_at = None
                job.last_error = None
                job.next_retry_at = None
            workflow.status = WorkflowStatus.RUNNING
            workflow.completed_at = None
            workflow.started_at = workflow.started_at or datetime.now(timezone.utc)
            session.commit()
            session.refresh(workflow)
            session.expunge(workflow)
    except (WorkflowNotFoundError, WorkflowConflictError):
        raise
    except SQLAlchemyError as exc:
        raise WorkflowDatabaseError from exc
    publish_event("workflow_retry_requested", {"workflow_id": str(workflow_id)})
    return workflow
