"""Transactional, metadata-only change feed across MAX and HTTP writes."""
from sqlalchemy import event
from sqlalchemy.orm import Session
from .db import Application, IntegrationEvent, Job, uid


def collect_changes(db, _flush_context, _instances):
    for row in list(db.new) + list(db.dirty):
        if not isinstance(row, (Job, Application)):
            continue
        fresh = row in db.new
        if not fresh and not db.is_modified(row, include_collections=True):
            continue
        if not row.id:
            row.id = uid()
        if isinstance(row, Job):
            owner, kind = row.owner_id, 'job.created' if fresh else 'job.updated'
        else:
            job = db.get(Job, row.job_id)
            if not job:
                job = next((j for j in db.new if isinstance(j, Job) and j.id == row.job_id), None)
            if not job:
                continue  # The FK constraint rejects an invalid application.
            owner = job.owner_id
            kind = 'application.created' if fresh else ('application.withdrawn' if row.status == 'withdrawn' else 'application.updated')
        db.add(IntegrationEvent(owner_id=owner, kind=kind, resource_id=row.id))


def install_events():
    if not event.contains(Session, 'before_flush', collect_changes):
        event.listen(Session, 'before_flush', collect_changes)
