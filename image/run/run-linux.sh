#!/bin/sh
# Start Hindsight in the QEMU emulator on Linux.
#   Needs QEMU:  sudo apt install qemu-system-x86   (or your distro's package)
# Close the window to turn the phone off. Your data is kept in hindsight.qcow2.
cd "$(dirname "$0")" || exit 1
ACCEL=tcg
if [ -w /dev/kvm ]; then ACCEL=kvm; else
  echo "Note: /dev/kvm isn't available, so this runs without hardware acceleration (slow)."
fi
exec qemu-system-x86_64 \
  -name Hindsight -machine q35,accel=$ACCEL -cpu max -smp 2 -m 2048 \
  -rtc base=localtime \
  -kernel vmlinuz -initrd initramfs \
  -append "root=/dev/vda rootfstype=btrfs rw quiet console=ttyS0" \
  -drive file=hindsight.qcow2,if=virtio,format=qcow2 \
  -device virtio-vga,xres=432,yres=936 \
  -device virtio-tablet-pci -device virtio-keyboard-pci \
  -display gtk,zoom-to-fit=off -serial mon:stdio -nic none "$@"
