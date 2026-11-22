# ep-tracker

An agent-first Python CLI for creating Git-backed team trackers. Teams use a
tracker repository separate from the code repositories it references.

The CLI stores authoritative tracker changes as append-only event files in
`.tracker/events/`; all current people, projects, repositories, features, tasks,
and PR links are projections of that history. Feature folders hold working
documentation and images. Daily memos are generated Markdown snapshots.

## Install for local development

```bash
uv tool install --editable .
tracker --help
```

The repository includes [AGENTS.md](AGENTS.md) for coding agents. Each tracker
created by `tracker init` also receives its own `AGENTS.md` with team workflow
instructions. From a team tracker, run `tracker agent next` for structured,
state-aware guidance; `tracker next` is an alias.

## Start a team tracker

```bash
tracker init ./team-tracker --name "Platform"
cd ./team-tracker
tracker person add --id alex --name "Alex Kim" --github alexkim
tracker project add --id billing --name "Billing"
tracker project repo --id billing-api --project billing --url https://github.com/acme/billing-api
tracker feature create --id saved-cards --project billing --title "Saved cards" --assignee alex
tracker task create --id saved-cards-build --feature saved-cards --title "Implement saved cards" --assignee alex
tracker memo build
```

Every command emits one JSON envelope to stdout. The event history is stored
under `.tracker/events/`, one event per file, which keeps concurrent Git changes
from colliding on a shared JSONL file. Commit and share the tracker repository
with the same review workflow used for code.

## Humanize updates

Install the optional EDSL integration with `uv pip install 'ep-tracker[fielding]'`.
An update round creates a personalized survey for every active team member.
Each survey asks for status and notes on that person's active tasks, then offers
the current unassigned tasks for self-claim or an assignment proposal.

```bash
tracker updates prepare --id 2026-11-22
tracker updates deploy 2026-11-22
# Send each returned respondent URL only to that person.
tracker updates collect 2026-11-22
tracker updates apply 2026-11-22
tracker task requests
```

Deployment is a separate command because it creates live Humanize surveys.
Survey links and raw responses are stored under `.tracker/private/`, which the
CLI adds to `.gitignore`. Applying responses appends reported task statuses and
leaves ownership changes as pending requests for review. Resolve those with
`tracker task resolve-request <event-id> --decision accept|reject`.

`tracker memo build` creates an immutable dated Markdown snapshot. If the team
needs another snapshot for the same date, choose a new date; existing memo files
are not overwritten.
