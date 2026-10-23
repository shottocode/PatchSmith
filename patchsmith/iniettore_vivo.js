
;(function () { // Patchsmith - traduzione dal vivo (aggiunto da Patchsmith: "Ripristina originali" lo toglie)
  /* Per i giochi Electron con i file illeggibili (cifrati): traduce i testi della pagina mentre il gioco gira,
   * senza aprire ne' cambiare i file del gioco. Le traduzioni gia' fatte sono in patchsmith-dizionario.json; le
   * frasi nuove le chiede a gruppi al traduttore di Patchsmith (servizio su 127.0.0.1, avviato qui e chiuso con il
   * gioco), che le traduce insieme e le salva nella sua memoria. Una frase in attesa resta nascosta al massimo
   * ATTESA_MS (poi si vede l'originale finche' la traduzione non arriva). I numeri si tolgono prima
   * ("1.28 minutes" -> "{{A}} minutes") e si rimettono. Le lettere accentate che i font non hanno diventano e', a'. */
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
  } catch (e) { /* nessun dizionario: tutto dal traduttore */ }
  // le traduzioni (e le frasi gia' in italiano): mai da richiedere come se fossero originali
  var tradotte = new Set();
  mappa.forEach(function (v) { tradotte.add(v); });

  // --- il traduttore di Patchsmith -------------------------------------------------------------------------------
  try {
    var c = CFG.comando;
    if (c && c.programma && fs.existsSync(c.programma)) {
      var figlio = cp.spawn(c.programma, c.argomenti.concat(["--pid", String(process.pid)]),
                            { cwd: c.cartella, detached: true, stdio: "ignore", windowsHide: true });
      figlio.on("error", function () {});
      figlio.unref();
    }
  } catch (e) { /* senza traduttore restano le traduzioni del dizionario */ }

  var chieste = new Map(), coda = [], occupato = false, errori = 0, inProgrammaInvio = false;
  function chiedi(testo) {
    if (!CFG.porta || chieste.has(testo) || chieste.size > 5000) return;
    chieste.set(testo, 0);
    coda.push(testo);
    programmaInvio();
  }
  function programmaInvio() {  // le frasi nuove di una schermata arrivano a raffica: si mandano insieme
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
      if (!ok) {  // traduttore non ancora pronto (si sta avviando) o fermo: si riprova piu' tardi
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
      (risposta.riprova || []).forEach(function (k) {  // lasciate in originale: di nuovo piu' tardi, poche volte
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

  // --- testi ---------------------------------------------------------------------------------------------------
  var NUMERO = /[+\-]?\d+(?:[.,]\d+)*/g;
  function daTradurre(s) {
    if (s.length <= 2 || !/\p{L}/u.test(s)) return false;  // tasti ("Q", "B"), simboli
    return !(/\d/.test(s) && !/\p{L}{3,}/u.test(s));        // solo numeri e unita' ("$5k", "x 2", "v1.0")
  }
  // (traduzione, o null; atteso=true se la traduzione e' stata chiesta e sta arrivando)
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

  // --- lettere che i font del gioco non hanno -----------------------------------------------------------------------
  var ACCENTATE = "àèéìòùÀÈÉÌÒÙ", RIPIEGO = { "à": "a'", "è": "e'", "é": "e'", "ì": "i'", "ò": "o'", "ù": "u'",
    "À": "A'", "È": "E'", "É": "E'", "Ì": "I'", "Ò": "O'", "Ù": "U'" };
  var fontMancanti = new Map(), misura = null;
  function mancanti(el) {
    if (!el || !window.getComputedStyle) return "";
    var stile = window.getComputedStyle(el);
    var famiglia = (stile.fontFamily || "").split(",")[0].trim();
    if (!famiglia || /^(serif|sans-serif|monospace|cursive|fantasy|system-ui)$/i.test(famiglia)) return "";
    if (fontMancanti.has(famiglia)) return fontMancanti.get(famiglia);
    if (!document.fonts || !document.fonts.check("32px " + famiglia)) return "";  // non ancora caricato: dopo
    misura = misura || document.createElement("canvas").getContext("2d");
    var assenti = "";
    for (var i = 0; i < ACCENTATE.length; i++) {
      var ch = ACCENTATE[i], larghezze = [];
      ["serif", "monospace"].forEach(function (riserva) {  // con la lettera nel font le due misure sono uguali
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

  // --- frasi in attesa: nascoste al massimo ATTESA_MS (solo se sono l'unico testo del loro elemento) -------------
  var nascosti = new Map();  // elemento -> visibilita' di prima
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

  // --- pagina --------------------------------------------------------------------------------------------------
  var IGNORA = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, NOSCRIPT: 1, CODE: 1, PRE: 1 };
  var ATTRIBUTI = ["title", "placeholder", "aria-label", "alt"];
  var scritti = new WeakMap(), originali = new WeakMap(), scrittiAttr = new WeakMap();
  function nodoTesto(n) {
    var p = n.parentNode;
    if (!p || IGNORA[p.nodeName] || p.isContentEditable) return;
    var attuale = n.nodeValue;
    var originale = scritti.get(n) === attuale ? originali.get(n) : attuale;  // gia' nostro: si rifa' dall'originale
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
      if (!v || (nostri && nostri[nome] === v)) continue;  // scritto da noi: non e' un testo nuovo
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
  function ridisegna() {  // traduzioni nuove (o font appena caricati): di nuovo tutta la pagina, al massimo ogni 100 ms
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
