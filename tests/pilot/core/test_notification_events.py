import logging
from types import SimpleNamespace
from unittest.mock import patch

from pilot.core.notification import NotificationStore
from pilot.core.notification.events import (
    _fan_out_to_sinks,
    backups_failing,
    record_alert,
    task_failed,
    task_succeeded,
)


def _feed(tmp_path):
    return NotificationStore(tmp_path / "logs").list(10)


def test_task_failure_lands_under_the_matching_category(tmp_path) -> None:
    task_failed(tmp_path, {"task_id": "t1", "command": "backup-site", "args": {"site": "a.local"}})

    (item,) = _feed(tmp_path)
    assert item.title == "Backup site failed"
    assert item.category == "Sites"
    assert item.severity == "Error"
    assert item.site == "a.local"
    assert item.task_id == "t1"
    assert item.action_route == "/insights/tasks/t1"


def test_update_commands_are_grouped_separately(tmp_path) -> None:
    task_failed(tmp_path, {"task_id": "t2", "command": "migrate", "args": {}})

    (item,) = _feed(tmp_path)
    assert item.category == "Updates"
    assert item.site is None


def test_unmapped_command_falls_back_to_tasks(tmp_path) -> None:
    task_failed(tmp_path, {"task_id": "t3", "command": "install-app", "args": {}})

    assert _feed(tmp_path)[0].category == "Tasks"


def test_noisy_commands_stay_out_of_the_feed(tmp_path) -> None:
    task_failed(tmp_path, {"task_id": "t4", "command": "clear-cache", "args": {}})
    task_failed(tmp_path, {"task_id": "t5", "command": "", "args": {}})

    assert _feed(tmp_path) == []


def test_task_failure_never_raises(tmp_path, caplog) -> None:
    caplog.set_level(logging.WARNING)
    with patch("pilot.core.notification.NotificationStore.create", side_effect=OSError("disk full")):
        task_failed(tmp_path, {"task_id": "t6", "command": "update", "args": {}})

    assert "Notification skipped for failed task t6" in caplog.text


def test_record_alert_writes_the_benchs_own_copy(tmp_path) -> None:
    bench = SimpleNamespace(notifications=NotificationStore(tmp_path / "logs"))
    payload = {"event": "site_down", "message": "bench: a.local unreachable"}

    record_alert(
        bench, payload, category="Sites", severity="Error", title="a.local is unreachable", site="a.local"
    )

    (item,) = _feed(tmp_path)
    assert item.title == "a.local is unreachable"
    assert item.event == "site_down"
    assert item.message == "bench: a.local unreachable"
    assert item.action_route == "/sites/a.local"


def test_record_alert_never_raises(tmp_path, caplog) -> None:
    bench = SimpleNamespace(notifications=NotificationStore(tmp_path / "logs"))
    caplog.set_level(logging.WARNING)
    with patch("pilot.core.notification.NotificationStore.create", side_effect=OSError("disk full")):
        record_alert(
            bench, {"event": "resource_limit_breached"}, category="Server", severity="Warning", title="x"
        )

    assert "Notification skipped for alert resource_limit_breached" in caplog.text


def test_long_running_tasks_announce_when_they_finish(tmp_path) -> None:
    task_succeeded(tmp_path, {"task_id": "t7", "command": "update", "args": {}})

    (item,) = _feed(tmp_path)
    assert item.event == "task_succeeded"
    assert item.severity == "Info"
    assert item.category == "Updates"
    assert item.action_route == "/insights/tasks/t7"


def test_quick_commands_stay_quiet_when_they_succeed(tmp_path) -> None:
    task_succeeded(tmp_path, {"task_id": "t8", "command": "backup-site", "args": {"site": "a.local"}})

    assert _feed(tmp_path) == []


def test_task_failure_reaches_the_alert_sinks(tmp_path) -> None:
    seen = []
    with patch("pilot.core.notification.events._fan_out_to_sinks", lambda *args: seen.append(args)):
        task_failed(tmp_path, {"task_id": "t9", "command": "restore-site", "args": {"site": "a.local"}})

    assert seen == [(tmp_path, "task_failed", "Restore site failed", "a.local", "t9")]


def test_task_success_reaches_the_alert_sinks(tmp_path) -> None:
    seen = []
    with patch("pilot.core.notification.events._fan_out_to_sinks", lambda *args: seen.append(args)):
        task_succeeded(tmp_path, {"task_id": "t10", "command": "migrate", "args": {}})

    assert seen == [(tmp_path, "task_succeeded", "Migrate finished", None, "t10")]


def test_sink_fan_out_builds_the_shared_payload(tmp_path) -> None:
    bench = SimpleNamespace(config=SimpleNamespace(name="b1"))
    payloads = []
    with (
        patch("pilot.core.bench.Bench", return_value=bench),
        patch("pilot.core.alerts.notify", lambda bench, payload: payloads.append(payload)),
    ):
        _fan_out_to_sinks(tmp_path, "task_succeeded", "Update finished", None, "t11")

    (payload,) = payloads
    assert payload["event"] == "task_succeeded"
    assert payload["message"] == "Update finished"
    assert payload["context"]["bench"] == "b1"
    assert payload["context"]["task_id"] == "t11"


def test_sink_fan_out_survives_an_unreadable_bench(tmp_path, caplog) -> None:
    caplog.set_level(logging.WARNING)
    with patch("pilot.core.bench.Bench", side_effect=OSError("no bench")):
        _fan_out_to_sinks(tmp_path, "task_failed", "Update failed", None, None)

    assert "Sink delivery skipped for task_failed" in caplog.text


def test_failing_backups_reach_the_alert_sinks(tmp_path) -> None:
    bench = SimpleNamespace(
        notifications=NotificationStore(tmp_path / "logs"), config=SimpleNamespace(name="b1")
    )
    payloads = []
    with patch("pilot.core.alerts.notify", lambda bench, payload: payloads.append(payload)):
        backups_failing(bench, "a.local", 3)

    (payload,) = payloads
    assert payload["event"] == "backups_failing"
    assert payload["context"]["bench"] == "b1"
    assert payload["context"]["failures"] == 3
