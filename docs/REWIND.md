# How Rewind works

## Storage

Every app gets its own data volume. On the device that is a **Btrfs
subvolume**, which is what makes snapshots instant and cheap:

```
/var/lib/hindsight/
├── apps/<app>/kv/<key>.json         live data, one small file per key
├── snapshots/<app>/<time>/          read-only Btrfs snapshots of apps/<app>
└── meta/
    ├── <app>/<time>.json            what each snapshot is (label, trigger, size)
    └── _points/<time>.json          phone-wide points
```

A Btrfs snapshot shares all unchanged data with the original, so a snapshot
of an app that changed one note only costs the space of that note.

In dev mode (`--dev`, or any folder that isn't on Btrfs) the same layout is
used with plain folder copies, so apps behave identically on any computer.

## When snapshots are taken

| Trigger | When | Label shown on the dial |
| --- | --- | --- |
| `auto` | Every minute (configurable in Settings), only if the app changed | Saved automatically |
| `close` | When you leave an app, if it changed | When you left Notes |
| `delete` | Right before an app deletes something | The app's label, e.g. *Before deleting "Groceries"* |
| `checkpoint` | When an app asks, e.g. before clearing a drawing | The app's label |
| `before-rewind` | Right before a rewind replaces data | Before you rewound |

If nothing changed since the last snapshot, no new one is taken; a
`delete` label is attached to the existing snapshot instead.

A **phone point** is recorded on every automatic pass where any app changed.
It lists, for each app, the snapshot showing that app at that moment.

## Rewinding one app

1. The app's current data is saved as a `before-rewind` snapshot (this is the Undo).
2. The live subvolume is moved aside, a writable snapshot of the chosen
   snapshot takes its place, and the old one is deleted. On Btrfs each step
   is a metadata operation, so it's instant whatever the data size.
3. The interface reloads the app.

**Preview** never touches live data: the app is loaded with `?hs_at=<snapshot>`
and the SDK reads from the read-only snapshot and refuses writes.

## Rewinding the whole phone

Takes a phone-wide `before-rewind` point (so it can be undone), then restores
every app whose data differs from the chosen point.

## How long history is kept

* every snapshot from the last 2 hours
* labeled moments (deletes, checkpoints, pre-rewind) for 7 days
* one per hour for the last 2 days
* one per day for the last 30 days

Snapshots referenced by a kept phone point are always kept.

## API

All on `http://127.0.0.1:7070`, used by the SDK and the shell.

| Method & path | Does |
| --- | --- |
| `GET /api/apps` | Installed apps with snapshot counts |
| `GET /api/apps/<app>/kv[?at=<snap>]` | All values, now or at a snapshot |
| `GET /api/apps/<app>/kv/<key>[?at=<snap>]` | One value |
| `PUT /api/apps/<app>/kv/<key>` `{"value": ...}` | Save a value |
| `DELETE /api/apps/<app>/kv/<key>?label=...` | Labeled snapshot, then delete |
| `POST /api/apps/<app>/checkpoint` `{"label": ...}` | Take a labeled snapshot now |
| `GET /api/apps/<app>/snapshots` | The app's history |
| `POST /api/apps/<app>/rewind` `{"snapshot": ...}` | Restore; returns `undo` |
| `GET /api/points` / `POST /api/points` | Phone-wide points / take one now |
| `POST /api/points/<point>/rewind` | Restore the whole phone; returns `undo` |
| `GET /api/events` | Server-sent events (`snapshot`, `point`, `changed`, `rewound`) |
| `GET /api/system` | Version, storage backend, counts |
