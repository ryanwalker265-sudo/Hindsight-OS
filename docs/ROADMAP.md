# Roadmap

## Now (v0.1)

* Boots in QEMU on Windows, macOS and Linux
* Rewind on real Btrfs snapshots: per app, whole phone, preview, undo
* App SDK, three built-in apps
* CI: engine tests, an interface test in headless Chrome, an image build,
  and a boot test that checks Rewind on the device image

## Next: Android apps through Waydroid

[Waydroid](https://waydro.id) runs a full Android system in a container on
Linux and shows each Android app as a normal window. Alpine 3.24 packages
it, so the plan is:

1. **Kernel.** Waydroid needs Android's binder driver. The emulator image
   uses Alpine's `linux-virt` kernel, which doesn't include it
   (`dist/kernel-features.txt` from each build shows this). Switch the image
   to a kernel with `CONFIG_ANDROID_BINDERFS` (Alpine's `linux-lts`, or a
   small custom config).
2. **Install.** Add `waydroid` to the image, run `waydroid init` on first
   use (it downloads the Android image), and start its container service.
3. **Shell.** Show Android apps on the home screen (Waydroid lists them) and
   launch each in its own window inside the compositor.
4. **Rewind.** Android apps store their data under Waydroid's data folder.
   Put that folder on its own subvolume so all Android apps rewind together;
   later, one subvolume per Android app.

What to expect:

* Google's Play Store can't be included in Hindsight (Google only licenses it
  to certified phones). You can add it yourself through Waydroid's Google-apps
  image, but sign-in can be unreliable. F-Droid and APK files are the
  dependable route.
* Banking, payment, streaming and some game apps check whether the phone is
  Google-certified and often refuse to run.
* In the x86 emulator, apps built only for ARM chips may not run. On a phone they would.

## Then: a real phone (OnePlus 6)

The OnePlus 6 and 6T run a mainline Linux kernel and are well supported by
postmarketOS, which is also based on Alpine. The plan is to package Hindsight
as a postmarketOS interface so the same build tool (`pmbootstrap`) produces
an image for the phone, or for `qemu-amd64`.

* Build for `aarch64` (Hindsight itself is Python and web code, so nothing needs porting).
* Touch input and screen rotation in the compositor.
* Calls and texts through ModemManager. On Linux phones these work but
  depend on the carrier, so treat it as a second phone at first.
* If buying one used: avoid T-Mobile's OnePlus 6T (single SIM slot); its
  bootloader needs a carrier unlock and a token from OnePlus.

## Also needed before anyone uses this daily

* App isolation: serve each app from its own origin with its own storage access
* Lock screen, notifications, Wi-Fi and Bluetooth settings
* Installing apps from a file (a zipped app folder)
* Encrypting the data volume
* Time zone setting (the emulator shows host-local time today)
