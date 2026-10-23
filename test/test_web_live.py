"""Live translation of Electron games with encrypted files (adapter_web_live + injector_live.js).

Usage: python -I test/test_web_live.py <test_folder_that_does_not_exist>
- fake Electron game with GemShell: game profile "web-live", script in preload.js with a backup, dictionary and the
  translator's command; Reapply with the saved translations; Restore originals with the files identical to the original;
- if Node.js is there: the real script in a fake page, with a fake translator on 127.0.0.1 (texts translated, numbers
  put back, keys and numbers left as they are, preload put back by the game -> the after-play check puts it back).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import (adapter_web_live, auto as automatic, checks,
                        project)  # noqa: E402

OUTCOMES = []
PRELOAD = "const{ipcRenderer}=require('electron');\nwindow.gemshell={version:'1.0.1'};\n"
MAIN = ("const{app,BrowserWindow}=require('electron');new BrowserWindow({webPreferences:{preload:'preload.js',"
        "contextIsolation:false,nodeIntegration:true,sandbox:false}});")


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))


def fingerprints(folder):
    output = {}
    for root, _, files in os.walk(folder):
        for n in files:
            with open(os.path.join(root, n), "rb") as f:
                output[os.path.relpath(os.path.join(root, n), folder)] = hashlib.sha256(f.read()).hexdigest()
    return output


def fake_game(folder):
    for name, data in {"chrome_100_percent.pak": b"pak", "v8_context_snapshot.bin": b"v8", "drago.exe": b"MZ",
                       "resources/app/main.js": MAIN, "resources/app/preload.js": PRELOAD,
                       "resources/app/package.json": '{"main": "main.js"}',
                       "resources/app/gemshell-assets": b"GS02\x16\x00drago" + bytes(range(64))}.items():
        write(os.path.join(folder, "game", *name.split("/")), data)


NODE = r"""
// fake page for injector_live.js: text nodes, TreeWalker, MutationObserver, no fonts; fake translator
// (POST /translate-group) that answers after 400 ms
const path = require('path'), http = require('http');
const [script, port, output] = process.argv.slice(2);
function text(v, parent) { return { nodeType: 3, nodeValue: v, parentNode: parent }; }
const body = { nodeType: 1, nodeName: 'BODY', children: [], childNodes: [], getAttribute: () => null };
const texts = ['Click or press Z to attack the dragon!', 'Elapsed: 1.28 minutes', 'Q', '999,999'];
const nodes = texts.map(t => { const n = text(t, body); body.children.push(n); body.childNodes.push(n); return n; });
const span = { nodeType: 1, nodeName: 'SPAN', style: { visibility: '' }, childNodes: [], getAttribute: () => null };
const alone = text('Settings', span); span.childNodes.push(alone); body.children.push(span, alone); nodes.push(alone);
const button = { nodeType: 1, nodeName: 'BUTTON', childNodes: [], attr: { title: 'Close' },
  getAttribute(k) { return this.attr[k] || null; }, setAttribute(k, v) { this.attr[k] = v; } };
body.children.push(button);
global.window = { getComputedStyle: () => ({ fontFamily: 'serif' }) };
global.NodeFilter = { SHOW_ELEMENT: 1, SHOW_TEXT: 4 };
global.MutationObserver = class { observe() {} };
global.document = { readyState: 'complete', body: body, documentElement: body,
  createTreeWalker: r => { let i = 0; const l = r.children || []; return { nextNode: () => l[i++] || null }; } };
const requests = [];
const server = http.createServer((req, res) => {
  let requestBody = '';
  req.on('data', p => requestBody += p);
  req.on('end', () => {
    const group = JSON.parse(requestBody).texts;
    requests.push(group);
    const translations = {};
    group.forEach(t => translations[t] = 'IT ' + t);
    setTimeout(() => res.end(JSON.stringify({ translations, retry: [] })), 400);
  });
}).listen(Number(port), '127.0.0.1', () => {
  require(path.resolve(script));
  let hidden = null;
  setTimeout(() => { hidden = span.style.visibility; }, 300);
  setTimeout(() => { require('fs').writeFileSync(output, JSON.stringify({ values: nodes.map(n => n.nodeValue),
    hidden, after: span.style.visibility, title: button.attr.title, requests }));
    server.close(); process.exit(0); }, 2500);
});
"""


def test_script(base, preload, port):
    node = shutil.which("node")
    if not node:
        print("(Node.js not found: the script in the page was not tested)")
        return
    # the script as PatchSmith writes it, with the fake translator's port and without the Python command
    with open(preload, encoding="utf-8") as f:
        script = f.read().split(adapter_web_live.MARKER, 1)[1]
    config = {"dictionary": adapter_web_live.DICTIONARY_NAME, "port": port, "from": "en", "to": "it", "command": None}
    start = script.index("var CFG = ") + len("var CFG = ")
    end = script.index(";", start)
    script = ";(function () { //" + script[:start] + json.dumps(config) + script[end:]
    page = os.path.join(os.path.dirname(preload), "prova-pagina.js")
    write(page, script)
    write(os.path.join(base, "nodo.js"), NODE)
    output = os.path.join(base, "nodi.json")
    subprocess.run([node, os.path.join(base, "nodo.js"), page, str(port), output], timeout=30)
    os.remove(page)
    with open(output, encoding="utf-8") as f:
        r = json.load(f)
    check("in the page: sentences translated by the dictionary and the translator (numbers removed and put back), "
              "keys and numbers as they are (%s)" % r["values"],
              r["values"] == ["Clicca o premi Z per attaccare il drago!", "IT Elapsed: 1.28 minutes", "Q", "999,999",
                              "IT Settings"] and r["title"] == "IT Close")
    check("new sentences requested in a single group, never the translations already written (%s)" % r["requests"],
              len(r["requests"]) == 1 and sorted(r["requests"][0]) == ["Close", "Elapsed: {{A}} minutes", "Settings"])
    check("pending sentence hidden briefly, then visible and translated (%r -> %r)" % (r["hidden"], r["after"]),
              r["hidden"] == "hidden" and r["after"] == "")


def main(base):
    base = os.path.abspath(base)
    os.makedirs(base)
    project.DATA_ROOT = os.path.join(base, "data_folder")
    automatic.PROFILES_FOLDER = os.path.join(project.DATA_ROOT, "profiles")
    game = os.path.join(base, "Drago")
    fake_game(game)
    before = fingerprints(game)
    lines = []
    path, completed, outcome = automatic.analyze_and_translate(game, "auto", "it", "fake", lines.append)
    preload = os.path.join(game, "game", "resources", "app", "preload.js")
    with open(preload, encoding="utf-8") as f:
        written = f.read()
    profile = json.load(open(path, encoding="utf-8")) if path else {}
    check("encrypted files with preload.js in plain text: live translation (web-live game profile), outcome OK",
              completed and profile.get("engine") == "web-live" and outcome["outcome"].code == "OK")
    check("preload.js: original at the top, script at the bottom; backup verified; dictionary and command next to it",
              written.startswith(PRELOAD.rstrip("\n")) and adapter_web_live.MARKER in written
              and os.path.exists(preload + project.BACKUP_SUFFIX)
              and os.path.exists(os.path.join(os.path.dirname(preload), adapter_web_live.CONFIG_NAME))
              and "xunity-service" in written and "gemshell-assets" not in [r for r in lines])
    # translations that arrived while playing (the game's translation memory): Reapply puts them in the dictionary
    memory = project.memory_path(profile, test=True)
    write(memory, json.dumps({"Click or press Z to attack the dragon!": "Clicca o premi Z per attaccare il drago!",
                                "{{A}} minutes": "{{A}} minuti"}))
    d = {}
    automatic.reapply(game, lines.append, test=True, outcome=d)
    with open(os.path.join(os.path.dirname(preload), adapter_web_live.DICTIONARY_NAME), encoding="utf-8") as f:
        dictionary = json.load(f)
    with open(preload, encoding="utf-8") as f:
        rewritten = f.read()
    check("Reapply: dictionary with the saved translations, script only once",
              dictionary.get("{{A}} minutes") == "{{A}} minuti" and rewritten.count(adapter_web_live.MARKER) == 1)
    test_script(base, preload, 47999)
    # "No" at the end of Analyze and translate: no translator in the preload, only the dictionary; "Yes": on again
    automatic.set_live(game, False, lines.append)
    with open(preload, encoding="utf-8") as f:
        off_button = f.read()
    profile_off = json.load(open(path, encoding="utf-8"))
    automatic.set_live(game, True, lines.append)
    with open(preload, encoding="utf-8") as f:
        is_on = f.read()
    check("translation while you play turned off (only the dictionary, no translator started) and then back on",
              '"port": 0' in off_button and '"command": null' in off_button and profile_off["live"] == "none"
              and '"port": 0' not in is_on and "xunity-service" in is_on
              and automatic.live_translation(game)[1] is True and is_on.count(adapter_web_live.MARKER) == 1)
    # the game puts back its own preload.js: the after-play check notices it
    write(preload, PRELOAD)
    problems = checks.check(path, apply=False, log=lambda _m: None, retranslate=False)
    check("preload.js put back by the game: the after-play check offers to put the translation back",
              any(p.code == "RESTORED_BY_GAME" for p in problems))
    automatic.reapply(game, lines.append, test=True, outcome={})
    automatic.restore(game, lines.append, outcome={})
    check("Restore originals: game folder identical to before (preload.js, no PatchSmith files)",
              fingerprints(game) == before)
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRESULT: %d/%d checks passed" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
