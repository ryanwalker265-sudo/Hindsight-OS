"""Boot-time self-test, run when the kernel command line has hs_selftest=1.

Prints HINDSIGHT-SELFTEST lines to the serial console so an automated boot
(image/boot-test.sh, CI) can see that Rewind works on the real device image.
Only meant for throwaway boots (the test leaves a hidden "selftest" key in Notes).
"""
import json
import subprocess
import time
import urllib.request

BASE = "http://127.0.0.1:7070"


def say(message):
    print(f"HINDSIGHT-SELFTEST: {message}", flush=True)


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read() or b"null")


def dump(path, lines=25):
    """Show the end of a log on the console so a failed boot explains itself."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            tail = fh.read().splitlines()[-lines:]
    except OSError as exc:
        tail = [f"(can't read: {exc})"]
    say(f"--- {path}")
    for line in tail:
        say(f"    {line}")


def main():
    info = None
    for _ in range(360):
        try:
            info = call("GET", "/api/system")
            break
        except Exception:
            time.sleep(0.5)
    if not info:
        say("FAIL hindsightd never answered")
        dump("/var/log/hindsight/hindsightd.log")
        say("DONE")
        return
    say(f"api ok version={info['version']} backend={info['backend']['name']}")

    out = subprocess.run(["btrfs", "subvolume", "list", "/"],
                         capture_output=True, text=True).stdout
    vols = sorted(line.split()[-1] for line in out.splitlines()
                  if "var/lib/hindsight/apps/" in line)
    say(f"app subvolumes: {', '.join(vols) or 'none'}")

    call("PUT", "/api/apps/notes/kv/selftest", {"value": "here"})
    snap = call("POST", "/api/apps/notes/checkpoint", {"label": "Self-test"})["snapshot"]
    call("DELETE", "/api/apps/notes/kv/selftest?label=Self-test%20delete")
    gone = call("GET", "/api/apps/notes/kv/selftest?missing=ok").get("missing")
    result = call("POST", "/api/apps/notes/rewind", {"snapshot": snap["id"]})
    back = call("GET", "/api/apps/notes/kv/selftest?missing=ok")
    ok = bool(gone) and back.get("value") == "here"
    say(f"rewind {'OK' if ok else 'FAIL'} (deleted={gone}, restored={back.get('value')!r}, "
        f"undo={result.get('undo')})")

    path = f"/var/lib/hindsight/snapshots/notes/{snap['id']}"
    prop = subprocess.run(["btrfs", "property", "get", "-ts", path, "ro"],
                          capture_output=True, text=True)
    say(f"snapshot is {prop.stdout.strip() or prop.stderr.strip()}")

    for _ in range(600):
        if subprocess.run(["pgrep", "-f", "chromium"], capture_output=True).returncode == 0:
            say("session OK (interface running)")
            break
        time.sleep(0.5)
    else:
        say("FAIL interface did not start")
        dump("/var/log/hindsight/session.log", 40)
        for cmd in (["ps", "-o", "pid,user,args"], ["ls", "-la", "/run/user/1000", "/dev/dri"],
                    ["rc-service", "hindsight-session", "status"]):
            out = subprocess.run(cmd, capture_output=True, text=True)
            say(f"--- {' '.join(cmd)}")
            for line in (out.stdout + out.stderr).splitlines()[-25:]:
                say(f"    {line}")
    say("DONE")


if __name__ == "__main__":
    main()
