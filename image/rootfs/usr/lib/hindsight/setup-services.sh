#!/bin/sh
# Which services start at boot. Run inside the image (build.sh and patch.sh
# both call it), so it is safe to run more than once.
set -u
add() { rc-update add "$1" "$2" >/dev/null 2>&1 || true; }

for s in devfs dmesg udev udev-trigger udev-settle; do add $s sysinit; done
# loopback brings up 127.0.0.1, which hindsightd and the interface talk over.
for s in loopback modules sysctl hostname bootmisc syslog root localmount; do add $s boot; done
for s in udev-postmount seatd hindsightd hindsight-session hindsight-selftest; do add $s default; done
for s in killprocs savecache mount-ro; do add $s shutdown; done
