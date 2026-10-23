/* Patchsmith - injector for web games (generated automatically, do not edit).
 * Replaces the displayed texts that exactly match an entry of the dictionary:
 * text nodes, title/placeholder/aria-label/alt attributes, buttons, the page title and text
 * drawn on canvas. The game's code is not changed.
 * To go back to the original: python -I smith.py restore <profile>
 */
(function () {
  "use strict";
  var DIZIONARIO = /*__DIZIONARIO__*/{};
  var mappa = new Map();
  Object.keys(DIZIONARIO).forEach(function (k) { mappa.set(k, DIZIONARIO[k]); });
  if (!mappa.size) return;

  var ATTRIBUTI = ["title", "placeholder", "aria-label", "alt"];
  var IGNORA = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, NOSCRIPT: 1, CODE: 1, PRE: 1 };
  var scrittiTesto = new WeakMap(); // text node -> value written by us
  var scrittiAttr = new WeakMap(); // element -> {attribute: value written by us}

  function traduci(s) {
    // no minimum length: in Japanese/Chinese/Korean a menu can be 1-2 characters
    if (typeof s !== "string" || !s || s.length > 2000) return null;
    var t = mappa.get(s);
    if (t === undefined) {
      var nucleo = s.replace(/\s+/g, " ").trim();
      if (!nucleo) return null;
      t = mappa.get(nucleo);
      if (t === undefined) return null;
    }
    var inizio = s.length - s.replace(/^\s+/, "").length;
    var fine = s.replace(/\s+$/, "").length;
    return s.slice(0, inizio) + t + s.slice(fine);
  }

  // Key names (<kbd>, class="key"...): they stay untranslated, as on the keyboard.
  var CLASSI_TASTO = ["key", "kbd", "keycap", "hotkey", "keybind", "shortcut-key"];
  function contestoTasto(el) {
    for (var i = 0; el && el.nodeType === 1 && i < 3; i++, el = el.parentNode) {
      if (el.nodeName === "KBD") return true;
      for (var j = 0; el.classList && j < CLASSI_TASTO.length; j++) {
        if (el.classList.contains(CLASSI_TASTO[j])) return true;
      }
    }
    return false;
  }

  function nodoTesto(n) {
    var p = n.parentNode;
    if (!p || IGNORA[p.nodeName]) return;
    var attuale = n.nodeValue;
    if (scrittiTesto.get(n) === attuale) return;
    var t = traduci(attuale);
    if (t === null || t === attuale || p.isContentEditable || contestoTasto(p)) return;
    // <option> without value: its value is the text, which the game may read as data
    if (p.nodeName === "OPTION" && !p.hasAttribute("value")) p.setAttribute("value", p.value);
    scrittiTesto.set(n, t);
    n.nodeValue = t;
  }

  function attributo(el, nome, v) {
    var scritti = scrittiAttr.get(el);
    if (scritti && scritti[nome] === v) return;
    var t = traduci(v);
    if (t === null || t === v) return;
    if (!scritti) { scritti = {}; scrittiAttr.set(el, scritti); }
    scritti[nome] = t;
    el.setAttribute(nome, t);
  }

  function elemento(el) {
    if (!el.getAttribute) return;
    for (var i = 0; i < ATTRIBUTI.length; i++) {
      var v = el.getAttribute(ATTRIBUTI[i]);
      if (v) attributo(el, ATTRIBUTI[i], v);
    }
    // <input> buttons: only if the value is not sent as data (no name attribute)
    if (el.nodeName === "INPUT" && (el.type === "button" || el.type === "submit") && !el.name && el.value) {
      var tv = traduci(el.value);
      if (tv !== null && tv !== el.value) el.value = tv;
    }
  }

  function visita(radice) {
    if (!radice) return;
    if (radice.nodeType === 3) { nodoTesto(radice); return; }
    if (radice.nodeType !== 1 && radice.nodeType !== 9 && radice.nodeType !== 11) return;
    if (radice.nodeType === 1) {
      if (IGNORA[radice.nodeName]) return;
      elemento(radice);
    }
    var w = document.createTreeWalker(radice, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    var n;
    while ((n = w.nextNode())) {
      if (n.nodeType === 3) nodoTesto(n); else elemento(n);
    }
  }

  var osservatore = new MutationObserver(function (mutazioni) {
    var aggiunti = new Set();
    for (var i = 0; i < mutazioni.length; i++) {
      var m = mutazioni[i];
      if (m.type === "characterData") nodoTesto(m.target);
      else if (m.type === "attributes") elemento(m.target);
      else for (var j = 0; j < m.addedNodes.length; j++) aggiunti.add(m.addedNodes[j]);
    }
    // visit only the added nodes whose ancestor was not already added in the same round
    aggiunti.forEach(function (n) {
      for (var a = n.parentNode; a; a = a.parentNode) if (aggiunti.has(a)) return;
      if (n.isConnected !== false) visita(n);
    });
  });
  osservatore.observe(document.documentElement || document, {
    subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: ATTRIBUTI.concat(["value"])
  });

  // Text drawn on canvas (HTML5 games: Phaser, RPG Maker MV/MZ, ...)
  var C = window.CanvasRenderingContext2D && window.CanvasRenderingContext2D.prototype;
  if (C) {
    ["fillText", "strokeText", "measureText"].forEach(function (nome) {
      var originale = C[nome];
      if (typeof originale !== "function") return;
      C[nome] = function (testo) {
        var t = typeof testo === "string" ? traduci(testo) : null;
        if (t === null) return originale.apply(this, arguments);
        var argomenti = Array.prototype.slice.call(arguments);
        argomenti[0] = t;
        return originale.apply(this, argomenti);
      };
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { visita(document.documentElement); });
  } else {
    visita(document.documentElement);
  }
})();
