# Agent guidance for ep-tracker

## Purpose

`ep-tracker` is a Python CLI that creates and manages Git-backed team trackers.
The installed CLI is the interface for tracker users; people and agents should
not edit tracker JSON files directly.

## Operating a team tracker

1. Run `tracker agent next` from the tracker directory before taking action.
2. Treat its `data` and `next_steps` as the current workflow guidance. Run it
   again after each material change.
3. Use `tracker guide` for the complete workflow and `tracker history` to
   inspect the append-only event record.
   Use `tracker survey guide` to explain survey types and `tracker survey list`
   to inspect their lifecycle without exposing private links.
4. Make state changes through `tracker` commands. Never edit or delete files in
   `.tracker/events/`, `.tracker/tracker.json`, `.tracker/updates/`, or
   `.tracker/assignments/` by hand.
5. Do not read, commit, or share `.tracker/private/`; it contains participant
   links and raw Humanize responses and is gitignored.
6. Humanize deployment creates live surveys. Run `tracker updates deploy` or
   `tracker assignments deploy` only when the user has asked to field that
   round. Send each respondent URL only to its intended person.
7. For unassigned work, use `tracker assignments prepare --id <round>
   --reviewer <person>` to create one allocation survey listing all unassigned
   tasks and all active engineers. Only an authorized reviewer should apply it.

## Implementing changes in this Python repo

- Keep commands agent-first: non-interactive by default, explicit about inputs
  and outputs, with one JSON envelope on stdout and structured errors. Logs
  belong on stderr. Keep `guide` and state-aware `agent next` discoverable.
- Make external side effects explicit commands. Never hide Humanize deployment
  or response retrieval inside artifact construction.
- Keep canonical tracker state append-only. Add event types in the store
  projection and expose mutations through Click commands; do not add direct
  JSON editing workflows.
- Keep Humanize deployment and response retrieval explicit. Use EDSL's native
  Survey and Results artifacts; preserve private links and raw responses under
  `.tracker/private/`.
- Keep team tracker guidance in `src/ep_tracker/templates/AGENTS.md` in sync
  with this tool's actual behavior.
