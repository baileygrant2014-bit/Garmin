#!/usr/bin/env python3
"""
sync_garmin.py -- read-only Garmin Connect -> local files (or your own ingest).

Garmin has no official consumer API. This script uses the open-source
python-garminconnect library (which drives Garmin's mobile login flow) to pull
activities + daily wellness and send them to one of two sinks:

  --sink files      write a folder of markdown notes + a data.json  (DEFAULT)
  --sink supabase   POST to your own ingest endpoint (the GitHub Actions path)

Security posture (hardened after a community security review -- thanks Patrick):
  - Password is typed once into a HIDDEN prompt. Never stored, never in env
    vars, never in shell history, never printed. The script refuses to run in
    a terminal that can't hide it.
  - The ~1-year login token is saved to a private dir (~/.garminconnect) with
    locked-down permissions and is NEVER printed to the screen. The only way to
    get the token out (for GitHub Actions) is an explicit --export-ci-token,
    which writes it to a file you delete after pasting it into a CI secret.
  - Every string Garmin returns is sanitized before it touches a filename or a
    note (treat the API as untrusted).
  - A network failure says "network problem, do NOT re-enter your password" so
    you never get trained into re-typing it -- the habit phishing lives on.

Usage:
  python sync_garmin.py --login              one-time interactive login (email/password/2FA)
  python sync_garmin.py --days 3 --dry-run   print what would be written
  python sync_garmin.py --days 3             write markdown notes + data.json
  python sync_garmin.py --export-ci-token    write the CI token bundle to a file (Path A only)

(Windows: use "py". macOS/Linux: "python3".)
"""

import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import sys
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from garminconnect import (
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )
except ImportError:
    sys.exit("garminconnect not installed. Run: pip install -r requirements.txt")

DEFAULT_TOKEN_DIR = Path(os.environ.get("GARMINTOKENS", "") or (Path.home() / ".garminconnect"))
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "garmin"
MAX_DAYS = 90
CMD = "py sync_garmin.py" if os.name == "nt" else "python3 sync_garmin.py"

# Console output should never crash on exotic codepages (Task Scheduler logs, etc.)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


# --------------------------------------------------------------------------- #
# Sanitizers -- everything coming back from Garmin is treated as untrusted.
# --------------------------------------------------------------------------- #
def clean_text(value, limit=100):
    """Strip control characters and newlines, cap length. For display/notes."""
    if value is None:
        return None
    s = re.sub(r"[\x00-\x1f\x7f]", " ", str(value)).strip()
    return (s[:limit] + "...") if len(s) > limit else s


def fs_name(value, limit=40):
    """Reduce a string to a safe filename component (allowlist only)."""
    s = re.sub(r"[^A-Za-z0-9_-]", "_", str(value or ""))
    return s[:limit] or "unknown"


def valid_day(value):
    """Return YYYY-MM-DD if it looks like one, else 'unknown-date'."""
    s = str(value or "")[:10]
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else "unknown-date"


def contained_path(base: Path, *parts) -> Path:
    """Join parts under base and refuse anything that escapes base."""
    p = base.joinpath(*parts).resolve()
    if not p.is_relative_to(base.resolve()):
        raise ValueError("path escape blocked")
    return p


def restrict_perms(path: Path) -> None:
    """chmod 700 (dir) / 600 (file). No-op on Windows (uses ACLs instead)."""
    try:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    except OSError:
        pass


def write_private(path: Path, text: str) -> None:
    """Write a secret to disk that is owner-only from the moment it exists.

    Deliberately not path.write_text(): that creates the file with the process
    umask (usually world-readable) and would leave a window before any chmod.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    restrict_perms(path)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def safe(fn, *args):
    """Call an API getter; tolerate one bad endpoint, but STOP the whole run on
    auth/rate-limit problems so we never overwrite good notes with n/a."""
    try:
        return fn(*args)
    except (GarminConnectAuthenticationError, GarminConnectTooManyRequestsError) as exc:
        sys.exit(
            f"Stopping: {type(exc).__name__}.\n"
            f"Either the saved token expired (run: {CMD} --login) "
            "or Garmin is rate-limiting (wait an hour and try again)."
        )
    except Exception as exc:  # noqa: BLE001 -- one bad endpoint becomes a gap, not a crash
        print(f"    (skipped {fn.__name__}: {type(exc).__name__})", file=sys.stderr)
        return None


def dig(obj, *keys, default=None):
    """Nested .get() that tolerates None, missing keys, and list indices."""
    for key in keys:
        if obj is None:
            return default
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list) and isinstance(key, int) and len(obj) > key:
            obj = obj[key]
        else:
            return default
    return obj if obj is not None else default


def fmt(value, suffix="", divisor=1, digits=None):
    if value is None:
        return "n/a"
    if divisor != 1:
        value = value / divisor
    if digits is not None:
        value = round(value, digits)
        if digits == 0:
            value = int(value)
    elif isinstance(value, float) and value.is_integer():
        # Garmin hands back 48.0 as often as 48; a resting HR should not
        # render as "48.0 bpm".
        value = int(value)
    return f"{value}{suffix}"


def hms(seconds):
    if seconds is None:
        return "n/a"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}:{m:02d} h" if h else f"{m} min"


# --------------------------------------------------------------------------- #
# Auth -- password is only ever entered here, into a hidden prompt.
# --------------------------------------------------------------------------- #
def do_login(token_dir: Path) -> None:
    if not sys.stdin.isatty():
        sys.exit(
            "Run --login from a real terminal (Terminal on macOS, PowerShell or "
            "cmd on Windows) -- not an IDE console, Git Bash, or a pipe. Your "
            "password cannot be hidden here."
        )
    print("Garmin Connect one-time login (nothing is stored or echoed).")
    email = input("Garmin email: ").strip()
    with warnings.catch_warnings():
        # getpass warns if it has to fall back to echoing the password; turn that
        # warning into a hard stop so a password can never appear on screen.
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            password = getpass.getpass("Garmin password (hidden): ")
        except getpass.GetPassWarning:
            sys.exit("This terminal cannot hide your password. Use PowerShell/Terminal instead.")

    def prompt_mfa():
        return input("2FA code from Garmin (if asked): ").strip()

    token_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    restrict_perms(token_dir)

    api = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    try:
        api.login()                 # fresh credential login (handles 2FA)
        api.garth.dump(str(token_dir))  # persist tokens so we never ask again
    except Exception as exc:  # noqa: BLE001 -- never dump a traceback next to a password prompt
        sys.exit(f"Login failed ({type(exc).__name__}). Check email, password and 2FA code.")

    for f in token_dir.iterdir():
        restrict_perms(f)
    print(f"Login OK. Token saved privately to {token_dir}.")
    print("You won't need your password again until the token expires (~1 year).")
    print(f"Using GitHub Actions? Run: {CMD} --export-ci-token")


def resume(token_dir: Path) -> Garmin:
    """Load the saved token. No password, no 2FA, no prompts."""
    api = Garmin()
    api.login(str(token_dir))
    return api


def export_ci_token(token_dir: Path) -> None:
    """Write the token bundle to a file for a GitHub Actions secret.

    We write to a file (perms 600) instead of printing, so a year of account
    access never lands in terminal scrollback. Delete the file after you paste
    it into your CI secret.
    """
    import io
    import tarfile

    if not token_dir.exists() or not any(token_dir.iterdir()):
        sys.exit(f"No saved token in {token_dir}. Run: {CMD} --login")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(str(token_dir), arcname=".")
    blob = base64.b64encode(buf.getvalue()).decode()

    out = Path.cwd() / "garmin-ci-token.txt"
    write_private(out, blob)
    print(f"CI token written to: {out}")
    print("Paste its contents into your GARMIN_TOKEN_B64 GitHub secret, then DELETE this file.")
    print("Anyone who gets this file has ~1 year of access to your Garmin data.")


# --------------------------------------------------------------------------- #
# Pull + map (defensive: payloads vary by device/firmware, missing -> None)
# --------------------------------------------------------------------------- #
def pull_wellness(api: Garmin, day: str) -> dict:
    stats = safe(api.get_stats, day)
    sleep = safe(api.get_sleep_data, day)
    hrv = safe(api.get_hrv_data, day)
    readiness = safe(api.get_training_readiness, day)

    return {
        "day": day,
        "resting_hr": dig(stats, "restingHeartRate"),
        "steps": dig(stats, "totalSteps"),
        "stress_avg": dig(stats, "averageStressLevel"),
        "body_battery_low": dig(stats, "bodyBatteryLowestValue"),
        "body_battery_high": dig(stats, "bodyBatteryHighestValue"),
        "active_kcal": dig(stats, "activeKilocalories"),
        "sleep_seconds": dig(sleep, "dailySleepDTO", "sleepTimeSeconds"),
        "sleep_score": dig(sleep, "dailySleepDTO", "sleepScores", "overall", "value"),
        "hrv_ms": dig(hrv, "hrvSummary", "lastNightAvg"),
        "hrv_status": clean_text(dig(hrv, "hrvSummary", "status"), 30),
        "training_readiness": dig(readiness, 0, "score"),
        "training_readiness_level": clean_text(dig(readiness, 0, "level"), 30),
        "raw": {"stats": stats, "sleep": sleep, "hrv": hrv, "readiness": readiness},
    }


def has_data(w: dict) -> bool:
    return any(v is not None for k, v in w.items() if k not in ("day", "raw"))


def pull_activities(api: Garmin, start: str, end: str) -> list:
    acts = safe(api.get_activities_by_date, start, end) or []
    out = []
    for a in acts:
        item = {
            "id": dig(a, "activityId"),
            "name": clean_text(dig(a, "activityName")),
            "type": clean_text(dig(a, "activityType", "typeKey"), 40),
            "start_local": clean_text(dig(a, "startTimeLocal") or dig(a, "startTimeGMT"), 30),
            "distance_m": dig(a, "distance"),
            "duration_s": dig(a, "duration"),
            "avg_hr": dig(a, "averageHR"),
            "max_hr": dig(a, "maxHR"),
            "elev_gain_m": dig(a, "elevationGain"),
            "calories": dig(a, "calories"),
            "training_effect": dig(a, "aerobicTrainingEffect"),
            "raw": a,
        }
        if item["id"] is None:
            # stable fallback key so two id-less activities never collide
            seed = f"{item['name']}|{item['start_local']}".encode("utf-8")
            item["id"] = "x" + hashlib.sha1(seed).hexdigest()[:10]
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Rendering (all untrusted strings pass clean_text; names go in code spans)
# --------------------------------------------------------------------------- #
def render_daily(w: dict) -> str:
    sleep_h = w["sleep_seconds"] / 3600 if w.get("sleep_seconds") else None
    return "\n".join([
        f"# Garmin wellness {w['day']}",
        "",
        f"- Resting HR: {fmt(w['resting_hr'], ' bpm')}",
        f"- HRV (overnight): {fmt(w['hrv_ms'], ' ms')} ({w['hrv_status'] or 'n/a'})",
        f"- Sleep: {fmt(sleep_h, ' h', digits=1)} (score {fmt(w['sleep_score'])})",
        f"- Body battery: {fmt(w['body_battery_low'])} -> {fmt(w['body_battery_high'])}",
        f"- Stress (avg): {fmt(w['stress_avg'])}",
        f"- Steps: {fmt(w['steps'])}",
        f"- Training readiness: {fmt(w['training_readiness'])} ({w['training_readiness_level'] or 'n/a'})",
        f"- Active kcal: {fmt(w['active_kcal'])}",
        "",
    ])


def render_activity(a: dict, day: str) -> str:
    mi = fmt(a["distance_m"], " mi", divisor=1609.34, digits=2)
    ft = fmt(a["elev_gain_m"], " ft", divisor=0.3048, digits=0) if a["elev_gain_m"] else "n/a"
    pace = ""
    if a["distance_m"] and a["duration_s"] and a["distance_m"] > 0:
        sec_per_mi = a["duration_s"] / (a["distance_m"] / 1609.34)
        pace = f"\n- Pace: {int(sec_per_mi // 60)}:{int(sec_per_mi % 60):02d} /mi"
    return "\n".join([
        f"# Garmin activity {day} ({a['type'] or 'unknown'})",
        "",
        f"- Name: `{(a['name'] or 'Activity').replace('`', '')}`",
        f"- Start: {a['start_local'] or 'n/a'}",
        f"- Distance: {mi}",
        f"- Duration: {hms(a['duration_s'])}" + pace,
        f"- Avg HR: {fmt(a['avg_hr'], ' bpm', digits=0)} (max {fmt(a['max_hr'], '', digits=0)})",
        f"- Elevation gain: {ft}",
        f"- Training effect: {fmt(a['training_effect'], '', digits=1)}",
        f"- Calories: {fmt(a['calories'], '', digits=0)}",
        "",
    ])


# --------------------------------------------------------------------------- #
# Sinks
# --------------------------------------------------------------------------- #
def load_store(store_path: Path) -> dict:
    fresh = {"wellness": {}, "activities": {}, "last_sync": None}
    if not store_path.exists():
        return fresh
    try:
        store = json.loads(store_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print("  (existing data.json unreadable -- starting fresh)")
        return fresh
    if (
        not isinstance(store, dict)
        or not isinstance(store.get("wellness"), dict)
        or not isinstance(store.get("activities"), dict)
    ):
        print("  (existing data.json has unexpected shape -- starting fresh)")
        return fresh
    return store


def sink_files(wellness: list, activities: list, out_dir: Path, dry_run: bool) -> None:
    store_path = out_dir / "data.json"
    store = load_store(store_path)

    for w in wellness:
        if not has_data(w):
            print(f"  {w['day']} (no data -- skipped, existing note kept)")
            continue
        store["wellness"][w["day"]] = w
        note = render_daily(w)
        if dry_run:
            print(note)
        else:
            path = contained_path(out_dir, "daily", f"{w['day']}.md")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(note, encoding="utf-8")

    for a in activities:
        day = valid_day(a["start_local"])
        print(f"  {day}  {a['type'] or '?'}  {a['name'] or ''}")
        store["activities"][str(a["id"])] = a
        note = render_activity(a, day)
        if dry_run:
            print(note)
        else:
            fname = f"{day}-{fs_name(a['type'])}-{fs_name(a['id'], 20)}.md"
            try:
                path = contained_path(out_dir, "activities", fname)
            except ValueError:
                print("    (unsafe filename blocked)")
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(note, encoding="utf-8")

    if not dry_run:
        store["last_sync"] = datetime.now().isoformat(timespec="seconds")
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps(store, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"Done. Output in {out_dir}")
    else:
        print("Dry run -- nothing written.")


def sink_supabase(wellness: list, activities: list) -> None:
    import requests

    url = os.environ.get("GARMIN_INGEST_URL")
    secret = os.environ.get("GARMIN_INGEST_SECRET") or os.environ.get("SESSION_LOG_SECRET")
    if not url or not secret:
        sys.exit("--sink supabase needs GARMIN_INGEST_URL and GARMIN_INGEST_SECRET.")
    resp = requests.post(
        url,
        json={"activities": activities, "wellness": wellness},
        headers={"Authorization": f"Bearer {secret}"},
        timeout=60,
    )
    resp.raise_for_status()
    print(f"ingest OK: {resp.status_code}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def run_sync(api: Garmin, days: int, sink: str, out_dir: Path, dry_run: bool) -> None:
    today = date.today()
    start = (today - timedelta(days=days - 1)).isoformat()
    end = today.isoformat()

    print(f"Pulling {days} day(s): {start} .. {end}")
    wellness = [pull_wellness(api, (today - timedelta(days=i)).isoformat()) for i in range(days)]
    activities = pull_activities(api, start, end)
    print(f"Pulled {len(activities)} activities, {len(wellness)} wellness day(s).")

    if sink == "supabase":
        if dry_run:
            print(json.dumps({"wellness": wellness, "activities": activities}, indent=2, default=str))
            print("Dry run -- nothing sent.")
            return
        sink_supabase(wellness, activities)
    else:
        sink_files(wellness, activities, out_dir, dry_run)


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only Garmin Connect sync.")
    p.add_argument("--login", action="store_true", help="one-time interactive login")
    p.add_argument("--export-ci-token", action="store_true", help="write CI token bundle to a file (Path A)")
    p.add_argument("--days", type=int, default=3, help=f"days back to pull (1-{MAX_DAYS}, default 3)")
    p.add_argument("--sink", choices=["files", "supabase"], default="files")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="output dir for --sink files")
    p.add_argument("--tokens", type=Path, default=DEFAULT_TOKEN_DIR, help="token directory")
    p.add_argument("--dry-run", action="store_true", help="print instead of writing/sending")
    args = p.parse_args()

    if args.login:
        do_login(args.tokens)
        return
    if args.export_ci_token:
        export_ci_token(args.tokens)
        return

    days = max(1, min(args.days, MAX_DAYS))
    if days != args.days:
        print(f"--days limited to {days} (protects against rate-limiting).")

    try:
        api = resume(args.tokens)
    except GarminConnectAuthenticationError:
        sys.exit(f"No valid saved token in {args.tokens}.\nRun: {CMD} --login")
    except (GarminConnectConnectionError, OSError) as exc:
        sys.exit(
            f"Could not reach Garmin ({type(exc).__name__}). This looks like a "
            "network problem, NOT a login problem -- do not re-enter your "
            "password. Try again later."
        )
    except Exception as exc:  # noqa: BLE001
        sys.exit(
            f"Unexpected error loading the saved token ({type(exc).__name__}).\n"
            f"If this persists, run: {CMD} --login"
        )

    run_sync(api, days, args.sink, args.out, args.dry_run)


if __name__ == "__main__":
    main()
