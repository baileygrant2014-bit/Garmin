# Connect your Garmin to AI (free setup)

> **Paste this into Claude Code, not a regular Claude chat.** This guide installs
> software and runs commands on your computer, which a normal chat window cannot
> do. Claude Code is the free coding agent that runs in your terminal and can
> actually do these steps for you.
>
> New to Claude Code? Here is the setup walkthrough:
> [Claude Code setup video](https://www.skool.com/athlete-ai-community/classroom/4d559c78?md=9ff91bc6878648d382f7b1cac766d504)

Pull your own Garmin data (workouts plus sleep, HRV, resting HR, body battery,
stress, training readiness) into a folder your AI coach reads, or into your own
database. This is the recovery data Strava cannot give you.

This is built on the open-source **python-garminconnect** library by cyberjunky.
The script here is a thin wrapper around it. Full library and docs:
[github.com/cyberjunky/python-garminconnect](https://github.com/cyberjunky/python-garminconnect)

> **Security-hardened after a community red-team (thanks to Patrick in the
> group).** Your password is typed once into a hidden prompt and is never
> stored, never put in an environment variable, never left in your command
> history, and never printed. Your year-long login token is saved privately and
> never shown on screen. The script even refuses to run where your password
> can't be hidden. Everything Garmin sends back is treated as untrusted and
> sanitized before it reaches a filename or a note. Read it top to bottom if
> you want -- it's about 490 lines, comments and all.

## The easy way: paste this into Claude Code and let it build everything

You do not download anything, make any folders, or write any code. Claude Code does
all of it. Open Claude Code, paste the prompt below, and send it. It walks you
through the whole thing one step at a time, and the only thing you ever type is your
Garmin login (once), because it's your account.

```text
I want to connect my Garmin watch so you can read my training and recovery data,
and I'm starting from scratch. I don't want to download anything, create any
folders, or write any code myself. You do all of it. Walk me through it one step
at a time, in plain English, and stop to wait for me whenever you need something
from me.

The goal: pull my own Garmin data (my workouts plus my recovery numbers: sleep,
HRV, resting heart rate, body battery, stress, and training readiness) into a
folder on my computer that you can read every time I ask. This is the recovery
data Strava can't give you. Read-only, you should never write anything back to my
Garmin account.

Here is everything I need you to do, end to end. Do it all yourself:

1. Make a new folder for this on my computer (something like "garmin-ai") and work
   inside it. I should not have to create it myself.
2. Write the sync script for me. Use the open-source python-garminconnect library
   by cyberjunky (https://github.com/cyberjunky/python-garminconnect). The script
   should pull my recent activities plus my daily wellness (sleep, HRV, resting
   HR, body battery, stress, steps, training readiness) and save them as a clean
   "garmin/" folder of plain-English markdown notes plus a data.json file. One
   wellness note per day, one note per workout.
3. Write the requirements.txt and install whatever is needed. Check whether I
   already have Python first, and help me install it if I don't.
4. Do the one-time Garmin login. Stop and ask me for my Garmin email and password
   when you reach this step, that is the only thing I will type. Handle the 2FA
   code if Garmin asks for one. Keep my password and login token out of this chat,
   never print them back to me, and save the login token so I don't have to log in
   again next time.
5. Test it by pulling my last 3 days and showing me the results so I know it works.
6. Once that works, ask me whether I want it to run automatically every morning,
   and if I say yes, set that up for me too.

Go one step at a time. Don't dump everything at once, and don't make me touch any
files, folders, or code myself. You handle all of that. When you need me to
approve a command, just ask and I'll say yes.
```

That is the whole setup. Everything below is reference: what the data looks like,
how to schedule it, and the manual steps if you would rather build it by hand or
understand what Claude made for you.

---

## Manual setup (by hand, if you skip the prompt above)

The bundled [`files/`](files/) folder next to this guide has everything:
[`sync_garmin.py`](files/sync_garmin.py) (the pull script),
[`requirements.txt`](files/requirements.txt) (what to install), and
[`garmin-sync.yml`](files/garmin-sync.yml) (the optional cloud automation for
Path A). Put `sync_garmin.py` and `requirements.txt` in a folder on your
computer, for example `garmin-ai/`, and open a terminal there.

Then run the one-time setup:

1. Install Python 3.11+ from python.org, then install the library:

   ```bash
   pip install -r requirements.txt
   ```

   On Windows, if `python`/`pip` is not found, use the `py` launcher:
   `py -m pip install -r requirements.txt`.

2. Log in once. This is the only time you enter your password or a 2FA code.
   Run it from a real terminal (Terminal on Mac, PowerShell or cmd on Windows):

   ```bash
   python sync_garmin.py --login
   ```

   ```powershell
   py sync_garmin.py --login
   ```

   It asks for your Garmin email, then your password in a **hidden prompt**
   (nothing shows as you type), then a 2FA code if Garmin asks. Your password is
   never stored and never printed. It saves a login token on your computer that
   lasts about a year -- also never printed to the screen.

   **Only if you're using Path A (GitHub Actions):** after login, run
   `python sync_garmin.py --export-ci-token`. That writes the token bundle to a
   file called `garmin-ci-token.txt`. Paste its contents into your
   `GARMIN_TOKEN_B64` GitHub secret, then delete the file. (Path B / local users
   never need this.)

3. Test it:

   ```bash
   python sync_garmin.py --days 3 --dry-run
   ```

   You should see your last 3 days of activities and wellness print out.

---

## What you get

By default the script writes a clean folder your AI can read:

```text
garmin/
  daily/2026-06-28.md                       # one wellness note per day
  activities/2026-06-28-running-12345.md    # one note per workout
  data.json                                 # the full store, updated each run
```

A daily note looks like this:

```text
# Garmin wellness 2026-06-28

- Resting HR: 48 bpm
- HRV (overnight): 72 ms (BALANCED)
- Sleep: 7.7 h (score 84)
- Body battery: 28 -> 96
- Stress (avg): 31
- Steps: 11240
- Training readiness: 81 (HIGH)
- Active kcal: 842
```

Anything your device didn't record shows as `n/a` rather than vanishing, so you
can tell "I slept badly" apart from "the watch wasn't on my wrist".

And a workout note:

```text
# Garmin activity 2026-06-28 (running)

- Name: `Morning Run`
- Start: 2026-06-28 06:12:33
- Distance: 10.0 mi
- Duration: 52 min
- Pace: 5:13 /mi
- Avg HR: 148 bpm (max 171)
- Elevation gain: 276 ft
- Training effect: 3.4
- Calories: 712
```

Distances are miles and feet. If you want metric, `render_activity()` is the one
function to change.

Point your AI coach at the `garmin/` folder and it has your recovery context every
morning. (Sleep and HRV only fill in on nights you actually wear the watch to bed.)

`data.json` is cumulative -- each run merges into it rather than replacing it, so
your history builds up even though you only pull the last few days at a time. It
also keeps the raw Garmin payload for every record, so you can pull out a field
the notes don't show without re-syncing.

A day with no data at all is skipped rather than written, so a re-sync can never
blank out a note you already have.

### Options

| Flag | What it does |
|------|--------------|
| `--login` | one-time interactive login (the only step that asks for a password) |
| `--days N` | how many days back to pull (default 3) |
| `--dry-run` | print what it would save, save nothing |
| `--sink files` | write the `garmin/` folder of notes (default) |
| `--sink supabase` | POST to your own endpoint instead |
| `--out PATH` | where to write the notes (default: a `garmin/` folder next to the script) |
| `--tokens PATH` | where the login token lives (default `~/.garminconnect`) |
| `--export-ci-token` | write the token bundle for a GitHub secret (Path A only) |

`--days` is capped at 90. Pulling more in one go gets you rate-limited by Garmin,
which costs you the whole run.

Why `--days 3` and not `--days 1`? Garmin backfills sleep and HRV for a night or
two after the fact. Re-pulling a day just overwrites it, so a small overlap costs
nothing and catches the late arrivals.

---

## Path A: GitHub Actions (cloud, automatic)

1. Put the script in a GitHub repo of your own -- **a private one**. The
   workflow expects `sync_garmin.py` and `requirements.txt` at
   `scripts/garmin/`; if you put them elsewhere, change the two paths in the
   workflow and nothing else. Copy `garmin-sync.yml` into `.github/workflows/`
   in that repo.

2. In the repo: Settings > Secrets and variables > Actions, add:

   | Secret | Value |
   |--------|-------|
   | `GARMIN_TOKEN_B64` | the contents of `garmin-ci-token.txt` from `--export-ci-token` (a base64 tarball of your token folder, which the workflow unpacks) |
   | `GARMIN_INGEST_URL` | your ingest endpoint, if you use one |
   | `SESSION_LOG_SECRET` | the shared secret your endpoint checks |

   If you only want the files mode and no database, change the workflow's last
   step to `--sink files` and skip the URL and secret -- but then the notes only
   exist inside the CI runner, which is thrown away when the job ends. To keep
   them you would have to commit them back, which means your sleep, HRV and
   resting heart rate live in a git repo. Fine in a private repo you own; if
   that sounds wrong, Path B is the answer.

3. Open the Actions tab and click Run workflow once to confirm a green run. After
   that it runs every morning on its own.

You only touch it again if your password changes or the yearly token expires; then
re-run `--login`, re-run `--export-ci-token`, and update the `GARMIN_TOKEN_B64`
secret.

---

## Path B: Local cron (your computer, no GitHub)

After Step 1, schedule the script on your own machine.

**Mac / Linux:**

```bash
crontab -e
# run every morning at 6am:
0 6 * * * cd /path/to/garmin-ai && python3 sync_garmin.py --days 3 --sink files --out ./garmin
```

**Windows (Task Scheduler):** create a Basic Task that runs daily and calls:

```text
py C:\path\to\garmin-ai\sync_garmin.py --days 3 --sink files --out C:\path\to\garmin-ai\garmin
```

Your machine has to be on and awake at the scheduled time.

---

## Sending to a database instead of files

If you have your own endpoint that accepts the data, use `--sink supabase`:

```bash
export GARMIN_INGEST_URL="https://yoursite.com/api/garmin/ingest"
export GARMIN_INGEST_SECRET="your-shared-secret"
python sync_garmin.py --days 3 --sink supabase
```

It POSTs `{activities, wellness}` with an `Authorization: Bearer` header, and
sends the raw Garmin payload alongside the tidied fields. `SESSION_LOG_SECRET`
is accepted as an alias for `GARMIN_INGEST_SECRET`, which is what the GitHub
Actions path uses.

---

## When something breaks

| What you see | What to do |
|--------------|------------|
| `No valid saved token` | run `python3 sync_garmin.py --login` |
| `Stopping: GarminConnectAuthenticationError` | the yearly token expired -- run `--login` again |
| `Stopping: GarminConnectTooManyRequestsError` | Garmin is rate-limiting you; wait an hour, and pull fewer days |
| `Could not reach Garmin ... network problem` | exactly what it says. **Do not re-enter your password** -- nothing is wrong with your login |
| `Run --login from a real terminal` | you're in an IDE console, Git Bash, or a pipe; use Terminal or PowerShell |
| `(skipped get_training_readiness: ...)` | normal -- your device doesn't report that one; everything else still saves |
| `(no data -- skipped, existing note kept)` | you didn't wear the watch that day |
| login suddenly fails for everyone | Garmin changed something: bump the pinned `garminconnect` version, then re-run `--login` |

That third row is deliberate. A tool that says "login failed, try your password
again" every time the wifi drops is training you to type your Garmin password
into whatever asks -- which is the exact habit phishing relies on.

---

## Notes and limits

- This uses an unofficial login flow (Garmin has no public API). It works through
  the [python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
  library. If Garmin changes their login and it stops working, update the library:
  `pip install -U garminconnect`, then re-run `--login`.
- Read-only. The script never writes anything back to your Garmin account.
- Keep your token private. It is a login credential (about a year of access).
  Never post it, never commit it to git, never paste it into a chat. The included
  `.gitignore` already excludes it, along with the `garmin/` data folder.
- The dependencies are pinned to exact versions on purpose, so a future
  compromised release can't slip in during an install. Bump them deliberately.
- Your notes are plain text files. If this folder lives in Dropbox / OneDrive /
  iCloud, they sync to that cloud. Fine if that's what you want -- just know it.

## Local vs cloud (your choice, made on purpose)

- **Path B (local)** keeps everything on your machine. Nothing leaves your
  computer except the read-only calls to Garmin. Most private option.
- **Path A (cloud)** deliberately sends your data to your own ingest endpoint so
  it can feed a dashboard. That's a conscious trade: convenience and a live
  dashboard in exchange for your data leaving your machine. Pick the one that
  fits you -- both are supported, neither is hidden.

## Credits

The heavy lifting is done by
[python-garminconnect](https://github.com/cyberjunky/python-garminconnect) by
cyberjunky (MIT). `sync_garmin.py` is a wrapper that calls it and formats the
results. Security hardening came out of a community red-team -- thanks to Patrick
in the group.
