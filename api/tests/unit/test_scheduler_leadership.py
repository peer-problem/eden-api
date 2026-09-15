from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from app.scheduler.runtime import verify_scheduler_leadership


def test_scheduler_heartbeat_keeps_current_lock_owner() -> None:
    connection = Mock()
    connection.execute.return_value.one.return_value = (41, 41)
    scheduler = Mock()
    leader_lock = SimpleNamespace(name="eden:scheduler:leader", acquired=True)

    assert verify_scheduler_leadership(scheduler, connection, leader_lock)
    scheduler.shutdown.assert_not_called()
    connection.close.assert_not_called()


def test_scheduler_heartbeat_shuts_down_after_database_lock_loss() -> None:
    connection = Mock()
    connection.execute.return_value.one.return_value = (None, 72)
    scheduler = Mock()
    leader_lock = SimpleNamespace(name="eden:scheduler:leader", acquired=True)

    assert not verify_scheduler_leadership(scheduler, connection, leader_lock)
    assert leader_lock.acquired is False
    scheduler.shutdown.assert_called_once_with(wait=False)
    connection.close.assert_called_once_with()
