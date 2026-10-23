"""XNA / FNA / MonoGame adapter, and .NET games in general: translates the .NET resources (.resources).

Many .NET games keep their texts in localized resources: a "neutral" version inside the executable
(e.g. RogueCastle.Resources.LocStrings.resources in RogueLegacy.exe) and a "satellite assembly" per language
(de/RogueLegacy.resources.dll, fr/...). .NET first looks for the satellite of the requested language and only if
it is missing uses the texts inside the executable.

So PatchSmith does NOT modify the game's files:
1. it reads the neutral resources from the executable, read-only;
2. it translates them (translation memory + Google, with the placeholders protected);
3. it adapts the characters to those the game's fonts can draw (in XNA a character missing
   from the font makes the game crash);
4. it creates NEW satellites (e.g. en/RogueLegacy.resources.dll and it/RogueLegacy.resources.dll) with the
   C# compiler that every Windows has (.NET Framework), and verifies them by reading them back.
To go back, it is enough to remove the added satellites (listed with their fingerprints in patchsmith-xna.json).

Limits: assemblies signed with a strong name and non-string resources are not handled.
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

from . import __version__, dotnet, language as lingua, detection as rilevamento, xnb
from .characters import ASCII, adatta_caratteri  # noqa: F401 (also used by the tests)
from .adapter_webjson import SUFFISSO_TMP, scrivi_atomico
from .system import gioco_aperto

MANIFESTO = "patchsmith-xna.json"
MARCATORE = "Patchsmith"  # [AssemblyProduct] of the satellites we create: a game's satellite does not have it
_REGIONE = {"en": "US", "ja": "JP", "zh": "CN", "ko": "KR", "pt": "BR", "sv": "SE", "da": "DK", "cs": "CZ",
            "el": "GR", "uk": "UA", "he": "IL", "ar": "SA", "hi": "IN", "vi": "VN"}
_CULTURA = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


class ErroreXna(Exception):
    def __init__(self, messaggio="", codice=None):
        super().__init__(messaggio)
        self.codice = codice  # outcome for the interface (see outcome.py), e.g. GIOCO_APERTO


# --- analysis ------------------------------------------------------------------------------------

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
    """The game's localized resources: one entry per assembly that has resources with satellites in other languages.

    The satellites created by PatchSmith (internal marker) do not count as languages of the game."""
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
    """{nome: testo} of the neutral resource, or {} if it cannot be read."""
    try:
        return dotnet.stringhe_resources(dotnet.leggi_resources(assembly.leggi_risorsa(base + ".resources")))
    except (dotnet.ErroreDotnet, ValueError):
        return {}




def _trovabili(caratteri):
    """Characters that XNA really finds: it looks them up with a binary search, so the order of the list matters."""
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
    """Characters that ALL the game's XNB text fonts can draw.

    - fonts with almost no letters (only digits, for numbers) do not count;
    - an uppercase-only font (the game converts to uppercase) also counts for lowercase;
    - if an .xnb cannot be read (e.g. compressed) it might be a font: to be safe, ASCII only;
    - None (no limit) only if the game has no .xnb file at all."""
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
    """{"insiemi": usable resources, "problemi": [...]}. Assemblies signed with a strong name are excluded."""
    insiemi, problemi = [], []
    for i in trova_risorse(cartella):
        if i["firmato"]:
            problemi.append("%s e' firmato con nome sicuro: non ancora supportato (lo salto)" % i["file"])
        else:
            insiemi.append(i)
    return {"insiemi": insiemi, "problemi": problemi}


def lingue_presenti(insiemi):
    """Languages the game already has (satellites and declared neutral language), as base codes ('de', 'fr'...)."""
    presenti = {c.split("-")[0].lower() for i in insiemi for culture in i["basi"].values() for c in culture}
    return presenti | {(i["lingua_neutra"] or "").split("-")[0].lower() for i in insiemi if i["lingua_neutra"]}


# --- manifest -----------------------------------------------------------------------------------

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
    """True if the file is a satellite created by PatchSmith (it has the internal marker)."""
    if not os.path.isfile(percorso):
        return False
    a = _assembly(percorso)
    return a is not None and _ha_marcatore(a)


def satelliti_nostri(cartella):
    """Relative paths ('en/X.resources.dll') of the satellites created by PatchSmith, and of their temporary files."""
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
    """True if there is something of PatchSmith in the folder (manifest or satellites), even without a manifest."""
    return manifesto_presente(cartella) or any(satelliti_nostri(cartella))


# --- building the satellites --------------------------------------------------------------------

def compilatore(runtime):
    """The .NET Framework csc.exe suited to the game's runtime (v4.x or v2.x/3.5)."""
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
    """Creates <cultura>/<nome>.resources.dll with the resources {nome_risorsa: byte} and verifies it. Returns the
    bytes."""
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
    # (the compiler writes in the console encoding: from Python 3.15 the text would be read as UTF-8)
    esito = subprocess.run(argomenti, capture_output=True, text=True, cwd=cartella_lavoro,
                           encoding="oem" if os.name == "nt" else None, errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    uscita = os.path.join(cartella_lavoro, _nome_satellite(nome_assembly))
    if esito.returncode != 0 or not os.path.exists(uscita):
        raise ErroreXna("Il compilatore C# non e' riuscito a creare il satellite: %s"
                        % (esito.stdout + esito.stderr).strip()[:500])
    with open(uscita, "rb") as f:
        dati = f.read()
    # check: identity and resources exactly the expected ones
    sat = dotnet.Assembly(dati=dati)
    identita = sat.identita()
    if (identita["nome"] != nome_assembly + ".resources" or identita["cultura"] != cultura
            or identita["versione"] != versione or identita["chiave_pubblica"] or not _ha_marcatore(sat)):
        raise ErroreXna("Satellite creato con identita' inattesa: %s" % identita)
    if set(sat.risorse()) != set(risorse) or any(sat.leggi_risorsa(n) != d for n, d in risorse.items()):
        raise ErroreXna("Satellite creato con risorse diverse da quelle previste")
    os.remove(uscita)
    return dati


# --- installation -------------------------------------------------------------------------------

def _controlli(cartella):
    if gioco_aperto(cartella):
        raise ErroreXna("Il gioco e' aperto: chiudilo e riprova.", "GIOCO_APERTO")
    esito = rilevamento.rileva(cartella)
    nomi = [r["nome"] for r in esito["risultati"] if r["sezione"] == "AntiCheat"]
    if nomi:
        raise ErroreXna("Questo gioco ha un anti-cheat (%s): non aggiungo file (rischio di ban)." % ", ".join(nomi),
                        "ANTICHEAT")


def testi_da_tradurre(cartella, log=print):
    """(insiemi, unique texts to translate). Only strings with letters."""
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
    """Cultures of the satellites to add: the original language (games that choose it explicitly,
    e.g. "English" in the menu) and the target one (games that follow the Windows language)."""
    culture = []
    neutra = (insieme["lingua_neutra"] or "").lower()
    presenti = {b.split("-")[0].lower() for culture_b in insieme["basi"].values() for b in culture_b}
    for c in (origine.split("-")[0].lower(), destinazione.split("-")[0].lower()):
        if c in presenti:
            continue  # the game already has that language
        if neutra.split("-")[0] == c:
            # .NET does not look for the satellite of the declared neutral language ([NeutralResourcesLanguage]):
            # if it is generic ("en") I use the most common variant ("en-US"), which is looked for instead
            if neutra != c:
                continue
            c = "%s-%s" % (c, _REGIONE.get(c, c.upper()))
        if c not in culture:
            culture.append(c)
    return culture


def installa(cartella, origine, destinazione, nome_profilo, memoria, log=print):
    """Creates the translated satellites using 'memoria' {originale: traduzione}. Returns the number of translated
    texts."""
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
                        t = lingua.endonimo(destinazione)  # the menu's "English" entry now shows Italian
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

    # a file already there is replaced only if it is ours (marker)
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

    # 1) manifest BEFORE creating folders and files: an interruption can always be restored
    scrivi_manifesto(nuovi)
    # 2) temporary files next to them (they do not have the right name, so .NET does not load them)
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
    # 3) into their place
    for rel, p in pronti.items():
        os.replace(p + SUFFISSO_TMP, p)
    # 4) our satellites no longer needed (e.g. change of language) and leftover temporary files
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
    """Removes the satellites created by PatchSmith (recognized by the internal marker), even without a manifest."""
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
