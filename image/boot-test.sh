#!/bin/sh
# Boot the built image without a window, run the on-device self-test, and
# save a screenshot of the phone's screen.
#
#   ./image/boot-test.sh [dist-dir] [timeout-seconds]
#
# Writes boot-serial.log and boot-screen.png into dist-dir. The disk itself
# is not changed (QEMU's -snapshot mode throws all writes away).
set -eu
DIST=$(cd "${1:-dist}" && pwd)
TIMEOUT=${2:-900}
ACCEL=tcg
[ -w /dev/kvm ] && ACCEL=kvm
LOG="$DIST/boot-serial.log"
QMP=$(mktemp -u /tmp/hindsight-qmp.XXXXXX)
: > "$LOG"

qemu-system-x86_64 \
  -machine q35,accel=$ACCEL -cpu max -smp 2 -m 2048 -snapshot \
  -kernel "$DIST/vmlinuz" -initrd "$DIST/initramfs" \
  -append "root=/dev/vda rootfstype=btrfs rw console=ttyS0 hs_selftest=1" \
  -drive file="$DIST/hindsight.qcow2",if=virtio,format=qcow2 \
  -device virtio-vga,xres=432,yres=936 \
  -device virtio-tablet-pci -device virtio-keyboard-pci \
  -display none -serial file:"$LOG" -qmp unix:"$QMP",server,nowait -nic none &
QEMU_PID=$!
trap 'kill $QEMU_PID 2>/dev/null || true; rm -f "$QMP"' EXIT

echo "Booting ($ACCEL). Waiting up to ${TIMEOUT}s for the self-test..."
start=$(date +%s)
while ! grep -q "HINDSIGHT-SELFTEST: DONE" "$LOG" 2>/dev/null; do
  if ! kill -0 $QEMU_PID 2>/dev/null; then echo "QEMU exited early"; break; fi
  if [ $(( $(date +%s) - start )) -gt "$TIMEOUT" ]; then echo "Timed out"; break; fi
  sleep 5
done
grep "HINDSIGHT-SELFTEST" "$LOG" || true

# The self-test waits until the home screen has loaded; give it a moment to
# finish drawing, then take a picture.
SETTLE=${SETTLE:-20}
[ "$ACCEL" = kvm ] && SETTLE=${SETTLE_KVM:-5}
sleep "$SETTLE"
python3 - "$QMP" "$DIST/boot-screen.png" <<'EOF'
import json, socket, sys
sock = socket.socket(socket.AF_UNIX)
sock.connect(sys.argv[1])
f = sock.makefile("rw")
def send(cmd, **args):
    f.write(json.dumps({"execute": cmd, "arguments": args} if args else {"execute": cmd}) + "\n")
    f.flush()
    while True:
        msg = json.loads(f.readline())
        if "return" in msg or "error" in msg:
            return msg
json.loads(f.readline())          # greeting
send("qmp_capabilities")
print("screendump:", send("screendump", filename=sys.argv[2], format="png"))
EOF

if grep -q "HINDSIGHT-SELFTEST: rewind OK" "$LOG" && grep -q "HINDSIGHT-SELFTEST: interface OK" "$LOG"; then
  echo "Boot test passed. Screenshot: $DIST/boot-screen.png"
else
  echo "Boot test FAILED. Last serial output:"
  tail -n 40 "$LOG"
  exit 1
fi
