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

People default to the `engineer` role. Use `tracker person add --role <role>`
for reviewers or other team members who should not appear as assignment choices.

Every command emits one JSON envelope to stdout. The event history is stored
under `.tracker/events/`, one event per file, which keeps concurrent Git changes
from colliding on a shared JSONL file. Commit and share the tracker repository
with the same review workflow used for code.

## Humanize surveys

Install the optional EDSL integration with `uv tool install --editable '.[fielding]'`.
An update round creates a personalized survey for every active team member,
showing the tracker’s current status for each of that person’s active assigned
tasks and asking whether to keep it or change it. Optional notes capture
progress or blockers. Unassigned work has
a separate allocation survey for one named reviewer; it lists every active
unassigned task and lets the reviewer assign it, leave it open, flag it as
probably complete for verification, or merge it into another active issue in
the same project.

```bash
tracker updates prepare --id 2026-11-22
tracker updates deploy 2026-11-22
# Send each returned respondent URL only to that person.
tracker updates collect 2026-11-22
tracker updates apply 2026-11-22
tracker assignments prepare --id allocation-2026-11-22 --reviewer alex
tracker assignments deploy allocation-2026-11-22
tracker assignments collect allocation-2026-11-22
tracker assignments apply allocation-2026-11-22
tracker intake prepare --id public-issues
tracker intake deploy public-issues
# Share the public respondent URL wherever people can report issues.
tracker intake collect public-issues
tracker intake apply public-issues
```

Deployment is a separate command because it creates live Humanize surveys.
Survey links and raw responses are stored under `.tracker/private/`, which the
CLI adds to `.gitignore`. Applying status responses appends reported task
statuses and notes. An assignment response assigns the selected engineer,
records a merge link for merged tasks, and leaves deferred tasks open.
“Probably already completed?” flags the task for verification; it does not mark
the task done. Review those tasks with `tracker status`, then confirm with
`tracker task status <task-id> done` or return them to `open`.

The issue-report survey is public and reusable. It asks for a name, project,
issue title and description, plus optional email and relevant URLs. Tell
respondents that reports become visible to the team when applied. `collect`
stores raw results privately; `apply` creates one feature and one unassigned
task per valid report, including a feature README with the report and links.
Only share the respondent URL after explicitly deploying the form.

`tracker memo build` creates an immutable dated Markdown snapshot. If the team
needs another snapshot for the same date, choose a new date; existing memo files
are not overwritten.

## Survey catalog

Use `tracker survey list` to see status-update, task-allocation, and public
issue-report rounds and whether each is prepared, deployed, collected, or
applied. It does not print respondent links. Run `tracker survey guide` for
each survey's audience, questions, and exact lifecycle commands. The engineer
status survey is personalized and covers that engineer's active assigned tasks.
