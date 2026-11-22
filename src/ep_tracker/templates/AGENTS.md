# Agent guidance for this team tracker

This repository is the canonical tracker for this team. Code may live in other
repositories linked from projects here.

## Before acting

- Run `tracker agent next` from this directory and follow its `next_steps`.
- Run it again after material changes so the guidance reflects current state.
- Use `tracker guide` for the full workflow and `tracker status` for the
  current summary.

## State and files

- Use the `tracker` CLI for people, projects, repos, features, tasks, status,
  assignments, PR links, update rounds, and memos.
- People default to role `engineer`; other roles are excluded from assignment
  survey choices unless updated to engineer.
- Do not edit `.tracker/events/`, `.tracker/tracker.json`, or
  `.tracker/updates/` by hand. State changes are append-only events.
- Feature documentation belongs in `projects/<project>/features/<feature>/README.md`;
  images belong in that feature's `images/` directory. Git history records
  edits to these working documents.
- Tasks may be attached to a feature or stand alone; a standalone task may
  optionally name a project. Use `tracker task create` for standalone build,
  review, or approval work. Unassigned standalone tasks appear in the normal
  reviewer assignment survey.
- Never commit, print, or share `.tracker/private/`. It holds personalized
  Humanize links and raw respondent data.

## Update rounds

- Use `tracker survey guide` to explain status-update and assignment surveys;
  use `tracker survey list` to show existing rounds and lifecycle stages.
- The status-update survey is personalized for each active engineer, shows
  each active assigned task's current tracker status, and asks whether to keep
  or change it. Public issue intake uses `tracker intake
  prepare/deploy/collect/apply`; tell respondents reports become team-visible
  when applied. Intake creates a feature and an unassigned task per valid
  report. Its reusable respondent URL and raw results stay in `.tracker/private/`.
- `tracker updates prepare` builds local EDSL survey artifacts. Review them
  before fielding.
- `tracker updates deploy` creates live Humanize surveys. Do this only when the
  team has authorized the round, then share each respondent link only with its
  intended person.
- Collect responses with `tracker updates collect`. Applying them changes task
  status and records notes.
- For unassigned work, choose a reviewer and prepare one allocation survey:
  `tracker assignments prepare --id <round-id> --reviewer <person-id>`. It lists
  every unassigned task and lets the reviewer assign an engineer, defer it, flag
  probable completion for verification, or merge it into another active issue
  in the same project.
- Deploy, collect, and apply an assignment round using the matching
  `tracker assignments` commands. The merge keeps the original task in history
  and records its surviving task ID. Probable completion requires verification;
  it does not mark the task done.
- `tracker intake deploy` creates a public, reusable issue-report URL. Share it
  intentionally. Collect results privately, review them, and apply them to
  append one feature and unassigned task per valid report.

## Daily review

Build the day's memo with `tracker memo build`. It groups features and tasks by
status and preserves each dated memo as an immutable snapshot.
