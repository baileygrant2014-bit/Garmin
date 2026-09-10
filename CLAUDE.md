# Garmin sync -- notes for Claude

This folder pulls the owner's own Garmin Connect data into plain-text notes and
reads them back. Read-only against Garmin: never write anything to their account.

## Where the data is

    garmin/daily/YYYY-MM-DD.md   one note per day: sleep, HRV, resting HR,
                                 body battery, stress, steps, readiness
    garmin/activities/*.md       one note per workout
    garmin/data.json             cumulative store, including the raw Garmin
                                 payload for every record

`garmin/` is gitignored -- it is personal health data and does not belong in the
repo. If it is missing or stale, the sync has not been run recently.

## Refreshing it

    .venv/bin/python files/sync_garmin.py --days 3 --sink files --out ./garmin

`--days` is capped at 90; pulling more gets rate-limited by Garmin. Re-pulling a
day overwrites it, so a small overlap is free and catches sleep and HRV that
Garmin backfills a night or two late.

## Reading the notes

- `n/a` means the device did not record that metric, not that the value was zero.
- A day with no data at all gets no note, rather than a note full of `n/a`. A
  missing day usually means the watch was not worn.
- Sleep and HRV only exist for nights the watch was worn to bed.
- Distances are miles and feet. `render_activity()` is the only place to change
  that.
- Single-day readings are noisy. Trends across a week are the useful signal, and
  the interesting comparison is against that person's own baseline, not a
  population norm.

You are not their doctor. Describe what the numbers show and how it relates to
their training; leave diagnosis alone.

## What not to do

- Do not run `--login` on their behalf, and do not ask for their Garmin password.
  Login is interactive, needs a real terminal, and is theirs to run: `./setup.sh`.
- Never print, echo, cat, or commit `~/.garminconnect/*` or `garmin-ci-token.txt`.
  Both are login credentials worth about a year of account access.
- Do not commit anything under `garmin/`.

## Layout

    setup.sh              one-command setup (macOS/Linux): venv, deps, login, first pull
    files/sync_garmin.py  the sync script
    files/requirements.txt pinned deps -- bump deliberately, not casually
    files/garmin-sync.yml  optional GitHub Actions daily run (Path A in the README)
    README.md             the full guide
