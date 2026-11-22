# ep-tracker

`ep-tracker` is an agent-first Python CLI for running a team's work tracker in
Git. A team keeps one tracker repository, separate from the code repositories
it tracks. People and agents use `tracker` commands; they do not edit tracker
JSON or event files by hand.

## How the tracker is organized

- **People** represent engineers and other team members. Engineers can own
  features and tasks; reviewers can be selected to allocate unassigned work.
- **Projects** group related work and one or more code repositories.
- **Features** describe a larger piece of work within a project. A feature can
  link pull requests from multiple repositories in that project. Its folder
  contains a `README.md` for notes and an `images/` directory for supporting
  images.
- **Tasks** are actionable work. They may belong to a feature or stand alone;
  an optional project can group a standalone task. A task can be assigned to a
  person and can represent building, reviewing, or approving work.
- **Surveys** collect task updates, allocate unassigned work, or report new
  issues. Survey responses become tracker events only when explicitly applied.
- **Daily memos** summarize features and tasks by status in a dated snapshot.

```mermaid
flowchart TD
    Reporter[Anyone with public issue URL] -->|issue report| Intake[Issue intake survey]
    Intake -->|collect, then apply| New[Feature and unassigned task]
    Team[Engineer or tracker CLI] -->|create work| New
    New --> Project[Project]
    Project --> Feature[Feature]
    Feature -->|optional association| Task[Task: build, review, or approve]
    Project -->|optional grouping| Task
    Project --> Repos[One or more code repositories]
    Feature --> PRs[Pull request links]
    Repos -. contains .-> PRs
    Task -->|assigned to| Engineer[Engineer]
    Engineer -->|personal status survey| Updates[Keep current status or change it; add notes]
    Task -->|unassigned| Allocation[Reviewer allocation survey]
    Allocation -->|assign, defer, flag for verification, or merge| Task
    Updates -->|collect, then apply| Events[Append-only event history]
    Allocation -->|collect, then apply| Events
    New --> Events
    Task --> Events
    Events --> Memo[Daily memo by status]
```

The event history under `.tracker/events/` is the authoritative state. The CLI
derives current people, projects, repositories, features, tasks, assignments,
statuses, and pull request links from these append-only events. Feature
README files and images are working documents tracked in Git; memos are
immutable dated snapshots. `.tracker/private/` holds survey links and raw
responses and is gitignored.

## Install and create a team tracker

```bash
uv tool install --editable .
tracker --help

tracker init ./team-tracker --name "Platform"
cd ./team-tracker
tracker person add --id alex --name "Alex Kim" --github alexkim
tracker project add --id billing --name "Billing"
tracker project repo --id billing-api --project billing --url https://github.com/acme/billing-api
tracker feature create --id saved-cards --project billing --title "Saved cards" --assignee alex
tracker task create --id saved-cards-build --feature saved-cards --title "Implement saved cards" --assignee alex
tracker feature pr saved-cards --repo billing-api --url https://github.com/acme/billing-api/pull/42 --number 42
tracker memo build
```

People default to the `engineer` role. Use `tracker person add --role <role>`
for reviewers or other team members who should not appear in engineer
assignment choices. A feature can have multiple tasks and pull request links.
Link each PR to a registered repository in the feature's project. Tasks can
also be created without a feature, either independently or grouped under a
project:

```bash
tracker task create --id rotate-keys --project billing --title "Rotate API keys" --kind other
tracker task create --id review-runbook --title "Review incident runbook" --kind review
```

Every command emits one JSON envelope to stdout. Event files are stored one per
file to avoid concurrent Git changes colliding on a shared JSONL file. Commit
and share the tracker repository with the same review workflow used for code.

## Everyday CLI commands

The CLI is organized by the kind of object being changed. IDs are stable
team-chosen names used by later commands. Commands append events; for example,
changing a task's status adds a new status event and leaves the earlier event
in history.

### People and projects

```bash
# Add an engineer (the default role).
tracker person add --id alex --name "Alex Kim" --github alexkim

# Add a reviewer who can run allocation surveys but is not offered as an engineer.
tracker person add --id sam --name "Sam Lee" --role reviewer

# Update profile information or deactivate someone without deleting history.
tracker person update alex --email alex@example.com
tracker person deactivate sam
tracker person list

# A project can track several code repositories.
tracker project add --id billing --name "Billing"
tracker project repo --id billing-api --project billing --url https://github.com/acme/billing-api
tracker project repo --id billing-web --project billing --url https://github.com/acme/billing-web
tracker project list
```

### Features, tasks, and pull requests

```bash
# Create a feature; this also creates its README.md and images/ folder.
tracker feature create --id saved-cards --project billing --title "Saved cards"

# Add build and review work. The feature is optional for tasks.
tracker task create --id saved-cards-build --feature saved-cards \
  --title "Implement saved cards" --kind build --assignee alex
tracker task create --id saved-cards-review --feature saved-cards \
  --title "Review the API change" --kind review

# Create a standalone task, optionally grouped under a project.
tracker task create --id rotate-keys --project billing \
  --title "Rotate API keys" --kind other
tracker task create --id review-runbook --title "Review incident runbook" --kind approve

# Link PRs from any repository in the feature's project.
tracker feature pr saved-cards --repo billing-api \
  --url https://github.com/acme/billing-api/pull/42 --number 42
tracker feature pr saved-cards --repo billing-web \
  --url https://github.com/acme/billing-web/pull/18 --number 18

# Inspect work or filter lists.
tracker feature list --project billing
tracker task list --assignee alex
tracker task list --status blocked
```

`--kind` can be `build`, `review`, `approve`, or `other`. Tasks start as
`open`; features start as `planned`. A task with `--feature` inherits the
feature's project. A task without a feature may use `--project`, or can be
fully standalone.

### Assign and update work directly

For one-off changes, use task commands. To allocate many unassigned tasks in
one review, use the assignment survey below.

```bash
# Assign a task or remove its assignment by omitting the person ID.
tracker task assign rotate-keys alex
tracker task assign rotate-keys

# Append a status change and an optional note.
tracker task status rotate-keys in_progress --update "Coordinating key rotation"
tracker task status rotate-keys blocked --update "Waiting on the vendor"
tracker task status rotate-keys done

# Feature status is tracked separately from task status.
tracker feature status saved-cards in_progress
tracker feature assign saved-cards alex

# Review the aggregate state, or inspect recent events.
tracker status
tracker history --limit 20
```

Task statuses are `open`, `in_progress`, `blocked`, `done`, and `cancelled`;
feature statuses are `planned`, `in_progress`, `blocked`, `done`, and
`cancelled`. Merged tasks are marked by the assignment workflow and point to a
surviving task.

### Reading command output

Commands print one JSON object to standard output, so agents can inspect or
pipe results. A typical response has this shape (the `data` fields depend on
the command):

```json
{
  "schema_version": "1.0",
  "command": "tracker task list",
  "status": "ok",
  "data": {"tasks": []},
  "artifacts": [],
  "warnings": [],
  "errors": [],
  "next_steps": []
}
```

Validation failures use `status: "error"` and include a structured error
instead of changing tracker state. Use `tracker guide`, `tracker survey guide`,
and `tracker --help` for built-in command guidance.

## A team's regular workflow

1. Run `tracker agent next` for state-aware guidance, or `tracker status` for a
   current overview. `tracker guide` describes the CLI workflow.
2. Add people, projects, and repositories. Create features for larger goals,
   then add build, review, or approval tasks and assign them when known. Create
   standalone tasks directly with `tracker task create` when there is no
   feature to attach them to.
3. Link pull requests to the feature as work lands across the project's code
   repositories. Keep feature notes and images in its folder.
4. Use surveys to gather updates from engineers and make decisions about
   unassigned work. Use the public issue form when people need to report new
   work.
5. Apply collected responses to append events, inspect `tracker status`, and
   build the daily snapshot with `tracker memo build`.

## Humanize surveys

Install the optional EDSL integration with:

```bash
uv tool install --editable '.[fielding]'
```

Each survey lifecycle is explicit: **prepare** creates local EDSL artifacts;
**deploy** creates live Humanize forms; **collect** saves responses privately;
**apply** appends tracker events. Preparing or reviewing a survey does not
deploy it. Do not commit or share `.tracker/private/`.

### Engineer task updates

An update round creates one personalized survey for each active team member.
For every active task assigned to that person, it displays the tracker status
recorded when the survey was prepared and asks whether to keep it or change it.
The engineer can add an optional progress note, blocker, or general update. A
“keep current status” answer does not append a status-change event; notes are
still recorded. Done and cancelled tasks are not included.

```bash
tracker updates prepare --id 2026-11-22
tracker updates deploy 2026-11-22
# Send each returned respondent URL only to its named engineer.
tracker updates collect 2026-11-22
tracker updates apply 2026-11-22
```

### Allocate unassigned tasks

One named reviewer gets a survey containing all active unassigned tasks,
including standalone tasks, and available engineers. For each task, the reviewer can assign it, leave it
unassigned, flag it as probably complete, or merge it into another active task
in the same project. A probable-completion flag is a request to verify; it does
not mark the task done. A merge preserves the original task in history and
records its surviving task ID.

```bash
tracker assignments prepare --id allocation-2026-11-22 --reviewer alex
tracker assignments deploy allocation-2026-11-22
# Send the returned private link to the named reviewer.
tracker assignments collect allocation-2026-11-22
tracker assignments apply allocation-2026-11-22
```

### Public issue reporting

Issue intake creates a reusable public respondent URL. The form asks who is
reporting, which project the issue concerns, a title and description, and
optional email and relevant URLs. Tell respondents their reports become
visible to the team when applied. Each valid report creates a feature with a
README containing the description and links, plus an unassigned task for team
triage.

```bash
tracker intake prepare --id public-issues
tracker intake deploy public-issues
# Share the public respondent URL intentionally.
tracker intake collect public-issues
tracker intake apply public-issues
```

### Survey discovery and privacy

Use `tracker survey guide` for the available survey types and exact commands.
Use `tracker survey list` to see rounds and whether they are prepared,
deployed, collected, or applied; it does not print respondent links. Filter by
type with `tracker survey list --type issue_report`, `tracker survey list
--type task_assignment`, or `tracker survey list --type task_status_update`.

Private respondent links and raw Results artifacts are stored under
`.tracker/private/`. Engineer update and reviewer assignment links are private
and should go only to the intended respondent. Issue intake links are public
and reusable, so share them intentionally.

## Daily memo

Build a dated team snapshot with:

```bash
tracker memo build
```

The memo groups features and tasks by status and preserves each date as an
immutable Markdown snapshot. To create another snapshot after one has been
made for a date, choose a different date.

## Agent guidance

The repository includes [AGENTS.md](AGENTS.md) for agents contributing to this
Python project. Each tracker created by `tracker init` also receives an
`AGENTS.md` with that team's workflow guidance. From a team tracker, agents
should run `tracker agent next` before taking action and again after material
changes. `tracker next` is an alias.
