"""Agent-first CLI for creating and maintaining team trackers."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import click

from . import __version__
from .store import TrackerError, append_event, events, read_config, slug, state, tracker_root, validate_id


class TrackerGroup(click.Group):
    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except TrackerError as exc:
            fail(ctx.command_path, "validation_error", str(exc))


def emit(command: str, data: dict[str, Any], status: str = "ok", warnings: list[str] | None = None) -> None:
    payload_data = data.copy()
    artifacts = payload_data.pop("artifacts", [])
    next_steps = payload_data.pop("next_steps", [])
    message_warnings = payload_data.pop("warnings", warnings or [])
    click.echo(json.dumps({
        "schema_version": "1.0", "command": command, "status": status, "data": payload_data,
        "artifacts": artifacts, "warnings": message_warnings, "errors": [], "next_steps": next_steps,
    }))


def fail(command: str, code: str, message: str, hint: str = "") -> None:
    click.echo(json.dumps({
        "schema_version": "1.0", "command": command, "status": "error", "data": {},
        "artifacts": [], "warnings": [], "errors": [{"code": code, "message": message, "hint": hint}],
        "next_steps": [],
    }))
    raise click.exceptions.Exit(1)


def root_or_fail(command: str, directory: Path) -> Path:
    root = tracker_root(directory)
    if root is None:
        fail(command, "tracker_not_found", f"No tracker found from {directory.resolve()}.", "Run `tracker init <directory> --name <team>`.")
    return root


def check_id(identifier: str, collection: dict[str, Any], label: str) -> None:
    if identifier not in collection:
        raise TrackerError(f"Unknown {label} ID: {identifier}")


def task_project_id(task: dict[str, Any], features: dict[str, Any]) -> str | None:
    """Return a task's explicit or feature-inherited project, if any."""
    if task.get("project_id"):
        return task["project_id"]
    feature = features.get(task.get("feature_id"), {})
    return feature.get("project_id")


def github_repo_slug(repo_url: str) -> str:
    """Convert a repository URL into the OWNER/REPO form accepted by gh."""
    if "://" not in repo_url and ":" in repo_url:
        host, path = repo_url.rsplit(":", 1)
        host = host.rsplit("@", 1)[-1]
    else:
        parsed = urlsplit(repo_url)
        host = parsed.hostname or ""
        path = parsed.path
    parts = [part for part in path.strip("/").removesuffix(".git").split("/") if part]
    if not host or len(parts) != 2:
        raise TrackerError(f"Repository URL must identify a GitHub owner and repository: {repo_url}")
    return "/".join(parts) if host.lower() == "github.com" else f"{host}/{parts[0]}/{parts[1]}"


@click.group(cls=TrackerGroup)
@click.option("-C", "directory", type=click.Path(path_type=Path, file_okay=False), default=Path.cwd())
@click.pass_context
def main(ctx: click.Context, directory: Path) -> None:
    """Create and operate a Git-backed team tracker."""
    ctx.ensure_object(dict)
    ctx.obj.update(directory=directory)


@main.command("init")
@click.argument("directory", required=False, type=click.Path(path_type=Path, file_okay=False))
@click.option("--name", required=True, help="Team name.")
@click.option("--team-id", help="Stable team ID; defaults to a slug of --name.")
@click.option("--git-init/--no-git-init", default=True, help="Initialize a Git repository in the new tracker directory.")
@click.pass_context
def init_tracker(ctx: click.Context, directory: Path | None, name: str, team_id: str | None, git_init: bool) -> None:
    """Initialize a team tracker in a directory (usually a dedicated Git repo)."""
    target = (directory or ctx.obj["directory"]).resolve()
    if (target / ".tracker").exists() and not (target / ".tracker" / "tracker.json").is_file():
        fail("tracker init", "tracker_directory_conflict", f"{target / '.tracker'} already exists without a tracker configuration.")
    if (target / ".tracker" / "tracker.json").exists():
        fail("tracker init", "tracker_already_exists", f"A tracker already exists at {target}.")
    target.mkdir(parents=True, exist_ok=True)
    (target / ".tracker" / "events").mkdir(parents=True, exist_ok=True)
    config = {"schema_version": "ep-tracker/1.0", "team_id": slug(team_id or name), "team_name": name}
    (target / ".tracker" / "tracker.json").write_text(json.dumps(config, indent=2) + "\n")
    ignore_path = target / ".gitignore"
    ignore_text = ignore_path.read_text() if ignore_path.exists() else ""
    if ignore_text and not ignore_text.endswith("\n"):
        ignore_text += "\n"
    for entry in (".tracker/private/", "__pycache__/", "*.py[cod]"):
        if entry not in ignore_text.splitlines():
            ignore_text += entry + "\n"
    ignore_path.write_text(ignore_text)
    guidance = Path(__file__).parent / "templates" / "AGENTS.md"
    team_guidance = target / "AGENTS.md"
    guidance_warning = []
    if team_guidance.exists():
        guidance_warning.append("Existing AGENTS.md was preserved; see the packaged tracker guidance template if you want to merge updates.")
    else:
        team_guidance.write_text(guidance.read_text())
    append_event(target, "team.created", config)
    if git_init:
        subprocess.run(["git", "init", str(target)], check=True, capture_output=True, text=True)
    emit("tracker init", {"tracker": config, "path": str(target), "warnings": guidance_warning, "next_steps": [
        f"Add team members with `tracker person add --id <handle> --name <name>` from {target}.",
        "Add projects and repositories, then create features and tasks.",
    ]})


@main.command("guide")
def guide() -> None:
    """Describe the tracker lifecycle and important command boundaries."""
    emit("tracker guide", {"lifecycle": [
        "tracker init → people/projects/repos → features/tasks → daily memo",
        "updates prepare → deploy → collect → apply task status updates",
        "assignments prepare → deploy → collect → apply the reviewer's task assignments",
        "triage prepare → deploy → collect → plan recommendations → agent verifies and runs gh issue actions",
    ], "storage": "Team state is derived from immutable event files under .tracker/events/.",
        "agent_guidance": ["Run `tracker agent next` before acting and after each material change.",
            "Use CLI commands for state changes; do not edit tracker JSON or event files directly.",
            "Keep .tracker/private/ out of Git and share respondent links only with their named recipients."],
        "next_steps": ["Run `tracker agent next` to inspect the current tracker and recommended action.",
            "Run `tracker survey guide` to explain survey options or `tracker survey list` to inspect existing rounds."]})


@main.command("next")
@click.pass_context
def next_command(ctx: click.Context) -> None:
    """Alias for `tracker agent next`."""
    agent_next(ctx, "tracker next")


@main.group("agent")
def agent_group() -> None:
    """Agent workflow guidance."""


def agent_next(ctx: click.Context, command: str = "tracker agent next") -> None:
    root = tracker_root(ctx.obj["directory"])
    if root is None:
        emit(command, {"stage": "setup", "action": {"kind": "team_input", "prompt": "Ask for the team name and tracker directory."},
            "recommendation": "Initialize the team's tracker.",
            "next_steps": ["Ask the user for the team name and tracker directory.",
                "Run `tracker init <directory> --name <team>`."]})
        return
    current = state(root)
    history = events(root)
    counts = {name: len(current[name]) for name in ("people", "projects", "repos", "features", "tasks")}
    planned_triage_rounds = {event["data"].get("round_id") for event in history
        if event["kind"] == "issue_triage.planned"}
    pending_triage_rounds = sorted({event["data"].get("round_id") for event in history
        if event["kind"] == "issue_triage.collected" and event["data"].get("round_id")}
        - planned_triage_rounds)
    if not current["people"]:
        stage, recommendation = "setup-people", "Register the team's engineers and other task owners."
        steps = ["Collect each person's stable ID, display name, and optional GitHub handle.",
                 "Run `tracker person add --id <id> --name <name> [--github <handle>]` for each person."]
    elif not current["projects"]:
        stage, recommendation = "setup-projects", "Add projects and the repositories they span."
        steps = ["Add a project with `tracker project add --id <id> --name <name>`.",
                 "Link each code repository with `tracker project repo --id <repo-id> --project <project-id> --url <url>`."]
    elif not current["repos"]:
        stage, recommendation = "link-repositories", "Link the code repositories this team tracks."
        steps = ["Inspect projects with `tracker project list`.",
                 "Link repositories with `tracker project repo --id <repo-id> --project <project-id> --url <url>`."]
    elif current["assignment_requests"]:
        stage, recommendation = "review-assignments", "Review pending task claims and assignment proposals."
        steps = ["Inspect requests with `tracker task requests`.",
                 "Resolve each request with `tracker task resolve-request <event-id> --decision accept|reject`."]
    elif pending_triage_rounds:
        stage, recommendation = "process-issue-triage", "Review the collected GitHub issue triage recommendations."
        steps = [f"Build the agent handoff with `tracker triage plan {pending_triage_rounds[-1]}`.",
                 "Verify each issue's live state and selected target, then execute appropriate GitHub actions."]
    elif not current["features"]:
        stage, recommendation = "setup-features", "Create the team's first feature and its tasks."
        steps = ["Create a feature with `tracker feature create --id <id> --project <project-id> --title <title>`.",
                 "Add build, review, or approval work with `tracker task create`."]
    elif any(task.get("completion_suspected") for task in current["tasks"].values()):
        stage, recommendation = "verify-completion", "Check tasks the assignment reviewer thinks may already be complete."
        suspected_ids = [task["id"] for task in current["tasks"].values() if task.get("completion_suspected")]
        steps = [f"Review suspected completions with `tracker task list` (task IDs: {', '.join(suspected_ids)}).",
                 "After checking the work, confirm each with `tracker task status <task-id> done` or return it to `open`."]
    elif any(not task.get("assignee_id") and task.get("status") not in {"done", "cancelled"}
             for task in current["tasks"].values()):
        stage, recommendation = "unassigned-work", "Prepare an assignment survey for the unassigned tasks."
        steps = ["Review unassigned work with `tracker status`.",
                 "Choose an authorized reviewer, then run `tracker assignments prepare --id <round-id> --reviewer <person-id>`. "]
    else:
        today = date.today().isoformat()
        memo_exists = (root / "memos" / f"{today}.md").exists()
        if not memo_exists:
            stage, recommendation = "daily-review", "Build today's memo for the team review."
            steps = [f"Run `tracker memo build --date {today}`."]
        else:
            stage, recommendation = "current", "Tracker state is current; review recent activity or start the next update round."
            steps = ["Inspect survey rounds with `tracker survey list` and recent events with `tracker history`.",
                     "Start the next status round with `tracker updates prepare --id <round-id>` or assignment round with `tracker assignments prepare --id <round-id> --reviewer <person-id>`. "]
    emit(command, {"stage": stage, "tracker": read_config(root), "counts": counts,
        "recommendation": recommendation, "next_steps": steps})


@agent_group.command("next")
@click.pass_context
def agent_next_command(ctx: click.Context) -> None:
    """Inspect tracker state and return executable agent guidance."""
    agent_next(ctx)


@main.command("status")
@click.pass_context
def status_command(ctx: click.Context) -> None:
    """Show a compact current view of team work."""
    root = root_or_fail("tracker status", ctx.obj["directory"])
    current = state(root)
    feature_counts: dict[str, int] = {}
    task_counts: dict[str, int] = {}
    for item in current["features"].values():
        key = item.get("status", "planned")
        feature_counts[key] = feature_counts.get(key, 0) + 1
    for item in current["tasks"].values():
        key = item.get("status", "open")
        task_counts[key] = task_counts.get(key, 0) + 1
    emit("tracker status", {"team": read_config(root), "feature_counts": feature_counts,
        "task_counts": task_counts, "unassigned_tasks": [item for item in current["tasks"].values()
            if not item.get("assignee_id") and item.get("status") not in {"done", "cancelled", "merged"}],
        "completion_suspected": [item for item in current["tasks"].values() if item.get("completion_suspected")],
        "assignment_requests": current["assignment_requests"]})


@main.command("history")
@click.option("--limit", type=click.IntRange(min=1), default=50)
@click.pass_context
def history_command(ctx: click.Context, limit: int) -> None:
    """Inspect the most recent append-only events."""
    root = root_or_fail("tracker history", ctx.obj["directory"])
    emit("tracker history", {"events": events(root)[-limit:]})


@main.group("person")
def person_group() -> None:
    """Manage team people."""


@person_group.command("add")
@click.option("--id", "person_id", required=True)
@click.option("--name", "display_name", required=True)
@click.option("--github")
@click.option("--email")
@click.option("--role", default="engineer", show_default=True, help="Team role; assignment surveys offer active engineers.")
@click.pass_context
def person_add(ctx: click.Context, person_id: str, display_name: str, github: str | None, email: str | None, role: str) -> None:
    root = root_or_fail("tracker person add", ctx.obj["directory"])
    validate_id(person_id, "Person ID")
    current = state(root)
    if person_id in current["people"]:
        fail("tracker person add", "person_exists", f"Person {person_id} already exists.", "Use `tracker person update` to add a new profile event.")
    data = {"person_id": person_id, "display_name": display_name, "github": github, "email": email, "role": role}
    append_event(root, "person.created", data)
    emit("tracker person add", {"person": {**data, "active": True}})


@person_group.command("list")
@click.pass_context
def person_list(ctx: click.Context) -> None:
    root = root_or_fail("tracker person list", ctx.obj["directory"])
    emit("tracker person list", {"people": list(state(root)["people"].values())})


@person_group.command("update")
@click.argument("person_id")
@click.option("--name", "display_name")
@click.option("--github")
@click.option("--email")
@click.option("--role")
@click.pass_context
def person_update(ctx: click.Context, person_id: str, display_name: str | None, github: str | None, email: str | None, role: str | None) -> None:
    root = root_or_fail("tracker person update", ctx.obj["directory"])
    person = state(root)["people"].get(person_id)
    if person is None:
        raise TrackerError(f"Unknown person ID: {person_id}")
    changes = {key: value for key, value in {"display_name": display_name, "github": github, "email": email, "role": role}.items() if value is not None}
    if not changes:
        raise TrackerError("Provide at least one of --name, --github, --email, or --role")
    append_event(root, "person.updated", {"person_id": person_id, **changes})
    emit("tracker person update", {"person": {**person, **changes}})


@person_group.command("deactivate")
@click.argument("person_id")
@click.pass_context
def person_deactivate(ctx: click.Context, person_id: str) -> None:
    root = root_or_fail("tracker person deactivate", ctx.obj["directory"])
    check_id(person_id, state(root)["people"], "person")
    append_event(root, "person.deactivated", {"person_id": person_id})
    emit("tracker person deactivate", {"person_id": person_id, "active": False})


@main.group("project")
def project_group() -> None:
    """Manage projects and participating repositories."""


@project_group.command("add")
@click.option("--id", "project_id", required=True)
@click.option("--name", required=True)
@click.pass_context
def project_add(ctx: click.Context, project_id: str, name: str) -> None:
    root = root_or_fail("tracker project add", ctx.obj["directory"])
    validate_id(project_id, "Project ID")
    if project_id in state(root)["projects"]:
        fail("tracker project add", "project_exists", f"Project {project_id} already exists.")
    append_event(root, "project.created", {"project_id": project_id, "name": name})
    emit("tracker project add", {"project": {"id": project_id, "name": name}})


@project_group.command("repo")
@click.option("--id", "repo_id", required=True)
@click.option("--project", "project_id", required=True)
@click.option("--url", required=True)
@click.pass_context
def repo_add(ctx: click.Context, repo_id: str, project_id: str, url: str) -> None:
    root = root_or_fail("tracker project repo", ctx.obj["directory"])
    validate_id(repo_id, "Repository ID")
    current = state(root)
    check_id(project_id, current["projects"], "project")
    if repo_id in current["repos"]:
        fail("tracker project repo", "repo_exists", f"Repository {repo_id} already exists.")
    data = {"repo_id": repo_id, "project_id": project_id, "url": url}
    append_event(root, "repo.linked", data)
    emit("tracker project repo", {"repo": data})


@project_group.command("list")
@click.pass_context
def project_list(ctx: click.Context) -> None:
    root = root_or_fail("tracker project list", ctx.obj["directory"])
    current = state(root)
    projects = []
    for project in current["projects"].values():
        projects.append({**project, "repositories": [repo for repo in current["repos"].values() if repo["project_id"] == project["id"]]})
    emit("tracker project list", {"projects": projects})


@main.group("feature")
def feature_group() -> None:
    """Create and update features."""


@feature_group.command("create")
@click.option("--id", "feature_id", required=True)
@click.option("--project", "project_id", required=True)
@click.option("--title", required=True)
@click.option("--assignee")
@click.pass_context
def feature_create(ctx: click.Context, feature_id: str, project_id: str, title: str, assignee: str | None) -> None:
    root = root_or_fail("tracker feature create", ctx.obj["directory"])
    validate_id(feature_id, "Feature ID")
    current = state(root)
    check_id(project_id, current["projects"], "project")
    if feature_id in current["features"]:
        fail("tracker feature create", "feature_exists", f"Feature {feature_id} already exists.")
    if assignee:
        check_id(assignee, current["people"], "person")
    data = {"feature_id": feature_id, "project_id": project_id, "title": title, "status": "planned", "assignee_id": assignee}
    append_event(root, "feature.created", data)
    feature_dir = root / "projects" / project_id / "features" / feature_id
    feature_dir.mkdir(parents=True, exist_ok=True)
    readme_path = feature_dir / "README.md"
    if not readme_path.exists():
        readme_path.write_text(f"# {title}\n\n")
    (feature_dir / "images").mkdir(exist_ok=True)
    emit("tracker feature create", {"feature": data, "artifacts": [str(readme_path), str(feature_dir / "images")]})


@feature_group.command("list")
@click.option("--project", "project_id")
@click.option("--status")
@click.pass_context
def feature_list(ctx: click.Context, project_id: str | None, status: str | None) -> None:
    root = root_or_fail("tracker feature list", ctx.obj["directory"])
    current = state(root)
    features = list(current["features"].values())
    if project_id:
        features = [item for item in features if item.get("project_id") == project_id]
    if status:
        features = [item for item in features if item.get("status") == status]
    for feature in features:
        feature["pull_requests"] = [pr for pr in current["pull_requests"] if pr["feature_id"] == feature["id"]]
        feature["tasks"] = [task for task in current["tasks"].values() if task["feature_id"] == feature["id"]]
    emit("tracker feature list", {"features": features})


@feature_group.command("status")
@click.argument("feature_id")
@click.argument("status", type=click.Choice(["planned", "in_progress", "blocked", "done", "cancelled"]))
@click.pass_context
def feature_status(ctx: click.Context, feature_id: str, status: str) -> None:
    root = root_or_fail("tracker feature status", ctx.obj["directory"])
    check_id(feature_id, state(root)["features"], "feature")
    append_event(root, "feature.status_changed", {"feature_id": feature_id, "status": status})
    emit("tracker feature status", {"feature_id": feature_id, "status": status})


@feature_group.command("assign")
@click.argument("feature_id")
@click.argument("person_id")
@click.pass_context
def feature_assign(ctx: click.Context, feature_id: str, person_id: str) -> None:
    root = root_or_fail("tracker feature assign", ctx.obj["directory"])
    current = state(root)
    check_id(feature_id, current["features"], "feature")
    check_id(person_id, current["people"], "person")
    append_event(root, "feature.assigned", {"feature_id": feature_id, "person_id": person_id})
    emit("tracker feature assign", {"feature_id": feature_id, "person_id": person_id})


@feature_group.command("pr")
@click.argument("feature_id")
@click.option("--repo", "repo_id", required=True)
@click.option("--url", required=True)
@click.option("--number")
@click.pass_context
def feature_pr(ctx: click.Context, feature_id: str, repo_id: str, url: str, number: str | None) -> None:
    root = root_or_fail("tracker feature pr", ctx.obj["directory"])
    current = state(root)
    check_id(feature_id, current["features"], "feature")
    check_id(repo_id, current["repos"], "repo")
    if current["features"][feature_id]["project_id"] != current["repos"][repo_id]["project_id"]:
        raise TrackerError(f"Repository {repo_id} does not belong to feature {feature_id}'s project")
    data = {"feature_id": feature_id, "repo_id": repo_id, "url": url, "number": number}
    append_event(root, "pull_request.linked", data)
    emit("tracker feature pr", {"pull_request": data})


@main.group("task")
def task_group() -> None:
    """Create and update work or review tasks."""


@task_group.command("create")
@click.option("--id", "task_id", required=True)
@click.option("--feature", "feature_id", help="Optional feature this task belongs to.")
@click.option("--project", "project_id", help="Optional project for a task without a feature.")
@click.option("--title", required=True)
@click.option("--kind", type=click.Choice(["build", "review", "approve", "other"]), default="build")
@click.option("--assignee")
@click.pass_context
def task_create(ctx: click.Context, task_id: str, feature_id: str | None, project_id: str | None,
                title: str, kind: str, assignee: str | None) -> None:
    root = root_or_fail("tracker task create", ctx.obj["directory"])
    validate_id(task_id, "Task ID")
    current = state(root)
    if task_id in current["tasks"]:
        fail("tracker task create", "task_exists", f"Task {task_id} already exists.")
    if feature_id:
        check_id(feature_id, current["features"], "feature")
        feature_project = current["features"][feature_id].get("project_id")
        if project_id and project_id != feature_project:
            raise TrackerError(f"Feature {feature_id} belongs to project {feature_project}, not {project_id}")
        project_id = feature_project
    elif project_id:
        check_id(project_id, current["projects"], "project")
    if assignee:
        check_id(assignee, current["people"], "person")
    data = {"task_id": task_id, "feature_id": feature_id, "project_id": project_id,
            "title": title, "kind": kind, "assignee_id": assignee, "status": "open"}
    append_event(root, "task.created", data)
    emit("tracker task create", {"task": data})


@task_group.command("assign")
@click.argument("task_id")
@click.argument("person_id", required=False)
@click.pass_context
def task_assign(ctx: click.Context, task_id: str, person_id: str | None) -> None:
    root = root_or_fail("tracker task assign", ctx.obj["directory"])
    current = state(root)
    check_id(task_id, current["tasks"], "task")
    if current["tasks"][task_id].get("status") == "merged":
        raise TrackerError(f"Task {task_id} was merged into another task and cannot be reassigned")
    if current["tasks"][task_id].get("completion_suspected"):
        raise TrackerError(f"Verify whether task {task_id} is complete before assigning it")
    if person_id:
        check_id(person_id, current["people"], "person")
    append_event(root, "task.assigned", {"task_id": task_id, "person_id": person_id})
    emit("tracker task assign", {"task_id": task_id, "person_id": person_id})


@task_group.command("status")
@click.argument("task_id")
@click.argument("status", type=click.Choice(["open", "in_progress", "blocked", "done", "cancelled"]))
@click.option("--update", help="Short status note recorded with the event.")
@click.pass_context
def task_status(ctx: click.Context, task_id: str, status: str, update: str | None) -> None:
    root = root_or_fail("tracker task status", ctx.obj["directory"])
    current = state(root)
    check_id(task_id, current["tasks"], "task")
    if current["tasks"][task_id].get("status") == "merged":
        raise TrackerError(f"Task {task_id} was merged into another task; update the surviving task instead")
    append_event(root, "task.status_changed", {"task_id": task_id, "status": status, "update": update})
    emit("tracker task status", {"task_id": task_id, "status": status, "update": update})


@task_group.command("list")
@click.option("--assignee")
@click.option("--status")
@click.pass_context
def task_list(ctx: click.Context, assignee: str | None, status: str | None) -> None:
    root = root_or_fail("tracker task list", ctx.obj["directory"])
    tasks = list(state(root)["tasks"].values())
    if assignee:
        tasks = [item for item in tasks if item.get("assignee_id") == assignee]
    if status:
        tasks = [item for item in tasks if item.get("status") == status]
    emit("tracker task list", {"tasks": tasks})


@task_group.command("requests")
@click.pass_context
def task_requests(ctx: click.Context) -> None:
    root = root_or_fail("tracker task requests", ctx.obj["directory"])
    emit("tracker task requests", {"requests": state(root)["assignment_requests"]})


@task_group.command("accept-claim")
@click.argument("task_id")
@click.argument("person_id")
@click.pass_context
def task_accept_claim(ctx: click.Context, task_id: str, person_id: str) -> None:
    root = root_or_fail("tracker task accept-claim", ctx.obj["directory"])
    current = state(root)
    check_id(task_id, current["tasks"], "task")
    check_id(person_id, current["people"], "person")
    if current["tasks"][task_id].get("assignee_id"):
        raise TrackerError(f"Task {task_id} is already assigned")
    request = next((item for item in current["assignment_requests"]
                    if item["task_id"] == task_id and item["kind"] == "task.claim_requested"
                    and item.get("person_id") == person_id), None)
    if request is None:
        raise TrackerError(f"No pending claim from {person_id} for task {task_id}")
    append_event(root, "task.assigned", {"task_id": task_id, "person_id": person_id})
    append_event(root, "task.request_resolved", {"request_event_id": request["event_id"], "decision": "accepted"})
    emit("tracker task accept-claim", {"task_id": task_id, "person_id": person_id})


@task_group.command("resolve-request")
@click.argument("request_id")
@click.option("--decision", type=click.Choice(["accept", "reject"]), required=True)
@click.pass_context
def task_resolve_request(ctx: click.Context, request_id: str, decision: str) -> None:
    root = root_or_fail("tracker task resolve-request", ctx.obj["directory"])
    current = state(root)
    request = next((item for item in current["assignment_requests"] if item["event_id"] == request_id), None)
    if request is None:
        raise TrackerError(f"No pending assignment request with event ID {request_id}")
    if current["tasks"].get(request["task_id"], {}).get("assignee_id"):
        raise TrackerError(f"Task {request['task_id']} has already been assigned; resolve the request as rejected")
    person_id = request.get("person_id") if request["kind"] == "task.claim_requested" else request.get("proposed_person_id")
    if decision == "accept":
        check_id(person_id, current["people"], "person")
        append_event(root, "task.assigned", {"task_id": request["task_id"], "person_id": person_id})
    append_event(root, "task.request_resolved", {"request_event_id": request_id, "decision": "accepted" if decision == "accept" else "rejected"})
    emit("tracker task resolve-request", {"request_id": request_id, "decision": decision, "person_id": person_id if decision == "accept" else None})


@main.group("memo")
def memo_group() -> None:
    """Build a dated daily task and feature memo."""


@main.group("updates")
def updates_group() -> None:
    """Prepare and field Humanize status-update rounds."""


def edsl_types() -> tuple[Any, Any, Any, Any]:
    try:
        from edsl import QuestionFreeText, QuestionMultipleChoice, Results, Survey
    except ImportError as exc:
        raise TrackerError("EDSL is required for survey workflows; install the fielding extra") from exc
    return QuestionFreeText, QuestionMultipleChoice, Results, Survey


def response_answers(result_path: str, Results: Any) -> list[dict[str, Any]]:
    try:
        results = Results.git.load(result_path)
        return [dict(result.answer or {}) for result in results]
    except Exception as results_error:
        try:
            from edsl import ScenarioList
            scenarios = ScenarioList.git.load(result_path)
            answers = []
            for scenario in scenarios:
                raw = json.loads(scenario.get("response_json_string", "{}"))
                answers.append({name: value.get("answer") if isinstance(value, dict) else value
                                for name, value in raw.items()})
            return answers
        except Exception as scenario_error:
            raise TrackerError(f"Could not read EDSL response package {result_path}: {results_error}; {scenario_error}") from scenario_error


@main.group("assignments")
def assignments_group() -> None:
    """Prepare Humanize surveys that assign unassigned tasks to engineers."""


@assignments_group.command("prepare")
@click.option("--id", "round_id", required=True)
@click.option("--reviewer", "reviewer_id", required=True, help="Person who will review and submit this survey.")
@click.pass_context
def assignments_prepare(ctx: click.Context, round_id: str, reviewer_id: str) -> None:
    """Build one survey listing all unassigned tasks and available engineers."""
    root = root_or_fail("tracker assignments prepare", ctx.obj["directory"])
    _, QuestionMultipleChoice, _, Survey = edsl_types()
    if not round_id or any(not (character.isalnum() or character in "_-") for character in round_id):
        raise TrackerError("Round ID may contain only letters, numbers, underscores, and hyphens")
    current = state(root)
    check_id(reviewer_id, current["people"], "reviewer")
    if not current["people"][reviewer_id].get("active", True):
        raise TrackerError(f"Reviewer {reviewer_id} is inactive")
    engineers = [person for person in current["people"].values()
                 if person.get("active", True) and person.get("role", "engineer").lower() == "engineer"]
    if not engineers:
        raise TrackerError("Add or activate at least one person with the engineer role")
    unassigned = [task for task in current["tasks"].values()
                  if not task.get("assignee_id") and task.get("status") not in {"done", "cancelled", "merged"}
                  and not task.get("completion_suspected")]
    if not unassigned:
        raise TrackerError("There are no eligible unassigned tasks; verify suspected completions first")
    directory = root / ".tracker" / "assignments" / round_id
    if directory.exists():
        raise TrackerError(f"Assignment round already exists: {round_id}")
    directory.mkdir(parents=True)
    questions: list[Any] = []
    task_map: dict[str, Any] = {}
    for index, task in enumerate(unassigned):
        action_name = f"assignment_{index:04d}_action"
        engineer_name = f"assignment_{index:04d}_engineer"
        merge_name = f"assignment_{index:04d}_merge_target"
        feature = current["features"].get(task.get("feature_id"), {})
        source_project_id = task_project_id(task, current["features"])
        active_tasks = [candidate for candidate in current["tasks"].values()
            if candidate.get("status") not in {"done", "cancelled", "merged"}
            and not candidate.get("completion_suspected")
            and task_project_id(candidate, current["features"]) == source_project_id]
        engineer_choices = {"No engineer selected": None}
        for person in engineers:
            label = f"{person['id']} — {person.get('display_name', person['id'])}"
            engineer_choices[label] = person["id"]
        merge_targets = [candidate for candidate in active_tasks
                         if candidate["id"] != task["id"] and not candidate.get("completion_suspected")]
        merge_choices = {"No merge target selected": None}
        for target in merge_targets:
            target_feature = current["features"].get(target.get("feature_id"), {})
            target_context = (f"feature {target_feature['title']}" if target_feature else
                              f"project {task_project_id(target, current['features'])}" if task_project_id(target, current["features"])
                              else "standalone")
            target_label = f"{target['id']} — {target['title']} ({target_context})"
            merge_choices[target_label] = target["id"]
        actions = ["Assign to engineer", "Leave unassigned", "Probably already completed?"]
        if merge_targets:
            actions.append("Merge into another issue")
        task_context = (f"feature {feature['title']}" if feature else
                        f"project {source_project_id}" if source_project_id else "standalone task")
        context = (f"{task['title']} (task {task['id']}; {task_context}; "
                   f"kind {task['kind']}; status {task.get('status', 'open')}).")
        questions.append(QuestionMultipleChoice(question_name=action_name,
            question_text=f"{context} What should happen to this unassigned issue?", question_options=actions))
        questions.append(QuestionMultipleChoice(question_name=engineer_name,
            question_text="If assigning this issue, choose an engineer; otherwise choose no selection.",
            question_options=list(engineer_choices)))
        questions.append(QuestionMultipleChoice(question_name=merge_name,
            question_text="If merging this issue, choose the surviving issue; otherwise choose no selection.",
            question_options=list(merge_choices)))
        task_map[task["id"]] = {"action_question": action_name, "engineer_question": engineer_name,
            "engineer_choices": engineer_choices, "merge_question": merge_name, "merge_choices": merge_choices}
    survey = Survey(questions)
    survey_path = directory / "assignments.survey.ep"
    saved = survey.git.save(str(survey_path), message=f"Prepare task assignment survey {round_id}")
    Survey.git.load(saved["path"])
    prepared = {"schema_version": "ep-tracker.assignment-round/1.0", "round_id": round_id,
        "reviewer_id": reviewer_id, "created_at": datetime.now(UTC).isoformat(),
        "survey_path": str(survey_path.relative_to(root)), "tasks": task_map}
    (directory / "prepared.json").write_text(json.dumps(prepared, indent=2) + "\n")
    append_event(root, "assignments.prepared", {"round_id": round_id, "reviewer_id": reviewer_id,
        "task_ids": list(task_map)})
    emit("tracker assignments prepare", {"round_id": round_id, "reviewer_id": reviewer_id,
        "task_count": len(task_map), "survey_path": str(survey_path), "artifacts": [str(directory)],
        "next_steps": ["Review the generated assignment survey.", f"Deploy it with `tracker assignments deploy {round_id}`."]})


@assignments_group.command("deploy")
@click.argument("round_id")
@click.pass_context
def assignments_deploy(ctx: click.Context, round_id: str) -> None:
    """Deploy an assignment survey to Humanize."""
    root = root_or_fail("tracker assignments deploy", ctx.obj["directory"])
    _, _, _, Survey = edsl_types()
    try:
        from edsl import Coop
    except ImportError as exc:
        raise TrackerError("EDSL Humanize support is unavailable in this installation") from exc
    directory = root / ".tracker" / "assignments" / round_id
    prepared_path = directory / "prepared.json"
    if not prepared_path.is_file():
        raise TrackerError(f"No prepared assignment round named {round_id}")
    private = root / ".tracker" / "private" / "assignments" / round_id
    private.mkdir(parents=True, exist_ok=True)
    deployed_path = private / "deployed.json"
    if deployed_path.exists():
        raise TrackerError(f"Assignment round {round_id} was already deployed")
    prepared = json.loads(prepared_path.read_text())
    reviewer = state(root)["people"][prepared["reviewer_id"]]
    survey = Survey.git.load(str(root / prepared["survey_path"]))
    optional_questions = {}
    for item in prepared["tasks"].values():
        optional_questions[item["engineer_question"]] = {"optional": True}
        optional_questions[item["merge_question"]] = {"optional": True}
    try:
        info = survey.humanize(human_survey_name=f"{read_config(root)['team_name']} task allocation {round_id}",
            survey_description=f"Assign unassigned team tasks; reviewer: {reviewer.get('display_name', reviewer['id'])}.",
            survey_visibility="private", humanize_schema={"questions": optional_questions})
    except Exception as exc:
        raise TrackerError(f"Humanize deployment failed for assignment round {round_id}: {exc}") from exc
    deployment = {key: info[key] for key in ("uuid", "respondent_url", "preview_url", "admin_url") if key in info}
    deployed_path.write_text(json.dumps(deployment, indent=2) + "\n")
    append_event(root, "assignments.deployed", {"round_id": round_id, "reviewer_id": prepared["reviewer_id"]})
    emit("tracker assignments deploy", {"round_id": round_id, "reviewer_id": prepared["reviewer_id"],
        "deployment": deployment, "warnings": ["The respondent URL is sensitive; send it only to the named reviewer."],
        "next_steps": [f"After submission, run `tracker assignments collect {round_id}`."]})


@assignments_group.command("collect")
@click.argument("round_id")
@click.pass_context
def assignments_collect(ctx: click.Context, round_id: str) -> None:
    """Fetch the assignment survey response into the private Results area."""
    root = root_or_fail("tracker assignments collect", ctx.obj["directory"])
    edsl_types()
    try:
        from edsl import Coop
    except ImportError as exc:
        raise TrackerError("EDSL Humanize support is unavailable in this installation") from exc
    private = root / ".tracker" / "private" / "assignments" / round_id
    deployed_path = private / "deployed.json"
    if not deployed_path.is_file():
        raise TrackerError(f"No deployed assignment survey found for {round_id}")
    if (private / "collected.json").exists():
        raise TrackerError(f"Responses for assignment round {round_id} were already collected")
    deployment = json.loads(deployed_path.read_text())
    try:
        results = Coop().get_human_survey_responses(deployment["uuid"])
    except Exception as exc:
        raise TrackerError(f"Could not collect assignment survey responses: {exc}") from exc
    saved = results.git.save(str(private / "assignments.results.ep"), message=f"Collect task assignment survey {round_id}")
    (private / "collected.json").write_text(json.dumps({"result_path": saved["path"]}, indent=2) + "\n")
    append_event(root, "assignments.collected", {"round_id": round_id})
    emit("tracker assignments collect", {"round_id": round_id,
        "next_steps": [f"Apply the reviewer's selections with `tracker assignments apply {round_id}`."]})


@assignments_group.command("apply")
@click.argument("round_id")
@click.pass_context
def assignments_apply(ctx: click.Context, round_id: str) -> None:
    """Apply the reviewer's selected engineer for each still-unassigned task."""
    root = root_or_fail("tracker assignments apply", ctx.obj["directory"])
    _, _, Results, _ = edsl_types()
    directory = root / ".tracker" / "assignments" / round_id
    private = root / ".tracker" / "private" / "assignments" / round_id
    prepared_path = directory / "prepared.json"
    collected_path, applied_path = private / "collected.json", private / "applied.json"
    if not prepared_path.is_file() or not collected_path.is_file():
        raise TrackerError(f"Assignment round {round_id} must be prepared and collected before applying")
    if applied_path.exists():
        raise TrackerError(f"Assignment round {round_id} was already applied")
    prepared = json.loads(prepared_path.read_text())
    collected = json.loads(collected_path.read_text())
    answers = response_answers(collected["result_path"], Results)
    if len(answers) != 1:
        raise TrackerError(f"Expected exactly one reviewer response; found {len(answers)}")
    answer = answers[0]
    current = state(root)
    reviewer = current["people"].get(prepared["reviewer_id"])
    if reviewer is None or not reviewer.get("active", True):
        raise TrackerError(f"Reviewer {prepared['reviewer_id']} is no longer active")
    outcome_kinds = {"task.assigned": "assigned", "task.completion_suspected": "probably_completed",
        "task.merged": "merged", "task.assignment_deferred": "left_unassigned"}
    existing_outcomes = [event for event in events(root)
        if event["kind"] in outcome_kinds and event["data"].get("assignment_round") == round_id]
    existing_task_ids = {event["data"].get("task_id") for event in existing_outcomes}
    outcomes = [{"task_id": event["data"]["task_id"], "disposition": outcome_kinds[event["kind"]],
        **({"person_id": event["data"].get("person_id")} if event["kind"] == "task.assigned" else {}),
        **({"target_task_id": event["data"].get("target_task_id")} if event["kind"] == "task.merged" else {})}
        for event in existing_outcomes]
    current_tasks = state(root)["tasks"]
    warnings: list[str] = []
    for task_id, item in prepared["tasks"].items():
        if task_id in existing_task_ids:
            continue
        task = current_tasks.get(task_id)
        if task is None:
            warnings.append(f"Task {task_id} no longer exists")
            continue
        if task.get("assignee_id"):
            warnings.append(f"Task {task_id} has been assigned since the survey was prepared")
            continue
        if "action_question" not in item:
            # Read assignment surveys prepared by the first tracker release.
            person_id = item.get("choices", {}).get(answer.get(item.get("question")))
            action = "Assign to engineer" if person_id else "Leave unassigned"
        else:
            action = answer.get(item["action_question"])
            person_id = item.get("engineer_choices", {}).get(answer.get(item["engineer_question"]))
        outcome: dict[str, Any] | None = None
        if action == "Assign to engineer":
            person = current["people"].get(person_id)
            if person is None or not person.get("active", True) or person.get("role", "engineer").lower() != "engineer":
                warnings.append(f"Selected engineer {person_id} is no longer active or has a different role")
                continue
            append_event(root, "task.assigned", {"task_id": task_id, "person_id": person_id,
                "assigned_by": prepared["reviewer_id"], "source": "humanize_assignment_survey", "assignment_round": round_id})
            current_tasks[task_id]["assignee_id"] = person_id
            outcome = {"task_id": task_id, "disposition": "assigned", "person_id": person_id}
        elif action == "Probably already completed?":
            append_event(root, "task.completion_suspected", {"task_id": task_id,
                "reviewer_id": prepared["reviewer_id"], "assignment_round": round_id})
            current_tasks[task_id]["completion_suspected"] = True
            outcome = {"task_id": task_id, "disposition": "probably_completed"}
        elif action == "Merge into another issue":
            target_id = item.get("merge_choices", {}).get(answer.get(item["merge_question"]))
            target = current_tasks.get(target_id)
            if target is None or target_id == task_id or target.get("status") in {"done", "cancelled", "merged"}:
                warnings.append(f"No valid surviving issue was selected for task {task_id}")
                continue
            source_project_id = task_project_id(task, current["features"])
            target_project_id = task_project_id(target, current["features"])
            if source_project_id != target_project_id:
                warnings.append(f"Cannot merge task {task_id} into a task in another project")
                continue
            append_event(root, "task.merged", {"task_id": task_id, "target_task_id": target_id,
                "reviewer_id": prepared["reviewer_id"], "assignment_round": round_id})
            current_tasks[task_id]["status"] = "merged"
            current_tasks[task_id]["merged_into"] = target_id
            outcome = {"task_id": task_id, "disposition": "merged", "target_task_id": target_id}
        elif action == "Leave unassigned":
            append_event(root, "task.assignment_deferred", {"task_id": task_id,
                "reviewer_id": prepared["reviewer_id"], "assignment_round": round_id})
            outcome = {"task_id": task_id, "disposition": "left_unassigned"}
        else:
            warnings.append(f"No valid disposition was selected for task {task_id}")
            continue
        outcomes.append(outcome)
        existing_task_ids.add(task_id)
    final_state = state(root)
    assignments = [item for item in outcomes if item["disposition"] == "assigned"]
    result = {"round_id": round_id, "reviewer_id": prepared["reviewer_id"], "assignments": assignments,
        "outcomes": outcomes,
        "outcome_counts": {disposition: sum(item["disposition"] == disposition for item in outcomes)
            for disposition in ("assigned", "left_unassigned", "probably_completed", "merged")},
        "unassigned_count": sum(1 for task_id in prepared["tasks"]
            if task_id in final_state["tasks"] and not final_state["tasks"][task_id].get("assignee_id")
            and final_state["tasks"][task_id].get("status") not in {"done", "cancelled", "merged"}),
        "warnings": warnings, "applied_at": datetime.now(UTC).isoformat()}
    applied_path.write_text(json.dumps(result, indent=2) + "\n")
    append_event(root, "assignments.applied", {"round_id": round_id, "reviewer_id": prepared["reviewer_id"],
        "assignments": assignments, "outcomes": outcomes, "unassigned_count": result["unassigned_count"]})
    emit("tracker assignments apply", {**result, "next_steps": ["Review ownership with `tracker status` or build a memo."]})


@main.group("triage")
def triage_group() -> None:
    """Survey GitHub issue triage and prepare recommendations for an agent."""


@triage_group.command("prepare")
@click.option("--id", "round_id", required=True)
@click.option("--reviewer", "reviewer_id", required=True)
@click.option("--project", "project_id", help="Limit the scan to one tracker project.")
@click.option("--limit-per-repo", type=click.IntRange(min=1, max=1000), default=50, show_default=True)
@click.pass_context
def triage_prepare(ctx: click.Context, round_id: str, reviewer_id: str, project_id: str | None,
                   limit_per_repo: int) -> None:
    """Fetch open GitHub issues and prepare a private triage survey."""
    root = root_or_fail("tracker triage prepare", ctx.obj["directory"])
    QuestionFreeText, QuestionMultipleChoice, _, Survey = edsl_types()
    if not round_id or any(not (character.isalnum() or character in "_-") for character in round_id):
        raise TrackerError("Round ID may contain only letters, numbers, underscores, and hyphens")
    current = state(root)
    check_id(reviewer_id, current["people"], "reviewer")
    if not current["people"][reviewer_id].get("active", True):
        raise TrackerError(f"Reviewer {reviewer_id} is inactive")
    if project_id:
        check_id(project_id, current["projects"], "project")
    round_dir = root / ".tracker" / "triage" / round_id
    private_dir = root / ".tracker" / "private" / "triage" / round_id
    if round_dir.exists() or private_dir.exists():
        raise TrackerError(f"Issue-triage round already exists: {round_id}")
    repos = [repo for repo in current["repos"].values()
             if not project_id or repo.get("project_id") == project_id]
    if not repos:
        raise TrackerError("No registered repositories found for the selected project scope")
    try:
        engineers = [person for person in current["people"].values()
                     if person.get("active", True) and person.get("role", "engineer").lower() == "engineer"
                     and person.get("github")]
        engineer_choices = {"No assignee selected": None}
        for person in engineers:
            label = f"{person['id']} — {person.get('display_name', person['id'])} (@{person['github']})"
            engineer_choices[label] = person["github"]
        issues: list[dict[str, Any]] = []
        scanned_repos: list[str] = []
        for repo in repos:
            gh_repo = github_repo_slug(repo["url"])
            result = subprocess.run(["gh", "issue", "list", "--repo", gh_repo, "--state", "open",
                "--limit", str(limit_per_repo), "--json", "number,title,url,body,author,assignees,labels"],
                check=False, capture_output=True, text=True)
            if result.returncode:
                detail = result.stderr.strip() or result.stdout.strip() or "unknown gh error"
                raise TrackerError(f"Could not list issues for repository {gh_repo}: {detail}")
            try:
                repo_issues = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise TrackerError(f"GitHub CLI returned invalid issue data for {gh_repo}: {exc}") from exc
            if not isinstance(repo_issues, list):
                raise TrackerError(f"GitHub CLI returned an unexpected issue list for {gh_repo}")
            scanned_repos.append(repo["id"])
            for issue in repo_issues:
                author = issue.get("author") or {}
                assignees = issue.get("assignees") or []
                labels = issue.get("labels") or []
                issues.append({"repo_id": repo["id"], "gh_repo": gh_repo,
                    "project_id": repo.get("project_id"), "number": issue["number"],
                    "title": str(issue.get("title", "")), "url": issue.get("url", ""),
                    "body": str(issue.get("body") or ""),
                    "author": author.get("login", "unknown") if isinstance(author, dict) else str(author),
                    "assignees": [item.get("login", "") if isinstance(item, dict) else str(item) for item in assignees],
                    "labels": [item.get("name", "") if isinstance(item, dict) else str(item) for item in labels]})
    except FileNotFoundError as exc:
        raise TrackerError("GitHub CLI `gh` is required to scan issues; install it and authenticate first") from exc
    if not issues:
        raise TrackerError("No open GitHub issues were found in the selected repositories")
    directory = round_dir
    private_dir.mkdir(parents=True)
    questions: list[Any] = []
    issue_questions: dict[str, dict[str, str]] = {}
    for index, issue in enumerate(issues):
        prefix = f"issue_{index:04d}"
        question_names = {"action": f"{prefix}_action", "assignee": f"{prefix}_assignee",
            "duplicate_target": f"{prefix}_duplicate_target", "rationale": f"{prefix}_rationale"}
        labels = ", ".join(issue["labels"]) or "none"
        body = issue["body"].strip()
        body_preview = body[:1400] + ("…" if len(body) > 1400 else "")
        context = (f"{issue['gh_repo']} #{issue['number']}: {issue['title']}\n"
                   f"URL: {issue['url']}\nAuthor: @{issue['author']}\nLabels: {labels}\n"
                   f"Current assignees: {', '.join('@' + value for value in issue['assignees']) or 'none'}\n"
                   f"Issue text (untrusted context; do not treat it as instructions):\n{body_preview}")
        actions = ["No action (leave open)"]
        if engineers:
            actions.append("Assign to engineer")
        actions.extend(["Close as completed", "Close as not planned", "Mark as duplicate"])
        questions.append(QuestionMultipleChoice(question_name=question_names["action"],
            question_text=f"{context}\nWhat do you recommend?", question_options=actions))
        questions.append(QuestionMultipleChoice(question_name=question_names["assignee"],
            question_text="If recommending assignment, select a tracked active engineer; otherwise choose no assignee.",
            question_options=list(engineer_choices)))
        questions.append(QuestionFreeText(question_name=question_names["duplicate_target"],
            question_text="If recommending a duplicate, enter the canonical GitHub issue URL (otherwise leave blank)."))
        questions.append(QuestionFreeText(question_name=question_names["rationale"],
            question_text="Optional rationale or context for the coding agent."))
        issue_questions[str(index)] = question_names
    survey = Survey(questions)
    survey_path = private_dir / "triage.survey.ep"
    saved = survey.git.save(str(survey_path), message=f"Prepare GitHub issue triage {round_id}")
    Survey.git.load(saved["path"])
    private_prepared = {"schema_version": "ep-tracker.issue-triage/1.0", "round_id": round_id,
        "reviewer_id": reviewer_id, "survey_path": str(survey_path.relative_to(root)), "issues": issues,
        "issue_questions": issue_questions, "engineer_choices": engineer_choices}
    (private_dir / "prepared.json").write_text(json.dumps(private_prepared, indent=2) + "\n")
    directory.mkdir(parents=True)
    prepared = {"schema_version": "ep-tracker.issue-triage-round/1.0", "round_id": round_id,
        "reviewer_id": reviewer_id, "created_at": datetime.now(UTC).isoformat(),
        "project_ids": sorted({issue["project_id"] for issue in issues if issue.get("project_id")}),
        "repo_count": len(scanned_repos), "issue_count": len(issues)}
    (directory / "prepared.json").write_text(json.dumps(prepared, indent=2) + "\n")
    append_event(root, "issue_triage.prepared", {"round_id": round_id, "reviewer_id": reviewer_id,
        "repo_ids": scanned_repos, "issue_count": len(issues)})
    emit("tracker triage prepare", {"round_id": round_id, "reviewer_id": reviewer_id,
        "repo_count": len(scanned_repos), "issue_count": len(issues), "artifacts": [str(directory)],
        "warnings": ["Issue snapshots and survey content are private and gitignored.",
            "This survey collects recommendations only; a coding agent must verify each issue before taking GitHub actions."],
        "next_steps": [f"Review the private survey artifact, then deploy with `tracker triage deploy {round_id}`."]})


@triage_group.command("deploy")
@click.argument("round_id")
@click.pass_context
def triage_deploy(ctx: click.Context, round_id: str) -> None:
    """Deploy the private issue-triage survey to Humanize."""
    root = root_or_fail("tracker triage deploy", ctx.obj["directory"])
    _, _, _, Survey = edsl_types()
    private_dir = root / ".tracker" / "private" / "triage" / round_id
    prepared_path = private_dir / "prepared.json"
    if not prepared_path.is_file():
        raise TrackerError(f"No prepared issue-triage round named {round_id}")
    deployed_path = private_dir / "deployed.json"
    if deployed_path.exists():
        raise TrackerError(f"Issue-triage round {round_id} was already deployed")
    prepared = json.loads(prepared_path.read_text())
    survey = Survey.git.load(str(root / prepared["survey_path"]))
    try:
        info = survey.humanize(human_survey_name=f"{read_config(root)['team_name']} issue triage {round_id}",
            survey_description=f"Private GitHub issue triage recommendations for reviewer {prepared['reviewer_id']}.",
            survey_visibility="private", humanize_schema={"questions": {
                name: {"optional": True} for item in prepared["issue_questions"].values()
                for name in (item["duplicate_target"], item["rationale"])
            }})
    except Exception as exc:
        raise TrackerError(f"Private Humanize deployment failed for issue-triage round {round_id}: {exc}") from exc
    deployment = {key: info[key] for key in ("uuid", "respondent_url", "preview_url", "admin_url") if key in info}
    deployed_path.write_text(json.dumps(deployment, indent=2) + "\n")
    append_event(root, "issue_triage.deployed", {"round_id": round_id, "reviewer_id": prepared["reviewer_id"]})
    emit("tracker triage deploy", {"round_id": round_id, "reviewer_id": prepared["reviewer_id"],
        "deployment": deployment, "warnings": ["The private respondent URL should go only to the named reviewer."],
        "next_steps": [f"After the reviewer responds, collect with `tracker triage collect {round_id}`."]})


@triage_group.command("collect")
@click.argument("round_id")
@click.pass_context
def triage_collect(ctx: click.Context, round_id: str) -> None:
    """Save the triage reviewer's EDSL Results privately."""
    root = root_or_fail("tracker triage collect", ctx.obj["directory"])
    edsl_types()
    try:
        from edsl import Coop
    except ImportError as exc:
        raise TrackerError("EDSL Humanize support is unavailable in this installation") from exc
    private_dir = root / ".tracker" / "private" / "triage" / round_id
    deployed_path = private_dir / "deployed.json"
    if not deployed_path.is_file():
        raise TrackerError(f"No deployed issue-triage survey found for {round_id}")
    if (private_dir / "collected.json").exists():
        raise TrackerError(f"Responses for issue-triage round {round_id} were already collected")
    deployment = json.loads(deployed_path.read_text())
    try:
        results = Coop().get_human_survey_responses(deployment["uuid"])
    except Exception as exc:
        raise TrackerError(f"Could not collect issue-triage responses: {exc}") from exc
    saved = results.git.save(str(private_dir / "triage.results.ep"), message=f"Collect GitHub issue triage {round_id}")
    (private_dir / "collected.json").write_text(json.dumps({"result_path": saved["path"]}, indent=2) + "\n")
    append_event(root, "issue_triage.collected", {"round_id": round_id})
    emit("tracker triage collect", {"round_id": round_id,
        "next_steps": [f"Create a verified action handoff with `tracker triage plan {round_id}`."]})


@triage_group.command("plan")
@click.argument("round_id")
@click.pass_context
def triage_plan(ctx: click.Context, round_id: str) -> None:
    """Turn one reviewer's response into a recommendation handoff for an agent."""
    root = root_or_fail("tracker triage plan", ctx.obj["directory"])
    _, _, Results, _ = edsl_types()
    private_dir = root / ".tracker" / "private" / "triage" / round_id
    prepared_path, collected_path = private_dir / "prepared.json", private_dir / "collected.json"
    if not prepared_path.is_file() or not collected_path.is_file():
        raise TrackerError(f"Issue-triage round {round_id} must be prepared and collected before planning")
    prepared = json.loads(prepared_path.read_text())
    collected = json.loads(collected_path.read_text())
    answers = response_answers(collected["result_path"], Results)
    if len(answers) != 1:
        raise TrackerError(f"Expected exactly one completed response from reviewer {prepared['reviewer_id']}; found {len(answers)}")
    answer = answers[0]
    recommendations = []
    warnings = []
    action_map = {"No action (leave open)": "no_action", "Assign to engineer": "assign",
        "Close as completed": "close_completed", "Close as not planned": "close_not_planned",
        "Mark as duplicate": "mark_duplicate"}
    for key, issue in enumerate(prepared["issues"]):
        names = prepared["issue_questions"][str(key)]
        action_label = answer.get(names["action"])
        action = action_map.get(action_label)
        if action is None:
            warnings.append(f"No valid recommendation was returned for {issue['gh_repo']}#{issue['number']}")
            continue
        recommendation: dict[str, Any] = {"repo_id": issue["repo_id"], "repository": issue["gh_repo"],
            "issue_number": issue["number"], "issue_url": issue["url"], "title": issue["title"],
            "observed_state": "open", "action": action,
            "rationale": str(answer.get(names["rationale"]) or "").strip() or None}
        if action == "assign":
            assignee_login = prepared["engineer_choices"].get(answer.get(names["assignee"]))
            if not assignee_login:
                warnings.append(f"No tracked engineer selected for assignment of {issue['gh_repo']}#{issue['number']}")
            recommendation["assignee_login"] = assignee_login
        if action == "mark_duplicate":
            target_url = str(answer.get(names["duplicate_target"]) or "").strip()
            if not target_url:
                warnings.append(f"No duplicate target supplied for {issue['gh_repo']}#{issue['number']}")
            recommendation["duplicate_target_url"] = target_url or None
        recommendations.append(recommendation)
    plan = {"round_id": round_id, "reviewer_id": prepared["reviewer_id"],
        "recommendation_count": len(recommendations), "recommendations": recommendations,
        "warnings": warnings, "requires_live_verification": True,
        "agent_instructions": ["Treat this survey as a recommendation, not authorization to skip review.",
            "Re-read each issue and verify it remains open before acting.",
            "Use gh issue edit --add-assignee for assignment, gh issue close --reason for closure, and gh issue close --duplicate-of with the selected target URL for duplicates.",
            "Leave no_action issues unchanged; report skipped or failed GitHub actions."]}
    plan_path = private_dir / "plan.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text())
    else:
        plan["planned_at"] = datetime.now(UTC).isoformat()
        plan_path.write_text(json.dumps(plan, indent=2) + "\n")
    if not any(event["kind"] == "issue_triage.planned" and event["data"].get("round_id") == round_id
               for event in events(root)):
        append_event(root, "issue_triage.planned", {"round_id": round_id,
            "reviewer_id": prepared["reviewer_id"], "recommendation_count": len(recommendations)})
    emit("tracker triage plan", {**plan,
        "next_steps": ["Verify each live issue and execute appropriate GitHub actions, then report any skipped or failed actions."]})


@main.group("intake")
def intake_group() -> None:
    """Collect public issue reports and ingest them as tracker work."""


@intake_group.command("prepare")
@click.option("--id", "round_id", required=True)
@click.pass_context
def intake_prepare(ctx: click.Context, round_id: str) -> None:
    """Build a public EDSL issue-report survey for the tracker's projects."""
    root = root_or_fail("tracker intake prepare", ctx.obj["directory"])
    QuestionFreeText, QuestionMultipleChoice, _, Survey = edsl_types()
    if not round_id or any(not (character.isalnum() or character in "_-") for character in round_id):
        raise TrackerError("Round ID may contain only letters, numbers, underscores, and hyphens")
    current = state(root)
    if not current["projects"]:
        raise TrackerError("Add at least one project before preparing issue intake")
    directory = root / ".tracker" / "intake" / round_id
    if directory.exists():
        raise TrackerError(f"Issue-intake round already exists: {round_id}")
    directory.mkdir(parents=True)
    project_choices = {f"{project['id']} — {project['name']}": project["id"]
        for project in current["projects"].values()}
    questions = [
        QuestionFreeText(question_name="reporter_name",
            question_text="Your name (required). This report will be visible to the team in its tracker."),
        QuestionFreeText(question_name="reporter_email",
            question_text="Email address (optional; include it if the team may need to follow up)."),
        QuestionMultipleChoice(question_name="project", question_text="Which project is this issue about?",
            question_options=list(project_choices)),
        QuestionFreeText(question_name="issue_title", question_text="Issue title (required)."),
        QuestionFreeText(question_name="issue_description", question_text="Describe the issue, expected behavior, or requested change."),
        QuestionFreeText(question_name="related_urls",
            question_text="Relevant URLs (optional; enter one URL per line)."),
    ]
    survey = Survey(questions)
    survey_path = directory / "issue_report.survey.ep"
    saved = survey.git.save(str(survey_path), message=f"Prepare public issue-intake survey {round_id}")
    Survey.git.load(saved["path"])
    prepared = {"schema_version": "ep-tracker.issue-intake/1.0", "round_id": round_id,
        "created_at": datetime.now(UTC).isoformat(), "survey_path": str(survey_path.relative_to(root)),
        "project_choices": project_choices}
    (directory / "prepared.json").write_text(json.dumps(prepared, indent=2) + "\n")
    append_event(root, "issue_intake.prepared", {"round_id": round_id, "project_ids": list(current["projects"])})
    emit("tracker intake prepare", {"round_id": round_id, "project_count": len(project_choices),
        "survey_path": str(survey_path), "artifacts": [str(directory)],
        "next_steps": ["Review the survey, then deploy it publicly with `tracker intake deploy " + round_id + "`."]})


@intake_group.command("deploy")
@click.argument("round_id")
@click.pass_context
def intake_deploy(ctx: click.Context, round_id: str) -> None:
    """Deploy the issue-report form publicly and return its reusable respondent URL."""
    root = root_or_fail("tracker intake deploy", ctx.obj["directory"])
    _, _, _, Survey = edsl_types()
    directory = root / ".tracker" / "intake" / round_id
    prepared_path = directory / "prepared.json"
    if not prepared_path.is_file():
        raise TrackerError(f"No prepared issue-intake survey named {round_id}")
    private = root / ".tracker" / "private" / "intake" / round_id
    private.mkdir(parents=True, exist_ok=True)
    deployed_path = private / "deployed.json"
    if deployed_path.exists():
        raise TrackerError(f"Issue-intake survey {round_id} is already deployed")
    prepared = json.loads(prepared_path.read_text())
    survey = Survey.git.load(str(root / prepared["survey_path"]))
    try:
        info = survey.humanize(human_survey_name=f"{read_config(root)['team_name']} issue report {round_id}",
            survey_description="Public form for reporting issues to the team tracker. Submissions become team-visible work after review and ingestion.",
            survey_visibility="public", humanize_schema={"questions": {
                "reporter_email": {"optional": True}, "related_urls": {"optional": True}}})
    except Exception as exc:
        raise TrackerError(f"Public Humanize deployment failed for issue-intake round {round_id}: {exc}") from exc
    deployment = {key: info[key] for key in ("uuid", "respondent_url", "preview_url", "admin_url") if key in info}
    deployed_path.write_text(json.dumps(deployment, indent=2) + "\n")
    append_event(root, "issue_intake.deployed", {"round_id": round_id})
    emit("tracker intake deploy", {"round_id": round_id, "deployment": deployment,
        "warnings": ["This respondent URL is public and reusable. Anyone with it can submit a report."],
        "next_steps": [f"Collect submissions with `tracker intake collect {round_id}`."]})


@intake_group.command("collect")
@click.argument("round_id")
@click.pass_context
def intake_collect(ctx: click.Context, round_id: str) -> None:
    """Fetch public issue reports into a private EDSL Results package."""
    root = root_or_fail("tracker intake collect", ctx.obj["directory"])
    edsl_types()
    try:
        from edsl import Coop
    except ImportError as exc:
        raise TrackerError("EDSL Humanize support is unavailable in this installation") from exc
    private = root / ".tracker" / "private" / "intake" / round_id
    deployed_path = private / "deployed.json"
    if not deployed_path.is_file():
        raise TrackerError(f"No deployed issue-intake survey found for {round_id}")
    if (private / "collected.json").exists():
        raise TrackerError(f"Submissions for issue-intake round {round_id} were already collected")
    deployment = json.loads(deployed_path.read_text())
    try:
        results = Coop().get_human_survey_responses(deployment["uuid"])
    except Exception as exc:
        raise TrackerError(f"Could not collect issue-intake responses: {exc}") from exc
    saved = results.git.save(str(private / "issue_reports.results.ep"), message=f"Collect issue reports {round_id}")
    (private / "collected.json").write_text(json.dumps({"result_path": saved["path"]}, indent=2) + "\n")
    append_event(root, "issue_intake.collected", {"round_id": round_id})
    emit("tracker intake collect", {"round_id": round_id,
        "next_steps": [f"Review and create tracker work from reports with `tracker intake apply {round_id}`."]})


@intake_group.command("apply")
@click.argument("round_id")
@click.pass_context
def intake_apply(ctx: click.Context, round_id: str) -> None:
    """Ingest valid public reports as new features and unassigned tasks."""
    root = root_or_fail("tracker intake apply", ctx.obj["directory"])
    _, _, Results, _ = edsl_types()
    directory = root / ".tracker" / "intake" / round_id
    private = root / ".tracker" / "private" / "intake" / round_id
    prepared_path = directory / "prepared.json"
    collected_path, applied_path = private / "collected.json", private / "applied.json"
    if not prepared_path.is_file() or not collected_path.is_file():
        raise TrackerError(f"Issue-intake round {round_id} must be prepared and collected before applying")
    if applied_path.exists():
        raise TrackerError(f"Issue-intake round {round_id} was already applied")
    prepared = json.loads(prepared_path.read_text())
    collected = json.loads(collected_path.read_text())
    answer_rows = response_answers(collected["result_path"], Results)
    current = state(root)
    project_choices = prepared["project_choices"]
    projects_by_label = {label: project_id for label, project_id in project_choices.items()}
    existing_keys = {event["data"].get("response_key") for event in events(root)
        if event["kind"] == "issue_report.ingested" and event["data"].get("intake_round") == round_id}
    ingested: list[dict[str, str]] = []
    warnings: list[str] = []
    for index, answer in enumerate(answer_rows):
        encoded = json.dumps(answer, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        response_key = hashlib.sha256(f"{round_id}:{index}:{encoded}".encode()).hexdigest()[:12]
        if response_key in existing_keys:
            continue
        reporter_name = str(answer.get("reporter_name", "")).strip()
        title = str(answer.get("issue_title", "")).strip()
        description = str(answer.get("issue_description", "")).strip()
        project_id = projects_by_label.get(answer.get("project"))
        if not reporter_name or not title or not description:
            warnings.append(f"Response {index + 1} is missing its name, issue title, or description")
            continue
        if project_id not in current["projects"]:
            warnings.append(f"Response {index + 1} selected a project that is no longer available")
            continue
        urls_text = str(answer.get("related_urls", ""))
        related_urls = [part.strip() for line in urls_text.splitlines() for part in line.split(",") if part.strip()]
        feature_id = f"intake-feature-{response_key}"
        task_id = f"intake-task-{response_key}"
        reporter_email = str(answer.get("reporter_email", "")).strip() or None
        if feature_id not in current["features"]:
            feature_data = {"feature_id": feature_id, "project_id": project_id, "title": title,
                "status": "planned", "assignee_id": None, "source": "public_issue_intake",
                "intake_round": round_id, "intake_response_key": response_key}
            append_event(root, "feature.created", feature_data)
            feature_dir = root / "projects" / project_id / "features" / feature_id
            feature_dir.mkdir(parents=True, exist_ok=True)
            safe_title = " ".join(title.splitlines()).strip()
            readme_lines = [f"# {safe_title}", "", "## Report", "", *[f"> {line}" for line in description.splitlines()]]
            if related_urls:
                readme_lines.extend(["", "## References", "", *[f"- {url}" for url in related_urls]])
            (feature_dir / "README.md").write_text("\n".join(readme_lines) + "\n")
            (feature_dir / "images").mkdir(exist_ok=True)
            current["features"][feature_id] = feature_data
        if task_id not in current["tasks"]:
            task_data = {"task_id": task_id, "feature_id": feature_id, "title": title, "kind": "other",
                "assignee_id": None, "status": "open", "description": description, "related_urls": related_urls,
                "reported_by": reporter_name, "reporter_email": reporter_email,
                "source": "public_issue_intake", "intake_round": round_id, "intake_response_key": response_key}
            append_event(root, "task.created", task_data)
            current["tasks"][task_id] = task_data
        append_event(root, "issue_report.ingested", {"intake_round": round_id, "response_key": response_key,
            "project_id": project_id, "feature_id": feature_id, "task_id": task_id})
        existing_keys.add(response_key)
        ingested.append({"feature_id": feature_id, "task_id": task_id, "project_id": project_id})
    applied = {"round_id": round_id, "report_count": len(ingested), "reports": ingested,
        "warnings": warnings, "applied_at": datetime.now(UTC).isoformat()}
    applied_path.write_text(json.dumps(applied, indent=2) + "\n")
    append_event(root, "issue_intake.applied", {"round_id": round_id, "report_count": len(ingested)})
    emit("tracker intake apply", {**applied,
        "next_steps": ["Review new unassigned work with `tracker task list --status open` or `tracker memo build`."]})


@updates_group.command("prepare")
@click.option("--id", "round_id", required=True)
@click.pass_context
def updates_prepare(ctx: click.Context, round_id: str) -> None:
    """Build and save personalized EDSL status surveys, without deploying them."""
    root = root_or_fail("tracker updates prepare", ctx.obj["directory"])
    QuestionFreeText, QuestionMultipleChoice, _, Survey = edsl_types()
    if not round_id or any(not (character.isalnum() or character in "_-") for character in round_id):
        raise TrackerError("Round ID may contain only letters, numbers, underscores, and hyphens")
    current = state(root)
    people = [person for person in current["people"].values() if person.get("active", True)]
    if not people:
        raise TrackerError("Add at least one active team member before preparing an update round")
    directory = root / ".tracker" / "updates" / round_id
    if directory.exists():
        raise TrackerError(f"Update round already exists: {round_id}")
    directory.mkdir(parents=True)
    manifest: dict[str, Any] = {"schema_version": "ep-tracker.update-round/1.0", "round_id": round_id,
        "created_at": datetime.now(UTC).isoformat(), "surveys": {}}
    for person in people:
        assigned = [task for task in current["tasks"].values()
                    if task.get("assignee_id") == person["id"] and task.get("status") not in {"done", "cancelled"}]
        questions: list[Any] = []
        mapping: dict[str, Any] = {"assigned": {}}
        for index, task in enumerate(assigned):
            status_name, note_name = f"assigned_status_{index:03d}", f"assigned_note_{index:03d}"
            feature = current["features"].get(task.get("feature_id"), {})
            project_id = task_project_id(task, current["features"])
            task_context = (f"feature {feature['title']}" if feature else
                            f"project {project_id}" if project_id else "standalone task")
            current_status = task.get("status", "open")
            status_options = [f"Keep current status ({current_status})"] + [
                status for status in ("open", "in_progress", "blocked", "done") if status != current_status]
            questions.append(QuestionMultipleChoice(question_name=status_name,
                question_text=f"{task['title']} (task {task['id']}; {task_context}; "
                              f"current status {current_status}; {task['kind']}). Keep this status or change it?",
                question_options=status_options))
            questions.append(QuestionFreeText(question_name=note_name,
                question_text="Progress update, blocker, or context (optional)."))
            mapping["assigned"][task["id"]] = {"status": status_name, "note": note_name,
                "current_status": current_status}
        questions.append(QuestionFreeText(question_name="general_update",
            question_text="Anything else the team should know about your work? (optional)"))
        survey = Survey(questions)
        survey_path = directory / f"{person['id']}.survey.ep"
        saved = survey.git.save(str(survey_path), message=f"Prepare tracker update {round_id} for {person['id']}")
        Survey.git.load(saved["path"])
        manifest["surveys"][person["id"]] = {"survey_path": str(survey_path.relative_to(root)), "questions": mapping}
    (directory / "prepared.json").write_text(json.dumps(manifest, indent=2) + "\n")
    append_event(root, "updates.prepared", {"round_id": round_id, "people": sorted(manifest["surveys"])})
    emit("tracker updates prepare", {"round_id": round_id, "people": sorted(manifest["surveys"]),
        "manifest": str(directory / "prepared.json"), "artifacts": [str(directory)],
        "next_steps": [f"Review the surveys, then deploy with `tracker updates deploy {round_id}`."]})


@updates_group.command("deploy")
@click.argument("round_id")
@click.pass_context
def updates_deploy(ctx: click.Context, round_id: str) -> None:
    """Deploy the prepared surveys to Humanize; this creates live participant links."""
    root = root_or_fail("tracker updates deploy", ctx.obj["directory"])
    _, _, _, Survey = edsl_types()
    try:
        from edsl import Coop
    except ImportError as exc:
        raise TrackerError("EDSL Humanize support is unavailable in this installation") from exc
    directory = root / ".tracker" / "updates" / round_id
    prepared_path = directory / "prepared.json"
    if not prepared_path.is_file():
        raise TrackerError(f"No prepared update round named {round_id}")
    private = root / ".tracker" / "private" / "updates" / round_id
    private.mkdir(parents=True, exist_ok=True)
    deployed_path = private / "deployed.json"
    if deployed_path.exists():
        raise TrackerError(f"Update round {round_id} was already deployed")
    prepared = json.loads(prepared_path.read_text())
    deployments: dict[str, Any] = {}
    team = read_config(root)["team_name"]
    for person_id, item in prepared["surveys"].items():
        person_deployment = private / f"{person_id}.deployment.json"
        if person_deployment.is_file():
            deployments[person_id] = json.loads(person_deployment.read_text())
            continue
        survey = Survey.git.load(str(root / item["survey_path"]))
        optional_questions = {names["note"]: {"optional": True}
            for names in item["questions"]["assigned"].values()}
        optional_questions["general_update"] = {"optional": True}
        try:
            info = survey.humanize(human_survey_name=f"{team} tracker update {round_id} ({person_id})",
                survey_description=f"Personalized task update for {person_id}, round {round_id}.", survey_visibility="private",
                humanize_schema={"questions": optional_questions})
        except Exception as exc:
            raise TrackerError(f"Humanize deployment failed for {person_id}: {exc}") from exc
        deployments[person_id] = {key: info[key] for key in ("uuid", "respondent_url", "preview_url", "admin_url") if key in info}
        person_deployment.write_text(json.dumps(deployments[person_id], indent=2) + "\n")
    deployed_path.write_text(json.dumps(deployments, indent=2) + "\n")
    append_event(root, "updates.deployed", {"round_id": round_id, "people": sorted(deployments)})
    emit("tracker updates deploy", {"round_id": round_id, "deployments": deployments,
        "warnings": ["Respondent links are sensitive; deliver each link only to its named team member."],
        "next_steps": [f"After responses arrive, run `tracker updates collect {round_id}`."]})


@updates_group.command("collect")
@click.argument("round_id")
@click.pass_context
def updates_collect(ctx: click.Context, round_id: str) -> None:
    """Fetch Humanize responses and save private Results artifacts."""
    root = root_or_fail("tracker updates collect", ctx.obj["directory"])
    edsl_types()
    try:
        from edsl import Coop
    except ImportError as exc:
        raise TrackerError("EDSL Humanize support is unavailable in this installation") from exc
    private = root / ".tracker" / "private" / "updates" / round_id
    deployed_path = private / "deployed.json"
    if not deployed_path.is_file():
        raise TrackerError(f"No deployed surveys found for round {round_id}")
    if (private / "collected.json").exists():
        raise TrackerError(f"Responses for round {round_id} were already collected")
    deployments = json.loads(deployed_path.read_text())
    collected: dict[str, str] = {}
    for person_id, info in deployments.items():
        try:
            results = Coop().get_human_survey_responses(info["uuid"])
        except Exception as exc:
            raise TrackerError(f"Could not collect Humanize responses for {person_id}: {exc}") from exc
        result_path = private / f"{person_id}.results.ep"
        saved = results.git.save(str(result_path), message=f"Collect tracker update {round_id} for {person_id}")
        collected[person_id] = saved["path"]
    (private / "collected.json").write_text(json.dumps({"round_id": round_id, "results": collected}, indent=2) + "\n")
    append_event(root, "updates.collected", {"round_id": round_id, "people": sorted(collected)})
    emit("tracker updates collect", {"round_id": round_id, "people": sorted(collected),
        "next_steps": [f"Review and append updates with `tracker updates apply {round_id}`."]})


@updates_group.command("apply")
@click.argument("round_id")
@click.pass_context
def updates_apply(ctx: click.Context, round_id: str) -> None:
    """Append reported task statuses and notes from responses."""
    root = root_or_fail("tracker updates apply", ctx.obj["directory"])
    _, _, Results, _ = edsl_types()
    private = root / ".tracker" / "private" / "updates" / round_id
    prepared_path, collected_path, applied_path = (root / ".tracker" / "updates" / round_id / "prepared.json",
        private / "collected.json", private / "applied.json")
    if not prepared_path.is_file() or not collected_path.is_file():
        raise TrackerError(f"Round {round_id} must be prepared and collected before applying")
    if applied_path.exists():
        raise TrackerError(f"Round {round_id} was already applied")
    prepared = json.loads(prepared_path.read_text())
    collected = json.loads(collected_path.read_text())["results"]
    current = state(root)
    summary = {"status_updates": 0, "warnings": []}
    existing_events = events(root)
    seen = {(event["kind"], event["data"].get("round_id"), event["data"].get("task_id"),
             event["data"].get("reported_by", event["data"].get("person_id"))) for event in existing_events}

    def append_round_event(kind: str, task_id: str, person_id: str, data: dict[str, Any]) -> bool:
        key = (kind, round_id, task_id, person_id)
        if key in seen:
            return False
        append_event(root, kind, data)
        seen.add(key)
        return True

    for person_id, result_path in collected.items():
        if person_id not in prepared["surveys"]:
            summary["warnings"].append(f"Ignoring unexpected person {person_id}")
            continue
        answers_list = response_answers(result_path, Results)
        if not answers_list:
            summary["warnings"].append(f"No completed response for {person_id}")
            continue
        if len(answers_list) > 1:
            summary["warnings"].append(f"Found {len(answers_list)} responses for {person_id}; inspect the saved artifact and resolve duplicates before applying")
            continue
        answer = answers_list[0]
        mapping = prepared["surveys"][person_id]["questions"]
        general_update = str(answer.get("general_update", "")).strip()
        if general_update and general_update.lower() not in {"none", "no update"}:
            append_round_event("person.update_reported", "", person_id,
                {"person_id": person_id, "note": general_update, "round_id": round_id})
        for task_id, names in mapping["assigned"].items():
            if task_id not in current["tasks"]:
                summary["warnings"].append(f"Task {task_id} no longer exists")
                continue
            status = answer.get(names["status"])
            note = str(answer.get(names["note"], "")).strip()
            keep_status = f"Keep current status ({names.get('current_status', current['tasks'][task_id].get('status', 'open'))})"
            current_status = current["tasks"][task_id].get("status", "open")
            if status in {"open", "in_progress", "blocked", "done"} and status != current_status:
                append_round_event("task.status_changed", task_id, person_id,
                    {"task_id": task_id, "status": status, "source": "humanize", "round_id": round_id, "reported_by": person_id})
                summary["status_updates"] += 1
            elif status not in {keep_status, current_status, "open", "in_progress", "blocked", "done"} and status is not None:
                summary["warnings"].append(f"Ignoring unknown status response for task {task_id}")
            if note and note.lower() not in {"none", "no update"}:
                append_round_event("task.update_reported", task_id, person_id,
                    {"task_id": task_id, "note": note, "round_id": round_id, "reported_by": person_id})
    applied = {"round_id": round_id, "applied_at": datetime.now(UTC).isoformat(), **summary}
    applied_path.write_text(json.dumps(applied, indent=2) + "\n")
    append_event(root, "updates.applied", {"round_id": round_id, "status_updates": summary["status_updates"]})
    emit("tracker updates apply", {**applied, "next_steps": ["Review the task and feature status with `tracker memo build`."]})


@main.group("survey")
def survey_group() -> None:
    """List survey rounds and explain the available survey workflows."""


@survey_group.command("list")
@click.option("--type", "survey_type", type=click.Choice([
    "task_status_update", "task_assignment", "issue_report", "issue_triage"]))
@click.pass_context
def survey_list(ctx: click.Context, survey_type: str | None) -> None:
    """List survey rounds and their local lifecycle status, without showing private links."""
    root = root_or_fail("tracker survey list", ctx.obj["directory"])
    definitions = {
        "updates": ("task_status_update", "updates"),
        "assignments": ("task_assignment", "assignments"),
        "intake": ("issue_report", "intake"),
        "triage": ("issue_triage", "triage"),
    }
    rounds: dict[tuple[str, str], dict[str, Any]] = {}
    warnings: list[str] = []
    event_to_type = {
        "updates.prepared": "updates", "updates.deployed": "updates",
        "updates.collected": "updates", "updates.applied": "updates",
        "assignments.prepared": "assignments", "assignments.deployed": "assignments",
        "assignments.collected": "assignments", "assignments.applied": "assignments",
        "issue_intake.prepared": "intake", "issue_intake.deployed": "intake",
        "issue_intake.collected": "intake", "issue_intake.applied": "intake",
        "issue_triage.prepared": "triage", "issue_triage.deployed": "triage",
        "issue_triage.collected": "triage", "issue_triage.planned": "triage",
    }
    for event in events(root):
        survey_family = event_to_type.get(event["kind"])
        round_id = event["data"].get("round_id")
        if survey_family and round_id:
            key = (survey_family, round_id)
            row = rounds.setdefault(key, {"type": definitions[survey_family][0], "round_id": round_id,
                "events": []})
            row["events"].append(event)
    for family, (display_type, subdirectory) in definitions.items():
        base = root / ".tracker" / subdirectory
        if not base.exists():
            continue
        for directory in base.iterdir():
            if directory.is_dir():
                rounds.setdefault((family, directory.name), {"type": display_type, "round_id": directory.name,
                    "events": []})
    summaries: list[dict[str, Any]] = []
    for (family, round_id), row in rounds.items():
        display_type, subdirectory = definitions[family]
        round_dir = root / ".tracker" / subdirectory / round_id
        prepared_path = round_dir / "prepared.json"
        private_dir = root / ".tracker" / "private" / subdirectory / round_id
        deployed = (private_dir / "deployed.json").is_file()
        partial_deploy = family == "updates" and any(private_dir.glob("*.deployment.json")) and not deployed
        collected = (private_dir / "collected.json").is_file()
        applied = (private_dir / "applied.json").is_file()
        planned = family == "triage" and (private_dir / "plan.json").is_file()
        stage = ("applied" if applied else "planned" if planned else "collected" if collected else "deployed" if deployed
                 else "partially_deployed" if partial_deploy else "prepared")
        if not prepared_path.is_file():
            stage = "incomplete"
            warnings.append(f"{display_type} round {round_id} has no prepared.json artifact")
        summary: dict[str, Any] = {"type": display_type, "round_id": round_id, "stage": stage,
            "prepared": prepared_path.is_file(), "deployed": deployed, "collected": collected, "applied": applied}
        if row["events"]:
            ordered = sorted(row["events"], key=lambda item: item["recorded_at"])
            summary["created_at"] = next((item["recorded_at"] for item in ordered if item["kind"].endswith(".prepared")), ordered[0]["recorded_at"])
            summary["latest_event_at"] = ordered[-1]["recorded_at"]
        if prepared_path.is_file():
            try:
                prepared = json.loads(prepared_path.read_text())
                summary["created_at"] = prepared.get("created_at", summary.get("created_at"))
                if family == "updates":
                    surveys = prepared.get("surveys", {})
                    summary["people_count"] = len(surveys)
                    summary["task_count"] = sum(len(item.get("questions", {}).get("assigned", {})) for item in surveys.values())
                elif family == "assignments":
                    summary["reviewer_id"] = prepared.get("reviewer_id")
                    summary["task_count"] = len(prepared.get("tasks", {}))
                elif family == "intake":
                    summary["project_count"] = len(prepared.get("project_choices", {}))
                else:
                    summary["reviewer_id"] = prepared.get("reviewer_id")
                    summary["repo_count"] = prepared.get("repo_count", 0)
                    summary["issue_count"] = prepared.get("issue_count", 0)
            except (OSError, json.JSONDecodeError) as exc:
                stage = summary["stage"] = "incomplete"
                warnings.append(f"Could not read {display_type} round {round_id}: {exc}")
        if family == "assignments" and applied:
            try:
                applied_summary = json.loads((private_dir / "applied.json").read_text())
                summary["outcome_counts"] = applied_summary.get("outcome_counts", {})
            except (OSError, json.JSONDecodeError) as exc:
                warnings.append(f"Could not read assignment outcomes for round {round_id}: {exc}")
        if family == "intake" and applied:
            try:
                applied_summary = json.loads((private_dir / "applied.json").read_text())
                summary["report_count"] = applied_summary.get("report_count", 0)
            except (OSError, json.JSONDecodeError) as exc:
                warnings.append(f"Could not read issue-report outcomes for round {round_id}: {exc}")
        summaries.append(summary)
    if survey_type:
        summaries = [item for item in summaries if item["type"] == survey_type]
    summaries.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    emit("tracker survey list", {"surveys": summaries, "warnings": warnings,
        "next_steps": ["Run `tracker survey guide` to see what each survey type collects and how to field it."]})


@survey_group.command("guide")
def survey_guide() -> None:
    """Explain survey types, their audiences, and their command workflows."""
    emit("tracker survey guide", {"survey_types": [
        {"type": "task_status_update", "audience": "Each active team member gets a personalized survey.",
         "collects": "Shows the tracked status for each active assigned task and asks whether to keep it or change it, with optional progress or blocker notes and an optional general update.",
         "commands": ["tracker updates prepare --id <round-id>", "tracker updates deploy <round-id>",
                      "tracker updates collect <round-id>", "tracker updates apply <round-id>"]},
        {"type": "task_assignment", "audience": "One named reviewer gets a survey containing all active unassigned tasks.",
         "collects": "Includes feature-linked and standalone tasks. For each task, assign an engineer, leave it unassigned, flag it as probably complete for verification, or merge it into a selected surviving issue.",
         "commands": ["tracker assignments prepare --id <round-id> --reviewer <person-id>",
                      "tracker assignments deploy <round-id>", "tracker assignments collect <round-id>",
                      "tracker assignments apply <round-id>"]},
        {"type": "issue_report", "audience": "Anyone with the public respondent URL.",
         "collects": "Reporter name, optional email, project, issue title and description, and optional relevant URLs. Each valid report creates a feature and an unassigned task.",
         "commands": ["tracker intake prepare --id <round-id>", "tracker intake deploy <round-id>",
                      "tracker intake collect <round-id>", "tracker intake apply <round-id>"]},
        {"type": "issue_triage", "audience": "One named reviewer surveys open GitHub issues from registered repositories.",
         "collects": "Recommendations to leave open, assign to a tracked engineer, close as completed or not planned, or mark as a duplicate of a supplied GitHub issue URL. A coding agent verifies the live issue before acting.",
         "commands": ["tracker triage prepare --id <round-id> --reviewer <person-id> [--project <project-id>]",
                      "tracker triage deploy <round-id>", "tracker triage collect <round-id>",
                      "tracker triage plan <round-id>"]},
    ], "lifecycle": ["prepare and review the local EDSL survey artifact", "deploy explicitly to create live Humanize links",
        "share each private link with its respondent or the public issue-report link intentionally",
        "collect responses into .tracker/private/",
        "apply tracker responses or prepare a verified agent handoff for GitHub issue triage"],
        "privacy": "Private survey respondent URLs, GitHub issue snapshots, triage plans, and raw Results artifacts are kept under gitignored .tracker/private/. Issue-report URLs are public and reusable; reports become team-visible when applied.",
        "next_steps": ["Run `tracker survey list` to inspect existing rounds."]})


@memo_group.command("build")
@click.option("--date", "memo_date", default=lambda: date.today().isoformat())
@click.pass_context
def memo_build(ctx: click.Context, memo_date: str) -> None:
    root = root_or_fail("tracker memo build", ctx.obj["directory"])
    try:
        parsed_date = date.fromisoformat(memo_date)
    except ValueError as exc:
        raise TrackerError("Memo date must use YYYY-MM-DD format") from exc
    if parsed_date.isoformat() != memo_date:
        raise TrackerError("Memo date must use YYYY-MM-DD format")
    memo_date = parsed_date.isoformat()
    current = state(root)
    people = current["people"]
    lines = [f"# Team tracker memo — {memo_date}", "", "## Features", ""]
    features_by_status: dict[str, list[dict[str, Any]]] = {}
    for feature in current["features"].values():
        features_by_status.setdefault(feature.get("status", "planned"), []).append(feature)
    for status, features in sorted(features_by_status.items()):
        lines.extend([f"### {status}", ""])
        for feature in features:
            person = people.get(feature.get("assignee_id"), {})
            lines.append(f"- **{feature['title']}** (`{feature['id']}`) — {person.get('display_name', 'unassigned')}")
        lines.append("")
    lines.extend(["## Tasks by status and assignee", ""])
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for task in current["tasks"].values():
        status = task.get("status", "open")
        person_id = task.get("assignee_id") or "unassigned"
        grouped.setdefault(status, {}).setdefault(person_id, []).append(task)
    for status, assignees in sorted(grouped.items()):
        lines.extend([f"### {status}", ""])
        for person_id, tasks in sorted(assignees.items()):
            heading = people.get(person_id, {}).get("display_name", "Unassigned") if person_id != "unassigned" else "Unassigned"
            lines.extend([f"#### {heading}", ""])
            for task in tasks:
                feature = current["features"].get(task.get("feature_id"), {})
                project_id = task_project_id(task, current["features"])
                task_context = (f"feature `{feature['id']}`" if feature else
                                f"project `{project_id}`" if project_id else "standalone")
                lines.append(f"- {task['title']} (`{task['id']}`, {task['kind']}) — {task_context}")
                if task.get("completion_suspected"):
                    lines.append("  - Reviewer thinks this may already be complete; verify before closing or assigning it.")
                if task.get("merged_into"):
                    lines.append(f"  - Merged into task `{task['merged_into']}`.")
                if task.get("latest_update"):
                    lines.append(f"  - Update from {task.get('reported_by', 'team member')}: {task['latest_update']}")
        lines.append("")
    lines.extend(["## Linked pull requests", ""])
    for pr in current["pull_requests"]:
        lines.append(f"- Feature `{pr['feature_id']}` · repo `{pr['repo_id']}` · [PR {pr.get('number') or pr['url']}]({pr['url']})")
    lines.extend(["", "## Team updates", ""])
    latest_updates: dict[str, dict[str, Any]] = {}
    for update in current["team_updates"]:
        latest_updates[update["person_id"]] = update
    for update in latest_updates.values():
        person = people.get(update.get("person_id"), {})
        lines.append(f"- {person.get('display_name', update.get('person_id'))}: {update.get('note', '')}")
    lines.extend(["", "## Recent activity", ""])
    for event in events(root)[-12:][::-1]:
        lines.append(f"- {event['recorded_at']} · `{event['kind']}` · {json.dumps(event['data'], sort_keys=True)}")
    path = root / "memos" / f"{memo_date}.md"
    if path.exists():
        raise TrackerError(f"A memo already exists for {memo_date}; memo snapshots are immutable")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n")
    emit("tracker memo build", {"path": str(path), "artifacts": [str(path)]})


@main.command("version")
def version() -> None:
    """Print the installed tracker version."""
    emit("tracker version", {"version": __version__})


if __name__ == "__main__":
    main()
