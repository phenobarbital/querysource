"""Notification Callbacks for QSScheduler.

Pluggable callback registry for scheduler job error notifications.
v1 ships with a logging-only callback; future versions may add
Telegram, Slack, or webhook callbacks.
"""
from collections.abc import Callable

from navconfig.logging import logging

logger = logging.getLogger("QSScheduler.Notifications")


def logging_callback(job_id: str, slug: str, error: Exception) -> None:
    """Default notification callback — logs at WARNING level."""
    logger.warning(
        f"Scheduler job {job_id} (slug={slug}) failed: {error}"
    )


class NotificationManager:
    """Pluggable callback registry for scheduler job notifications."""

    def __init__(self):
        self._callbacks: list[Callable] = []
        # Register default logging callback
        self.add_callback(logging_callback)

    def add_callback(self, callback: Callable) -> None:
        """Register a notification callback.

        Args:
            callback: Callable with signature (job_id, slug, error) -> None.
        """
        self._callbacks.append(callback)

    def notify(self, job_id: str, slug: str, error: Exception) -> None:
        """Invoke all registered callbacks.

        Each callback is called independently; a failing callback
        does not prevent subsequent callbacks from running.

        Args:
            job_id: The scheduler job identifier.
            slug: The query slug associated with the job.
            error: The exception that triggered the notification.
        """
        for callback in self._callbacks:
            try:
                callback(job_id=job_id, slug=slug, error=error)
            except Exception as exc:
                logger.error(f"Notification callback failed: {exc}")
