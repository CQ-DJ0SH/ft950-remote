#!/bin/sh
# Nightly reboot of the Pi (root crontab, 04:00) - but not while the radio is on.
#
# "On": the FT-950 answers over CAT (/api/status connected) or the Shelly relay
# that powers it is on (/api/power-switch). A night QSO, a running Hunt or a
# recording must not be cut. When the backend does not answer at all the Pi is
# rebooted as before - that is when a reboot helps.
#
#   install -m 755 deploy/nightly-reboot.sh /usr/local/bin/ft950-nightly-reboot
#   crontab:  00 4 * * * /usr/local/bin/ft950-nightly-reboot
#   test:     DRY=1 /usr/local/bin/ft950-nightly-reboot
set -u
API=${FT950_API:-https://127.0.0.1:8444}

get() { curl -sk --max-time 5 "$API$1" 2>/dev/null; }
field() { python3 -c "import json,sys
try: print(str(json.load(sys.stdin).get('$1')).lower())
except Exception: print('?')"; }

status=$(get /api/status)
if [ -z "$status" ]; then
    why="backend not answering"
else
    connected=$(printf '%s' "$status" | field connected)
    power=$(get /api/power-switch | field on)
    if [ "$connected" = "true" ] || [ "$power" = "true" ]; then
        logger -t nightly-reboot "skipped: radio on (CAT connected=$connected, Shelly on=$power)"
        [ -n "${DRY:-}" ] && echo "skipped: radio on (CAT connected=$connected, Shelly on=$power)"
        exit 0
    fi
    why="radio off (CAT connected=$connected, Shelly on=$power)"
fi
logger -t nightly-reboot "rebooting: $why"
if [ -n "${DRY:-}" ]; then
    echo "would reboot: $why"
else
    /usr/sbin/reboot
fi
