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
- Do not edit `.tracker/events/`, `.tracker/tracker.json`, or
  `.tracker/updates/` by hand. State changes are append-only events.
- Feature documentation belongs in `projects/<project>/features/<feature>/README.md`;
  images belong in that feature's `images/` directory. Git history records
  edits to these working documents.
- Never commit, print, or share `.tracker/private/`. It holds personalized
  Humanize links and raw respondent data.

## Update rounds

- `tracker updates prepare` builds local EDSL survey artifacts. Review them
  before fielding.
- `tracker updates deploy` creates live Humanize surveys. Do this only when the
  team has authorized the round, then share each respondent link only with its
  intended person.
- Collect responses with `tracker updates collect`. Applying them changes task
  status and records notes; ownership changes remain pending requests.
- Review requests with `tracker task requests`, then accept or reject each
  using `tracker task resolve-request <event-id> --decision accept|reject`.

## Daily review

Build the day's memo with `tracker memo build`. It groups features and tasks by
status and preserves each dated memo as an immutable snapshot.
