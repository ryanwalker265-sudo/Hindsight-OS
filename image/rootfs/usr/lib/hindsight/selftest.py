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

    # The interface counts as up once its page has loaded and opened its
    # live event stream back to hindsightd (the browser merely running isn't enough).
    started = time.time()
    loaded = False
    while time.time() - started < 600:
        try:
            if call("GET", "/api/system").get("eventClients", 0) >= 1:
                loaded = True
                break
        except Exception:
            pass
        time.sleep(2)
    if loaded:
        took = time.time() - started
        # Still there a little later? (catches a browser that loads, then crashes)
        time.sleep(30)
        try:
            still = call("GET", "/api/system").get("eventClients", 0) >= 1
        except Exception:
            still = False
        try:
            with open("/var/log/hindsight/session.log", encoding="utf-8", errors="replace") as fh:
                fatal = [l for l in fh.read().splitlines() if ":FATAL:" in l or "Goodbye" in l]
        except OSError:
            fatal = []
        if still and not fatal:
            say(f"interface OK (home screen loaded after {took:.0f}s and stayed up)")
        else:
            say(f"FAIL interface loaded after {took:.0f}s but then went away")
            loaded = False
    else:
        say("FAIL interface never loaded")
        for cmd in (["ps", "-o", "pid,user,args"], ["ls", "-la", "/run/user/1000", "/dev/dri"]):
            out = subprocess.run(cmd, capture_output=True, text=True)
            say(f"--- {' '.join(cmd)}")
            for line in (out.stdout + out.stderr).splitlines()[-30:]:
                say(f"    {line}")
    dump("/var/log/hindsight/session.log", 15 if loaded else 50)
    say("DONE")


if __name__ == "__main__":
    main()
