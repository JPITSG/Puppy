#!/bin/sh
# Deploy-safe restart: wait until no puppy session is mid-turn before
# restarting, so engine processes are never interrupted by a deploy.
# Usage: restart.sh [initial-delay-seconds]   (default 5)
# Caps the wait at 30 min, then restarts anyway (graceful shutdown +
# process-group INT/KILL in the runner handle that case cleanly).
sleep "${1:-5}"
i=0
while [ "$i" -lt 360 ]; do
  busy=$(sqlite3 /etc/scripts/puppy/data/puppy.db \
    "SELECT count(*) FROM sessions WHERE status='running'" 2>/dev/null || echo 0)
  [ "${busy:-0}" = "0" ] && break
  i=$((i + 1))
  sleep 5
done
exec /usr/bin/supervisorctl restart puppy
