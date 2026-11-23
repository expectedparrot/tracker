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
    result: dict[str, Any] = {"people": {}, "repos": {}, "tasks": {},
                              "pull_requests": [], "assignment_requests": [], "team_updates": []}
    for event in events(root):
        kind, data = event["kind"], event["data"]
        if kind in {"person.created", "person.updated", "person.deactivated"}:
            person = result["people"].setdefault(data["person_id"], {"id": data["person_id"]})
            person.update(data)
            if kind in {"person.created", "person.deactivated"}:
                person["active"] = kind == "person.created"
        elif kind == "repo.linked":
            result["repos"].setdefault(data["repo_id"], {"id": data["repo_id"]}).update(
                {key: value for key, value in data.items() if key != "project_id"})
        elif kind == "task.created":
            task_fields = {"task_id", "title", "kind", "assignee_id", "repo_ids", "status", "description",
                "related_urls", "reported_by", "reporter_email", "source", "intake_round", "intake_response_key"}
            result["tasks"].setdefault(data["task_id"], {"id": data["task_id"]}).update(
                {key: value for key, value in data.items() if key in task_fields})
        elif kind in {"task.status_changed", "task.assigned"}:
            task = result["tasks"].setdefault(data["task_id"], {"id": data["task_id"]})
            if kind == "task.status_changed":
                task["status"] = data["status"]
                task["completion_suspected"] = False
                if data.get("update"):
                    task["latest_update"] = data["update"]
            else:
                task["assignee_id"] = data.get("person_id")
                if task.get("priority_rank_assignee_id") != data.get("person_id"):
                    task.pop("priority_rank", None)
                    task.pop("priority_rank_assignee_id", None)
                    task.pop("priority_ranked_by", None)
                    task.pop("priority_round", None)
        elif kind == "task.priority_ranked":
            task = result["tasks"].setdefault(data["task_id"], {"id": data["task_id"]})
            task["priority_rank"] = data["rank"]
            task["priority_rank_assignee_id"] = data["person_id"]
            task["priority_ranked_by"] = data.get("ranked_by")
            task["priority_round"] = data.get("ranking_round")
        elif kind == "task.completion_suspected":
            task = result["tasks"].setdefault(data["task_id"], {"id": data["task_id"]})
            task["completion_suspected"] = True
            task["completion_suspected_by"] = data.get("reviewer_id")
            task["completion_suspected_round"] = data.get("assignment_round")
        elif kind == "task.merged":
            task = result["tasks"].setdefault(data["task_id"], {"id": data["task_id"]})
            task["status"] = "merged"
            task["merged_into"] = data["target_task_id"]
            task["merged_by"] = data.get("reviewer_id")
        elif kind == "task.assignment_deferred":
            task = result["tasks"].setdefault(data["task_id"], {"id": data["task_id"]})
            task["last_assignment_review"] = data.get("assignment_round")
        elif kind == "task.update_reported":
            task = result["tasks"].setdefault(data["task_id"], {"id": data["task_id"]})
            task["latest_update"] = data.get("note")
            task["reported_by"] = data.get("reported_by")
        elif kind == "person.update_reported":
            result["team_updates"].append(data)
        elif kind == "pull_request.linked":
            if data.get("task_id"):
                result["pull_requests"].append(data)
                result["tasks"].setdefault(data["task_id"], {"id": data["task_id"]}).setdefault("pull_requests", []).append(data)
        elif kind in {"task.claim_requested", "task.assignment_proposed"}:
            result["assignment_requests"].append({**data, "kind": kind, "event_id": event["event_id"]})
        elif kind == "task.request_resolved":
            result["assignment_requests"] = [request for request in result["assignment_requests"]
                                              if request["event_id"] != data["request_event_id"]]
    return result


def read_config(root: Path) -> dict[str, Any]:
    return json.loads((root / ".tracker" / "tracker.json").read_text())
