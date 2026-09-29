#!/bin/bash
# Functional test for `./faxomemo web`, which opens the settings UI in a browser.
# A real listener stands in for the server; xdg-open and systemctl are PATH shims.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
SERVER_PID=""
cleanup() {
    [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT

FAILURES=0
check() {
    local desc="$1"
    shift
    if "$@"; then
        echo "ok: $desc"
    else
        echo "FAIL: $desc"
        FAILURES=$((FAILURES + 1))
    fi
}

SHIM_DIR="$WORK/bin"
mkdir -p "$SHIM_DIR"
OPENED="$WORK/opened.log"
CALLS="$WORK/calls.log"
: > "$OPENED"
: > "$CALLS"

cat > "$SHIM_DIR/xdg-open" <<EOF
#!/bin/sh
echo "\$1" >> "$OPENED"
EOF
cat > "$SHIM_DIR/systemctl" <<EOF
#!/bin/sh
echo "systemctl \$*" >> "$CALLS"
exit 0
EOF
chmod +x "$SHIM_DIR/xdg-open" "$SHIM_DIR/systemctl"

UNIT_DIR="$WORK/systemd-user"
mkdir -p "$UNIT_DIR"

free_port() {
    python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

run_web() {
    PATH="$SHIM_DIR:$PATH" \
    FAXOMEMO_UNIT_DIR="$UNIT_DIR" \
    FAXOMEMO_PORT="$1" \
        "$REPO_DIR/faxomemo" web > "$WORK/web.log" 2>&1
}

# --- server already running ---------------------------------------------------
PORT="$(free_port)"
python3 -m http.server "$PORT" --bind 127.0.0.1 > /dev/null 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 40); do
    (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && break
    sleep 0.25
done

run_web "$PORT"
check "exits 0 when the server is up" test $? -eq 0
check "opens the settings UI" grep -qx "http://127.0.0.1:$PORT/" "$OPENED"
check "does not touch systemd when the server is up" test ! -s "$CALLS"

EXPECTED_PASSWORD="$(cd "$REPO_DIR" && python3 -c 'from app import device_password; print(device_password.get_device_password())')"
check "device password is not empty" test -n "$EXPECTED_PASSWORD"
check "prints the device password" grep -q "Device Password: $EXPECTED_PASSWORD" "$WORK/web.log"

kill "$SERVER_PID" 2>/dev/null
wait "$SERVER_PID" 2>/dev/null
SERVER_PID=""

# --- server down, no service installed ---------------------------------------
: > "$OPENED"
DEAD_PORT="$(free_port)"
run_web "$DEAD_PORT"
check "fails when nothing is listening and no service is installed" test $? -ne 0
check "does not open a browser at a dead port" test ! -s "$OPENED"
check "explains how to start faxomemo" grep -q -- "--install-daemon" "$WORK/web.log"

# --- server down, service installed ------------------------------------------
: > "$OPENED"
: > "$CALLS"
touch "$UNIT_DIR/faxomemo.service"
run_web "$DEAD_PORT"
check "starts the installed service" grep -q 'systemctl --user start faxomemo' "$CALLS"
check "still fails when the service never binds the port" test ! -s "$OPENED"

echo
if [ "$FAILURES" -eq 0 ]; then
    echo "All checks passed."
else
    echo "$FAILURES check(s) failed."
    exit 1
fi
