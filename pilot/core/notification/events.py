"""The events a bench raises for its own feed.

Producers are best-effort: a feed that cannot be written must never take down the
task or the monitoring tick that noticed the problem.
"""

from __future__ import annotations

import logging
import typing
from pathlib import Path

from pilot.core.notification import NotificationStore
from pilot.internal.jsonl_log import utc_now

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

# Which part of the dashboard a failed command belongs to. Anything unlisted is
# ordinary task noise and lands under "Tasks".
_CATEGORY_BY_COMMAND = {
    "backup-site": "Sites",
    "delete-backup": "Sites",
    "drop-site": "Sites",
    "new-site": "Sites",
    "new-site-from-backup": "Sites",
    "reinstall-site": "Sites",
    "restore-site": "Sites",
    "setup-letsencrypt": "Sites",
    "migrate": "Updates",
    "migration-backup": "Updates",
    "retry-update": "Updates",
    "revert-apps": "Updates",
    "revert-migration": "Updates",
    "revert-site": "Updates",
    "update": "Updates",
    "update-cli": "Updates",
    "restart-database": "Server",
    "restart-services": "Server",
    "setup-nginx": "Server",
}

# Commands whose failure the user already sees in the page that queued them, or
# that a retry loop is expected to shrug off. Keeping them out of the feed is what
# stops it becoming a second, noisier task list.
_UNREPORTED_COMMANDS = frozenset(
    {
        "build",
        "clear-cache",
        "fetch-all-app-updates",
        "wizard-setup",
    }
)

# Commands long enough that whoever queued them has walked away by the time they
# end: these also raise a success event, so "it finished" reaches a user who is
# not looking at the dashboard.
_SUCCESS_COMMANDS = frozenset({"migrate", "restore-site", "update"})


def report_task_result(bench_root: Path, meta: dict, status: str) -> None:
    """Record a finished task. Called from the task wrapper with "failed" or
    "success"; failures of noisy commands and successes of quick ones stay out."""
    if status == "failed":
        task_failed(bench_root, meta)
    elif status == "success":
        task_succeeded(bench_root, meta)


def task_failed(bench_root: Path, meta: dict) -> None:
    """Record a failed task. Called from the task wrapper, so it runs in the child
    process after the task has already been marked failed."""
    command = str(meta.get("command") or "")
    if not command or command in _UNREPORTED_COMMANDS:
        return

    task_id = str(meta.get("task_id") or "")
    site = (meta.get("args") or {}).get("site")
    title = f"{_readable(command)} failed"
    try:
        NotificationStore(Path(bench_root) / "logs").create(
            title,
            category=_CATEGORY_BY_COMMAND.get(command, "Tasks"),
            event="task_failed",
            severity="Error",
            message=f"{site}: see the task log for the failure."
            if site
            else "See the task log for the failure.",
            site=site,
            task_id=task_id or None,
            action_route=f"/insights/tasks/{task_id}" if task_id else None,
        )
    except Exception as exc:
        logging.warning("Notification skipped for failed task %s: %s", task_id, exc)

    _fan_out_to_sinks(Path(bench_root), "task_failed", title, site, task_id or None)


def task_succeeded(bench_root: Path, meta: dict) -> None:
    """Record a finished long-running task. Called from the task wrapper, so it
    runs in the child process after the task has already been marked successful."""
    command = str(meta.get("command") or "")
    if command not in _SUCCESS_COMMANDS:
        return

    task_id = str(meta.get("task_id") or "")
    site = (meta.get("args") or {}).get("site")
    title = f"{_readable(command)} finished"
    try:
        NotificationStore(Path(bench_root) / "logs").create(
            title,
            category=_CATEGORY_BY_COMMAND.get(command, "Tasks"),
            event="task_succeeded",
            severity="Info",
            message=f"{site}: the command finished successfully."
            if site
            else "The command finished successfully.",
            site=site,
            task_id=task_id or None,
            action_route=f"/insights/tasks/{task_id}" if task_id else None,
        )
    except Exception as exc:
        logging.warning("Notification skipped for finished task %s: %s", task_id, exc)

    _fan_out_to_sinks(Path(bench_root), "task_succeeded", title, site, task_id or None)


def _fan_out_to_sinks(bench_root: Path, event: str, title: str, site: str, task_id: str | None) -> None:
    """Copy one task event to the alert sinks - the same webhook, mail and Central
    fan-out the monitoring alerts take, so it reaches a user who is not looking at
    the feed. Best-effort: this runs in the task wrapper's child process, where a
    missing or half-configured bench only skips the delivery."""
    try:
        from pilot.core.alerts import notify
        from pilot.core.bench import Bench

        bench = Bench(bench_root)
        payload = {
            "event": event,
            "message": f"{title} on {site}" if site else title,
            "context": {"bench": bench.config.name, "time": utc_now(), "site": site, "task_id": task_id},
        }
        notify(bench, payload)
    except Exception as exc:
        logging.warning("Sink delivery skipped for %s: %s", event, exc)


def record_alert(
    bench: "Bench",
    payload: dict,
    *,
    category: str,
    severity: str,
    title: str,
    site: str | None = None,
) -> bool:
    """Record a sustained alert in the bench's own feed, reporting whether it landed.

    The caller still hands the same payload to `alerts.notify` for the webhook and
    Central fan-out; this is the copy that stays on the bench. A write that failed
    returns False so the caller leaves the condition unrecorded and tries again on
    the next tick, rather than losing the incident until it clears and recurs."""
    try:
        bench.notifications.create(
            title,
            category=category,
            event=str(payload.get("event") or "alert"),
            severity=severity,
            message=payload.get("message"),
            site=site,
            action_route=f"/sites/{site}" if site else "/insights/analytics",
        )
        return True
    except Exception as exc:
        logging.warning("Notification skipped for alert %s: %s", payload.get("event"), exc)
        return False


def backups_failing(bench: "Bench", site: str, failures: int) -> None:
    """Raised once when a site reaches the failure limit since its last successful backup."""
    try:
        bench.notifications.create(
            f"Backups of {site} are failing",
            category="Sites",
            event="backups_failing",
            severity="Error",
            message=f"{failures} backup attempts failed since the last successful backup.",
            site=site,
            action_route=f"/sites/{site}",
        )
    except Exception as exc:
        logging.warning("Notification skipped for failing backups of %s: %s", site, exc)

    try:
        from pilot.core.alerts import notify

        notify(
            bench,
            {
                "event": "backups_failing",
                "message": f"Backups of {site} are failing: {failures} backup attempts failed since the last successful backup.",
                "context": {
                    "bench": bench.config.name,
                    "time": utc_now(),
                    "site": site,
                    "failures": failures,
                },
            },
        )
    except Exception as exc:
        logging.warning("Sink delivery skipped for failing backups of %s: %s", site, exc)


def _readable(command: str) -> str:
    """`backup-site` -> `Backup site`, so the title reads like a sentence."""
    return command.replace("-", " ").capitalize()
