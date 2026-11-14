#!/bin/sh
# Starts SubGen as PUID:PGID (default 1000:1000) so subtitles, the database and models belong to that
# user on the host, never to root.
#
# Started as root (the default): create /config and /models, give them to PUID:PGID, then drop to that
# user with setpriv. Needs the CHOWN, SETUID and SETGID capabilities only for this step; the server
# itself runs without any. Started as another user (compose `user:`): run directly, change nothing.
# The media folders are never touched: SubGen only adds subtitle files there.
set -eu

if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
case "$PUID" in '' | *[!0-9]*) echo "subgen: PUID must be a number (got '$PUID')" >&2; exit 64 ;; esac
case "$PGID" in '' | *[!0-9]*) echo "subgen: PGID must be a number (got '$PGID')" >&2; exit 64 ;; esac

if [ "$PUID" = "0" ]; then
    echo "subgen: warning: PUID=0 runs SubGen as root; files it writes will belong to root" >&2
    exec "$@"
fi

# Effective capabilities of this shell: CHOWN is bit 0, SETGID bit 6, SETUID bit 7.
caps=$(sed -n 's/^CapEff:[[:space:]]*//p' "/proc/$$/status")
if [ $((0x${caps:-0} & 0xC1)) -ne $((0xC1)) ]; then
    echo "subgen: cannot switch to PUID=$PUID PGID=$PGID: the container lacks the CHOWN, SETUID or SETGID" \
        "capability. Add 'cap_add: [CHOWN, SETUID, SETGID]' next to 'cap_drop: [ALL]'," \
        "or start the container as that user with 'user: \"$PUID:$PGID\"'." >&2
    exit 1
fi

for dir in "${SUBGEN_CONFIG_DIR:-/config}" "${SUBGEN_MODEL_DIR:-/models}" ${SUBGEN_WORKSPACE_DIR:+"$SUBGEN_WORKSPACE_DIR"}; do
    mkdir -p "$dir"
    chown "$PUID:$PGID" "$dir"
    # Re-own entries with another owner (left by an older root-run version or a different PUID). Folders
    # already owned by PUID:PGID were written by SubGen itself and are skipped: that keeps later starts
    # fast and avoids folders root can't read without DAC capabilities. -h changes a symlink, never its target.
    if ! find "$dir" -xdev -mindepth 1 \( -type d -user "$PUID" -group "$PGID" -prune \) \
        -o \( ! -user "$PUID" -o ! -group "$PGID" \) -exec chown -h "$PUID:$PGID" {} + 2>/dev/null; then
        echo "subgen: warning: could not give everything in $dir to $PUID:$PGID; check its permissions" >&2
    fi
done

# Keep supplementary groups added with compose `group_add` (for example a shared media group).
groups=$(id -G | tr ' ' '\n' | grep -vx -e 0 -e "$PGID" | paste -sd, -) || true
if [ -n "$groups" ]; then
    exec setpriv --reuid="$PUID" --regid="$PGID" --groups="$groups" --inh-caps=-all -- "$@"
fi
exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups --inh-caps=-all -- "$@"
