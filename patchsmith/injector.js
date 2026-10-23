/* Patchsmith - injector for web games (generated automatically, do not edit).
 * Replaces the displayed texts that exactly match an entry of the dictionary:
 * text nodes, title/placeholder/aria-label/alt attributes, buttons, the page title and text
 * drawn on canvas. The game's code is not changed.
 * To go back to the original: python -I smith.py restore <profile>
 */
(function () {
  "use strict";
  var DICTIONARY = /*__DICTIONARY__*/{};
  var map = new Map();
  Object.keys(DICTIONARY).forEach(function (k) { map.set(k, DICTIONARY[k]); });
  if (!map.size) return;

  var ATTRIBUTES = ["title", "placeholder", "aria-label", "alt"];
  var IGNORE = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, NOSCRIPT: 1, CODE: 1, PRE: 1 };
  var writtenText = new WeakMap(); // text node -> value written by us
  var writtenAttr = new WeakMap(); // element -> {attribute: value written by us}

  function translate(s) {
    // no minimum length: in Japanese/Chinese/Korean a menu can be 1-2 characters
    if (typeof s !== "string" || !s || s.length > 2000) return null;
    var t = map.get(s);
    if (t === undefined) {
      var core = s.replace(/\s+/g, " ").trim();
      if (!core) return null;
      t = map.get(core);
      if (t === undefined) return null;
    }
    var start = s.length - s.replace(/^\s+/, "").length;
    var end = s.replace(/\s+$/, "").length;
    return s.slice(0, start) + t + s.slice(end);
  }

  // Key names (<kbd>, class="key"...): they stay untranslated, as on the keyboard.
  var KEY_CLASSES = ["key", "kbd", "keycap", "hotkey", "keybind", "shortcut-key"];
  function keyContext(el) {
    for (var i = 0; el && el.nodeType === 1 && i < 3; i++, el = el.parentNode) {
      if (el.nodeName === "KBD") return true;
      for (var j = 0; el.classList && j < KEY_CLASSES.length; j++) {
        if (el.classList.contains(KEY_CLASSES[j])) return true;
      }
    }
    return false;
  }

  function textNode(n) {
    var p = n.parentNode;
    if (!p || IGNORE[p.nodeName]) return;
    var current = n.nodeValue;
    if (writtenText.get(n) === current) return;
    var t = translate(current);
    if (t === null || t === current || p.isContentEditable || keyContext(p)) return;
    // <option> without value: its value is the text, which the game may read as data
    if (p.nodeName === "OPTION" && !p.hasAttribute("value")) p.setAttribute("value", p.value);
    writtenText.set(n, t);
    n.nodeValue = t;
  }

  function attribute(el, name, v) {
    var written = writtenAttr.get(el);
    if (written && written[name] === v) return;
    var t = translate(v);
    if (t === null || t === v) return;
    if (!written) { written = {}; writtenAttr.set(el, written); }
    written[name] = t;
    el.setAttribute(name, t);
  }

  function element(el) {
    if (!el.getAttribute) return;
    for (var i = 0; i < ATTRIBUTES.length; i++) {
      var v = el.getAttribute(ATTRIBUTES[i]);
      if (v) attribute(el, ATTRIBUTES[i], v);
    }
    // <input> buttons: only if the value is not sent as data (no name attribute)
    if (el.nodeName === "INPUT" && (el.type === "button" || el.type === "submit") && !el.name && el.value) {
      var tv = translate(el.value);
      if (tv !== null && tv !== el.value) el.value = tv;
    }
  }

  function visit(root) {
    if (!root) return;
    if (root.nodeType === 3) { textNode(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
    if (root.nodeType === 1) {
      if (IGNORE[root.nodeName]) return;
      element(root);
    }
    var w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    var n;
    while ((n = w.nextNode())) {
      if (n.nodeType === 3) textNode(n); else element(n);
    }
  }

  var observer = new MutationObserver(function (mutations) {
    var added = new Set();
    for (var i = 0; i < mutations.length; i++) {
      var m = mutations[i];
      if (m.type === "characterData") textNode(m.target);
      else if (m.type === "attributes") element(m.target);
      else for (var j = 0; j < m.addedNodes.length; j++) added.add(m.addedNodes[j]);
    }
    // visit only the added nodes whose ancestor was not already added in the same round
    added.forEach(function (n) {
      for (var a = n.parentNode; a; a = a.parentNode) if (added.has(a)) return;
      if (n.isConnected !== false) visit(n);
    });
  });
  observer.observe(document.documentElement || document, {
    subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: ATTRIBUTES.concat(["value"])
  });

  // Text drawn on canvas (HTML5 games: Phaser, RPG Maker MV/MZ, ...)
  var C = window.CanvasRenderingContext2D && window.CanvasRenderingContext2D.prototype;
  if (C) {
    ["fillText", "strokeText", "measureText"].forEach(function (name) {
      var original = C[name];
      if (typeof original !== "function") return;
      C[name] = function (text) {
        var t = typeof text === "string" ? translate(text) : null;
        if (t === null) return original.apply(this, arguments);
        var args = Array.prototype.slice.call(arguments);
        args[0] = t;
        return original.apply(this, args);
      };
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { visit(document.documentElement); });
  } else {
    visit(document.documentElement);
  }
})();
