#!/bin/sh
# Start sshd with new host keys and the public key given in AUTHORIZED_KEY.
set -eu

: "${AUTHORIZED_KEY:?set AUTHORIZED_KEY to a public key line}"

ssh-keygen -A
printf '%s\n' "$AUTHORIZED_KEY" > /home/tester/.ssh/authorized_keys
chown tester:tester /home/tester/.ssh/authorized_keys
chmod 600 /home/tester/.ssh/authorized_keys

exec /usr/sbin/sshd -D -e
