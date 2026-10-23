"""Live translation of Electron games with encrypted files (adapter_web_vivo + injector_live.js).

Usage: python -I test/test_web_live.py <test_folder_that_does_not_exist>
- fake Electron game with GemShell: game profile "web-vivo", script in preload.js with a backup, dictionary and the
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

from patchsmith import (adapter_web_live as adapter_web_vivo, auto as automatico, checks as controllo,
                        project as progetto)  # noqa: E402

ESITI = []
PRELOAD = "const{ipcRenderer}=require('electron');\nwindow.gemshell={version:'1.0.1'};\n"
MAIN = ("const{app,BrowserWindow}=require('electron');new BrowserWindow({webPreferences:{preload:'preload.js',"
        "contextIsolation:false,nodeIntegration:true,sandbox:false}});")


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def scrivi(percorso, dati):
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "wb") as f:
        f.write(dati if isinstance(dati, bytes) else dati.encode("utf-8"))


def impronte(cartella):
    uscita = {}
    for radice, _, files in os.walk(cartella):
        for n in files:
            with open(os.path.join(radice, n), "rb") as f:
                uscita[os.path.relpath(os.path.join(radice, n), cartella)] = hashlib.sha256(f.read()).hexdigest()
    return uscita


def gioco_finto(cartella):
    for nome, dati in {"chrome_100_percent.pak": b"pak", "v8_context_snapshot.bin": b"v8", "drago.exe": b"MZ",
                       "resources/app/main.js": MAIN, "resources/app/preload.js": PRELOAD,
                       "resources/app/package.json": '{"main": "main.js"}',
                       "resources/app/gemshell-assets": b"GS02\x16\x00drago" + bytes(range(64))}.items():
        scrivi(os.path.join(cartella, "game", *nome.split("/")), dati)


NODO = r"""
// pagina finta per injector_live.js: nodi di testo, TreeWalker, MutationObserver, nessun font; traduttore finto
// (POST /traduci-gruppo) che risponde dopo 400 ms
const path = require('path'), http = require('http');
const [script, porta, uscita] = process.argv.slice(2);
function testo(v, genitore) { return { nodeType: 3, nodeValue: v, parentNode: genitore }; }
const corpo = { nodeType: 1, nodeName: 'BODY', figli: [], childNodes: [], getAttribute: () => null };
const testi = ['Click or press Z to attack the dragon!', 'Elapsed: 1.28 minutes', 'Q', '999,999'];
const nodi = testi.map(t => { const n = testo(t, corpo); corpo.figli.push(n); corpo.childNodes.push(n); return n; });
const span = { nodeType: 1, nodeName: 'SPAN', style: { visibility: '' }, childNodes: [], getAttribute: () => null };
const solo = testo('Settings', span); span.childNodes.push(solo); corpo.figli.push(span, solo); nodi.push(solo);
const bottone = { nodeType: 1, nodeName: 'BUTTON', childNodes: [], attr: { title: 'Close' },
  getAttribute(k) { return this.attr[k] || null; }, setAttribute(k, v) { this.attr[k] = v; } };
corpo.figli.push(bottone);
global.window = { getComputedStyle: () => ({ fontFamily: 'serif' }) };
global.NodeFilter = { SHOW_ELEMENT: 1, SHOW_TEXT: 4 };
global.MutationObserver = class { observe() {} };
global.document = { readyState: 'complete', body: corpo, documentElement: corpo,
  createTreeWalker: r => { let i = 0; const l = r.figli || []; return { nextNode: () => l[i++] || null }; } };
const richieste = [];
const server = http.createServer((req, res) => {
  let corpoRichiesta = '';
  req.on('data', p => corpoRichiesta += p);
  req.on('end', () => {
    const testi = JSON.parse(corpoRichiesta).testi;
    richieste.push(testi);
    const traduzioni = {};
    testi.forEach(t => traduzioni[t] = 'IT ' + t);
    setTimeout(() => res.end(JSON.stringify({ traduzioni, riprova: [] })), 400);
  });
}).listen(Number(porta), '127.0.0.1', () => {
  require(path.resolve(script));
  let nascosto = null;
  setTimeout(() => { nascosto = span.style.visibility; }, 300);
  setTimeout(() => { require('fs').writeFileSync(uscita, JSON.stringify({ valori: nodi.map(n => n.nodeValue),
    nascosto, dopo: span.style.visibility, title: bottone.attr.title, richieste }));
    server.close(); process.exit(0); }, 2500);
});
"""


def prova_script(base, preload, porta):
    node = shutil.which("node")
    if not node:
        print("(Node.js non c'e': lo script nella pagina non e' stato provato)")
        return
    # the script as PatchSmith writes it, with the fake translator's port and without the Python command
    with open(preload, encoding="utf-8") as f:
        script = f.read().split(adapter_web_vivo.SEGNO, 1)[1]
    config = {"dizionario": adapter_web_vivo.NOME_DIZIONARIO, "porta": porta, "da": "en", "a": "it", "comando": None}
    inizio = script.index("var CFG = ") + len("var CFG = ")
    fine = script.index(";", inizio)
    script = ";(function () { //" + script[:inizio] + json.dumps(config) + script[fine:]
    pagina = os.path.join(os.path.dirname(preload), "prova-pagina.js")
    scrivi(pagina, script)
    scrivi(os.path.join(base, "nodo.js"), NODO)
    uscita = os.path.join(base, "nodi.json")
    subprocess.run([node, os.path.join(base, "nodo.js"), pagina, str(porta), uscita], timeout=30)
    os.remove(pagina)
    with open(uscita, encoding="utf-8") as f:
        r = json.load(f)
    controlla("nella pagina: frasi tradotte dal dizionario e dal traduttore (numeri tolti e rimessi), tasti e numeri "
              "come sono (%s)" % r["valori"],
              r["valori"] == ["Clicca o premi Z per attaccare il drago!", "IT Elapsed: 1.28 minutes", "Q", "999,999",
                              "IT Settings"] and r["title"] == "IT Close")
    controlla("frasi nuove chieste in un solo gruppo, mai le traduzioni gia' scritte (%s)" % r["richieste"],
              len(r["richieste"]) == 1 and sorted(r["richieste"][0]) == ["Close", "Elapsed: {{A}} minutes", "Settings"])
    controlla("frase in attesa nascosta per poco, poi visibile e tradotta (%r -> %r)" % (r["nascosto"], r["dopo"]),
              r["nascosto"] == "hidden" and r["dopo"] == "")


def main(base):
    base = os.path.abspath(base)
    os.makedirs(base)
    progetto.RADICE_DATI = os.path.join(base, "dati")
    automatico.CARTELLA_PROFILI = os.path.join(progetto.RADICE_DATI, "profiles")
    gioco = os.path.join(base, "Drago")
    gioco_finto(gioco)
    prima = impronte(gioco)
    righe = []
    percorso, completato, esito = automatico.analizza_e_traduci(gioco, "auto", "it", "finto", righe.append)
    preload = os.path.join(gioco, "game", "resources", "app", "preload.js")
    with open(preload, encoding="utf-8") as f:
        scritto = f.read()
    profilo = json.load(open(percorso, encoding="utf-8")) if percorso else {}
    controlla("file cifrati con preload.js in chiaro: traduzione dal vivo (profilo web-vivo), esito OK",
              completato and profilo.get("motore") == "web-vivo" and esito["esito"].codice == "OK")
    controlla("preload.js: originale in testa, script in fondo; backup verificato; dizionario e comando accanto",
              scritto.startswith(PRELOAD.rstrip("\n")) and adapter_web_vivo.SEGNO in scritto
              and os.path.exists(preload + progetto.SUFFISSO_BACKUP)
              and os.path.exists(os.path.join(os.path.dirname(preload), adapter_web_vivo.NOME_CONFIG))
              and "servizio-xunity" in scritto and "gemshell-assets" not in [r for r in righe])
    # translations that arrived while playing (the game's translation memory): Reapply puts them in the dictionary
    memoria = progetto.percorso_memoria(profilo, prova=True)
    scrivi(memoria, json.dumps({"Click or press Z to attack the dragon!": "Clicca o premi Z per attaccare il drago!",
                                "{{A}} minutes": "{{A}} minuti"}))
    d = {}
    automatico.riapplica(gioco, righe.append, prova=True, esito=d)
    with open(os.path.join(os.path.dirname(preload), adapter_web_vivo.NOME_DIZIONARIO), encoding="utf-8") as f:
        dizionario = json.load(f)
    with open(preload, encoding="utf-8") as f:
        riscritto = f.read()
    controlla("Riapplica: dizionario con le traduzioni salvate, script una volta sola",
              dizionario.get("{{A}} minutes") == "{{A}} minuti" and riscritto.count(adapter_web_vivo.SEGNO) == 1)
    prova_script(base, preload, 47999)
    # "No" at the end of Analyze and translate: no translator in the preload, only the dictionary; "Yes": on again
    automatico.imposta_dal_vivo(gioco, False, righe.append)
    with open(preload, encoding="utf-8") as f:
        spento = f.read()
    profilo_spento = json.load(open(percorso, encoding="utf-8"))
    automatico.imposta_dal_vivo(gioco, True, righe.append)
    with open(preload, encoding="utf-8") as f:
        acceso = f.read()
    controlla("traduzione mentre giochi spenta (solo dizionario, nessun traduttore avviato) e poi riaccesa",
              '"porta": 0' in spento and '"comando": null' in spento and profilo_spento["dal_vivo"] == "nessuno"
              and '"porta": 0' not in acceso and "servizio-xunity" in acceso
              and automatico.traduzione_dal_vivo(gioco)[1] is True and acceso.count(adapter_web_vivo.SEGNO) == 1)
    # the game puts back its own preload.js: the after-play check notices it
    scrivi(preload, PRELOAD)
    problemi = controllo.controlla(percorso, applica=False, log=lambda _m: None, ritraduci=False)
    controlla("preload.js rimesso dal gioco: il controllo dopo aver giocato propone di rimettere la traduzione",
              any(p.codice == "RIPRISTINATO_DAL_GIOCO" for p in problemi))
    automatico.riapplica(gioco, righe.append, prova=True, esito={})
    automatico.ripristina(gioco, righe.append, esito={})
    controlla("Ripristina originali: cartella del gioco identica a prima (preload.js, nessun file di PatchSmith)",
              impronte(gioco) == prima)
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return 1 if falliti else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
