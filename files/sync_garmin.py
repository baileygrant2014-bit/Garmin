#!/usr/bin/env python3
"""Pull your own Garmin data into plain-text notes your AI coach can read.

Read-only. This script never writes anything back to your Garmin account.

It is a thin wrapper around the open-source python-garminconnect library by
cyberjunky (https://github.com/cyberjunky/python-garminconnect).

Typical use:

    python sync_garmin.py --login              # once, interactive
    python sync_garmin.py --days 3 --dry-run   # check it works
    python sync_garmin.py --days 3             # write ./garmin/

About your credentials:

  * Your password is typed once, into a hidden prompt. It is never stored,
    never read from an environment variable or a command-line flag, never
    written to disk, and never printed.
  * The login token Garmin hands back (good for about a year) is saved to a
    private directory with owner-only permissions, and is never printed.
  * --login refuses to run anywhere the password prompt cannot be hidden.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import stat
import sys
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

# Where the yearly login token lives. Override with GARMINTOKENS if you like.
TOKEN_DIR = Path(os.environ.get("GARMINTOKENS", Path.home() / ".garminconnect"))

# Written by --export-ci-token, read by nobody. You paste it into a GitHub
# secret and then delete it.
CI_TOKEN_FILE = Path("garmin-ci-token.txt")

# If someone tries to hand us a password the unsafe way, we say so and ignore it.
FORBIDDEN_PASSWORD_ENV = ("GARMIN_PASSWORD", "GARMIN_PASSWD", "GARMINCONNECT_PASSWORD")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def die(msg: str, code: int = 1):
    print(f"\nError: {msg}\n", file=sys.stderr)
    sys.exit(code)


def info(msg: str = "") -> None:
    print(msg, file=sys.stderr)


def private_dir(path: Path) -> Path:
    """Create a directory only the current user can read."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(stat.S_IRWXU)  # 0700
    except OSError:
        pass  # Windows / odd filesystems: best effort
    return path


def private_write(path: Path, text: str) -> None:
    """Write a file only the current user can read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with restrictive permissions before any bytes land in it.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass


def import_garmin():
    try:
        from garminconnect import Garmin  # noqa: WPS433 (deliberate late import)
    except ImportError:
        die(
            "the garminconnect library is not installed.\n"
            "  Run:  pip install -r requirements.txt\n"
            "  (Windows:  py -m pip install -r requirements.txt)"
        )
    return Garmin


def warn_about_password_env() -> None:
    leaked = [name for name in FORBIDDEN_PASSWORD_ENV if os.environ.get(name)]
    if leaked:
        info(
            "Note: ignoring "
            + ", ".join(leaked)
            + ". This script never takes your password from the environment."
            "\n      Unset it so it does not sit in your shell history or process list."
        )


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:60] or "activity"


def fmt_duration(seconds) -> str:
    if not seconds:
        return ""
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def fmt_pace(meters_per_second) -> str:
    if not meters_per_second:
        return ""
    secs_per_km = 1000.0 / float(meters_per_second)
    minutes, secs = divmod(int(round(secs_per_km)), 60)
    return f"{minutes}:{secs:02d} /km"


def first_number(*values):
    """Return the first value that is a real number (0 counts, None does not)."""
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def safe(label: str, fn, *args):
    """Call a Garmin endpoint, and shrug politely if it is unavailable.

    Garmin returns nothing for days you did not wear the watch, and
    occasionally 404s an endpoint your device does not support. Neither is
    worth crashing a morning sync over.
    """
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - any endpoint hiccup is non-fatal
        info(f"  (skipped {label}: {type(exc).__name__})")
        return None


# --------------------------------------------------------------------------
# login
# --------------------------------------------------------------------------

def cmd_login() -> int:
    """One-time interactive login. The only step that ever asks for a password."""
    Garmin = import_garmin()
    warn_about_password_env()

    if not sys.stdin.isatty():
        die(
            "--login needs a real terminal so your password can be hidden as you\n"
            "type it. Run it from Terminal (Mac) or PowerShell/cmd (Windows),\n"
            "not from a pipe, an editor's output pane, or a CI job."
        )

    info("Garmin login (one time only).")
    info("Your password is hidden as you type and is never stored or printed.\n")

    email = input("Garmin email: ").strip()
    if not email:
        die("no email entered.")

    try:
        with warnings.catch_warnings():
            # getpass only *warns* when it has to fall back to echoing your
            # keystrokes. Make that a hard stop instead.
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Garmin password (hidden): ")
    except getpass.GetPassWarning:
        die("this terminal cannot hide your password as you type. Refusing to continue.")
    except (EOFError, KeyboardInterrupt):
        die("cancelled.")
    if not password:
        die("no password entered.")

    garmin = Garmin(email=email, password=password, is_cn=False, return_on_mfa=True)

    try:
        result, state = garmin.login()
        if result == "needs_mfa":
            code = input("Garmin sent you a code. Enter it here: ").strip()
            if not code:
                die("no code entered.")
            garmin.resume_login(state, code)
    except Exception as exc:  # noqa: BLE001
        die(f"Garmin refused the login ({type(exc).__name__}: {exc})")
    finally:
        # Drop the password from memory as soon as it is no longer needed.
        del password

    private_dir(TOKEN_DIR)
    garmin.garth.dump(str(TOKEN_DIR))
    for child in TOKEN_DIR.iterdir():
        try:
            child.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    info("")
    info(f"Logged in. Token saved privately to {TOKEN_DIR} (not printed, good for ~1 year).")
    info("Next:  python sync_garmin.py --days 3 --dry-run")
    return 0


def cmd_export_ci_token() -> int:
    """Write the token bundle to a file, for pasting into a GitHub secret.

    Only needed for Path A (GitHub Actions). Local users never run this.
    """
    Garmin = import_garmin()

    if not TOKEN_DIR.exists():
        die("no saved token found. Run:  python sync_garmin.py --login")

    garmin = Garmin()
    try:
        garmin.login(str(TOKEN_DIR))
        bundle = garmin.garth.dumps()
    except Exception as exc:  # noqa: BLE001
        die(f"could not read the saved token ({type(exc).__name__}). Re-run --login.")

    private_write(CI_TOKEN_FILE, bundle)
    info(f"Wrote {CI_TOKEN_FILE} (owner-only).")
    info("")
    info("This file IS a login credential. Paste its contents into your")
    info("GARMIN_TOKEN_B64 GitHub secret, then delete the file:")
    info(f"  rm {CI_TOKEN_FILE}")
    info("Never commit it, never paste it into a chat.")
    return 0


def connect():
    """Log in from the saved token (local) or GARMIN_TOKEN_B64 (CI)."""
    Garmin = import_garmin()
    garmin = Garmin()

    ci_token = os.environ.get("GARMIN_TOKEN_B64", "").strip()
    if ci_token:
        if len(ci_token) < 512:
            die(
                "GARMIN_TOKEN_B64 looks truncated. Paste the whole contents of\n"
                "garmin-ci-token.txt into the secret, with no line breaks trimmed."
            )
        try:
            garmin.login(ci_token)
            return garmin
        except Exception as exc:  # noqa: BLE001
            die(
                f"GARMIN_TOKEN_B64 was rejected ({type(exc).__name__}). It has probably\n"
                "expired. Re-run --login, then --export-ci-token, and update the secret."
            )

    if not TOKEN_DIR.exists():
        die("not logged in yet. Run:  python sync_garmin.py --login")

    try:
        garmin.login(str(TOKEN_DIR))
    except Exception as exc:  # noqa: BLE001
        die(
            f"the saved login token no longer works ({type(exc).__name__}).\n"
            "Tokens last about a year. Run:  python sync_garmin.py --login"
        )
    return garmin


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def fetch_wellness(garmin, day: date) -> dict:
    """One day of recovery numbers, flattened into plain fields."""
    iso = day.isoformat()
    info(f"  wellness {iso}")

    summary = safe("daily summary", garmin.get_user_summary, iso) or {}
    sleep = safe("sleep", garmin.get_sleep_data, iso) or {}
    hrv = safe("hrv", garmin.get_hrv_data, iso) or {}
    readiness = safe("training readiness", garmin.get_training_readiness, iso)

    sleep_dto = (sleep or {}).get("dailySleepDTO") or {}
    sleep_seconds = first_number(
        sleep_dto.get("sleepTimeSeconds"),
        summary.get("sleepingSeconds"),
    )
    sleep_scores = sleep_dto.get("sleepScores") or {}
    sleep_score = first_number(
        (sleep_scores.get("overall") or {}).get("value"),
        sleep_dto.get("sleepScore"),
    )

    hrv_summary = (hrv or {}).get("hrvSummary") or {}
    hrv_avg = first_number(
        hrv_summary.get("lastNightAvg"),
        sleep_dto.get("avgOvernightHrv"),
        (sleep or {}).get("avgOvernightHrv"),
    )

    if isinstance(readiness, list) and readiness:
        readiness_score = first_number(readiness[0].get("score"))
    elif isinstance(readiness, dict):
        readiness_score = first_number(readiness.get("score"))
    else:
        readiness_score = None

    return {
        "date": iso,
        "resting_hr": first_number(summary.get("restingHeartRate")),
        "hrv_overnight_ms": hrv_avg,
        "hrv_status": hrv_summary.get("status"),
        "sleep_hours": round(sleep_seconds / 3600.0, 1) if sleep_seconds else None,
        "sleep_score": sleep_score,
        "body_battery_low": first_number(summary.get("bodyBatteryLowestValue")),
        "body_battery_high": first_number(summary.get("bodyBatteryHighestValue")),
        "stress_avg": first_number(summary.get("averageStressLevel")),
        "steps": first_number(summary.get("totalSteps")),
        "training_readiness": readiness_score,
        "calories_total": first_number(summary.get("totalKilocalories")),
    }


def fetch_activities(garmin, start: date, end: date) -> list:
    info(f"  activities {start} .. {end}")
    raw = safe(
        "activities",
        garmin.get_activities_by_date,
        start.isoformat(),
        end.isoformat(),
    ) or []

    activities = []
    for item in raw:
        type_key = ((item.get("activityType") or {}).get("typeKey") or "").lower()
        distance_m = first_number(item.get("distance"))
        speed = first_number(item.get("averageSpeed"))
        elevation = first_number(item.get("elevationGain"))
        activities.append(
            {
                "id": str(item.get("activityId")),
                "name": item.get("activityName") or type_key or "Activity",
                "type": type_key or "unknown",
                "start_local": item.get("startTimeLocal"),
                "date": (item.get("startTimeLocal") or "")[:10],
                "distance_km": round(distance_m / 1000.0, 2) if distance_m else None,
                "duration_seconds": first_number(item.get("duration")),
                "avg_hr": first_number(item.get("averageHR")),
                "max_hr": first_number(item.get("maxHR")),
                "avg_speed_mps": speed,
                "elevation_gain_m": round(elevation) if elevation is not None else None,
                "calories": first_number(item.get("calories")),
                "aerobic_training_effect": first_number(item.get("aerobicTrainingEffect")),
                "anaerobic_training_effect": first_number(item.get("anaerobicTrainingEffect")),
            }
        )
    activities.sort(key=lambda a: a.get("start_local") or "")
    return activities


def fetch(garmin, days: int):
    today = date.today()
    start = today - timedelta(days=days - 1)
    info(f"Pulling {days} day(s): {start} .. {today}")
    activities = fetch_activities(garmin, start, today)
    wellness = [fetch_wellness(garmin, start + timedelta(days=n)) for n in range(days)]
    return activities, wellness


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def wellness_markdown(day: dict) -> str:
    lines = [f"# Garmin wellness {day['date']}"]

    def add(label, value, suffix=""):
        if value is not None:
            lines.append(f"- {label}: {value}{suffix}")

    add("Resting HR", day["resting_hr"], " bpm")
    if day["hrv_overnight_ms"] is not None:
        raw_status = day.get("hrv_status")
        status = f" ({str(raw_status).lower()})" if raw_status else ""
        lines.append(f"- HRV (overnight): {day['hrv_overnight_ms']} ms{status}")
    if day["sleep_hours"] is not None:
        score = f" (score {day['sleep_score']})" if day["sleep_score"] is not None else ""
        lines.append(f"- Sleep: {day['sleep_hours']} h{score}")
    if day["body_battery_low"] is not None and day["body_battery_high"] is not None:
        lines.append(
            f"- Body battery: {day['body_battery_low']} -> {day['body_battery_high']}"
        )
    add("Stress (avg)", day["stress_avg"])
    add("Steps", day["steps"])
    add("Training readiness", day["training_readiness"])

    if len(lines) == 1:
        lines.append("- No data recorded (watch not worn?)")
    return "\n".join(lines) + "\n"


def activity_markdown(act: dict) -> str:
    lines = [f"# {act['name']}"]

    def add(label, value, suffix=""):
        if value not in (None, ""):
            lines.append(f"- {label}: {value}{suffix}")

    add("Date", (act.get("start_local") or "").replace("T", " ")[:16])
    add("Type", act["type"])
    add("Distance", act["distance_km"], " km")
    add("Duration", fmt_duration(act["duration_seconds"]))
    if act["avg_hr"] is not None:
        max_hr = f" (max {act['max_hr']})" if act["max_hr"] is not None else ""
        lines.append(f"- Avg HR: {act['avg_hr']} bpm{max_hr}")
    if act["avg_speed_mps"]:
        if any(word in act["type"] for word in ("run", "walk", "hik")):
            add("Avg pace", fmt_pace(act["avg_speed_mps"]))
        else:
            add("Avg speed", round(act["avg_speed_mps"] * 3.6, 1), " km/h")
    add("Elevation gain", act["elevation_gain_m"], " m")
    add("Calories", act["calories"])
    if act["aerobic_training_effect"] is not None:
        anaerobic = act["anaerobic_training_effect"]
        tail = f", anaerobic {anaerobic}" if anaerobic is not None else ""
        lines.append(f"- Training effect: aerobic {act['aerobic_training_effect']}{tail}")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# sinks
# --------------------------------------------------------------------------

def sink_files(out: Path, activities: list, wellness: list) -> None:
    """Write the garmin/ folder: one note per day, one per workout, plus data.json."""
    daily_dir = out / "daily"
    activity_dir = out / "activities"
    daily_dir.mkdir(parents=True, exist_ok=True)
    activity_dir.mkdir(parents=True, exist_ok=True)

    for day in wellness:
        (daily_dir / f"{day['date']}.md").write_text(
            wellness_markdown(day), encoding="utf-8"
        )

    for act in activities:
        day = act.get("date") or "undated"
        name = f"{day}-{slugify(act['name'])}-{act['id']}.md"
        (activity_dir / name).write_text(activity_markdown(act), encoding="utf-8")

    # data.json is cumulative: merge this run into whatever is already there.
    store_path = out / "data.json"
    store = {"activities": {}, "wellness": {}}
    if store_path.exists():
        try:
            existing = json.loads(store_path.read_text(encoding="utf-8"))
            store["activities"] = existing.get("activities") or {}
            store["wellness"] = existing.get("wellness") or {}
        except (ValueError, OSError):
            info("  (existing data.json was unreadable; starting a fresh one)")

    for act in activities:
        store["activities"][act["id"]] = act
    for day in wellness:
        store["wellness"][day["date"]] = day
    store["updated_at"] = datetime.now().isoformat(timespec="seconds")

    store_path.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")

    info("")
    info(f"Wrote {len(wellness)} daily note(s) and {len(activities)} workout note(s) to {out}/")
    info(f"Point your AI coach at {out}/ and it has your recovery context.")


def sink_supabase(activities: list, wellness: list) -> None:
    """POST {activities, wellness} to your own ingest endpoint."""
    try:
        import requests
    except ImportError:
        die("the requests library is not installed. Run: pip install -r requirements.txt")

    url = os.environ.get("GARMIN_INGEST_URL", "").strip()
    secret = os.environ.get("GARMIN_INGEST_SECRET", "").strip()
    if not url:
        die("--sink supabase needs GARMIN_INGEST_URL set to your endpoint.")
    if not url.lower().startswith("https://"):
        die("GARMIN_INGEST_URL must be https:// - refusing to send your data in the clear.")

    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    response = requests.post(
        url,
        json={"activities": activities, "wellness": wellness},
        headers=headers,
        timeout=30,
    )
    if response.status_code >= 400:
        die(f"your endpoint returned HTTP {response.status_code}: {response.text[:300]}")

    info("")
    info(
        f"Sent {len(activities)} activity record(s) and {len(wellness)} wellness "
        f"record(s) to your endpoint (HTTP {response.status_code})."
    )


def print_preview(activities: list, wellness: list) -> None:
    print()
    for day in wellness:
        print(wellness_markdown(day))
    if activities:
        for act in activities:
            print(activity_markdown(act))
    else:
        print("(no activities in this window)\n")
    print("Dry run: nothing was written. Drop --dry-run to save these.")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pull your own Garmin activities and recovery data. Read-only.",
        epilog="First run:  python sync_garmin.py --login",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="one-time interactive login (asks for email, hidden password, 2FA code)",
    )
    parser.add_argument(
        "--export-ci-token",
        action="store_true",
        help="write the token bundle to garmin-ci-token.txt for a GitHub secret (Path A only)",
    )
    parser.add_argument("--days", type=int, default=3, help="how many days back to pull (default 3)")
    parser.add_argument(
        "--sink",
        choices=("files", "supabase"),
        default="files",
        help="files: write markdown notes. supabase: POST to your own endpoint.",
    )
    parser.add_argument("--out", default="./garmin", help="output folder for --sink files")
    parser.add_argument("--dry-run", action="store_true", help="print what would be saved, save nothing")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.login:
        return cmd_login()
    if args.export_ci_token:
        return cmd_export_ci_token()

    if args.days < 1:
        die("--days must be at least 1.")
    warn_about_password_env()

    garmin = connect()
    activities, wellness = fetch(garmin, args.days)

    if args.dry_run:
        print_preview(activities, wellness)
    elif args.sink == "files":
        sink_files(Path(args.out), activities, wellness)
    else:
        sink_supabase(activities, wellness)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        info("\nCancelled.")
        sys.exit(130)
