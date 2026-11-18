/* Hindsight app SDK.
 *
 * Include it in your app:   <script src="/sdk/hindsight.js"></script>
 * Then use window.Hindsight:
 *
 *   await Hindsight.storage.set("note:1", { title: "Hi" })
 *   const notes = await Hindsight.storage.getAll("note:")
 *   await Hindsight.storage.delete("note:1", { label: "Before deleting “Hi”" })
 *   await Hindsight.checkpoint("Before clearing the canvas")
 *   Hindsight.onFlush(saveNow)          // save unsaved work before a rewind
 *   Hindsight.isPreview                  // true when the app is showing the past
 *
 * Everything an app stores this way is covered by Rewind automatically.
 */
(function () {
  "use strict";

  var match = location.pathname.match(/^\/apps\/([a-z0-9][a-z0-9-]{0,31})\//);
  var appId = match ? match[1] : null;
  var at = new URLSearchParams(location.search).get("hs_at");
  var inShell = window.parent !== window;
  var listeners = {};
  var flushers = [];

  if (!appId) console.warn("Hindsight SDK: this page is not served from /apps/<id>/");
  if (at) document.documentElement.setAttribute("data-hs-preview", "");

  function api(method, path, body) {
    return fetch(path, {
      method: method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) {
        if (!res.ok) {
          var err = new Error((data && data.error) || ("Request failed (" + res.status + ")"));
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  function base() { return "/api/apps/" + appId; }
  function atQuery(prefix) { return at ? prefix + "at=" + encodeURIComponent(at) : ""; }
  function keyUrl(key) { return base() + "/kv/" + encodeURIComponent(key); }

  function readOnly() {
    if (at) {
      return Promise.reject(new Error("This is a view of the past. Restore it to make changes."));
    }
    return null;
  }

  var storage = {
    /** All stored values (optionally only keys starting with `prefix`). */
    getAll: function (prefix) {
      return api("GET", base() + "/kv" + atQuery("?")).then(function (data) {
        var out = {};
        Object.keys(data.records).forEach(function (k) {
          if (!prefix || k.indexOf(prefix) === 0) out[k] = data.records[k].value;
        });
        return out;
      });
    },
    /** One value, or `fallback` if it doesn't exist. */
    get: function (key, fallback) {
      var dflt = fallback === undefined ? null : fallback;
      return api("GET", keyUrl(key) + "?missing=ok" + atQuery("&")).then(function (rec) {
        return rec.missing ? dflt : rec.value;
      });
    },
    /** Save a JSON-compatible value. */
    set: function (key, value) {
      return readOnly() || api("PUT", keyUrl(key), { value: value }).then(function () {});
    },
    /** Delete a key. The phone saves a labeled Rewind point first. */
    delete: function (key, options) {
      var label = options && options.label ? "?label=" + encodeURIComponent(options.label) : "";
      return readOnly() || api("DELETE", keyUrl(key) + label).then(function () {});
    },
  };

  function emit(type, detail) {
    (listeners[type] || []).slice().forEach(function (fn) {
      try { fn(detail); } catch (e) { console.error(e); }
    });
  }

  function dispatch(ev) {
    if (!ev || (ev.app && ev.app !== appId)) return;
    if (ev.type === "rewound") emit("rewound", ev);
    if (ev.type === "changed") emit("changed", ev);
    emit("event", ev);
  }

  window.addEventListener("message", function (e) {
    if (e.origin !== location.origin || !e.data || typeof e.data !== "object") return;
    if (e.data.hindsight === "event") dispatch(e.data.event);
    if (e.data.hindsight === "flush") flushers.forEach(function (fn) { try { fn(); } catch (err) { console.error(err); } });
  });

  // Outside the phone shell (e.g. opened directly in a browser), listen ourselves.
  if (!inShell && appId && !at && window.EventSource) {
    var es = new EventSource("/api/events?app=" + appId);
    es.onmessage = function (m) { try { dispatch(JSON.parse(m.data)); } catch (e) { /* ignore */ } };
  }
  window.addEventListener("pagehide", function () { flushers.forEach(function (fn) { try { fn(); } catch (e) { /* ignore */ } }); });

  function post(message) {
    if (inShell) window.parent.postMessage(message, location.origin);
  }

  window.Hindsight = {
    appId: appId,
    isPreview: !!at,
    previewAt: at,
    storage: storage,
    /** Save a named Rewind point now, e.g. before a big change. */
    checkpoint: function (label) {
      return readOnly() || api("POST", base() + "/checkpoint", { label: label || "Checkpoint" });
    },
    /** Listen for "rewound", "changed" or every "event". */
    on: function (type, fn) {
      (listeners[type] = listeners[type] || []).push(fn);
      return function () { listeners[type] = (listeners[type] || []).filter(function (f) { return f !== fn; }); };
    },
    /** Called right before the phone rewinds or closes the app: save now. */
    onFlush: function (fn) { flushers.push(fn); },
    system: {
      goHome: function () { post({ hindsight: "go-home" }); },
      openRewind: function () { post({ hindsight: "rewind-this-app" }); },
      openPhoneRewind: function () { post({ hindsight: "open-phone-rewind" }); },
      toast: function (text) { post({ hindsight: "toast", text: String(text) }); },
    },
  };
})();
