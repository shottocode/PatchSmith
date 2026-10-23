"""Adapter XNA / FNA / MonoGame e in generale giochi .NET: traduce le risorse .NET (.resources).

Molti giochi .NET tengono i testi in risorse localizzate: una versione "neutra" dentro l'eseguibile
(es. RogueCastle.Resources.LocStrings.resources in RogueLegacy.exe) e un "assembly satellite" per lingua
(de/RogueLegacy.resources.dll, fr/...). .NET cerca prima il satellite della lingua richiesta e solo se
manca usa i testi dentro l'eseguibile.

PatchSmith quindi NON modifica i file del gioco:
1. legge in sola lettura le risorse neutre dall'eseguibile;
2. le traduce (memoria + Google, con i segnaposto protetti);
3. adatta i caratteri a quelli che i font del gioco sanno disegnare (in XNA un carattere assente
   dal font fa andare in crash il gioco);
4. crea NUOVI satelliti (es. en/RogueLegacy.resources.dll e it/RogueLegacy.resources.dll) con il
   compilatore C# che c'e' in ogni Windows (.NET Framework), e li verifica rileggendoli.
Per tornare indietro basta togliere i satelliti aggiunti (elencati con le impronte in patchsmith-xna.json).

Limiti: assembly firmati con nome sicuro (strong name) e risorse non stringa non sono gestiti.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile

from . import __version__, dotnet, lingua, rilevamento, xnb
from .caratteri import ASCII, adatta_caratteri  # noqa: F401 (usati anche dai test)
from .adapter_webjson import SUFFISSO_TMP, scrivi_atomico
from .sistema import gioco_aperto

MANIFESTO = "patchsmith-xna.json"
MARCATORE = "Patchsmith"  # [AssemblyProduct] dei satelliti creati da noi: un satellite del gioco non lo ha
_REGIONE = {"en": "US", "ja": "JP", "zh": "CN", "ko": "KR", "pt": "BR", "sv": "SE", "da": "DK", "cs": "CZ",
            "el": "GR", "uk": "UA", "he": "IL", "ar": "SA", "hi": "IN", "vi": "VN"}
_CULTURA = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


class ErroreXna(Exception):
    def __init__(self, messaggio="", codice=None):
        super().__init__(messaggio)
        self.codice = codice  # esito per l'interfaccia (vedi esito.py), es. GIOCO_APERTO


# --- analisi -------------------------------------------------------------------------------------

def _assembly(percorso):
    try:
        return dotnet.Assembly(percorso)
    except (dotnet.ErroreDotnet, struct.error, IndexError, ValueError, OSError):
        return None


def _nome_satellite(nome_assembly):
    return nome_assembly + ".resources.dll"


def _cartelle_cultura(cartella):
    try:
        voci = os.listdir(cartella)
    except OSError:
        return []
    return sorted(v for v in voci if _CULTURA.match(v) and os.path.isdir(os.path.join(cartella, v)))


def trova_risorse(cartella):
    """Risorse localizzate del gioco: una voce per assembly che ha risorse con satelliti in altre lingue.

    I satelliti creati da PatchSmith (marcatore interno) non contano come lingue del gioco."""
    culture = _cartelle_cultura(cartella)
    trovati = []
    for nome_file in sorted(os.listdir(cartella)):
        if not nome_file.lower().endswith((".exe", ".dll")) or nome_file.lower().endswith(".resources.dll"):
            continue
        percorso = os.path.join(cartella, nome_file)
        a = _assembly(percorso)
        if a is None:
            continue
        try:
            identita = a.identita()
            risorse = a.risorse()
        except dotnet.ErroreDotnet:
            continue
        if identita["cultura"]:
            continue
        basi = {}
        for nome in risorse:
            if not nome.endswith(".resources"):
                continue
            base = nome[:-len(".resources")]
            presenti = []
            for c in culture:
                sat = _assembly(os.path.join(cartella, c, _nome_satellite(identita["nome"])))
                try:
                    if sat and not _ha_marcatore(sat) and "%s.%s.resources" % (base, c) in sat.risorse():
                        presenti.append(c)
                except (dotnet.ErroreDotnet, ValueError):
                    continue
            if presenti and _stringhe(a, base):
                basi[base] = presenti
        if basi:
            neutra, _ = a.lingua_neutra()
            trovati.append({"file": nome_file, "assembly": a, "nome": identita["nome"],
                            "versione": a.versione_satelliti(), "firmato": bool(identita["chiave_pubblica"]),
                            "runtime": a.versione_runtime, "basi": basi, "lingua_neutra": neutra})
    return trovati


def _stringhe(assembly, base):
    """{nome: testo} della risorsa neutra, oppure {} se non si riesce a leggerla."""
    try:
        return dotnet.stringhe_resources(dotnet.leggi_resources(assembly.leggi_risorsa(base + ".resources")))
    except (dotnet.ErroreDotnet, ValueError):
        return {}




def _trovabili(caratteri):
    """Caratteri che XNA trova davvero: cerca con una ricerca binaria, quindi conta l'ordine dell'elenco."""
    def trovabile(c):
        basso, alto = 0, len(caratteri) - 1
        while basso <= alto:
            mezzo = basso + ((alto - basso) >> 1)
            if caratteri[mezzo] == c:
                return True
            basso, alto = (mezzo + 1, alto) if caratteri[mezzo] < c else (basso, mezzo - 1)
        return False
    return {c for c in caratteri if trovabile(c)}


def caratteri_font(cartella, log=None):
    """Caratteri che TUTTI i font di testo XNB del gioco sanno disegnare.

    - i font quasi senza lettere (solo cifre, per i numeri) non contano;
    - un font di sole maiuscole (il gioco mette in maiuscolo) vale anche per le minuscole;
    - se un .xnb non si riesce a leggere (es. compresso) potrebbe essere un font: per sicurezza solo ASCII;
    - None (nessun limite) solo se il gioco non ha nessun file .xnb."""
    comuni, illeggibili, visti = None, 0, 0
    radice = os.path.join(cartella, "Content")
    for cartella_corrente, _, files in os.walk(radice):
        for nome in files:
            if not nome.lower().endswith(".xnb"):
                continue
            visti += 1
            try:
                with open(os.path.join(cartella_corrente, nome), "rb") as f:
                    dati = f.read()
                if xnb.tipo_principale(dati) != "SpriteFontReader":
                    continue
                font = xnb.leggi_spritefont(dati)
            except (xnb.ErroreXnb, struct.error, IndexError, UnicodeDecodeError, ValueError, OSError):
                illeggibili += 1
                continue
            caratteri = _trovabili(font["caratteri"])
            lettere = [c for c in caratteri if c.isalpha()]
            if len(lettere) < 20:
                continue
            if not any(c.islower() for c in lettere):
                caratteri |= {c.lower() for c in caratteri}
            elif not any(c.isupper() for c in lettere):
                caratteri |= {c.upper() for c in caratteri}
            comuni = caratteri if comuni is None else comuni & caratteri
    if illeggibili:
        comuni = ASCII if comuni is None else comuni & ASCII
        if log:
            log("  ! %d file .xnb non leggibili (compressi?): per sicurezza uso solo caratteri ASCII" % illeggibili)
    elif comuni is None and visti and log:
        log("  ! Nessun font XNB riconosciuto: per sicurezza uso solo caratteri ASCII")
    if comuni is None and visti:
        comuni = ASCII
    return comuni


def analizza(cartella):
    """{"insiemi": risorse utilizzabili, "problemi": [...]}. Gli assembly firmati con nome sicuro sono esclusi."""
    insiemi, problemi = [], []
    for i in trova_risorse(cartella):
        if i["firmato"]:
            problemi.append("%s e' firmato con nome sicuro: non ancora supportato (lo salto)" % i["file"])
        else:
            insiemi.append(i)
    return {"insiemi": insiemi, "problemi": problemi}


def lingue_presenti(insiemi):
    """Lingue che il gioco ha gia' (satelliti e lingua neutra dichiarata), come codici base ('de', 'fr'...)."""
    presenti = {c.split("-")[0].lower() for i in insiemi for culture in i["basi"].values() for c in culture}
    return presenti | {(i["lingua_neutra"] or "").split("-")[0].lower() for i in insiemi if i["lingua_neutra"]}


# --- manifesto ----------------------------------------------------------------------------------

def _rel_valido(rel):
    parti = rel.split("/") if isinstance(rel, str) else []
    return (len(parti) == 2 and _CULTURA.match(parti[0]) is not None and parti[1].endswith(".resources.dll")
            and ":" not in rel and "\\" not in rel and parti[1] not in (".", ".."))


def leggi_manifesto(cartella):
    try:
        with open(os.path.join(cartella, MANIFESTO), encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None

    def nostri(voci):
        return isinstance(voci, dict) and all(_rel_valido(r) and isinstance(h, str) for r, h in voci.items())

    if not (isinstance(m, dict) and m.get("patchsmith") is True and nostri(m.get("file"))
            and nostri(m.get("precedenti", {})) and set(m.get("precedenti", {})) <= set(m["file"])):
        return None
    cartelle = m.get("cartelle_create", [])
    if not (isinstance(cartelle, list) and all(isinstance(c, str) and _CULTURA.match(c) for c in cartelle)):
        return None
    return m


def manifesto_presente(cartella):
    return os.path.exists(os.path.join(cartella, MANIFESTO))


def _sha256(percorso):
    h = hashlib.sha256()
    with open(percorso, "rb") as f:
        for blocco in iter(lambda: f.read(1 << 20), b""):
            h.update(blocco)
    return h.hexdigest()


def _ha_marcatore(assembly):
    atteso = b"\x01\x00" + bytes([len(MARCATORE)]) + MARCATORE.encode("ascii")
    return any(n == "System.Reflection.AssemblyProductAttribute" and v.startswith(atteso)
               for n, v in assembly.attributi_assembly())


def _nostro(percorso):
    """True se il file e' un satellite creato da PatchSmith (ha il marcatore interno)."""
    if not os.path.isfile(percorso):
        return False
    a = _assembly(percorso)
    return a is not None and _ha_marcatore(a)


def satelliti_nostri(cartella):
    """Percorsi relativi ('en/X.resources.dll') dei satelliti creati da PatchSmith, e dei loro temporanei."""
    trovati, temporanei = [], []
    for c in _cartelle_cultura(cartella):
        try:
            nomi = os.listdir(os.path.join(cartella, c))
        except OSError:
            continue
        for nome in nomi:
            if nome.lower().endswith(".resources.dll") and _nostro(os.path.join(cartella, c, nome)):
                trovati.append(c + "/" + nome)
            elif nome.lower().endswith(".resources.dll" + SUFFISSO_TMP):
                temporanei.append(c + "/" + nome)
    return trovati, temporanei


def installato(cartella):
    """True se nella cartella c'e' qualcosa di PatchSmith (manifesto o satelliti), anche senza manifesto."""
    return manifesto_presente(cartella) or any(satelliti_nostri(cartella))


# --- costruzione dei satelliti ------------------------------------------------------------------

def compilatore(runtime):
    """csc.exe di .NET Framework adatto al runtime del gioco (v4.x oppure v2.x/3.5)."""
    windir = os.environ.get("WINDIR", r"C:\Windows")
    versioni = ["v4.0.30319"] if runtime.startswith("v4") else ["v3.5", "v2.0.50727"]
    for quadro in ("Framework64", "Framework"):
        for v in versioni:
            p = os.path.join(windir, "Microsoft.NET", quadro, v, "csc.exe")
            if os.path.exists(p):
                return p
    raise ErroreXna("Non trovo il compilatore C# di .NET Framework %s (csc.exe), che serve a creare i file "
                    "di traduzione. Attiva .NET Framework nelle funzionalita' di Windows." % runtime)


def costruisci_satellite(runtime, nome_assembly, versione, cultura, risorse, cartella_lavoro):
    """Crea <cultura>/<nome>.resources.dll con le risorse {nome_risorsa: byte} e lo verifica. Restituisce i byte."""
    cs = os.path.join(cartella_lavoro, "satellite.cs")
    with open(cs, "w", encoding="utf-8") as f:
        f.write('[assembly: System.Reflection.AssemblyCulture("%s")]\n' % cultura)
        f.write('[assembly: System.Reflection.AssemblyVersion("%s")]\n' % versione)
        f.write('[assembly: System.Reflection.AssemblyProduct("%s")]\n' % MARCATORE)
    argomenti = [compilatore(runtime), "/nologo", "/noconfig", "/target:library", "/optimize+",
                 "/out:" + os.path.join(cartella_lavoro, _nome_satellite(nome_assembly))]
    for k, (nome, dati) in enumerate(sorted(risorse.items())):
        file_ris = os.path.join(cartella_lavoro, "r%d.resources" % k)
        with open(file_ris, "wb") as f:
            f.write(dati)
        argomenti.append("/resource:%s,%s" % (file_ris, nome))
    argomenti.append(cs)
    # (il compilatore scrive nella codifica della console: da Python 3.15 il testo si leggerebbe in UTF-8)
    esito = subprocess.run(argomenti, capture_output=True, text=True, cwd=cartella_lavoro,
                           encoding="oem" if os.name == "nt" else None, errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    uscita = os.path.join(cartella_lavoro, _nome_satellite(nome_assembly))
    if esito.returncode != 0 or not os.path.exists(uscita):
        raise ErroreXna("Il compilatore C# non e' riuscito a creare il satellite: %s"
                        % (esito.stdout + esito.stderr).strip()[:500])
    with open(uscita, "rb") as f:
        dati = f.read()
    # verifica: identita' e risorse esattamente quelle previste
    sat = dotnet.Assembly(dati=dati)
    identita = sat.identita()
    if (identita["nome"] != nome_assembly + ".resources" or identita["cultura"] != cultura
            or identita["versione"] != versione or identita["chiave_pubblica"] or not _ha_marcatore(sat)):
        raise ErroreXna("Satellite creato con identita' inattesa: %s" % identita)
    if set(sat.risorse()) != set(risorse) or any(sat.leggi_risorsa(n) != d for n, d in risorse.items()):
        raise ErroreXna("Satellite creato con risorse diverse da quelle previste")
    os.remove(uscita)
    return dati


# --- installazione ------------------------------------------------------------------------------

def _controlli(cartella):
    if gioco_aperto(cartella):
        raise ErroreXna("Il gioco e' aperto: chiudilo e riprova.", "GIOCO_APERTO")
    esito = rilevamento.rileva(cartella)
    nomi = [r["nome"] for r in esito["risultati"] if r["sezione"] == "AntiCheat"]
    if nomi:
        raise ErroreXna("Questo gioco ha un anti-cheat (%s): non aggiungo file (rischio di ban)." % ", ".join(nomi),
                        "ANTICHEAT")


def testi_da_tradurre(cartella, log=print):
    """(insiemi, testi unici da tradurre). Solo stringhe con lettere."""
    insiemi = analizza(cartella)["insiemi"]
    if not insiemi:
        raise ErroreXna("Non ho trovato risorse di testo localizzate (.resources con satelliti) utilizzabili nel gioco.")
    testi = {}
    for i in insiemi:
        for base in i["basi"]:
            stringhe = _stringhe(i["assembly"], base)
            log("  %s: %s (%d testi; lingue gia' presenti: %s)" % (i["file"], base, len(stringhe), ", ".join(i["basi"][base])))
            for testo in stringhe.values():
                if testo and any(c.isalpha() for c in testo):
                    testi.setdefault(testo, None)
    return insiemi, list(testi)


def culture_da_creare(insieme, origine, destinazione):
    """Culture dei satelliti da aggiungere: la lingua originale (giochi che la scelgono esplicitamente,
    es. "English" nel menu) e quella di destinazione (giochi che seguono la lingua di Windows)."""
    culture = []
    neutra = (insieme["lingua_neutra"] or "").lower()
    presenti = {b.split("-")[0].lower() for culture_b in insieme["basi"].values() for b in culture_b}
    for c in (origine.split("-")[0].lower(), destinazione.split("-")[0].lower()):
        if c in presenti:
            continue  # il gioco ha gia' quella lingua
        if neutra.split("-")[0] == c:
            # .NET non cerca il satellite della lingua neutra dichiarata ([NeutralResourcesLanguage]):
            # se e' generica ("en") uso la variante piu' comune ("en-US"), che invece viene cercata
            if neutra != c:
                continue
            c = "%s-%s" % (c, _REGIONE.get(c, c.upper()))
        if c not in culture:
            culture.append(c)
    return culture


def installa(cartella, origine, destinazione, nome_profilo, memoria, log=print):
    """Crea i satelliti tradotti usando 'memoria' {originale: traduzione}. Restituisce il numero di testi tradotti."""
    cartella = os.path.abspath(cartella)
    _controlli(cartella)
    manifesto = leggi_manifesto(cartella) if manifesto_presente(cartella) else None
    if manifesto_presente(cartella) and manifesto is None:
        raise ErroreXna("Il file %s non e' valido: non lo uso. Controlla la cartella a mano." % MANIFESTO)
    info_gioco = analizza(cartella)
    for problema in info_gioco["problemi"]:
        log("  ! " + problema)
    insiemi = info_gioco["insiemi"]
    if not insiemi:
        raise ErroreXna("Non ho trovato risorse di testo localizzate utilizzabili nel gioco.")
    culture_per_insieme = {}
    for i in insiemi:
        culture = culture_da_creare(i, origine, destinazione)
        for c in culture:
            if not _CULTURA.match(c):
                raise ErroreXna("Codice di lingua non valido: '%s'. Scegli la lingua dall'elenco." % c)
        culture_per_insieme[i["file"]] = culture
    ammessi = caratteri_font(cartella, log)
    if ammessi is not None:
        log("  Font del gioco: %d caratteri disegnabili da tutti i font (gli altri vengono adattati)" % len(ammessi))

    piano, tradotte, totale, adattate, scartate = {}, 0, 0, 0, 0
    lavoro = tempfile.mkdtemp(prefix="patchsmith-xna-")
    try:
        for i in insiemi:
            culture = culture_per_insieme[i["file"]]
            if not culture:
                log("  %s: niente da aggiungere (lingua gia' presente nel gioco)" % i["file"])
                continue
            per_cultura = {c: {} for c in culture}
            for base in i["basi"]:
                originale = i["assembly"].leggi_risorsa(base + ".resources")
                info = dotnet.leggi_resources(originale)
                nuove = {}
                for nome, testo in dotnet.stringhe_resources(info).items():
                    totale += 1
                    t = memoria.get(testo)
                    if testo.strip() == lingua.endonimo(origine) and lingua.endonimo(destinazione):
                        t = lingua.endonimo(destinazione)  # la voce "English" del menu ora mostra l'italiano
                    if not t or t == testo:
                        continue
                    pronta = adatta_caratteri(t, None if ammessi is None else ammessi | set(testo), destinazione)
                    if pronta is None:
                        scartate += 1
                        continue
                    adattate += pronta != t
                    nuove[nome] = pronta
                    tradotte += 1
                dati = dotnet.scrivi_resources(info, nuove)
                controllo = dotnet.stringhe_resources(dotnet.leggi_resources(dati))
                atteso = dict(dotnet.stringhe_resources(info), **nuove)
                if controllo != atteso:
                    raise ErroreXna("Controllo delle risorse tradotte fallito per %s: non scrivo nulla" % base)
                for c in culture:
                    per_cultura[c]["%s.%s.resources" % (base, c)] = dati
            for c, risorse in per_cultura.items():
                sottocartella = os.path.join(lavoro, c)
                os.makedirs(sottocartella)
                rel = c + "/" + _nome_satellite(i["nome"])
                if not _rel_valido(rel):
                    raise ErroreXna("Percorso del satellite non valido: %s" % rel)
                piano[rel] = costruisci_satellite(i["runtime"], i["nome"], i["versione"], c, risorse, sottocartella)
    finally:
        shutil.rmtree(lavoro, ignore_errors=True)

    # un file gia' presente si sostituisce solo se e' nostro (marcatore)
    for rel in piano:
        destinazione_file = os.path.join(cartella, *rel.split("/"))
        if os.path.exists(destinazione_file) and not _nostro(destinazione_file):
            raise ErroreXna("Esiste gia' %s non creato da PatchSmith: non lo sovrascrivo." % rel)
    nostri_prima, _ = satelliti_nostri(cartella)
    cartelle_create = list((manifesto or {}).get("cartelle_create", []))
    for rel in piano:
        c = rel.split("/")[0]
        if not os.path.isdir(os.path.join(cartella, c)) and c not in cartelle_create:
            cartelle_create.append(c)
    nuovi = {rel: hashlib.sha256(dati).hexdigest() for rel, dati in piano.items()}

    def scrivi_manifesto(file):
        scrivi_atomico(os.path.join(cartella, MANIFESTO), json.dumps({
            "versione": 1, "patchsmith": True, "patchsmith_versione": __version__, "lingua": destinazione,
            "profilo": nome_profilo,
            "data": datetime.datetime.now().isoformat(timespec="seconds"),
            "file": file, "cartelle_create": cartelle_create,
            "sorgenti": {i["file"]: _sha256(os.path.join(cartella, i["file"])) for i in insiemi},
        }, ensure_ascii=False, indent=2).encode("utf-8"))

    # 1) manifesto PRIMA di creare cartelle e file: un'interruzione resta sempre ripristinabile
    scrivi_manifesto(nuovi)
    # 2) file temporanei accanto (non hanno il nome giusto, quindi .NET non li carica)
    pronti = {}
    try:
        for rel, dati in piano.items():
            destinazione_file = os.path.join(cartella, *rel.split("/"))
            os.makedirs(os.path.dirname(destinazione_file), exist_ok=True)
            pronti[rel] = destinazione_file
            with open(destinazione_file + SUFFISSO_TMP, "wb") as f:
                f.write(dati)
    except BaseException:
        for p in pronti.values():
            if os.path.exists(p + SUFFISSO_TMP):
                os.remove(p + SUFFISSO_TMP)
        _togli_cartelle_vuote(cartella, cartelle_create)
        raise
    # 3) al loro posto
    for rel, p in pronti.items():
        os.replace(p + SUFFISSO_TMP, p)
    # 4) nostri satelliti non piu' necessari (es. cambio di lingua) e temporanei rimasti
    nostri_dopo, temporanei = satelliti_nostri(cartella)
    for rel in set(nostri_prima + nostri_dopo) - set(nuovi):
        p = os.path.join(cartella, *rel.split("/"))
        if _nostro(p):
            os.remove(p)
    for rel in temporanei:
        os.remove(os.path.join(cartella, *rel.split("/")))
    _togli_cartelle_vuote(cartella, cartelle_create, tenere={rel.split("/")[0] for rel in nuovi})
    cartelle_create = [c for c in cartelle_create if os.path.isdir(os.path.join(cartella, c))]
    scrivi_manifesto(nuovi)
    log("  Satelliti di traduzione scritti: %s" % ", ".join(sorted(nuovi)))
    log("  %d testi tradotti su %d%s%s" % (tradotte, totale,
                                          "; %d adattati ai caratteri dei font" % adattate if adattate else "",
                                          "; %d lasciati in originale (caratteri non disegnabili)" % scartate
                                          if scartate else ""))
    return tradotte


def _togli_cartelle_vuote(cartella, cartelle, tenere=()):
    for c in cartelle:
        p = os.path.join(cartella, c)
        if c not in tenere and os.path.isdir(p) and not os.listdir(p):
            os.rmdir(p)


def disinstalla(cartella, log=print):
    """Toglie i satelliti creati da PatchSmith (riconosciuti dal marcatore interno), anche senza manifesto."""
    cartella = os.path.abspath(cartella)
    if not installato(cartella):
        return False
    manifesto = leggi_manifesto(cartella) if manifesto_presente(cartella) else None
    if manifesto_presente(cartella) and manifesto is None:
        raise ErroreXna("Il file %s non e' valido: per sicurezza non cancello nulla." % MANIFESTO)
    if gioco_aperto(cartella):
        raise ErroreXna("Il gioco e' aperto: chiudilo e riprova.", "GIOCO_APERTO")
    nostri, temporanei = satelliti_nostri(cartella)
    for rel in nostri:
        os.remove(os.path.join(cartella, *rel.split("/")))
        log("  Rimosso %s" % rel)
    for rel in temporanei:
        os.remove(os.path.join(cartella, *rel.split("/")))
    cartelle = set((manifesto or {}).get("cartelle_create", [])) | {rel.split("/")[0] for rel in nostri + temporanei}
    _togli_cartelle_vuote(cartella, sorted(cartelle))
    for nome in (MANIFESTO + SUFFISSO_TMP, MANIFESTO):
        if os.path.exists(os.path.join(cartella, nome)):
            os.remove(os.path.join(cartella, nome))
    log("  Traduzione rimossa: il gioco usa di nuovo solo i suoi file originali.")
    return True


def stato(cartella, log=print):
    manifesto = leggi_manifesto(cartella)
    if not manifesto:
        nostri, _ = satelliti_nostri(cartella)
        if nostri:
            log("  ! Satelliti di PatchSmith presenti senza manifesto: %s ('Ripristina originali' li toglie)"
                % ", ".join(nostri))
            return True
        log("  Traduzione non installata.")
        return False
    for rel, impronta in manifesto["file"].items():
        p = os.path.join(cartella, *rel.split("/"))
        ok = os.path.exists(p) and _sha256(p) == impronta
        log("  %s %s (lingua %s, %s)" % ("OK " if ok else "NO ", rel, manifesto.get("lingua"), manifesto.get("data")))
    for nome, impronta in manifesto.get("sorgenti", {}).items():
        p = os.path.join(cartella, nome)
        if not os.path.exists(p) or _sha256(p) != impronta:
            log("  ! Il gioco e' stato aggiornato (%s): premi 'Analizza e traduci' per tradurre anche i testi nuovi."
                % nome)
    return True
