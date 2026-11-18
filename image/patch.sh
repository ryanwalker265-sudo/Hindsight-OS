#!/bin/sh
# Put your latest Hindsight code (hindsightd, shell, sdk, apps, image/rootfs)
# into an existing emulator disk, without rebuilding the whole image.
# Data already on the phone is kept.
#
#   sudo ./image/patch.sh [dist-dir]
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
DIST=$(cd "${1:-$REPO/dist}" && pwd)
WORK=$(mktemp -d /tmp/hindsight-patch.XXXXXX)
ROOT=$WORK/root

cleanup() {
  set +e
  mountpoint -q "$ROOT" 2>/dev/null && umount "$ROOT"
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM
[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)."; exit 1; }

echo "==> Opening $DIST/hindsight.qcow2"
qemu-img convert -O raw "$DIST/hindsight.qcow2" "$WORK/disk.img"
mkdir "$ROOT"
mount -o loop,compress=zstd:3 "$WORK/disk.img" "$ROOT"

echo "==> Copying Hindsight"
rm -rf "$ROOT/usr/lib/hindsight/hindsightd" "$ROOT/usr/share/hindsight/shell" \
  "$ROOT/usr/share/hindsight/sdk" "$ROOT/usr/share/hindsight/apps"
cp -r "$REPO/hindsightd" "$ROOT/usr/lib/hindsight/"
find "$ROOT/usr/lib/hindsight" -name __pycache__ -prune -exec rm -rf {} +
cp -r "$REPO/shell" "$REPO/sdk" "$REPO/apps" "$ROOT/usr/share/hindsight/"
cp -a "$HERE/rootfs/." "$ROOT/"
chmod 0755 "$ROOT/usr/bin/hindsightd" "$ROOT/usr/bin/hindsight-session" \
  "$ROOT/etc/init.d/hindsightd" "$ROOT/etc/init.d/hindsight-session" \
  "$ROOT/etc/init.d/hindsight-selftest"
git -C "$REPO" describe --always --dirty 2>/dev/null > "$ROOT/usr/share/hindsight/BUILD" || true
chroot "$ROOT" /bin/sh /usr/lib/hindsight/setup-services.sh
umount "$ROOT"

echo "==> Compressing"
qemu-img convert -c -O qcow2 "$WORK/disk.img" "$DIST/hindsight.qcow2.new"
mv "$DIST/hindsight.qcow2.new" "$DIST/hindsight.qcow2"
echo "==> Done"
