
;(function () { // Patchsmith - traduzione dal vivo (live translation; "Restore originals" removes it)
  /* For Electron games with unreadable (encrypted) files: translates the page's texts while the game runs,
   * without opening or changing the game's files. The translations already made are in
   * patchsmith-dizionario.json; new sentences are sent in groups to Patchsmith's translator (a service on
   * 127.0.0.1, started here and closed with the game), which translates them together and saves them in its
   * memory. A pending sentence stays hidden for at most WAIT_MS (then the original shows until the translation
   * arrives). Numbers are taken out first ("1.28 minutes" -> "{{A}} minutes") and put back. Accented letters
   * the fonts lack become e', a'.
   * The marker in the first line stays Italian: PatchSmith recognizes its script in the games by it. */
  "use strict";
  var CFG = /*__CONFIG__*/{};
  var fs, path, http, cp;
  try { fs = require("fs"); path = require("path"); http = require("http"); cp = require("child_process"); }
  catch (e) { return; }
  var WAIT_MS = 1000, GROUP = 40, GROUP_PAUSE_MS = 120, ATTEMPTS = 3;
  var map = new Map();
  try {
    var d = JSON.parse(fs.readFileSync(path.join(__dirname, CFG.dictionary), "utf8"));
    Object.keys(d).forEach(function (k) { map.set(k, d[k]); });
  } catch (e) { /* no dictionary: everything from the translator */ }
  // the translations (sentences already in the target language): never requested as if they were originals
  var translated = new Set();
  map.forEach(function (v) { translated.add(v); });

  // --- Patchsmith's translator -----------------------------------------------------------------------------------
  try {
    var c = CFG.command;
    if (c && c.program && fs.existsSync(c.program)) {
      var child = cp.spawn(c.program, c.arguments.concat(["--pid", String(process.pid)]),
                           { cwd: c.folder, detached: true, stdio: "ignore", windowsHide: true });
      child.on("error", function () {});
      child.unref();
    }
  } catch (e) { /* without the translator the dictionary's translations remain */ }

  var requested = new Map(), queue = [], busy = false, errors = 0, sendScheduled = false;
  function request(text) {
    if (!CFG.port || requested.has(text) || requested.size > 5000) return;
    requested.set(text, 0);
    queue.push(text);
    scheduleSend();
  }
  function scheduleSend() {  // the new sentences of a screen arrive in bursts: they are sent together
    if (sendScheduled) return;
    sendScheduled = true;
    setTimeout(function () { sendScheduled = false; send(); }, GROUP_PAUSE_MS);
  }
  function send() {
    if (busy || !queue.length || errors > 40) return;
    busy = true;
    var group = queue.splice(0, GROUP), done = false;
    function end(ok, response) {
      if (done) return;
      done = true;
      busy = false;
      if (!ok) {  // translator not ready yet (still starting) or stopped: retry later
        errors++;
        queue = group.concat(queue);
        setTimeout(send, Math.min(30000, 1000 * errors));
        return;
      }
      errors = 0;
      var t = response.translations || {}, fresh = false;
      Object.keys(t).forEach(function (k) {
        if (t[k] && t[k] !== k) { map.set(k, t[k]); translated.add(t[k]); fresh = true; }
      });
      (response.retry || []).forEach(function (k) {  // left untranslated: again later, a few times
        var n = (requested.get(k) || 0) + 1;
        requested.set(k, n);
        if (n < ATTEMPTS) setTimeout(function () { queue.push(k); scheduleSend(); }, 5000 * n);
      });
      if (fresh) redraw();
      if (queue.length) scheduleSend();
    }
    var body = Buffer.from(JSON.stringify({ from: CFG.from, to: CFG.to, texts: group }), "utf8");
    var req = http.request({ host: "127.0.0.1", port: CFG.port, method: "POST", path: "/translate-group",
                             timeout: 300000, headers: { "Content-Type": "application/json",
                                                         "Content-Length": body.length } }, function (res) {
      var parts = [];
      res.setEncoding("utf8");
      res.on("data", function (p) { parts.push(p); });
      res.on("end", function () {
        var data = null;
        try { data = JSON.parse(parts.join("")); } catch (e) { data = null; }
        end(res.statusCode === 200 && !!data, data);
      });
      res.on("error", function () { end(false); });
    });
    req.on("timeout", function () { req.destroy(); });
    req.on("error", function () { end(false); });
    req.end(body);
  }

  // --- texts ---------------------------------------------------------------------------------------------------
  var NUMBER = /[+\-]?\d+(?:[.,]\d+)*/g;
  function toTranslate(s) {
    if (s.length <= 2 || !/\p{L}/u.test(s)) return false;  // keys ("Q", "B"), symbols
    return !(/\d/.test(s) && !/\p{L}{3,}/u.test(s));        // only numbers and units ("$5k", "x 2", "v1.0")
  }
  // (translation, or null; pending=true if the translation was requested and is on its way)
  var pending = false;
  function translateCore(core) {
    pending = false;
    var t = map.get(core);
    if (t !== undefined) return t;
    if (!toTranslate(core) || translated.has(core)) return null;
    var numbers = [];
    var template = core.replace(NUMBER, function (m) {
      numbers.push(m);
      return "{{" + String.fromCharCode(64 + numbers.length) + "}}";
    });
    var key = numbers.length && numbers.length <= 26 ? template : core;
    var tm = map.get(key);
    if (tm === undefined) {
      if (translated.has(key)) return null;
      pending = !requested.has(key) || requested.get(key) === 0;
      request(key);
      return null;
    }
    if (key === core) return tm;
    return tm.replace(/\{\{([A-Z])\}\}/g, function (x, l) {
      var v = numbers[l.charCodeAt(0) - 65];
      return v === undefined ? x : v;
    });
  }
  function translate(s, el) {
    pending = false;
    if (typeof s !== "string" || !s || s.length > 2000) return null;
    var core = s.replace(/\s+/g, " ").trim();
    if (!core) return null;
    var t = translateCore(core);
    if (t === null || t === core) return null;
    t = adapt(t, el);
    var start = s.length - s.replace(/^\s+/, "").length;
    return s.slice(0, start) + t + s.slice(s.replace(/\s+$/, "").length);
  }

  // --- letters the game's fonts lack --------------------------------------------------------------------------------
  var ACCENTED = "àèéìòùÀÈÉÌÒÙ", FALLBACK = { "à": "a'", "è": "e'", "é": "e'", "ì": "i'", "ò": "o'", "ù": "u'",
    "À": "A'", "È": "E'", "É": "E'", "Ì": "I'", "Ò": "O'", "Ù": "U'" };
  var fontMissing = new Map(), measure = null;
  function missing(el) {
    if (!el || !window.getComputedStyle) return "";
    var style = window.getComputedStyle(el);
    var family = (style.fontFamily || "").split(",")[0].trim();
    if (!family || /^(serif|sans-serif|monospace|cursive|fantasy|system-ui)$/i.test(family)) return "";
    if (fontMissing.has(family)) return fontMissing.get(family);
    if (!document.fonts || !document.fonts.check("32px " + family)) return "";  // not loaded yet: later
    measure = measure || document.createElement("canvas").getContext("2d");
    var absent = "";
    for (var i = 0; i < ACCENTED.length; i++) {
      var ch = ACCENTED[i], widths = [];
      ["serif", "monospace"].forEach(function (fallback) {  // with the letter in the font the two widths are equal
        measure.font = "32px " + family + ", " + fallback;
        widths.push(measure.measureText(ch).width);
      });
      if (widths[0] !== widths[1]) absent += ch;
    }
    fontMissing.set(family, absent);
    return absent;
  }
  function adapt(t, el) {
    if (!/[àèéìòùÀÈÉÌÒÙ]/.test(t)) return t;
    var absent = missing(el);
    if (!absent) return t;
    return t.replace(/[àèéìòùÀÈÉÌÒÙ]/g, function (ch) { return absent.indexOf(ch) >= 0 ? FALLBACK[ch] : ch; });
  }

  // --- pending sentences: hidden for at most WAIT_MS (only if they are the only text of their element) -----------
  var hidden = new Map();  // element -> previous visibility
  function hide(p) {
    if (hidden.has(p) || !p.style || p.childNodes.length !== 1) return;
    hidden.set(p, p.style.visibility);
    p.style.visibility = "hidden";
    setTimeout(function () { show(p); }, WAIT_MS);
  }
  function show(p) {
    if (!hidden.has(p)) return;
    p.style.visibility = hidden.get(p);
    hidden.delete(p);
  }

  // --- page ----------------------------------------------------------------------------------------------------
  var IGNORE = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, NOSCRIPT: 1, CODE: 1, PRE: 1 };
  var ATTRIBUTES = ["title", "placeholder", "aria-label", "alt"];
  var written = new WeakMap(), originals = new WeakMap(), writtenAttr = new WeakMap();
  function textNode(n) {
    var p = n.parentNode;
    if (!p || IGNORE[p.nodeName] || p.isContentEditable) return;
    var current = n.nodeValue;
    var original = written.get(n) === current ? originals.get(n) : current;  // already ours: redo it from the original
    var t = translate(original, p);
    if (t === null) { if (pending && original === current) hide(p); return; }
    if (t !== current) {
      originals.set(n, original);
      written.set(n, t);
      n.nodeValue = t;
    }
    show(p);
  }
  function element(el) {
    if (!el.getAttribute) return;
    var ours = writtenAttr.get(el);
    for (var i = 0; i < ATTRIBUTES.length; i++) {
      var name = ATTRIBUTES[i], v = el.getAttribute(name);
      if (!v || (ours && ours[name] === v)) continue;  // written by us: not a new text
      var t = translate(v, el);
      if (t === null || t === v) continue;
      if (!ours) { ours = {}; writtenAttr.set(el, ours); }
      ours[name] = t;
      el.setAttribute(name, t);
    }
  }
  function visit(root) {
    if (!root) return;
    if (root.nodeType === 3) { textNode(root); return; }
    if (root.nodeType === 1) { if (IGNORE[root.nodeName]) return; element(root); }
    var w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT), n;
    while ((n = w.nextNode())) { if (n.nodeType === 3) textNode(n); else element(n); }
  }
  var redrawScheduled = false;
  function redraw() {  // new translations (or fonts just loaded): the whole page again, at most every 100 ms
    if (redrawScheduled) return;
    redrawScheduled = true;
    setTimeout(function () { redrawScheduled = false; if (document.body) visit(document.body); }, 100);
  }
  function start() {
    new MutationObserver(function (ms) {
      for (var i = 0; i < ms.length; i++) {
        var m = ms[i];
        if (m.type === "characterData") textNode(m.target);
        else if (m.type === "attributes") element(m.target);
        else for (var j = 0; j < m.addedNodes.length; j++) visit(m.addedNodes[j]);
      }
    }).observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true,
                                           attributeFilter: ATTRIBUTES });
    visit(document.body || document.documentElement);
    if (document.fonts && document.fonts.addEventListener) document.fonts.addEventListener("loadingdone", redraw);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
