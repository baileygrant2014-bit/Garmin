#!/usr/bin/env bash
#
# One-command setup for the Garmin -> AI sync (macOS / Linux).
#
#     ./setup.sh
#
# Creates a private virtualenv, installs the pinned dependencies, logs you into
# Garmin once, and pulls your last few days so you can see it working.
#
# Safe to re-run: every step checks whether it is already done. It never asks
# for your password twice, and never touches anything outside this folder and
# your Garmin token directory.
#
# Windows: follow the manual steps in the README instead (this is bash).

set -euo pipefail

cd "$(dirname "$0")"
REPO="$PWD"
VENV="$REPO/.venv"
OUT="$REPO/garmin"
SCRIPT="$REPO/files/sync_garmin.py"
TOKENS="${GARMINTOKENS:-$HOME/.garminconnect}"
DAYS="${1:-3}"

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$SCRIPT" ] || fail "Can't find $SCRIPT -- run this from inside the cloned repo."

# --------------------------------------------------------------------------
bold "1/5  Looking for Python 3.9+"
PY=""
for candidate in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        PY="$candidate"
        break
    fi
done
[ -n "$PY" ] || fail "Python 3.9+ not found.
  macOS:  brew install python     (or download from python.org)
  Linux:  sudo apt install python3 python3-venv"
echo "     $($PY --version) -- $(command -v "$PY")"

# --------------------------------------------------------------------------
bold "2/5  Setting up a private virtualenv (.venv)"
if [ -x "$VENV/bin/python" ]; then
    echo "     Already there, reusing it."
else
    "$PY" -m venv "$VENV" 2>/dev/null || fail "Could not create a virtualenv.
  On Debian/Ubuntu you may need:  sudo apt install python3-venv"
    echo "     Created $VENV"
fi
VPY="$VENV/bin/python"

# --------------------------------------------------------------------------
bold "3/5  Installing dependencies (pinned versions)"
"$VPY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
"$VPY" -m pip install --quiet --timeout 120 -r "$REPO/files/requirements.txt" ||
    fail "Dependency install failed. Check your internet connection and retry."
echo "     $("$VPY" -m pip list 2>/dev/null | grep -i '^garminconnect' | tr -s ' ')"

# --------------------------------------------------------------------------
bold "4/5  Garmin login"
if [ -d "$TOKENS" ] && [ -n "$(ls -A "$TOKENS" 2>/dev/null)" ]; then
    echo "     Already logged in (token in $TOKENS). Skipping."
    echo "     To log in again as someone else: rm -rf \"$TOKENS\" && ./setup.sh"
else
    cat <<'EXPLAIN'
     This is the ONE step that needs your Garmin account, and the only
     time you ever type your password.

     Your password goes into a hidden prompt (nothing appears as you type),
     is never saved, never printed, and never leaves your computer except
     as the login itself. What gets stored is a token, in a private folder.

EXPLAIN
    if [ ! -t 0 ]; then
        fail "No terminal attached, so your password can't be hidden.
  Run ./setup.sh directly in Terminal, not through a pipe or an editor pane."
    fi
    read -r -p "     Ready? Press Enter to log in (Ctrl-C to stop) " _
    "$VPY" "$SCRIPT" --login
fi

# --------------------------------------------------------------------------
bold "5/5  Pulling your last $DAYS day(s)"
"$VPY" "$SCRIPT" --days "$DAYS" --sink files --out "$OUT"

bold "Done."
cat <<EOF
Your data is in:  $OUT
  daily/       one note per day (sleep, HRV, resting HR, body battery, stress)
  activities/  one note per workout
  data.json    everything, cumulative

Open Claude Code in this folder and ask it something like
"read my garmin notes and tell me how my recovery looks this week".

To refresh it by hand any time:
  $VPY $SCRIPT --days 3 --sink files --out $OUT

To run it automatically every morning at 6am, run 'crontab -e' and add:
  0 6 * * * $VPY $SCRIPT --days 3 --sink files --out $OUT
EOF
