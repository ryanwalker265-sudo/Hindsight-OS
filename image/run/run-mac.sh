#!/bin/sh
# Start Hindsight in the QEMU emulator on a Mac.
#   Needs QEMU:  brew install qemu
# Intel Macs use hardware acceleration. On Apple silicon the x86 image runs
# under emulation, so expect it to be slow.
cd "$(dirname "$0")" || exit 1
ACCEL=tcg
[ "$(uname -m)" = "x86_64" ] && ACCEL=hvf
exec qemu-system-x86_64 \
  -name Hindsight -machine q35,accel=$ACCEL -cpu max -smp 2 -m 2048 \
  -rtc base=localtime \
  -kernel vmlinuz -initrd initramfs \
  -append "root=/dev/vda rootfstype=btrfs rw quiet console=ttyS0" \
  -drive file=hindsight.qcow2,if=virtio,format=qcow2 \
  -device virtio-vga,xres=432,yres=936 \
  -device virtio-tablet-pci -device virtio-keyboard-pci \
  -display cocoa -serial mon:stdio -nic none "$@"
