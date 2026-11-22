"""Append-only event storage and tracker projections."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class TrackerError(ValueError):
    """A user-facing tracker validation or state error."""


def slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not result:
        raise TrackerError("ID must contain letters or numbers")
    return result


def validate_id(value: str, label: str = "ID") -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise TrackerError(f"{label} must start with a letter or number and contain only letters, numbers, underscores, or hyphens")


def tracker_root(start: Path) -> Path | None:
    current = start.resolve()
    for path in (current, *current.parents):
        if (path / ".tracker" / "tracker.json").is_file():
            return path
    return None


def append_event(root: Path, kind: str, data: dict[str, Any], actor: str = "cli") -> dict[str, Any]:
    event = {
        "schema_version": "ep-tracker.event/1.0",
        "event_id": str(uuid.uuid4()),
        "recorded_at": datetime.now(UTC).isoformat(),
        "actor": actor,
        "kind": kind,
        "data": data,
    }
    directory = root / ".tracker" / "events"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{event['event_id']}.json").write_text(json.dumps(event, indent=2, sort_keys=True) + "\n")
    return event


def events(root: Path) -> list[dict[str, Any]]:
    directory = root / ".tracker" / "events"
    if not directory.exists():
        return []
    records = [json.loads(path.read_text()) for path in directory.glob("*.json")]
    return sorted(records, key=lambda event: (event["recorded_at"], event["event_id"]))


def state(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"people": {}, "projects": {}, "repos": {}, "features": {}, "tasks": {},
                              "pull_requests": [], "assignment_requests": [], "team_updates": []}
    for event in events(root):
        kind, data = event["kind"], event["data"]
        if kind in {"person.created", "person.updated", "person.deactivated"}:
            person = result["people"].setdefault(data["person_id"], {"id": data["person_id"]})
            person.update(data)
            if kind in {"person.created", "person.deactivated"}:
                person["active"] = kind == "person.created"
        elif kind in {"project.created", "project.updated"}:
            result["projects"].setdefault(data["project_id"], {"id": data["project_id"]}).update(data)
        elif kind == "repo.linked":
            result["repos"].setdefault(data["repo_id"], {"id": data["repo_id"]}).update(data)
        elif kind in {"feature.created", "feature.status_changed"}:
            feature = result["features"].setdefault(data["feature_id"], {"id": data["feature_id"]})
            feature.update(data)
        elif kind == "feature.assigned":
            result["features"].setdefault(data["feature_id"], {"id": data["feature_id"]})["assignee_id"] = data["person_id"]
        elif kind == "task.created":
            result["tasks"].setdefault(data["task_id"], {"id": data["task_id"]}).update(data)
        elif kind in {"task.status_changed", "task.assigned"}:
            task = result["tasks"].setdefault(data["task_id"], {"id": data["task_id"]})
            if kind == "task.status_changed":
                task["status"] = data["status"]
                if data.get("update"):
                    task["latest_update"] = data["update"]
            else:
                task["assignee_id"] = data.get("person_id")
        elif kind == "task.update_reported":
            task = result["tasks"].setdefault(data["task_id"], {"id": data["task_id"]})
            task["latest_update"] = data.get("note")
            task["reported_by"] = data.get("reported_by")
        elif kind == "person.update_reported":
            result["team_updates"].append(data)
        elif kind == "pull_request.linked":
            result["pull_requests"].append(data)
        elif kind in {"task.claim_requested", "task.assignment_proposed"}:
            result["assignment_requests"].append({**data, "kind": kind, "event_id": event["event_id"]})
        elif kind == "task.request_resolved":
            result["assignment_requests"] = [request for request in result["assignment_requests"]
                                              if request["event_id"] != data["request_event_id"]]
    return result


def read_config(root: Path) -> dict[str, Any]:
    return json.loads((root / ".tracker" / "tracker.json").read_text())
