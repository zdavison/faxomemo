#!/bin/bash
# Functional test for ./faxomemo --install-daemon / --uninstall-daemon.
# Runs the real installer against a temp install dir, with systemctl/loginctl
# replaced by PATH shims so the user's systemd manager is never touched.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

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

# --- shims -------------------------------------------------------------------
SHIM_DIR="$WORK/bin"
mkdir -p "$SHIM_DIR"
CALLS="$WORK/calls.log"
: > "$CALLS"
for cmd in systemctl loginctl; do
    cat > "$SHIM_DIR/$cmd" <<EOF
#!/bin/sh
echo "$cmd \$*" >> "$CALLS"
exit 0
EOF
    chmod +x "$SHIM_DIR/$cmd"
done

INSTALL_DIR="$WORK/install"
UNIT_DIR="$WORK/systemd-user"

# Pre-seed a fake venv so setup_venv skips the slow pip install. The fake
# python exits 0 for any invocation, which also passes the Bluetooth probe.
mkdir -p "$INSTALL_DIR/.venv/bin"
printf '#!/bin/sh\nexit 0\n' > "$INSTALL_DIR/.venv/bin/python"
chmod +x "$INSTALL_DIR/.venv/bin/python"
touch "$INSTALL_DIR/.venv/.faxomemo-deps-installed"

# Pre-seed user state that a re-install must preserve.
echo '{"marker": "user-settings"}' > "$INSTALL_DIR/config.json"
printf 'PC1_PRINTER_DRIVER=phomemo\nPC1_PHOMEMO_TRANSPORT=usb\nPC1_PHOMEMO_USB_DEVICE=/dev/usb/lp0\n' > "$INSTALL_DIR/.faxomemo.env"

run_faxomemo() {
    PATH="$SHIM_DIR:$PATH" \
    FAXOMEMO_INSTALL_DIR="$INSTALL_DIR" \
    FAXOMEMO_UNIT_DIR="$UNIT_DIR" \
        "$REPO_DIR/faxomemo" "$@"
}

# --- install -----------------------------------------------------------------
run_faxomemo --install-daemon > "$WORK/install.log" 2>&1
check "install exits 0" test $? -eq 0

check "app code copied" test -f "$INSTALL_DIR/app/main.py"
check "icons copied" test -d "$INSTALL_DIR/icons/regular"
check "web dist copied" test -f "$INSTALL_DIR/web/dist/index.html"
check "web fonts copied" test -d "$INSTALL_DIR/web/public/fonts"
check "launcher copied" test -x "$INSTALL_DIR/faxomemo"
check "requirements copied" test -f "$INSTALL_DIR/requirements.txt"
check "label designer copied" test -f "$INSTALL_DIR/vendor/phomymo/src/web/index.html"
check "node_modules not copied" test ! -e "$INSTALL_DIR/web/node_modules"
check "repo venv not copied over fake" test -f "$INSTALL_DIR/.venv/.faxomemo-deps-installed"
check "existing config.json preserved" grep -q user-settings "$INSTALL_DIR/config.json"
check "existing .faxomemo.env preserved" grep -q 'PC1_PHOMEMO_TRANSPORT=usb' "$INSTALL_DIR/.faxomemo.env"

UNIT="$UNIT_DIR/faxomemo.service"
check "unit file written" test -f "$UNIT"
check "unit ExecStart points at install dir" grep -q "ExecStart=/bin/bash $INSTALL_DIR/faxomemo --daemon --skip-ready" "$UNIT"
check "unit restarts always" grep -q 'Restart=always' "$UNIT"
check "linger enabled" grep -q 'loginctl enable-linger' "$CALLS"
check "daemon reloaded" grep -q 'systemctl --user daemon-reload' "$CALLS"
check "service enabled and started" grep -q 'systemctl --user enable --now faxomemo' "$CALLS"

# --- re-install restarts the service ----------------------------------------
: > "$CALLS"
run_faxomemo --install-daemon > "$WORK/reinstall.log" 2>&1
check "re-install exits 0" test $? -eq 0
check "re-install restarts service" grep -q 'systemctl --user restart faxomemo' "$CALLS"
check "config.json still preserved" grep -q user-settings "$INSTALL_DIR/config.json"

# --- uninstall ---------------------------------------------------------------
: > "$CALLS"
run_faxomemo --uninstall-daemon > "$WORK/uninstall.log" 2>&1
check "uninstall exits 0" test $? -eq 0
check "service stopped and disabled" grep -q 'systemctl --user disable --now faxomemo' "$CALLS"
check "unit file removed" test ! -e "$UNIT"
check "install dir removed" test ! -e "$INSTALL_DIR"

echo
if [ "$FAILURES" -gt 0 ]; then
    echo "$FAILURES check(s) failed. Logs in $WORK (deleted on exit); re-run with 'trap' removed to inspect."
    exit 1
fi
echo "All checks passed."
