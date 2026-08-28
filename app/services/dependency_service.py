"""Validation and state operations for directed job dependencies."""

from __future__ import annotations

import logging
import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import delete, exists, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from app.database.session import session_scope
from app.models import Job, JobDependency, JobStatus

logger = logging.getLogger("taskforge.dependencies")


class DependencyState(StrEnum):
    READY = "READY"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"


class DependencyError(RuntimeError):
    pass


class DependencyNotFoundError(DependencyError):
    pass


class DependencyValidationError(DependencyError):
    pass


class DependencyCycleError(DependencyError):
    pass


class DependencyImmutableError(DependencyError):
    pass


class DependencyDatabaseError(DependencyError):
    pass


def validate_dependency_ids(values: Any, *, maximum: int) -> list[uuid.UUID]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise DependencyValidationError("dependencies must be an array.")
    if len(values) > maximum:
        raise DependencyValidationError(f"A job may have at most {maximum} dependencies.")
    result: list[uuid.UUID] = []
    for value in values:
        if not isinstance(value, str):
            raise DependencyValidationError("Dependency IDs must be UUID strings.")
        try:
            parsed = uuid.UUID(value)
        except ValueError as exc:
            raise DependencyValidationError("Dependency IDs must be valid UUIDs.") from exc
        if parsed in result:
            raise DependencyValidationError("Duplicate dependency IDs are not allowed.")
        result.append(parsed)
    return result


def add_edges(session: Session, job: Job, dependency_ids: list[uuid.UUID], *, max_nodes: int = 1000) -> None:
    if job.id in dependency_ids:
        raise DependencyValidationError("A job cannot depend on itself.")
    dependencies = list(session.scalars(select(Job).where(Job.id.in_(dependency_ids))))
    found = {dependency.id for dependency in dependencies}
    missing = next((dependency_id for dependency_id in dependency_ids if dependency_id not in found), None)
    if missing is not None:
        raise DependencyNotFoundError(str(missing))
    for dependency_id in dependency_ids:
        if session.scalar(select(JobDependency.id).where(
            JobDependency.job_id == job.id, JobDependency.depends_on_job_id == dependency_id
        )) is not None:
            raise DependencyValidationError("Duplicate dependency edges are not allowed.")
        if _would_cycle(session, job.id, dependency_id, max_nodes=max_nodes):
            raise DependencyCycleError
        session.add(JobDependency(job_id=job.id, depends_on_job_id=dependency_id))
        logger.info("dependency_created", extra={"job_id": str(job.id), "depends_on_job_id": str(dependency_id)})


def _would_cycle(session: Session, job_id: uuid.UUID, dependency_id: uuid.UUID, *, max_nodes: int) -> bool:
    frontier = [dependency_id]
    visited: set[uuid.UUID] = set()
    while frontier and len(visited) < max_nodes:
        current = frontier.pop()
        if current == job_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        frontier.extend(session.scalars(select(JobDependency.depends_on_job_id).where(JobDependency.job_id == current)))
    return False


def dependency_state(session: Session, job_id: uuid.UUID) -> DependencyState:
    dependencies = list(
        session.execute(
            select(Job.status)
            .join(JobDependency, Job.id == JobDependency.depends_on_job_id)
            .where(JobDependency.job_id == job_id)
        ).scalars()
    )
    if not dependencies:
        return DependencyState.READY
    if any(status in {JobStatus.FAILED, JobStatus.CANCELLED} for status in dependencies):
        return DependencyState.BLOCKED
    return DependencyState.READY if all(status == JobStatus.COMPLETED for status in dependencies) else DependencyState.WAITING


def dependency_ready_clause():
    dependency_parent = aliased(Job)
    unresolved = select(JobDependency.id).join(
        dependency_parent, dependency_parent.id == JobDependency.depends_on_job_id
    ).where(JobDependency.job_id == Job.id, dependency_parent.status != JobStatus.COMPLETED)
    return ~exists(unresolved)


def get_dependencies(job_id: uuid.UUID) -> list[tuple[JobDependency, Job]]:
    try:
        with session_scope() as session:
            rows = list(session.execute(
                select(JobDependency, Job)
                .join(Job, Job.id == JobDependency.depends_on_job_id)
                .where(JobDependency.job_id == job_id)
                .order_by(JobDependency.created_at.asc(), Job.id.asc())
            ))
            if not session.get(Job, job_id):
                raise DependencyNotFoundError
            return rows
    except DependencyError:
        raise
    except SQLAlchemyError as exc:
        raise DependencyDatabaseError from exc


def get_dependents(job_id: uuid.UUID) -> list[Job]:
    try:
        with session_scope() as session:
            if not session.get(Job, job_id):
                raise DependencyNotFoundError
            jobs = list(session.scalars(
                select(Job).join(JobDependency, JobDependency.job_id == Job.id)
                .where(JobDependency.depends_on_job_id == job_id).order_by(Job.created_at.asc())
            ))
            for job in jobs:
                session.expunge(job)
            return jobs
    except DependencyError:
        raise
    except SQLAlchemyError as exc:
        raise DependencyDatabaseError from exc


def mutate_dependency(job_id: uuid.UUID, dependency_id: uuid.UUID, *, add: bool, max_nodes: int) -> None:
    try:
        with session_scope() as session:
            job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is None:
                raise DependencyNotFoundError
            if job.status != JobStatus.PENDING:
                raise DependencyImmutableError
            if add:
                add_edges(session, job, [dependency_id], max_nodes=max_nodes)
            else:
                result = session.execute(delete(JobDependency).where(
                    JobDependency.job_id == job_id, JobDependency.depends_on_job_id == dependency_id
                ))
                if not result.rowcount:
                    raise DependencyNotFoundError
                logger.info("dependency_removed", extra={"job_id": str(job_id), "depends_on_job_id": str(dependency_id)})
            session.commit()
    except DependencyError:
        raise
    except SQLAlchemyError as exc:
        raise DependencyDatabaseError from exc


def propagate_dependency_failure(session: Session, failed_job_id: uuid.UUID, *, max_depth: int) -> None:
    frontier = [(failed_job_id, 0)]
    while frontier:
        source_id, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        dependents = list(session.scalars(
            select(Job).join(JobDependency, JobDependency.job_id == Job.id)
            .where(JobDependency.depends_on_job_id == source_id).with_for_update()
        ))
        for dependent in dependents:
            if dependent.status in {JobStatus.PENDING, JobStatus.SCHEDULED, JobStatus.RETRYING}:
                dependent.status = JobStatus.CANCELLED
                dependent.completed_at = dependent.completed_at or dependent.updated_at
                dependent.last_error = f"Dependency job {failed_job_id} failed or was cancelled."
                logger.info("dependency_blocked", extra={"job_id": str(dependent.id), "depends_on_job_id": str(source_id)})
                frontier.append((dependent.id, depth + 1))