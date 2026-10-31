#!/usr/bin/env bash
# Start/stop/status for the local politracker web UI (http://127.0.0.1:5057).
# Usage: ./tracker.sh {start|stop|restart|status}
DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE=/tmp/politracker.pid
LOGFILE=/tmp/politracker-web.log
PORT=5057

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "${1:-status}" in
  start)
    if running; then echo "already running (PID $(cat "$PIDFILE"))"; exit 0; fi
    cd "$DIR"   # separate from the launch: `cd && cmd &` would background a subshell and $! would be its PID
    nohup ./venv/bin/python -m politracker serve --port $PORT > "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    for _ in $(seq 1 20); do
      curl -s -o /dev/null "http://127.0.0.1:$PORT/" && { echo "started (PID $(cat "$PIDFILE")) - http://127.0.0.1:$PORT"; exit 0; }
      sleep 0.5
    done
    echo "failed to start; see $LOGFILE"; exit 1 ;;
  stop)
    if running; then kill "$(cat "$PIDFILE")" && echo "stopped"; else echo "not running"; fi
    rm -f "$PIDFILE" ;;
  restart) "$0" stop; sleep 1; "$0" start ;;
  status)
    if running && curl -s -o /dev/null "http://127.0.0.1:$PORT/"; then echo "running (PID $(cat "$PIDFILE")) - http://127.0.0.1:$PORT"
    else echo "not running"; exit 1; fi ;;
esac
