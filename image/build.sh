#!/bin/sh
# Build a bootable Hindsight disk image for the QEMU emulator (x86_64).
#
#   sudo ./image/build.sh [output-dir]
#
# Needs: Linux, root, btrfs-progs, qemu-utils, curl, tar. Takes ~5-10 minutes
# and downloads ~400 MB of Alpine Linux packages.
#
# Output (in output-dir, default ./dist):
#   hindsight.qcow2   the phone's disk (Btrfs, so Rewind uses real snapshots)
#   vmlinuz           Linux kernel
#   initramfs         early boot files
#   run-*.sh/.bat     scripts that start the emulator
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
OUT=$(mkdir -p "${1:-$REPO/dist}" && cd "${1:-$REPO/dist}" && pwd)
ALPINE=${ALPINE:-v3.24}
ARCH=x86_64
MIRROR=${MIRROR:-https://dl-cdn.alpinelinux.org/alpine}
SIZE=${SIZE:-6G}
WORK=$(mktemp -d /tmp/hindsight-build.XXXXXX)
ROOT=$WORK/root
IMG=$WORK/disk.img

PACKAGES="
alpine-base
linux-virt
btrfs-progs
python3
eudev udev-init-scripts udev-init-scripts-openrc
seatd seatd-openrc
cage
chromium
mesa-dri-gallium
font-dejavu
"

say() { printf '\n==> %s\n' "$*"; }

cleanup() {
  set +e
  for m in dev/pts dev sys proc; do
    mountpoint -q "$ROOT/$m" 2>/dev/null && umount -l "$ROOT/$m"
  done
  mountpoint -q "$ROOT" 2>/dev/null && umount "$ROOT"
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)."; exit 1; }
for tool in mkfs.btrfs qemu-img curl tar; do
  command -v "$tool" >/dev/null || { echo "Missing $tool"; exit 1; }
done

# --------------------------------------------------------------- apk tools
say "Fetching apk tools and Alpine signing keys ($ALPINE)"
mkdir -p "$WORK/apk" "$WORK/keys" "$ROOT"
INDEX_URL="$MIRROR/$ALPINE/main/$ARCH/APKINDEX.tar.gz"
curl -fsSL "$INDEX_URL" | tar -xzO APKINDEX > "$WORK/APKINDEX"
pkg_version() {
  awk -v p="$1" 'BEGIN{RS="";FS="\n"} {n="";v="";for(i=1;i<=NF;i++){if($i~/^P:/)n=substr($i,3);if($i~/^V:/)v=substr($i,3)} if(n==p){print v; exit}}' "$WORK/APKINDEX"
}
fetch_apk() {
  ver=$(pkg_version "$1")
  [ -n "$ver" ] || { echo "Package $1 not found in $INDEX_URL"; exit 1; }
  curl -fsSL -o "$WORK/apk/$1.apk" "$MIRROR/$ALPINE/main/$ARCH/$1-$ver.apk"
  mkdir -p "$WORK/apk/$1"
  tar -xzf "$WORK/apk/$1.apk" -C "$WORK/apk/$1" 2>/dev/null || true
}
# Bootstrapping trust: these two come over HTTPS from the Alpine mirror.
# Every other package is checked against the keys from alpine-keys.
fetch_apk apk-tools-static
fetch_apk alpine-keys
APK="$WORK/apk/apk-tools-static/sbin/apk.static"
[ -x "$APK" ] || { echo "apk.static not found in apk-tools-static"; exit 1; }
cp "$WORK/apk/alpine-keys/usr/share/apk/keys/$ARCH/"*.pub "$WORK/keys/" 2>/dev/null \
  || cp "$WORK/apk/alpine-keys/etc/apk/keys/"*.pub "$WORK/keys/"

# ------------------------------------------------------------- filesystem
say "Creating a $SIZE Btrfs disk"
truncate -s "$SIZE" "$IMG"
mkfs.btrfs -q -L hindsight "$IMG"
mount -o loop,compress=zstd:3,noatime "$IMG" "$ROOT"

mkdir -p "$ROOT/etc/apk" "$ROOT/etc/mkinitfs" "$ROOT/proc" "$ROOT/sys" "$ROOT/dev"
cp -r "$WORK/keys" "$ROOT/etc/apk/keys"
printf '%s\n%s\n' "$MIRROR/$ALPINE/main" "$MIRROR/$ALPINE/community" > "$ROOT/etc/apk/repositories"
# Early-boot image needs Btrfs and virtio drivers to find the disk.
echo 'features="base btrfs virtio"' > "$ROOT/etc/mkinitfs/mkinitfs.conf"
mount -t proc none "$ROOT/proc"
mount --rbind /sys "$ROOT/sys"
mount --rbind /dev "$ROOT/dev"

say "Installing Alpine packages"
# shellcheck disable=SC2086
"$APK" --root "$ROOT" --initdb --arch "$ARCH" --keys-dir "$ROOT/etc/apk/keys" \
  --repositories-file "$ROOT/etc/apk/repositories" --update-cache --no-progress \
  add $PACKAGES

# ----------------------------------------------------------------- Hindsight
say "Installing Hindsight"
install -d "$ROOT/usr/lib/hindsight" "$ROOT/usr/share/hindsight"
cp -r "$REPO/hindsightd" "$ROOT/usr/lib/hindsight/"
find "$ROOT/usr/lib/hindsight" -name __pycache__ -prune -exec rm -rf {} +
cp -r "$REPO/shell" "$REPO/sdk" "$REPO/apps" "$ROOT/usr/share/hindsight/"
cp -a "$HERE/rootfs/." "$ROOT/"
chmod 0755 "$ROOT/usr/bin/hindsightd" "$ROOT/usr/bin/hindsight-session" \
  "$ROOT/etc/init.d/hindsightd" "$ROOT/etc/init.d/hindsight-session" \
  "$ROOT/etc/init.d/hindsight-selftest"
git -C "$REPO" describe --always --dirty 2>/dev/null > "$ROOT/usr/share/hindsight/BUILD" || true

# ------------------------------------------------------------------ system
say "Configuring the system"
chroot "$ROOT" /bin/sh -eu <<'EOS'
echo hindsight > /etc/hostname
printf '127.0.0.1 localhost hindsight\n::1 localhost hindsight\n' > /etc/hosts
echo 'LABEL=hindsight / btrfs rw,noatime,compress=zstd:3 0 0' > /etc/fstab

# The phone's user. The interface runs as this account, not as root.
adduser -D -u 1000 -g "Hindsight" -s /bin/sh hindsight
for g in video input seat audio; do addgroup hindsight "$g" 2>/dev/null || true; done

# No text logins on the screen (the interface owns it). The serial console
# gives a root shell for debugging: there's no network, so only someone
# running the emulator can reach it.
sed -i -e '/^tty[1-6]::/d' -e '/^#ttyS0::/d' -e '/^ttyS0::/d' /etc/inittab
echo 'ttyS0::respawn:/sbin/getty -n -l /bin/sh -L 115200 ttyS0 vt100' >> /etc/inittab

sh /usr/lib/hindsight/setup-services.sh

install -d -m 0755 /var/lib/hindsight
KVER=$(ls /lib/modules | head -n1)
mkinitfs -c /etc/mkinitfs/mkinitfs.conf "$KVER"
EOS

# What the kernel offers for later work (Android apps need binder).
grep -E '^CONFIG_(ANDROID_BINDER_IPC|ANDROID_BINDERFS|DRM_VIRTIO_GPU)=' "$ROOT"/boot/config-* \
  > "$OUT/kernel-features.txt" || true

say "Collecting boot files"
cp "$ROOT/boot/vmlinuz-virt" "$OUT/vmlinuz"
cp "$ROOT/boot/initramfs-virt" "$OUT/initramfs"
chmod 0644 "$OUT/vmlinuz" "$OUT/initramfs"   # /boot copies are root-only
du -sh "$ROOT" | awk '{print "installed size: " $1}'

for m in dev/pts dev sys proc; do
  mountpoint -q "$ROOT/$m" && umount -l "$ROOT/$m"
done
umount "$ROOT"

say "Compressing the disk"
qemu-img convert -c -O qcow2 "$IMG" "$OUT/hindsight.qcow2"
cp "$HERE/run/"* "$OUT/"
chmod +x "$OUT"/*.sh
sed -i 's/\r*$/\r/' "$OUT/run-windows.bat"   # Windows wants CRLF line endings
ls -lh "$OUT"
say "Done. Start it with $OUT/run-linux.sh (or run-windows.bat / run-mac.sh)."
