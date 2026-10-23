
;(function () { // Patchsmith - traduzione dal vivo (live translation; "Restore originals" removes it)
  /* For Electron games with unreadable (encrypted) files: translates the page's texts while the game runs,
   * without opening or changing the game's files. The translations already made are in
   * patchsmith-dizionario.json; new sentences are sent in groups to Patchsmith's translator (a service on
   * 127.0.0.1, started here and closed with the game), which translates them together and saves them in its
   * memory. A pending sentence stays hidden for at most ATTESA_MS (then the original shows until the translation
   * arrives). Numbers are taken out first ("1.28 minutes" -> "{{A}} minutes") and put back. Accented letters
   * the fonts lack become e', a'. */
  "use strict";
  var CFG = /*__CONFIG__*/{};
  var fs, path, http, cp;
  try { fs = require("fs"); path = require("path"); http = require("http"); cp = require("child_process"); }
  catch (e) { return; }
  var ATTESA_MS = 1000, GRUPPO = 40, PAUSA_GRUPPO_MS = 120, TENTATIVI = 3;
  var mappa = new Map();
  try {
    var d = JSON.parse(fs.readFileSync(path.join(__dirname, CFG.dizionario), "utf8"));
    Object.keys(d).forEach(function (k) { mappa.set(k, d[k]); });
  } catch (e) { /* no dictionary: everything from the translator */ }
  // the translations (sentences already in the target language): never requested as if they were originals
  var tradotte = new Set();
  mappa.forEach(function (v) { tradotte.add(v); });

  // --- Patchsmith's translator -----------------------------------------------------------------------------------
  try {
    var c = CFG.comando;
    if (c && c.programma && fs.existsSync(c.programma)) {
      var figlio = cp.spawn(c.programma, c.argomenti.concat(["--pid", String(process.pid)]),
                            { cwd: c.cartella, detached: true, stdio: "ignore", windowsHide: true });
      figlio.on("error", function () {});
      figlio.unref();
    }
  } catch (e) { /* without the translator the dictionary's translations remain */ }

  var chieste = new Map(), coda = [], occupato = false, errori = 0, inProgrammaInvio = false;
  function chiedi(testo) {
    if (!CFG.porta || chieste.has(testo) || chieste.size > 5000) return;
    chieste.set(testo, 0);
    coda.push(testo);
    programmaInvio();
  }
  function programmaInvio() {  // the new sentences of a screen arrive in bursts: they are sent together
    if (inProgrammaInvio) return;
    inProgrammaInvio = true;
    setTimeout(function () { inProgrammaInvio = false; invia(); }, PAUSA_GRUPPO_MS);
  }
  function invia() {
    if (occupato || !coda.length || errori > 40) return;
    occupato = true;
    var gruppo = coda.splice(0, GRUPPO), fatto = false;
    function fine(ok, risposta) {
      if (fatto) return;
      fatto = true;
      occupato = false;
      if (!ok) {  // translator not ready yet (still starting) or stopped: retry later
        errori++;
        coda = gruppo.concat(coda);
        setTimeout(invia, Math.min(30000, 1000 * errori));
        return;
      }
      errori = 0;
      var t = risposta.traduzioni || {}, nuove = false;
      Object.keys(t).forEach(function (k) {
        if (t[k] && t[k] !== k) { mappa.set(k, t[k]); tradotte.add(t[k]); nuove = true; }
      });
      (risposta.riprova || []).forEach(function (k) {  // left untranslated: again later, a few times
        var n = (chieste.get(k) || 0) + 1;
        chieste.set(k, n);
        if (n < TENTATIVI) setTimeout(function () { coda.push(k); programmaInvio(); }, 5000 * n);
      });
      if (nuove) ridisegna();
      if (coda.length) programmaInvio();
    }
    var corpo = Buffer.from(JSON.stringify({ from: CFG.da, to: CFG.a, testi: gruppo }), "utf8");
    var req = http.request({ host: "127.0.0.1", port: CFG.porta, method: "POST", path: "/traduci-gruppo",
                             timeout: 300000, headers: { "Content-Type": "application/json",
                                                         "Content-Length": corpo.length } }, function (res) {
      var parti = [];
      res.setEncoding("utf8");
      res.on("data", function (p) { parti.push(p); });
      res.on("end", function () {
        var dati = null;
        try { dati = JSON.parse(parti.join("")); } catch (e) { dati = null; }
        fine(res.statusCode === 200 && !!dati, dati);
      });
      res.on("error", function () { fine(false); });
    });
    req.on("timeout", function () { req.destroy(); });
    req.on("error", function () { fine(false); });
    req.end(corpo);
  }

  // --- texts ---------------------------------------------------------------------------------------------------
  var NUMERO = /[+\-]?\d+(?:[.,]\d+)*/g;
  function daTradurre(s) {
    if (s.length <= 2 || !/\p{L}/u.test(s)) return false;  // keys ("Q", "B"), symbols
    return !(/\d/.test(s) && !/\p{L}{3,}/u.test(s));        // only numbers and units ("$5k", "x 2", "v1.0")
  }
  // (translation, or null; atteso=true if the translation was requested and is on its way)
  var atteso = false;
  function traduciNucleo(nucleo) {
    atteso = false;
    var t = mappa.get(nucleo);
    if (t !== undefined) return t;
    if (!daTradurre(nucleo) || tradotte.has(nucleo)) return null;
    var numeri = [];
    var modello = nucleo.replace(NUMERO, function (m) {
      numeri.push(m);
      return "{{" + String.fromCharCode(64 + numeri.length) + "}}";
    });
    var chiave = numeri.length && numeri.length <= 26 ? modello : nucleo;
    var tm = mappa.get(chiave);
    if (tm === undefined) {
      if (tradotte.has(chiave)) return null;
      atteso = !chieste.has(chiave) || chieste.get(chiave) === 0;
      chiedi(chiave);
      return null;
    }
    if (chiave === nucleo) return tm;
    return tm.replace(/\{\{([A-Z])\}\}/g, function (x, l) {
      var v = numeri[l.charCodeAt(0) - 65];
      return v === undefined ? x : v;
    });
  }
  function traduci(s, el) {
    atteso = false;
    if (typeof s !== "string" || !s || s.length > 2000) return null;
    var nucleo = s.replace(/\s+/g, " ").trim();
    if (!nucleo) return null;
    var t = traduciNucleo(nucleo);
    if (t === null || t === nucleo) return null;
    t = adatta(t, el);
    var inizio = s.length - s.replace(/^\s+/, "").length;
    return s.slice(0, inizio) + t + s.slice(s.replace(/\s+$/, "").length);
  }

  // --- letters the game's fonts lack --------------------------------------------------------------------------------
  var ACCENTATE = "àèéìòùÀÈÉÌÒÙ", RIPIEGO = { "à": "a'", "è": "e'", "é": "e'", "ì": "i'", "ò": "o'", "ù": "u'",
    "À": "A'", "È": "E'", "É": "E'", "Ì": "I'", "Ò": "O'", "Ù": "U'" };
  var fontMancanti = new Map(), misura = null;
  function mancanti(el) {
    if (!el || !window.getComputedStyle) return "";
    var stile = window.getComputedStyle(el);
    var famiglia = (stile.fontFamily || "").split(",")[0].trim();
    if (!famiglia || /^(serif|sans-serif|monospace|cursive|fantasy|system-ui)$/i.test(famiglia)) return "";
    if (fontMancanti.has(famiglia)) return fontMancanti.get(famiglia);
    if (!document.fonts || !document.fonts.check("32px " + famiglia)) return "";  // not loaded yet: later
    misura = misura || document.createElement("canvas").getContext("2d");
    var assenti = "";
    for (var i = 0; i < ACCENTATE.length; i++) {
      var ch = ACCENTATE[i], larghezze = [];
      ["serif", "monospace"].forEach(function (riserva) {  // with the letter in the font the two widths are equal
        misura.font = "32px " + famiglia + ", " + riserva;
        larghezze.push(misura.measureText(ch).width);
      });
      if (larghezze[0] !== larghezze[1]) assenti += ch;
    }
    fontMancanti.set(famiglia, assenti);
    return assenti;
  }
  function adatta(t, el) {
    if (!/[àèéìòùÀÈÉÌÒÙ]/.test(t)) return t;
    var assenti = mancanti(el);
    if (!assenti) return t;
    return t.replace(/[àèéìòùÀÈÉÌÒÙ]/g, function (ch) { return assenti.indexOf(ch) >= 0 ? RIPIEGO[ch] : ch; });
  }

  // --- pending sentences: hidden for at most ATTESA_MS (only if they are the only text of their element) ---------
  var nascosti = new Map();  // element -> previous visibility
  function nascondi(p) {
    if (nascosti.has(p) || !p.style || p.childNodes.length !== 1) return;
    nascosti.set(p, p.style.visibility);
    p.style.visibility = "hidden";
    setTimeout(function () { mostra(p); }, ATTESA_MS);
  }
  function mostra(p) {
    if (!nascosti.has(p)) return;
    p.style.visibility = nascosti.get(p);
    nascosti.delete(p);
  }

  // --- page ----------------------------------------------------------------------------------------------------
  var IGNORA = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, NOSCRIPT: 1, CODE: 1, PRE: 1 };
  var ATTRIBUTI = ["title", "placeholder", "aria-label", "alt"];
  var scritti = new WeakMap(), originali = new WeakMap(), scrittiAttr = new WeakMap();
  function nodoTesto(n) {
    var p = n.parentNode;
    if (!p || IGNORA[p.nodeName] || p.isContentEditable) return;
    var attuale = n.nodeValue;
    var originale = scritti.get(n) === attuale ? originali.get(n) : attuale;  // already ours: redo it from the original
    var t = traduci(originale, p);
    if (t === null) { if (atteso && originale === attuale) nascondi(p); return; }
    if (t !== attuale) {
      originali.set(n, originale);
      scritti.set(n, t);
      n.nodeValue = t;
    }
    mostra(p);
  }
  function elemento(el) {
    if (!el.getAttribute) return;
    var nostri = scrittiAttr.get(el);
    for (var i = 0; i < ATTRIBUTI.length; i++) {
      var nome = ATTRIBUTI[i], v = el.getAttribute(nome);
      if (!v || (nostri && nostri[nome] === v)) continue;  // written by us: not a new text
      var t = traduci(v, el);
      if (t === null || t === v) continue;
      if (!nostri) { nostri = {}; scrittiAttr.set(el, nostri); }
      nostri[nome] = t;
      el.setAttribute(nome, t);
    }
  }
  function visita(radice) {
    if (!radice) return;
    if (radice.nodeType === 3) { nodoTesto(radice); return; }
    if (radice.nodeType === 1) { if (IGNORA[radice.nodeName]) return; elemento(radice); }
    var w = document.createTreeWalker(radice, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT), n;
    while ((n = w.nextNode())) { if (n.nodeType === 3) nodoTesto(n); else elemento(n); }
  }
  var inProgramma = false;
  function ridisegna() {  // new translations (or fonts just loaded): the whole page again, at most every 100 ms
    if (inProgramma) return;
    inProgramma = true;
    setTimeout(function () { inProgramma = false; if (document.body) visita(document.body); }, 100);
  }
  function avvia() {
    new MutationObserver(function (ms) {
      for (var i = 0; i < ms.length; i++) {
        var m = ms[i];
        if (m.type === "characterData") nodoTesto(m.target);
        else if (m.type === "attributes") elemento(m.target);
        else for (var j = 0; j < m.addedNodes.length; j++) visita(m.addedNodes[j]);
      }
    }).observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true,
                                           attributeFilter: ATTRIBUTI });
    visita(document.body || document.documentElement);
    if (document.fonts && document.fonts.addEventListener) document.fonts.addEventListener("loadingdone", ridisegna);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", avvia); else avvia();
})();
