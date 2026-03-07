"""
Celery Background Tasks
"""
import os
import logging
from datetime import datetime, timedelta
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="workers.tasks.cleanup_sessions", max_retries=3)
def cleanup_sessions(self):
    """Cleanup inactive sessions from the database."""
    try:
        from models.database import get_session, SSHSession
        db = get_session()
        timeout = int(os.getenv("SESSION_TIMEOUT", "1800"))
        cutoff = datetime.utcnow() - timedelta(seconds=timeout)

        stale = db.query(SSHSession).filter(
            SSHSession.is_active == True,
            SSHSession.connected_at < cutoff,
        ).all()

        for session in stale:
            session.is_active = False
            session.disconnected_at = datetime.utcnow()
            session.disconnect_reason = "Session timeout (auto-cleanup)"

        db.commit()
        db.close()
        logger.info(f"Cleaned up {len(stale)} stale sessions")
        return len(stale)
    except Exception as exc:
        logger.error(f"Cleanup error: {exc}")
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(bind=True, name="workers.tasks.record_session_start", max_retries=3)
def record_session_start(self, user_id: int, host: str, port: int, ssh_username: str, server_id: int = None):
    try:
        from models.database import get_session, SSHSession, User
        db = get_session()
        user = db.query(User).filter(User.telegram_id == user_id).first()
        if user:
            session = SSHSession(
                user_id=user.id,
                server_id=server_id,
                host=host,
                port=port,
                ssh_username=ssh_username,
                is_active=True,
            )
            db.add(session)
            db.commit()
            session_id = session.id
            db.close()
            return session_id
    except Exception as exc:
        logger.error(f"record_session_start error: {exc}")
        raise self.retry(exc=exc, countdown=10)


@celery_app.task(bind=True, name="workers.tasks.record_session_end", max_retries=3)
def record_session_end(self, session_id: int, reason: str = "User disconnected"):
    try:
        from models.database import get_session, SSHSession
        db = get_session()
        session = db.query(SSHSession).filter(SSHSession.id == session_id).first()
        if session:
            session.is_active = False
            session.disconnected_at = datetime.utcnow()
            session.disconnect_reason = reason
            db.commit()
        db.close()
    except Exception as exc:
        logger.error(f"record_session_end error: {exc}")
        raise self.retry(exc=exc, countdown=10)


@celery_app.task(bind=True, name="workers.tasks.increment_command_count", max_retries=2)
def increment_command_count(self, session_id: int):
    try:
        from models.database import get_session, SSHSession
        db = get_session()
        session = db.query(SSHSession).filter(SSHSession.id == session_id).first()
        if session:
            session.commands_count = (session.commands_count or 0) + 1
            db.commit()
        db.close()
    except Exception as exc:
        raise self.retry(exc=exc, countdown=5)


# Periodic task: cleanup stale sessions every 10 minutes
from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    "cleanup-stale-sessions": {
        "task": "workers.tasks.cleanup_sessions",
        "schedule": 600.0,  # every 10 minutes
    },
}
