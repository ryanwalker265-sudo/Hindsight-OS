@echo off
rem Start Hindsight in the QEMU emulator on Windows.
rem   1. Install QEMU from https://qemu.weilnetz.de/w64/ (default folder is fine).
rem   2. Optional but much faster: turn on "Windows Hypervisor Platform" in
rem      "Turn Windows features on or off", then restart.
rem   3. Double-click this file. Close the window to turn the phone off.
cd /d "%~dp0"

set QEMU=qemu-system-x86_64.exe
where %QEMU% >nul 2>nul
if errorlevel 1 set QEMU="C:\Program Files\qemu\qemu-system-x86_64.exe"

%QEMU% -name Hindsight -machine q35 -accel whpx,kernel-irqchip=off -accel tcg ^
  -cpu max -smp 2 -m 2048 -rtc base=localtime ^
  -kernel vmlinuz -initrd initramfs ^
  -append "root=/dev/vda rootfstype=btrfs rw quiet console=ttyS0" ^
  -drive file=hindsight.qcow2,if=virtio,format=qcow2 ^
  -device virtio-vga,xres=432,yres=936 ^
  -device virtio-tablet-pci -device virtio-keyboard-pci ^
  -display sdl -nic none

if errorlevel 1 (
  echo.
  echo QEMU didn't start. Check that it's installed in C:\Program Files\qemu
  pause
)
