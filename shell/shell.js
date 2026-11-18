/* Hindsight shell: home screen, app hosting, and the Rewind dial. */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const screen = $("screen");
  const SVGNS = "http://www.w3.org/2000/svg";

  const S = {
    apps: [],
    byId: {},
    current: null,      // id of the open app
    settings: {},
    lastSaved: null,    // seconds since epoch of the newest Rewind point
    rw: null,           // the open Rewind session, if any
  };
  const nowSec = () => Date.now() / 1000;

  // ------------------------------------------------------------------ API
  async function api(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
    return data;
  }

  // ------------------------------------------------------------ time text
  const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
  const dateFmt = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long" });
  const dayFmt = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short" });
  const hourFmt = new Intl.DateTimeFormat(undefined, { hour: "numeric" });
  const listFmt = Intl.ListFormat ? new Intl.ListFormat(undefined, { type: "conjunction" }) : null;
  const joinNames = (names) => (listFmt ? listFmt.format(names) : names.join(", "));

  function relTime(sec) {
    const d = Math.max(0, nowSec() - sec);
    if (d < 45) return "Just now";
    if (d < 90) return "1 minute ago";
    if (d < 3600) return `${Math.round(d / 60)} minutes ago`;
    if (d < 5400) return "1 hour ago";
    if (d < 86400) return `${Math.round(d / 3600)} hours ago`;
    const days = Math.round(d / 86400);
    return days <= 1 ? "Yesterday" : `${days} days ago`;
  }
  function sameDay(a, b) { return a.toDateString() === b.toDateString(); }
  function absTime(sec) {
    const t = new Date(sec * 1000);
    const today = new Date();
    const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
    const time = timeFmt.format(t);
    if (sameDay(t, today)) return `Today at ${time}`;
    if (sameDay(t, yesterday)) return `Yesterday at ${time}`;
    return `${dayFmt.format(t)} at ${time}`;
  }
  function shortTime(sec) {
    const t = new Date(sec * 1000);
    return sameDay(t, new Date()) ? timeFmt.format(t) : `${dayFmt.format(t)}, ${timeFmt.format(t)}`;
  }

  // ------------------------------------------------------------ settings
  async function loadSettings() {
    try {
      const { records } = await api("GET", "/api/apps/settings/kv");
      S.settings = Object.fromEntries(Object.entries(records).map(([k, r]) => [k, r.value]));
    } catch (_) {
      S.settings = {};
    }
    document.documentElement.dataset.sky = S.settings["home.sky"] || "time";
    paintSky();
  }

  // ------------------------------------------------------- sky & status bar
  // The sun's path is drawn in real pixels so it fits any screen shape.
  const ARC = { x0: -4, x1: 438, base: 150, peak: 30, w: 434, h: 178 };
  // Half an ellipse from the left horizon (sunrise) to the right (sunset).
  const arcRy = () => Math.min(ARC.base - ARC.peak, ((ARC.x1 - ARC.x0) / 2) * 0.75);
  function arcPoint(t) {
    const rx = (ARC.x1 - ARC.x0) / 2, ry = arcRy();
    const a = Math.PI * (1 - t);
    return [ARC.x0 + rx + rx * Math.cos(a), ARC.base - ry * Math.sin(a)];
  }
  function mixHex(a, b, t) {
    const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
    const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
    return "#" + pa.map((v, i) => Math.round(v + (pb[i] - v) * t).toString(16).padStart(2, "0")).join("");
  }
  function paintSky() {
    const now = new Date();
    const hours = now.getHours() + now.getMinutes() / 60;
    const mode = document.documentElement.dataset.sky;
    // Daylight rises from 5:00, peaks at noon, sets by 19:00.
    const light = Math.max(0, Math.sin(Math.PI * (hours - 5) / 14));
    if (mode === "time") screen.style.setProperty("--sky", mixHex("#0e2c4f", "#1f5a99", Math.min(1, light * 1.4)));
    else if (mode === "night") screen.style.setProperty("--sky", "#0e2c4f");
    else screen.style.removeProperty("--sky");

    const sky = $("sky");
    const w = sky.clientWidth || 434, h = sky.clientHeight || 178;
    Object.assign(ARC, { w, h, x0: -4, x1: w + 4, base: h - 28, peak: 26 });
    sky.setAttribute("viewBox", `0 0 ${w} ${h}`);
    sky.textContent = "";
    const el = (tag, attrs) => {
      const n = document.createElementNS(SVGNS, tag);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      sky.append(n);
      return n;
    };
    const rx = (ARC.x1 - ARC.x0) / 2, ry = arcRy();
    el("line", { class: "horizon", x1: 0, y1: ARC.base, x2: w, y2: ARC.base });
    el("path", { class: "path", d: `M ${ARC.x0} ${ARC.base} A ${rx} ${ry} 0 0 1 ${ARC.x1} ${ARC.base}` });
    const label = (hour, x, anchor) => {
      const d = new Date(now); d.setHours(hour, 0, 0, 0);
      const t = el("text", { class: "hour", x, y: ARC.base + 18, "text-anchor": anchor });
      t.textContent = timeFmt.format(d);
    };
    label(6, 22, "start");
    label(18, w - 22, "end");
    const day = (hours - 6) / 12;
    if (day >= 0 && day <= 1) {
      const [x, y] = arcPoint(day);
      el("circle", { class: "sun", cx: x, cy: y, r: 13 });
    } else {
      const night = (((hours - 18) % 24) + 24) % 24 / 12;
      const [x, y] = arcPoint(night);
      el("circle", { class: "moon", cx: x, cy: y, r: 10 });
    }
  }

  function renderStatus() {
    const now = new Date();
    $("clock").textContent = timeFmt.format(now);
    $("date").textContent = dateFmt.format(now);
    const saved = $("saved");
    saved.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"
      stroke-linecap="round" aria-hidden="true"><path d="M5.2 8.6A8 8 0 1 1 4 13"/><path d="M4.6 4.2v4.6h4.6"/></svg>`;
    const text = document.createElement("span");
    text.textContent = S.lastSaved ? `Saved ${relTime(S.lastSaved).toLowerCase()}` : "Rewind is on";
    saved.append(text);
  }

  async function refreshSaved() {
    try {
      const [{ points }, { apps }] = await Promise.all([api("GET", "/api/points"), api("GET", "/api/apps")]);
      const latest = [points.length ? points[points.length - 1].created : 0,
                      ...apps.map((a) => a.latest || 0)];
      S.lastSaved = Math.max(...latest) || null;
    } catch (_) { /* keep the old value */ }
    renderStatus();
  }

  // ---------------------------------------------------------------- home
  async function loadApps() {
    for (let attempt = 0; ; attempt++) {
      try {
        const { apps } = await api("GET", "/api/apps");
        S.apps = apps;
        S.byId = Object.fromEntries(apps.map((a) => [a.id, a]));
        break;
      } catch (_) {
        await new Promise((r) => setTimeout(r, Math.min(2000, 250 * (attempt + 1))));
      }
    }
    renderGrid();
  }

  function renderGrid() {
    const grid = $("grid");
    grid.textContent = "";
    for (const app of S.apps) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "icon";
      btn.style.setProperty("--c", app.color);
      const face = document.createElement("span");
      face.className = "face";
      if (app.icon) {
        const img = document.createElement("img");
        img.src = app.icon;
        img.alt = "";
        face.append(img);
      }
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = app.name;
      btn.append(face, name);
      onPress(btn, () => openApp(app.id), (x, y) => showMenu(app, x, y));
      li.append(btn);
      grid.append(li);
    }
  }

  // Tap opens; long-press or right-click shows the menu.
  function onPress(el, tap, hold) {
    let timer = null, start = null, held = false;
    el.addEventListener("pointerdown", (e) => {
      held = false;
      start = [e.clientX, e.clientY];
      timer = setTimeout(() => { held = true; hold(e.clientX, e.clientY); }, 520);
    });
    el.addEventListener("pointermove", (e) => {
      if (start && Math.hypot(e.clientX - start[0], e.clientY - start[1]) > 10) clearTimeout(timer);
    });
    const cancel = () => clearTimeout(timer);
    el.addEventListener("pointerup", cancel);
    el.addEventListener("pointerleave", cancel);
    el.addEventListener("click", (e) => { if (held) { e.preventDefault(); return; } tap(); });
    el.addEventListener("contextmenu", (e) => { e.preventDefault(); cancel(); held = true; hold(e.clientX, e.clientY); });
  }

  let menuEls = null;
  function closeMenu() {
    if (menuEls) { menuEls.forEach((n) => n.remove()); menuEls = null; }
  }
  function showMenu(app, x, y) {
    closeMenu();
    const scrim = document.createElement("div");
    scrim.className = "scrim";
    scrim.addEventListener("pointerdown", closeMenu);
    const menu = document.createElement("div");
    menu.className = "menu";
    menu.setAttribute("role", "menu");
    const item = (label, svg, fn) => {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "menuitem");
      b.innerHTML = svg;
      const span = document.createElement("span");
      span.textContent = label;
      b.append(span);
      b.addEventListener("click", () => { closeMenu(); fn(); });
      menu.append(b);
      return b;
    };
    const first = item(`Open ${app.name}`,
      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="8"/></svg>`,
      () => openApp(app.id));
    item(`Rewind ${app.name}`,
      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5.2 8.6A8 8 0 1 1 4 13"/><path d="M4.6 4.2v4.6h4.6"/></svg>`,
      () => openApp(app.id, { rewind: true }));
    const rect = screen.getBoundingClientRect();
    menu.style.left = `${Math.min(Math.max(12, x - rect.left - 20), rect.width - 210)}px`;
    menu.style.top = `${Math.min(y - rect.top + 12, rect.height - 140)}px`;
    screen.append(scrim, menu);
    menuEls = [scrim, menu];
    first.focus();
  }

  // ----------------------------------------------------------------- apps
  function openApp(id, opts = {}) {
    const app = S.byId[id];
    if (!app) return;
    closeMenu();
    if (S.current && S.current !== id) notifyClosed(S.current);
    if (S.current !== id) $("toast").hidden = true;
    S.current = id;
    const live = $("live");
    if (live.dataset.app !== id) {
      live.src = app.url;
      live.dataset.app = id;
      live.title = app.name;
    }
    $("app").hidden = false;
    $("home").hidden = true;
    screen.classList.add("in-app");
    if (opts.rewind) openRewind({ kind: "app", id });
  }

  function goHome() {
    closeMenu();
    if (S.rw) closeRewind();
    if (S.current) notifyClosed(S.current);
    S.current = null;
    const live = $("live");
    live.src = "about:blank";
    delete live.dataset.app;
    $("app").hidden = true;
    $("home").hidden = false;
    screen.classList.remove("in-app");
    paintSky();
  }

  function notifyClosed(id) {
    flushApp();
    setTimeout(() => {
      api("POST", `/api/apps/${encodeURIComponent(id)}/closed`).then(refreshSaved).catch(() => {});
    }, 250);
  }

  // Ask the open app to save anything it is holding in memory.
  function flushApp() {
    const live = $("live");
    try { live.contentWindow.postMessage({ hindsight: "flush" }, location.origin); } catch (_) { /* ignore */ }
  }

  function reloadLive() {
    const live = $("live");
    if (live.dataset.app) {
      try { live.contentWindow.location.reload(); } catch (_) { live.src = live.src; }
    }
  }

  // --------------------------------------------------------------- toast
  let toastTimer = null;
  function toast(message, actionLabel, action) {
    const box = $("toast");
    $("toastMsg").textContent = message;
    const btn = $("toastAction");
    btn.hidden = !actionLabel;
    btn.onclick = null;
    if (actionLabel) {
      btn.textContent = actionLabel;
      btn.onclick = async () => { box.hidden = true; try { await action(); } catch (e) { toast(`That didn't work: ${e.message}`); } };
    }
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.hidden = true; }, actionLabel ? 9000 : 5000);
  }

  // ============================================================== REWIND
  const DIAL = { cx: 195, cy: 520, R: 480, step: 5.2, band: 58 };

  async function fetchItems(target) {
    if (target.kind === "app") {
      const data = await api("GET", `/api/apps/${encodeURIComponent(target.id)}/snapshots`);
      let items = data.snapshots.slice().reverse();
      // The newest snapshot may simply be "now"; don't show it twice.
      if (items.length && items[0].fingerprint === data.current && !data.dirty) items = items.slice(1);
      return items;
    }
    const { points } = await api("GET", "/api/points");
    const metas = {};
    await Promise.all(S.apps.map(async (a) => {
      const { snapshots } = await api("GET", `/api/apps/${encodeURIComponent(a.id)}/snapshots`);
      metas[a.id] = Object.fromEntries(snapshots.map((s) => [s.id, s]));
    }));
    target.metas = metas;
    return points.slice().reverse();
  }

  async function openRewind(target) {
    closeMenu();
    flushApp();
    $("toast").hidden = true;
    await new Promise((r) => setTimeout(r, target.kind === "app" ? 180 : 0));
    let items;
    try {
      items = await fetchItems(target);
    } catch (e) {
      toast(`Rewind couldn't load: ${e.message}`);
      return;
    }
    S.rw = { target, items, sel: 0, pos: 0, anim: null, previewTimer: null, dragging: null };
    if (target.kind === "phone") {
      $("app").hidden = false;
      $("home").hidden = true;
      $("live").hidden = true;
      $("phoneView").hidden = false;
    }
    const sheet = $("rewind");
    sheet.hidden = false;
    void sheet.offsetWidth; // let the slide-up transition run
    screen.classList.add("rewinding");
    renderDial();
    select(0, true);
    $("dial").focus({ preventScroll: true });
  }

  function closeRewind() {
    const rw = S.rw;
    if (!rw) return;
    clearTimeout(rw.previewTimer);
    cancelAnimationFrame(rw.anim);
    S.rw = null;
    screen.classList.remove("rewinding", "viewing-past");
    const past = $("past");
    past.hidden = true;
    past.removeAttribute("src");
    $("live").hidden = false;
    $("pastLabel").hidden = true;
    $("phoneView").hidden = true;
    setTimeout(() => { if (!S.rw) $("rewind").hidden = true; }, 420);
    if (rw.target.kind === "phone" && !S.current) {
      $("app").hidden = true;
      $("home").hidden = false;
    }
  }

  function describe(item, target) {
    if (target.kind === "app") {
      const name = S.byId[target.id] ? S.byId[target.id].name : target.id;
      switch (item.trigger) {
        case "auto": return item.label || "Saved automatically";
        case "close": return item.label || `When you left ${name}`;
        case "before-rewind": return "Before you rewound";
        default: return item.label || "Saved";
      }
    }
    if (item.trigger === "before-rewind") return "Before you rewound the phone";
    if (item.trigger === "manual") return item.label || "Checkpoint";
    const names = (item.changed || []).map((id) => (S.byId[id] ? S.byId[id].name : id));
    return names.length ? `${joinNames(names)} changed` : "Saved automatically";
  }

  function renderDial() {
    const rw = S.rw;
    const svg = $("dialSvg");
    svg.textContent = "";
    const { cx, cy, R, step, band } = DIAL;
    const el = (parent, tag, attrs) => {
      const n = document.createElementNS(SVGNS, tag);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      parent.append(n);
      return n;
    };
    const polar = (r, deg) => {
      const a = (deg * Math.PI) / 180;
      return [cx + r * Math.sin(a), cy - r * Math.cos(a)];
    };
    // the band the ticks sit on
    const [ax, ay] = polar(R, -60), [bx, by] = polar(R, 60);
    const [cx2, cy2] = polar(R - band, 60), [dx, dy] = polar(R - band, -60);
    el(svg, "path", {
      class: "band",
      d: `M ${ax} ${ay} A ${R} ${R} 0 0 1 ${bx} ${by} L ${cx2} ${cy2} A ${R - band} ${R - band} 0 0 0 ${dx} ${dy} Z`,
    });
    const g = el(svg, "g", { id: "dialTicks" });
    const total = rw.items.length + 1;
    let lastLabelAt = -99, lastBucket = null;
    for (let i = 0; i < total; i++) {
      const deg = -i * step;
      const item = i === 0 ? null : rw.items[i - 1];
      const marked = item && ["delete", "checkpoint", "manual", "before-rewind"].includes(item.trigger);
      const inner = i === 0 ? R - 40 : marked ? R - 34 : R - 22;
      const [x1, y1] = polar(R - 8, deg), [x2, y2] = polar(inner, deg);
      el(g, "line", { class: `tick${i === 0 ? " now" : marked ? " marked" : ""}`, x1, y1, x2, y2 });
      if (marked) {
        const [px, py] = polar(R - 44, deg);
        el(g, "circle", { class: "dot", cx: px, cy: py, r: 3.2 });
      }
      if (item) {
        const t = new Date(item.created * 1000);
        const bucket = sameDay(t, new Date()) ? hourFmt.format(t) : dayFmt.format(t);
        if (bucket !== lastBucket && i - lastLabelAt >= 3) {
          const [tx, ty] = polar(R - 66, deg);
          const text = el(g, "text", { class: "hour", x: tx, y: ty, transform: `rotate(${deg} ${tx} ${ty})` });
          text.textContent = bucket;
          lastLabelAt = i;
        }
        lastBucket = bucket;
      }
    }
    // the fixed pointer at 12 o'clock
    const top = cy - R;
    el(svg, "path", { class: "marker", d: `M ${cx - 8} ${top - 16} L ${cx + 8} ${top - 16} L ${cx} ${top - 4} Z` });
    $("dial").setAttribute("aria-valuemax", String(total - 1));
    $("dialHint").hidden = rw.items.length === 0;
    setRotation(rw.pos);
  }

  function setRotation(pos) {
    const g = document.getElementById("dialTicks");
    if (g) g.setAttribute("transform", `rotate(${pos * DIAL.step} ${DIAL.cx} ${DIAL.cy})`);
  }

  function animateTo(target, immediate) {
    const rw = S.rw;
    cancelAnimationFrame(rw.anim);
    if (immediate || matchMedia("(prefers-reduced-motion: reduce)").matches) {
      rw.pos = target;
      setRotation(target);
      return;
    }
    const from = rw.pos, t0 = performance.now(), dur = 260;
    const frame = (t) => {
      if (!S.rw) return;
      const k = Math.min(1, (t - t0) / dur);
      const e = 1 - Math.pow(1 - k, 3);
      rw.pos = from + (target - from) * e;
      setRotation(rw.pos);
      if (k < 1) rw.anim = requestAnimationFrame(frame);
    };
    rw.anim = requestAnimationFrame(frame);
  }

  function select(i, immediate = false) {
    const rw = S.rw;
    if (!rw) return;
    const n = rw.items.length;
    i = Math.max(0, Math.min(n, Math.round(i)));
    rw.sel = i;
    animateTo(i, immediate);
    readout(i);
    $("rwRestore").disabled = i === 0;
    $("rwNow").textContent = i === 0 ? "Close" : "Back to now";
    $("dial").setAttribute("aria-valuenow", String(i));
    clearTimeout(rw.previewTimer);
    rw.previewTimer = setTimeout(preview, immediate ? 0 : 180);
  }

  function readout(i) {
    const rw = S.rw;
    const target = rw.target;
    const name = target.kind === "app" ? S.byId[target.id].name : "your phone";
    if (i === 0) {
      $("rwRel").textContent = "Now";
      $("rwAbs").textContent = absTime(nowSec());
      $("rwWhat").textContent = rw.items.length
        ? (target.kind === "app" ? `This is ${name} right now` : "This is your phone right now")
        : "No history yet. Hindsight saves a point every minute while you use your apps.";
      $("rwWhat").classList.toggle("empty", !rw.items.length);
    } else {
      const item = rw.items[i - 1];
      $("rwRel").textContent = relTime(item.created);
      $("rwAbs").textContent = absTime(item.created);
      $("rwWhat").textContent = describe(item, target);
      $("rwWhat").classList.remove("empty");
    }
    $("dial").setAttribute("aria-valuetext", `${$("rwRel").textContent}. ${$("rwWhat").textContent}`);
  }

  function preview() {
    const rw = S.rw;
    if (!rw) return;
    if (rw.target.kind === "phone") return previewPhone();
    const app = S.byId[rw.target.id];
    const past = $("past");
    if (rw.sel === 0) {
      past.hidden = true;
      $("live").hidden = false;
      $("pastLabel").hidden = true;
      screen.classList.remove("viewing-past");
      return;
    }
    const item = rw.items[rw.sel - 1];
    const src = `${app.url}?hs_at=${encodeURIComponent(item.id)}`;
    const show = () => {
      if (!S.rw || S.rw.sel === 0) return;
      past.hidden = false;
      $("live").hidden = true;
      const label = $("pastLabel");
      label.textContent = shortTime(item.created);
      label.hidden = false;
      screen.classList.add("viewing-past");
    };
    if (past.getAttribute("src") === src) return show();
    past.onload = show;
    past.setAttribute("src", src);
  }

  function previewPhone() {
    const rw = S.rw;
    const view = $("phoneView");
    view.textContent = "";
    const h = document.createElement("h2");
    const p = document.createElement("p");
    const ul = document.createElement("ul");
    if (rw.sel === 0) {
      h.textContent = "Your phone right now";
      p.textContent = rw.items.length
        ? "Turn the dial to a point in time to see what rewinding would change."
        : "Nothing to rewind yet. Use your apps for a minute and points appear here.";
    } else {
      const point = rw.items[rw.sel - 1];
      h.textContent = `Your phone ${absTime(point.created).replace(/^Today/, "today").replace(/^Yesterday/, "yesterday")}`;
      p.textContent = "Restoring brings every app below back to how it was at that moment. You can undo it.";
      for (const app of S.apps) {
        const sid = point.members[app.id];
        const meta = sid && rw.target.metas[app.id] ? rw.target.metas[app.id][sid] : null;
        const li = document.createElement("li");
        if ((point.changed || []).includes(app.id)) li.className = "changed";
        const img = document.createElement("img");
        img.src = app.icon || "";
        img.alt = "";
        const name = document.createElement("span");
        name.textContent = app.name;
        const what = document.createElement("span");
        what.className = "what";
        if (!sid) what.textContent = "Not on the phone yet";
        else {
          const status = document.createElement("b");
          status.textContent = (point.changed || []).includes(app.id) ? "Changed" : "No changes";
          const count = document.createElement("span");
          count.textContent = !meta ? "" : meta.keys === 0 ? "Empty" : `${meta.keys} ${meta.keys === 1 ? "item" : "items"} saved`;
          what.append(status, document.createElement("br"), count);
        }
        li.append(img, name, what);
        ul.append(li);
      }
    }
    view.append(h, p, ul);
  }

  async function restore() {
    const rw = S.rw;
    if (!rw || rw.sel === 0) return;
    const item = rw.items[rw.sel - 1];
    const btn = $("rwRestore");
    btn.disabled = true;
    try {
      if (rw.target.kind === "app") {
        const app = S.byId[rw.target.id];
        const res = await api("POST", `/api/apps/${encodeURIComponent(app.id)}/rewind`, { snapshot: item.id });
        closeRewind();
        reloadLive();
        refreshSaved();
        if (res.noop) toast(`${app.name} already looks like this.`);
        else toast(`${app.name} is back to ${timeFmt.format(new Date(item.created * 1000))}.`, "Undo", async () => {
          await api("POST", `/api/apps/${encodeURIComponent(app.id)}/rewind`, { snapshot: res.undo });
          reloadLive();
          toast(`Undone. ${app.name} is back to how it was.`);
        });
      } else {
        const res = await api("POST", `/api/points/${encodeURIComponent(item.id)}/rewind`);
        closeRewind();
        await loadSettings();
        refreshSaved();
        if (!res.apps.length) toast("Your phone already looks like this.");
        else toast(`Your phone is back to ${absTime(item.created).toLowerCase()}.`, "Undo", async () => {
          await api("POST", `/api/points/${encodeURIComponent(res.undo)}/rewind`);
          await loadSettings();
          toast("Undone. Your phone is back to how it was.");
        });
      }
    } catch (e) {
      toast(`Couldn't restore: ${e.message}`);
      btn.disabled = false;
    }
  }

  // Keep the dial current if new points arrive while it is open.
  let refreshTimer = null;
  function refreshRewindSoon() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(async () => {
      const rw = S.rw;
      if (!rw || rw.dragging) return;
      const selectedId = rw.sel > 0 ? rw.items[rw.sel - 1].id : null;
      try {
        const items = await fetchItems(rw.target);
        if (S.rw !== rw) return;
        rw.items = items;
        const idx = selectedId ? items.findIndex((it) => it.id === selectedId) + 1 : 0;
        rw.pos = Math.max(0, idx);
        renderDial();
        select(Math.max(0, idx), true);
      } catch (_) { /* try again next time */ }
    }, 400);
  }

  // ------------------------------------------------------- dial controls
  function unitsPerPixel() {
    const box = $("dial").getBoundingClientRect();
    const scale = Math.max(box.width / 390, box.height / 150);
    return 1 / scale;
  }

  function wireDial() {
    const dial = $("dial");
    dial.addEventListener("pointerdown", (e) => {
      const rw = S.rw;
      if (!rw) return;
      cancelAnimationFrame(rw.anim);
      rw.dragging = { x: e.clientX, pos0: rw.pos, moved: false, id: e.pointerId };
      dial.setPointerCapture(e.pointerId);
    });
    dial.addEventListener("pointermove", (e) => {
      const rw = S.rw;
      if (!rw || !rw.dragging) return;
      const dxUnits = (e.clientX - rw.dragging.x) * unitsPerPixel();
      if (Math.abs(e.clientX - rw.dragging.x) > 4) rw.dragging.moved = true;
      const deg = (dxUnits / DIAL.R) * (180 / Math.PI);
      const n = rw.items.length;
      let pos = rw.dragging.pos0 + deg / DIAL.step * 1.6;
      if (pos < 0) pos = -Math.sqrt(-pos) * 0.4;           // rubber band past "now"
      if (pos > n) pos = n + Math.sqrt(pos - n) * 0.4;     // and past the oldest point
      rw.pos = pos;
      setRotation(pos);
      const nearest = Math.max(0, Math.min(n, Math.round(pos)));
      if (nearest !== rw.sel) { rw.sel = nearest; readout(nearest); }
    });
    const end = (e) => {
      const rw = S.rw;
      if (!rw || !rw.dragging) return;
      const { moved } = rw.dragging;
      rw.dragging = null;
      if (!moved) {
        const box = dial.getBoundingClientRect();
        const mid = box.left + box.width / 2;
        if (e.clientX < mid - 30) return select(rw.sel + 1);
        if (e.clientX > mid + 30) return select(rw.sel - 1);
      }
      select(rw.pos);
    };
    dial.addEventListener("pointerup", end);
    dial.addEventListener("pointercancel", end);
    let wheelAcc = 0;
    dial.addEventListener("wheel", (e) => {
      const rw = S.rw;
      if (!rw) return;
      e.preventDefault();
      wheelAcc += Math.abs(e.deltaX) > Math.abs(e.deltaY) ? -e.deltaX : e.deltaY;
      if (Math.abs(wheelAcc) >= 40) {
        select(rw.sel + Math.sign(wheelAcc));
        wheelAcc = 0;
      }
    }, { passive: false });
    dial.addEventListener("keydown", (e) => {
      const rw = S.rw;
      if (!rw) return;
      const keys = { ArrowLeft: 1, ArrowDown: 1, ArrowRight: -1, ArrowUp: -1, PageDown: 5, PageUp: -5 };
      if (e.key in keys) { e.preventDefault(); select(rw.sel + keys[e.key]); }
      else if (e.key === "Home") { e.preventDefault(); select(0); }
      else if (e.key === "End") { e.preventDefault(); select(rw.items.length); }
      else if (e.key === "Enter" && rw.sel > 0) { e.preventDefault(); restore(); }
    });
  }

  // ------------------------------------------------------- events (SSE)
  function connectEvents() {
    const es = new EventSource("/api/events");
    es.onmessage = (m) => {
      let ev;
      try { ev = JSON.parse(m.data); } catch (_) { return; }
      if (ev.type === "point" || ev.type === "snapshot") {
        S.lastSaved = nowSec();
        renderStatus();
        const rw = S.rw;
        if (rw && (rw.target.kind === "phone" || ev.app === rw.target.id)) refreshRewindSoon();
      }
      if (ev.app === "settings" && (ev.type === "changed" || ev.type === "rewound")) loadSettings();
      if (ev.type === "phone-rewound") loadSettings();
      const live = $("live");
      if (S.current && (!ev.app || ev.app === S.current) && live.contentWindow) {
        try { live.contentWindow.postMessage({ hindsight: "event", event: ev }, location.origin); } catch (_) { /* ignore */ }
      }
    };
  }

  // Requests coming from apps (e.g. Settings opening the phone timeline).
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || !e.data || typeof e.data !== "object") return;
    const d = e.data;
    if (d.hindsight === "open-phone-rewind") { goHome(); openRewind({ kind: "phone" }); }
    else if (d.hindsight === "rewind-this-app" && S.current) openRewind({ kind: "app", id: S.current });
    else if (d.hindsight === "go-home") goHome();
    else if (d.hindsight === "toast" && d.text) toast(String(d.text).slice(0, 200));
  });

  // ------------------------------------------------------------- startup
  function wire() {
    $("homeBtn").addEventListener("click", goHome);
    $("rewindBtn").addEventListener("click", () => {
      if (S.rw) closeRewind();
      else if (S.current) openRewind({ kind: "app", id: S.current });
      else openRewind({ kind: "phone" });
    });
    $("rwNow").addEventListener("click", () => {
      if (!S.rw) return;
      if (S.rw.sel === 0) closeRewind(); else select(0);
    });
    $("rwRestore").addEventListener("click", restore);
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (menuEls) closeMenu();
      else if (S.rw) closeRewind();
      else if (S.current) goHome();
    });
    wireDial();
    const framed = () => {
      document.body.classList.toggle("framed", innerWidth >= 560 && innerHeight >= 700);
      paintSky();
    };
    framed();
    addEventListener("resize", framed);
  }

  async function start() {
    wire();
    renderStatus();
    await loadApps();
    await loadSettings();
    await refreshSaved();
    connectEvents();
    setInterval(() => { renderStatus(); if (!S.current) paintSky(); }, 15000);
    $("boot").classList.add("gone");
    setTimeout(() => $("boot").remove(), 600);
  }

  start();
})();
