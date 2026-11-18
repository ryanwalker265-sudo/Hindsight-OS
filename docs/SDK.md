# Building apps for Hindsight

A Hindsight app is a folder with a `manifest.json` and web pages (HTML, CSS,
JavaScript). Anything it stores through the SDK is covered by Rewind
automatically, with no extra work.

## 1. Make the folder

```
apps/counter/
├── manifest.json
├── icon.svg
└── index.html
```

`manifest.json`:

```json
{
  "id": "counter",
  "name": "Counter",
  "version": "0.1.0",
  "entry": "index.html",
  "icon": "icon.svg",
  "color": "#1f5a99",
  "order": 5,
  "description": "Counts taps."
}
```

* `id`: lowercase letters, digits and dashes, up to 32 characters. It's also the URL: `/apps/counter/`.
* `icon`: drawn in a circle on the home screen; give it a full-bleed background.
* `order`: position on the home screen (lower comes first).

## 2. Use the SDK

```html
<!doctype html>
<link rel="stylesheet" href="/sdk/hindsight.css">
<header class="hs-bar"><h1>Counter</h1></header>
<button class="hs-btn primary hs-edit-only" id="tap">Tap</button>
<p id="count"></p>
<script src="/sdk/hindsight.js"></script>
<script>
  const show = async () => {
    count.textContent = await Hindsight.storage.get("count", 0);
  };
  tap.onclick = async () => {
    const n = (await Hindsight.storage.get("count", 0)) + 1;
    await Hindsight.storage.set("count", n);
    show();
  };
  Hindsight.on("rewound", show);
  show();
</script>
```

### Storage

| Call | Does |
| --- | --- |
| `await Hindsight.storage.get(key, fallback)` | One value (or `fallback`) |
| `await Hindsight.storage.getAll(prefix)` | `{key: value}` for every key starting with `prefix` |
| `await Hindsight.storage.set(key, value)` | Save any JSON value (up to 8 MB) |
| `await Hindsight.storage.delete(key, {label})` | Delete. The phone saves a Rewind point first, named by `label` |

Keys are strings up to 150 bytes. Prefixes like `note:` make related keys easy to list.

### Rewind

| Call | Does |
| --- | --- |
| `await Hindsight.checkpoint("Before clearing the drawing")` | Save a named point now, before a big change |
| `Hindsight.onFlush(fn)` | Called right before the phone rewinds or leaves your app: save anything unsaved |
| `Hindsight.on("rewound", fn)` | Your data was restored; reload it |
| `Hindsight.isPreview` | `true` while the shell shows your app in the past |

Good labels say what's about to happen in plain words, like
*Before deleting "Groceries"*. They appear on the Rewind dial.

**Preview mode.** When someone turns the dial, the shell opens your app with
your data from that time. Reads work as normal; writes throw. Hide editing
controls with the `hs-edit-only` class (from `hindsight.css`), or check
`Hindsight.isPreview`.

### Talking to the phone

| Call | Does |
| --- | --- |
| `Hindsight.system.toast("Saved")` | Show a short message |
| `Hindsight.system.openRewind()` | Open the Rewind dial for your app |
| `Hindsight.system.goHome()` | Go to the home screen |

## 3. Run it

Put the folder in `apps/` and run `python3 -m hindsightd --dev`. Your app
appears on the home screen. Rebuild the image to put it on the device.

## Look and feel

`/sdk/hindsight.css` gives you the system font (Atkinson Hyperlegible Next),
colors as CSS variables (`--hs-blue`, `--hs-ink`, `--hs-paper`, `--hs-sun`...),
`.hs-bar`, `.hs-btn` (`.primary`, `.danger`, `.icon`) and `.hs-empty`.
Using it is optional.

## Limits today

* Apps share one web origin, so an app could read another app's storage.
  Per-app isolation is on the [roadmap](ROADMAP.md).
* No network permission model yet; the emulator has no network.
