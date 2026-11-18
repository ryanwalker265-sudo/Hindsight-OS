Hindsight - emulator build
==========================

This folder is a whole phone: hindsight.qcow2 is its disk, vmlinuz and
initramfs start it, and the run scripts open it in the QEMU emulator.

1. Install QEMU
   Windows: https://qemu.weilnetz.de/w64/ (then, for speed, turn on
            "Windows Hypervisor Platform" in Windows Features and restart)
   Mac:     brew install qemu
   Linux:   sudo apt install qemu-system-x86   (or your distro's package)

2. Start it
   Windows: double-click run-windows.bat
   Mac:     ./run-mac.sh
   Linux:   ./run-linux.sh

3. Try Rewind
   Open Notes, delete the "Try Rewind" note, then click the Rewind button
   (bottom right) and drag the dial to the right. Click "Restore this version".

The first start takes a minute or two. Click inside the window to use the
mouse as your finger; Ctrl+Alt+G releases the mouse.
Everything you do is saved in hindsight.qcow2. To start fresh, download
the zip again.
