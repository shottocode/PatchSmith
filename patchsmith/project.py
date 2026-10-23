"""Workflow on a game profile: translate, apply, restore, status.

Safety of the game's files:
- before the first change the original is copied next to it as <file>.patchsmith-originale
  (copy to a temporary file, fingerprint check, then rename: never a half-finished backup);
- next to the backup there is <file>.patchsmith-info with the fingerprint of the original, the fingerprints of
  everything we wrote and the files created. It lives in the game's folder, so it holds for
  any game profile and even if the tool is moved;
- the fingerprint of what we are about to write is recorded BEFORE writing;
- if the game's file is changed by someone else (an update), it becomes the new original and the
  old original is kept with a version number;
- the texts are always extracted from the original.
"""
import collections
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import stat
import unicodedata

from . import (__version__, adapter_csv, adapter_gamemaker, adapter_lines as adapter_righe, adapter_unity_loc,
               adapter_webjson)
from . import (adapter_webui, adapter_xna, shared_file as file_condiviso, font_unity, interruption as interruzione,
               language as lingua, names as nomi_gioco)
from .characters import ASCII, Filtro, adatta_caratteri, adatta_senza, lettere_tipiche, non_disegnabili
from .backend import ErroreBackend, _peso, _uguale, problemi_traduzione, pulisci
from .system import gioco_aperto
from .text import _TOKEN, dividi_righe, e_testo_leggibile, proteggi, ricomponi_righe, ripristina_segnaposto
from .text import DETTAGLIO, solo_parole

# The program: code, downloaded components (components/), rules (data/), smith.py. It always stays here.
CARTELLA_PROGETTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The user's data (DATI_UTENTE): normally next to the program (None = CARTELLA_PROGETTO, read when needed).
# The environment variable PATCHSMITH_DATI moves them to another folder: the automatic tests use it so they never
# touch the real data. The translator for XUnity is started by the game, which normally does not have the variable:
# it uses the data next to the program.
RADICE_DATI = os.path.abspath(os.environ["PATCHSMITH_DATI"]) if os.environ.get("PATCHSMITH_DATI") else None
DATI_UTENTE = ("profiles", "memory", "logs", "languages", "configurations", "save-backups", "settings.json",
               "services.json")
# the Italian names from before 0.1.0: migra_dati moves them to the English ones; until it succeeds (a folder in
# use) cartella_dati and cartella_componenti still find the old ones
NOMI_ITALIANI = {"profiles": "profili", "memory": "memoria", "logs": "registri", "languages": "lingue",
                 "configurations": "configurazioni", "save-backups": "salvataggi", "settings.json": "impostazioni.json",
                 "services.json": "stato_servizi.json", "components": "componenti"}
DATI_UTENTE_PRIMA = tuple(NOMI_ITALIANI[n] for n in DATI_UTENTE)
SUFFISSO_BACKUP = adapter_webui.SUFFISSO_BACKUP  # ".patchsmith-originale"
# without a final ".json": Forager (and other GameMaker games) load every local/*.json as a language, ours too
SUFFISSO_INFO = ".patchsmith-info"
SUFFISSO_INFO_VECCHIO = ".patchsmith-info.json"  # up to 0.12: still read, and renamed on the first write
SUFFISSI_INFO = (SUFFISSO_INFO, SUFFISSO_INFO_VECCHIO)
TESTI_PER_GRUPPO = 150  # the translation memory is saved after each group
MAX_IMPRONTE = 50
# translator passes for the same text (2 or 3 for each "Analyze and translate"): beyond that, a saved translation
# that is suspect or unfit for the game stays as it is (it is not re-translated at every run)
MAX_TENTATIVI = 6


def cartella_dati(*parti):
    """Path among the user's data (profiles, memory, settings.json...): see RADICE_DATI. A folder with the old Italian
    name not yet moved (migra_dati) is used as long as the new one does not exist."""
    return _con_ripiego(RADICE_DATI or CARTELLA_PROGETTO, parti)


def cartella_componenti(*parti):
    """The downloaded components (Ollama and the models, BepInEx, XUnity) and the plugin sources: components/ next to
    the program (or componenti/, the old name, until it has been moved)."""
    return _con_ripiego(CARTELLA_PROGETTO, ("components",) + parti)


def _con_ripiego(base, parti):
    nuovo = os.path.join(base, *parti)
    if parti and parti[0] in NOMI_ITALIANI and not os.path.exists(nuovo):
        vecchio = os.path.join(base, NOMI_ITALIANI[parti[0]], *parti[1:])
        if os.path.exists(vecchio):
            return vecchio
    return nuovo


def migra_dati():
    """Moves the data with the Italian names from before 0.1.0 (profili, memoria, registri...) to the English names: it
    moves them, without copying or deleting anything. If the new folder already exists it moves into it the entries
    that are missing (in subfolders too); those with the same name stay in the old one. A folder in use stays where it
    is: cartella_dati still finds it. Called by the window and the command line at startup; returns the moved names."""
    spostati = []
    for nuovo, vecchio in NOMI_ITALIANI.items():
        base = CARTELLA_PROGETTO if nuovo == "components" else (RADICE_DATI or CARTELLA_PROGETTO)
        if _sposta(os.path.join(base, vecchio), os.path.join(base, nuovo)):
            spostati.append(vecchio)
    memoria = os.path.join(RADICE_DATI or CARTELLA_PROGETTO, "memory")
    if _sposta(os.path.join(memoria, "copie"), os.path.join(memoria, "copies")):
        spostati.append("copie")
    return spostati


def _sposta(da, a):
    if not os.path.exists(da):
        return False
    try:
        if not os.path.exists(a):
            os.rename(da, a)
            return True
        if not (os.path.isdir(da) and os.path.isdir(a)):
            return False
        for voce in os.listdir(da):
            if not os.path.exists(os.path.join(a, voce)):
                os.rename(os.path.join(da, voce), os.path.join(a, voce))
            elif os.path.isdir(os.path.join(da, voce)):
                _sposta(os.path.join(da, voce), os.path.join(a, voce))
        if not os.listdir(da):
            os.rmdir(da)  # (empty: everything is already in the new one)
        return True
    except OSError:  # in use (e.g. the local translator running): try again at the next startup
        return False


def maiuscola_come(originale, tradotto):
    """If the original starts with a capital letter, so does the translation (Google writes 'francese' for 'French')."""
    if originale[:1].isupper() and tradotto[:1].islower():
        return tradotto[:1].upper() + tradotto[1:]
    return tradotto


def _impronta_testo(testo):
    """The text reduced to lowercase letters and digits without accents (to recognize an adapted translation)."""
    semplice = unicodedata.normalize("NFKD", testo).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[\W_]+", "", semplice).casefold()


_PAROLA = re.compile(r"[^\W\d_]{3,}")


def _da_tradurre(riga):
    """A line must be translated if it contains at least one letter; not numbers with symbols and units (no
    word of at least 3 letters: "$5k", "+$0,01/s", "x 2", "1920 x 1080", "v1.0"), which the translator would ruin
    ("+0,01/secondo", without the dollar)."""
    if not riga or not any(c.isalpha() for c in riga):
        return False
    return not (any(c.isdigit() for c in riga) and not _PAROLA.search(riga))


def _hash_file(percorso):
    h = hashlib.sha256()
    with open(percorso, "rb") as f:
        for blocco in iter(lambda: f.read(1 << 20), b""):
            h.update(blocco)
    return h.hexdigest()


def _hash_byte(dati):
    return hashlib.sha256(dati).hexdigest()


def leggi_info(percorso):
    for suffisso in SUFFISSI_INFO:
        try:
            with open(percorso + suffisso, encoding="utf-8") as f:
                info = json.load(f)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return None
        return info if isinstance(info, dict) and info.get("hash_originale") else None
    return None


def salva_info(percorso, info):
    adapter_webjson.scrivi_atomico(
        percorso + SUFFISSO_INFO, json.dumps(info, ensure_ascii=False, indent=2).encode("utf-8"))
    if os.path.exists(percorso + SUFFISSO_INFO_VECCHIO):  # old name: remove it (the game loaded it as a language)
        os.remove(percorso + SUFFISSO_INFO_VECCHIO)


def _togli_info(percorso):
    for suffisso in SUFFISSI_INFO:
        if os.path.exists(percorso + suffisso):
            os.remove(percorso + suffisso)


def crea_backup(percorso, extra=None):
    """Verified and atomic backup of the original. Returns the info just saved ('extra' is added to it
    before the single save). If something goes wrong no temporary file is left."""
    backup = percorso + SUFFISSO_BACKUP
    temporaneo = backup + adapter_webjson.SUFFISSO_TMP
    info_salvate = False
    prima = None  # info that was already there (e.g. a game update): put back if something goes wrong
    for suffisso in SUFFISSI_INFO:
        if os.path.exists(percorso + suffisso):
            with open(percorso + suffisso, "rb") as f:
                prima = (suffisso, f.read())
            break
    try:
        shutil.copyfile(percorso, temporaneo)  # not copy2: the file's read-only flag must not pass to the backup
        with open(temporaneo, "rb+") as f:
            os.fsync(f.fileno())
        impronta = _hash_file(percorso)
        if _hash_file(temporaneo) != impronta:
            raise RuntimeError("Backup non riuscito per %s (copia diversa dall'originale): nulla e' stato modificato."
                               % percorso)
        info = {"hash_originale": impronta, "hash_scritti": [], "creati": [], "precedenti": []}
        info.update(extra or {})
        salva_info(percorso, info)
        info_salvate = True
        os.replace(temporaneo, backup)
    except BaseException:
        da_togliere = [temporaneo] + ([percorso + SUFFISSO_INFO] if info_salvate else [])
        for p in da_togliere:
            if os.path.exists(p):
                try:
                    os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
                    os.remove(p)
                except OSError:
                    pass
        if prima and info_salvate:
            try:
                adapter_webjson.scrivi_atomico(percorso + prima[0], prima[1])
            except OSError:
                pass
        raise
    return info


def _contiene_tag(percorso):
    if not percorso.lower().endswith((".html", ".htm")):
        return False
    try:
        with open(percorso, encoding="utf-8", errors="replace") as f:
            return adapter_webui.NOME_SCRIPT in f.read()
    except OSError:
        return False


def ripristina_file(percorso, log):
    """Brings a game file back to the original using backup and info. Returns True if it acted."""
    backup = percorso + SUFFISSO_BACKUP
    info = leggi_info(percorso)
    nome = os.path.basename(percorso)
    if not info:
        if os.path.exists(backup):
            log("  ! %s: backup senza informazioni, lo lascio al suo posto (controlla a mano)" % nome)
        return False
    cartella = os.path.dirname(percorso)
    if os.path.exists(backup):
        if _hash_file(backup) != info["hash_originale"]:
            log("  ! %s: il backup non corrisponde all'originale registrato, NON lo uso (controlla a mano)" % nome)
            return False
        if not os.path.exists(percorso):
            log("  %s: il gioco ha rimosso il file, elimino solo il backup" % nome)
        else:
            attuale = _hash_file(percorso)
            if attuale in info.get("hash_scritti", []) or _contiene_tag(percorso):
                with open(backup, "rb") as f:
                    adapter_webjson.scrivi_atomico(percorso, f.read())
                if _hash_file(percorso) != info["hash_originale"]:
                    log("  ! %s: ripristino non verificato, il backup resta al suo posto" % nome)
                    return False
                log("  %s: ripristinato (identico all'originale)" % nome)
            elif attuale == info["hash_originale"]:
                log("  %s: era gia' originale" % nome)
            elif info.get("configuratore") or info.get("solo_creati"):
                # file replaced by an update: the configurator's change is gone and the backup
                # (of an old version) is not needed: the file stays the new one
                log("  %s: aggiornato dal gioco, la modifica di PatchSmith non c'e' piu'" % nome
                    if info.get("configuratore") else "  %s: aggiornato dal gioco (non era modificato da PatchSmith)"
                    % nome)
            else:
                log("  ! %s: e' stato cambiato dopo la traduzione (aggiornamento o modifica a mano): lo lascio "
                    "com'e' e conservo l'originale in %s" % (nome, os.path.basename(backup)))
                for altro in info.get("creati", []):
                    _togli_creato(cartella, altro, info, log)
                return False
        os.remove(backup)
    for altro in info.get("creati", []):
        _togli_creato(cartella, altro, info, log)
    for altro in info.get("precedenti", []):
        p = os.path.join(cartella, altro)
        if os.path.exists(p):
            os.remove(p)
    _togli_info(percorso)
    return True


def _togli_creato(cartella, nome, info, log):
    """Removes a file added by PatchSmith (e.g. local/italiano.json). If it has been changed in the meantime (by hand,
    or it now belongs to someone else) it is not lost: it is set aside as <nome>.patchsmith-modificato, which the game
    does not load."""
    p = os.path.join(cartella, nome)
    if not os.path.exists(p):
        return
    atteso = (info.get("hash_creati") or {}).get(nome)
    if atteso and _hash_file(p) != atteso:
        os.replace(p, p + ".patchsmith-modificato")
        log("  ! %s era stato cambiato dopo la traduzione: tolto dal gioco ma conservato come %s"
            % (nome, nome + ".patchsmith-modificato"))
        return
    os.remove(p)
    log("  %s: tolto (l'aveva aggiunto PatchSmith)" % nome)


def trova_info(cartella):
    """All the game's files that have a .patchsmith-info (that is, changed by us)."""
    trovati = set()
    for radice, dirs, files in os.walk(cartella):
        dirs[:] = [d for d in dirs if d.lower() not in ("node_modules", ".git")]
        for f in files:
            for suffisso in SUFFISSI_INFO:
                if f.endswith(suffisso):
                    trovati.add(os.path.join(radice, f[: -len(suffisso)]))
    return sorted(trovati)


def _gruppi(testi, massimo_testi, massimo_caratteri=None):
    """Texts split into groups (one request to the translator each, the translation memory is saved after each
    group)."""
    gruppi, gruppo, caratteri = [], [], 0
    for t in testi:
        if gruppo and (len(gruppo) >= massimo_testi or
                       (massimo_caratteri and caratteri + len(t) + 1 > massimo_caratteri)):
            gruppi.append(gruppo)
            gruppo, caratteri = [], 0
        gruppo.append(t)
        caratteri += len(t) + 1
    if gruppo:
        gruppi.append(gruppo)
    return gruppi


def _contesti_nuclei(divisi, nuclei, contesti):
    """The context of each line to translate: that of the text it comes from; of the official translations the
    matching line is given if they have the same number of lines, the whole text if there is only one line, nothing if
    it is unknown which piece matches (with the whole text the translator would add sentences that are not there)."""
    per_nucleo = {}
    for testo, parti in divisi.items():
        contesto = contesti.get(testo)
        if not contesto:
            continue
        righe = [n for _, n, _ in parti if n]
        for i, n in enumerate(righe):
            if n in per_nucleo or not _da_tradurre(n):
                continue
            scelti = {}
            for codice, r in (contesto.get("riferimenti") or {}).items():
                righe_r = [x.strip() for x in r.split("\n") if x.strip()]
                if len(righe_r) == len(righe):
                    scelti[codice] = righe_r[i]
                elif len(righe) == 1:
                    scelti[codice] = " ".join(righe_r)
            per_nucleo[n] = {"chiave": contesto.get("chiave"), "riferimenti": scelti,
                             "larghezza": contesto.get("larghezza") if len(righe) == 1 else None,
                             "non_usare": contesto.get("non_usare")}
    return [per_nucleo.get(n) for n in nuclei]


def percorso_comune(orig, dest):
    """Translation memory shared by all games for short texts (menus and buttons: 'options', 'back', 'press any
    key')."""
    return cartella_dati("memory", "comune.%s-%s.json" % (orig, dest))


def _breve(testo):
    """Text for the shared translation memory: one line, 1 to 4 words, at most 40 characters, with at least one
    letter. Long sentences almost never repeat identically across different games."""
    return "\n" not in testo and len(testo) <= 40 and 1 <= len(testo.split()) <= 4 and any(c.isalpha() for c in testo)


MAX_PAROLE_ETICHETTA = 3  # short labels (buttons, menu entries): up to 3 words...
MAX_CARATTERI_ETICHETTA = 30  # ...and 30 characters


def etichetta_corta(testo):
    """True for a button or menu text: one line, 1 to 3 words, at most 30 characters, with letters."""
    t = (testo or "").strip()
    return bool(t) and "\n" not in t and len(t) <= MAX_CARATTERI_ETICHETTA and \
        1 <= len(t.split()) <= MAX_PAROLE_ETICHETTA and any(c.isalpha() for c in t)


def spazio_etichetta(originale, riferimenti=(), spazio=None):
    """The space of a short label, in 'visual' characters (backend._peso: a Chinese character counts as two and a
    half), rounded up: 1.3 times the original plus 2, or the game's longest official translation
    (of one line) or the longest line of its versions ('spazio', GameMaker), if those are more.
    If the game says nothing about its space (no official translation): only translations at least twice as long
    and 7 characters longer ('Credits' -> 'Ringraziamenti', 'Slider' -> 'Dispositivo di scorrimento'). On real
    translation memories the 1.3 rule alone flagged 15-30% of the labels, even the ordinary 'Statistiche' and
    'Combattimento'; this way it is 3-8%, the obvious cases."""
    x = _peso(originale.strip())
    massimo = 1.3 * x + 2
    ufficiali = [_peso(r.strip()) for r in riferimenti or () if isinstance(r, str) and r.strip()
                 and "\n" not in r.strip()]
    if ufficiali or spazio:
        massimo = max([massimo, spazio or 0] + ufficiali)
    else:
        massimo = max(massimo, 2 * x - 1, x + 6)
    return math.ceil(massimo - 1e-9)


def percorso_memoria(profilo, prova=False):
    return cartella_dati("memory", "%s.%s%s.json" % (
        profilo["nome"], profilo["lingua_destinazione"], ".prova" if prova else ""))


def percorso_problemi(profilo, prova=False):
    """A game's texts with a problem (memory/<gioco>.<lingua>.problemi.json): {text: {motivo, dove,
    tentativi, quando, ...}}. 'dove': 'traduzione' (no valid translation, not even after the new attempts),
    'gioco' (the saved translation cannot be written to the game: different form, characters the fonts lack),
    'memoria' (the saved translation does not pass the current checks and redoing it did not succeed), 'lunghezza'
    (short label longer than its space even after asking for a shorter one, see Progetto._accorcia: shown in "Fix
    translations", list "Too long"). 'tentativi': the translator passes already made for that text; they decide the
    seeds of the local translator (see backend.Locale._tentativi): each round, even in another run, asks new
    questions."""
    return percorso_memoria(profilo, prova)[:-len(".json")] + ".problemi.json"


def percorso_origine_comune(orig, dest):
    """Where each entry of the shared translation memory comes from: {text: {backend, modello, versione, gioco, mano}}.
    Entries without one (put there by previous versions, which copied all the short texts into it) are used only if
    they pass the current checks (see voce_comune_valida)."""
    return percorso_comune(orig, dest)[:-len(".json")] + ".origine.json"


def schema_protetti(termini):
    """The expression that finds the game's codes and names in a text (like testo.proteggi), or None."""
    termini = [t for t in termini or () if t]
    if not termini:
        return None
    return re.compile(r"(?<![^\W\d_])(?:%s)(?![^\W\d_])" % "|".join(
        re.escape(t) for t in sorted(termini, key=len, reverse=True)))


def nomi_persi(originale, tradotto, schema):
    """The names (or codes) of the original that are no longer in the translation (e.g. 'Cup Dog Games' translated)."""
    if schema is None:
        return []
    return sorted(collections.Counter(schema.findall(originale)) - collections.Counter(schema.findall(tradotto)))


def problemi_voce(originale, tradotto, dest, schema=None, riferimenti=()):
    """Why a saved translation is not good (empty = it is fine): the checks of the local translator
    (backend.problemi_traduzione: letters of another language, numbers, line breaks...), a game name translated, an
    official translation of another language copied."""
    if not isinstance(tradotto, str) or not tradotto.strip():
        return ["vuota"]
    if schema is not None and _uguale(originale, tradotto) and \
            not _da_tradurre(schema.sub("", proteggi(originale)[0]).replace("[[", "").replace("]]", "")):
        return []  # only the game's names and codes ('Cult Of The Lamb'): they stay as they were on purpose
    problemi = problemi_traduzione(originale, tradotto, dest)
    if dest.split("-")[0] in lingua._PAROLE and not _uguale(originale, tradotto) and \
            lingua.lingua_evidente(solo_parole(tradotto), dest):  # e.g. left in English or came out in Spanish
        problemi.append("in un'altra lingua")
    if nomi_persi(originale, tradotto, schema):
        problemi.append("nome tradotto")
    # only for sentences: a word identical in the two languages is normal ('Oro' in Spanish and in Italian, 'Ferro' in
    # Portuguese), and the local translator accepts it if it says it again without the official translations in view
    if riferimenti and len(re.findall(r"[^\W\d_]+", tradotto)) >= 3 and not _uguale(originale, tradotto) and \
            any(r and _uguale(r, tradotto) for r in riferimenti):
        problemi.append("riferimento copiato")
    return problemi


def voce_comune_valida(testo, tradotto, dest, origine=None, schema=None):
    """True if an entry of the shared translation memory can be used for a game: with its origin (put there by this
    version, or fixed by hand) it is enough that it does not translate a game name; without it, it must also pass the
    current checks (problemi_voce)."""
    if not isinstance(tradotto, str) or not tradotto.strip():
        return False
    if origine:
        return not nomi_persi(testo, tradotto, schema)
    return not problemi_voce(testo, tradotto, dest, schema)


def _adesso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _adatta_tmp(tradotto, originale, ammessi, esclusi, dest):
    """(translation with only the characters the TextMeshPro fonts can draw, '') or (None, characters that cannot
    be replaced). ammessi: the fonts' characters (font_unity; None if unknown); esclusi: those the game said it
    does not have (Unity log): replaced only with ASCII characters if nothing is known about the fonts."""
    if ammessi is not None:
        insieme = Filtro(ammessi, esclusi, originale)
        if all(c in insieme or c in "\r\n" for c in tradotto):
            return tradotto, ""
        pronto = adatta_caratteri(tradotto, insieme, dest)
        if pronto is not None and any(c.isalpha() for c in pronto):
            return pronto, ""
        return None, non_disegnabili(tradotto, insieme, dest)
    pronto = adatta_senza(tradotto, esclusi, dest)
    if pronto is not None and any(c.isalpha() for c in pronto):
        return pronto, ""
    return None, "".join(c for c in dict.fromkeys(tradotto) if c in esclusi and adatta_senza(c, esclusi, dest) is None)


class Progetto:
    def __init__(self, percorso_profilo, log=print, prova=False):
        self.percorso_profilo = os.path.abspath(percorso_profilo)
        self.profilo = self._leggi_json(self.percorso_profilo, None, "profilo")
        self.log = log
        self.percorso_memoria = percorso_memoria(self.profilo, prova)
        self.prova = prova
        self.memoria = self._leggi_json(self.percorso_memoria, {}, "file delle traduzioni")
        # the translation memory is also written by the translator started by the game: only our own changes are saved
        self._file_memoria = file_condiviso.FileUnito(self.percorso_memoria, self.memoria)
        # for the outcome of the operation (see outcome.py): why the translation stopped and what applica() wrote
        self.esito_traduzione = None  # None, "TRADUTTORE_BLOCCATO" or "NULLA_DA_FARE"
        self.motivo_traduzione = ""
        self.ultimo_applica = None  # {ok, mancanti, esempi, scartate, scritti, aperto}
        # for the live translator (servizio_xunity): the translator error that stopped the last translation and
        # the last translated group ({divisi, protetti, mappa, ripiegati}), to build a fallback answer
        self.ultimo_errore = None
        self.ultimo_gruppo = None
        # False in the live translator (servizio_xunity): one sentence at a time while playing, no second rounds,
        # no revalidation nor problemi.json (its diary notes the rejected sentences)
        self.recupero = True
        self._giro_minimo = {}  # {text: round}: saved translations to redo already count one round (see _giri)
        self.ultimi_falliti = []  # texts of the last translation left without a valid translation (after new rounds)
        self.rifatte = 0  # saved translations redone in the last translation (suspect or unfit for the game)
        # labels asked shorter and shortened, and those noted as too long (see _accorcia), over the whole
        # operation (traduci() may translate twice)
        self.accorciate = self.lunghe_annotate = 0
        self.percorso_problemi = percorso_problemi(self.profilo, prova)
        self._problemi = self._leggi_problemi()
        self._file_problemi = file_condiviso.FileUnito(self.percorso_problemi, self._problemi)
        self._automatiche = None  # read when needed (_leggi_automatiche)
        self._nomi = ()  # game names not to translate, for the texts of this translation (_prepara_nomi)
        self._parole_comuni = None
        self._migra_stato_vecchio()
        # invisible characters inserted by Google in translations of previous versions: remove them
        pulite = {k: pulisci(v) for k, v in self.memoria.items() if isinstance(v, str) and pulisci(v) != v}
        if pulite:
            self.memoria.update(pulite)
            self._salva_memoria()

    @staticmethod
    def _leggi_json(percorso, predefinito, descrizione):
        try:  # (retries if another program is saving it right now: see file_condiviso)
            return file_condiviso.leggi_json(percorso)
        except FileNotFoundError:
            if predefinito is None:
                raise
            return predefinito
        except ValueError as e:
            raise RuntimeError("Il %s non e' un JSON valido: %s (%s). Correggilo e riprova." % (
                descrizione, percorso, e)) from e

    def _migra_stato_vecchio(self):
        """Versions 0.1-0.2 kept the fingerprints in profili/<nome>.stato.json: move them into the game."""
        vecchio = os.path.splitext(self.percorso_profilo)[0] + ".stato.json"
        if not os.path.exists(vecchio):
            return
        stato = self._leggi_json(vecchio, {}, "file di stato")
        for rel, dati in stato.get("file", {}).items():
            percorso = self._percorso_gioco(rel)
            if os.path.exists(percorso + SUFFISSO_BACKUP) and not leggi_info(percorso) and dati.get("hash_originale"):
                creati = [os.path.basename(c) for c in dati.get("creati", [])]
                salva_info(percorso, {"hash_originale": dati["hash_originale"],
                                      "hash_scritti": [h for h in [dati.get("hash_scritto")] if h],
                                      "creati": creati, "precedenti": []})
        os.remove(vecchio)

    def _salva_memoria(self):
        """Saves the new (or changed or removed) translations and takes those saved in the meantime by others (e.g. the
        translator started by the game): see file_condiviso."""
        self._file_memoria.salva(self.memoria)

    def _leggi_problemi(self):
        """The texts with a problem (see percorso_problemi). The user never writes the file: if it is damaged it
        starts over from scratch (on save the damaged copy stays in .illeggibile)."""
        try:
            dati = file_condiviso.leggi_json(self.percorso_problemi)
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in dati.items() if isinstance(v, dict)} if isinstance(dati, dict) else {}

    def _salva_problemi(self):
        try:
            self._file_problemi.salva(self._problemi)
        except OSError as e:
            self.log("  ! Elenco dei testi con problemi non salvato (%s)" % e)

    def problemi(self):
        """Copy of the texts with a problem: {text: {motivo, dove, tentativi, ...}} (see percorso_problemi)."""
        return {k: dict(v) for k, v in self._problemi.items()}

    def _segna_problema(self, testo, **campi):
        """Records (or updates) the problem of a text. Always a new dictionary: FileUnito compares the entries with
        how they were, and a dictionary changed in place would not appear changed."""
        voce = dict(self._problemi.get(testo) or {})
        voce.update({k: v for k, v in campi.items() if v is not None})
        voce["quando"] = _adesso()
        self._problemi[testo] = voce

    def _leggi_automatiche(self):
        """The machine translations (<memoria>.automatiche.json): an entry of the translation memory equal to that
        one is a machine translation, a different one was fixed by hand (and is not touched). Not kept in the test
        memory."""
        if self._automatiche is None:
            self._automatiche = {} if self.prova else \
                self._leggi_json(self._percorso_automatiche(), {}, "file delle traduzioni automatiche")
            self._file_automatiche = file_condiviso.FileUnito(self._percorso_automatiche(), self._automatiche)
        return self._automatiche

    def automatica(self, testo):
        """True if the saved translation of the text is the machine translation (not fixed by hand)."""
        v = self.memoria.get(testo)
        return isinstance(v, str) and self._leggi_automatiche().get(testo) == v

    def _percorso_gioco(self, rel):
        return os.path.join(self.profilo["cartella_gioco"], *rel.split("/"))

    # --- originals and updates ----------------------------------------------------------------

    def _sembra_tradotto(self, percorso, voce):
        """True if the file mostly contains our translations (that is, it is the translated file, changed by hand)."""
        if not voce or voce.get("formato") == "web-ui":
            return False
        try:
            if voce.get("formato") == "righe":
                testi = [t for _, t in adapter_righe.leggi(percorso).unita]
            elif voce.get("formato") == "csv":
                f = adapter_csv.leggi(percorso)
                testi = [t for _, t in f.testi(self._colonna(f, voce))]
            elif voce.get("formato") == "unity-tabelle":
                testi = [t for _, t in adapter_unity_loc.FileTabelle(percorso).testi()]
            else:
                testi = [t for _, t in self._estrai_json(adapter_webjson.FileJSON(percorso).dati, voce)]
        except (OSError, ValueError, KeyError):
            return False
        # in the file the translations may be adapted to the game (lowercase, font characters, line breaks): only
        # letters and digits are compared, without accents and capitals
        tradotti = {_impronta_testo(v) for v in self.memoria.values() if isinstance(v, str)}
        originali = {_impronta_testo(k) for k in self.memoria}
        nostri = sum(1 for t in testi if _impronta_testo(t) in tradotti and _impronta_testo(t) not in originali)
        return len(testi) >= 3 and nostri >= 0.3 * len(testi)

    def _originale(self, percorso, aggiorna=True, voce=None):
        """Path of the file to extract the original texts from (None if the file no longer exists)."""
        if not os.path.exists(percorso):
            return None
        backup = percorso + SUFFISSO_BACKUP
        if not os.path.exists(backup):
            return percorso
        info = leggi_info(percorso)
        if not info and _hash_file(backup) == _hash_file(percorso):
            info = {"hash_originale": _hash_file(backup), "hash_scritti": [], "creati": [], "precedenti": []}
            salva_info(percorso, info)
        if not info:
            raise RuntimeError("Per %s c'e' un backup ma non le sue informazioni: non posso sapere se e' "
                               "affidabile. Controlla a mano (o elimina il backup se il file e' originale)." % percorso)
        if _hash_file(backup) != info["hash_originale"]:
            raise RuntimeError("Il backup di %s non corrisponde all'originale registrato: mi fermo." % percorso)
        attuale = _hash_file(percorso)
        if attuale == info["hash_originale"] or attuale in info.get("hash_scritti", []) or _contiene_tag(percorso):
            return backup
        # The translated file was changed by hand: the original stays the one in the backup (it is not lost)
        if self._sembra_tradotto(percorso, voce):
            if aggiorna:
                self.log("  ! %s e' stato modificato a mano dopo la traduzione: lo riscrivo dall'originale "
                         "(per correggere una traduzione usa 'Correggi traduzioni')" % os.path.basename(percorso))
            return backup
        # The file was changed by someone else: an update of the game (or of another mod).
        if not aggiorna:
            return percorso
        n = len(info.get("precedenti", [])) + 1
        vecchio = "%s.%d" % (os.path.basename(backup), n)
        os.replace(backup, os.path.join(os.path.dirname(backup), vecchio))
        tenere = {k: info[k] for k in ("creati", "hash_creati", "solo_creati") if k in info}
        try:
            crea_backup(percorso, dict(tenere, precedenti=info.get("precedenti", []) + [vecchio]))
        except BaseException:
            os.replace(os.path.join(os.path.dirname(backup), vecchio), backup)  # everything as it was
            raise
        self.log("  ! %s e' cambiato (aggiornamento del gioco?): e' il nuovo originale, il precedente e' in %s"
                 % (os.path.basename(percorso), vecchio))
        return backup

    def _carica(self, voce, aggiorna=True):
        """(object, [(position, text)]) for the entry; (None, None) if the file no longer exists."""
        percorso = self._percorso_gioco(voce["percorso"])
        sorgente = self._originale(percorso, aggiorna, voce)
        if sorgente is None:
            return None, None
        if voce.get("formato", "json") == "web-ui":
            return None, [(None, t) for t in adapter_webui.estrai_testi(os.path.dirname(percorso), self._una_volta)]
        if voce.get("formato") == "righe":
            f = adapter_righe.leggi(sorgente)
            return f, f.testi()
        if voce.get("formato") == "csv":
            f = adapter_csv.leggi(sorgente)
            return f, f.testi(self._colonna(f, voce))
        if voce.get("formato") == "unity-tabelle":
            f = adapter_unity_loc.FileTabelle(sorgente)
            return f, f.testi()
        f = adapter_webjson.FileJSON(sorgente)
        return f, self._estrai_json(f.dati, voce)

    def _una_volta(self, messaggio):
        """Writes a message to the log only once (the files are read several times: translation, writing)."""
        visti = self.__dict__.setdefault("_detti", set())
        if messaggio not in visti:
            visti.add(messaggio)
            self.log(messaggio)

    @staticmethod
    def _colonna(f, voce):
        """The index of the column to translate of a CSV table (voce["colonna"]: its name in the header)."""
        i = f.colonna(voce.get("colonna"))
        if i is None:
            raise adapter_csv.ErroreCSV("la colonna '%s' non c'e' piu' nella tabella" % voce.get("colonna"))
        return i

    @staticmethod
    def _grezzi_json(dati, voce):
        """[(position, text as it is in the file)]: in localization files every value with letters is a text."""
        leggibile = adapter_gamemaker.testo_localizzazione if voce.get("localizzazione") else e_testo_leggibile
        return adapter_webjson.estrai(dati, voce["includi"], leggibile)

    @classmethod
    def _estrai_json(cls, dati, voce):
        """[(position, text to translate)]: with the game's conventions (line break '*', symbol at paragraph start)
        the text is normalized (whole sentences, paragraphs on separate lines)."""
        grezzi = cls._grezzi_json(dati, voce)
        if voce.get("a_capo") or voce.get("punto_elenco"):
            return [(p, adapter_gamemaker.normalizza_a_capo(t, voce.get("a_capo"), voce.get("punto_elenco")))
                    for p, t in grezzi]
        return grezzi

    def _prepara_scrittura(self, percorso, dati_byte):
        """Backup (once) and recording of the fingerprint BEFORE writing."""
        info = leggi_info(percorso) if os.path.exists(percorso + SUFFISSO_BACKUP) else None
        if info is None:
            info = crea_backup(percorso)
        impronta = _hash_byte(dati_byte)
        scritti = [h for h in info.get("hash_scritti", []) if h != impronta] + [impronta]
        info["hash_scritti"] = scritti[-MAX_IMPRONTE:]
        info["patchsmith_versione"] = __version__  # which version wrote it (for future checks)
        salva_info(percorso, info)
        return info

    # --- commands -------------------------------------------------------------------------------

    @property
    def codici(self):
        """Words of the game not to translate (font icons, e.g. 'EP' and 'H' in Forager), from all entries."""
        return sorted({c for v in self.profilo.get("file", []) for c in v.get("codici", [])})

    def _prepara_nomi(self, testi=()):
        """The game names not to translate for this translation (profilo["non_tradurre"]: studio and title, see
        names.py). A one-word name that also appears in lowercase in the game's texts ('Control') is an ordinary word
        and is translated: the texts to translate and those already in the translation memory are looked at."""
        elenco = self.profilo.get("non_tradurre") or []
        if not elenco:
            self._nomi = ()
            return
        if self._parole_comuni is None:
            self._parole_comuni = nomi_gioco.parole_comuni(self.memoria)
        self._nomi = tuple(nomi_gioco.da_proteggere(elenco, self._parole_comuni | nomi_gioco.parole_comuni(testi)))

    def _protetti(self):
        """The game's codes and names to protect in the texts to translate (testo.proteggi)."""
        codici = self.codici if hasattr(self, "profilo") else []
        return tuple(sorted(set(codici) | set(getattr(self, "_nomi", ()))))

    def _da_rifare(self, testi, dest, contesti=None):
        """The saved translations to redo, {text: reason}: they stay in the translation memory until a better one
        arrives (if the new translation fails, the previous one stays). Only machine translations (fixes made by
        hand are not touched):
        - that do not pass the current checks (problemi_voce: letters of another language, numbers, different line
          breaks, a game name translated, an official translation of another language copied). Previous versions, and
          Google, did not have these checks;
        - that could not be written to the game (problemi.json, dove 'gioco': form different from the original,
          characters the game's fonts lack): they are redone with the right instruction (see _con_correzioni).
        In addition a text that is only a game name ('Cup Dog Games') and is translated in the translation memory, even
        without a trace of the machine translation (memories of previous versions). After MAX_TENTATIVI passes a
        translation is left as it is."""
        if not self.recupero:
            return {}
        automatiche = self._leggi_automatiche()
        schema = schema_protetti(self._protetti())
        solo_nome = schema_protetti(self._nomi)
        rifare = {}
        for t in testi:
            v = self.memoria.get(t)
            if not isinstance(v, str) or (self._problemi.get(t) or {}).get("tentativi", 0) >= MAX_TENTATIVI:
                continue
            if solo_nome and solo_nome.fullmatch(t.strip()) and v.strip() != t.strip() and \
                    (automatiche.get(t) == v or t not in automatiche):  # machine translation or from a previous version
                rifare[t] = "nome del gioco tradotto"
                continue
            if automatiche.get(t) != v:
                continue
            problema = self._problemi.get(t) or {}
            if problema.get("dove") == "gioco" and problema.get("valore") == v:
                rifare[t] = problema.get("motivo") or "non adatta al gioco"
                continue
            riferimenti = ((contesti or {}).get(t) or {}).get("riferimenti") or {}
            motivi = problemi_voce(t, v, dest, schema, list(riferimenti.values()))
            if motivi:
                rifare[t] = "traduzione salvata sospetta (%s)" % ", ".join(motivi)
        if rifare:
            esempio = next(iter(rifare))
            self.log("  %d traduzioni salvate non vanno bene (es. %r: %s): le rifaccio; se non riesco resta quella "
                     "di prima" % (len(rifare), esempio[:50], rifare[esempio]))
        return rifare

    def _con_correzioni(self, testi, contesti):
        """The contexts for the local translator with the instructions for the texts whose translation could not be
        written to the game: the characters not to use (those the game's fonts cannot draw, or the game's line break
        sign written inside the sentence). The labels noted as too long ('lunghezza', see _accorcia), if
        re-translated (e.g. "Re-translate" in the "Too long" list), are requested with their space ('larghezza')."""
        uscita = dict(contesti or {})
        for t in testi:
            problema = self._problemi.get(t) or {}
            non_usare = problema.get("non_usare")
            if non_usare:
                uscita[t] = dict(uscita.get(t) or {}, non_usare=non_usare)
            spazio = problema.get("spazio")
            if problema.get("dove") == "lunghezza" and isinstance(spazio, int) and spazio > 0 and etichetta_corta(t):
                uscita[t] = dict(uscita.get(t) or {}, larghezza=spazio)
        return uscita

    def _dimentica_senza_codici(self, testi):
        """The saved translations that lost a code of the original (e.g. 'max H' translated 'massimo', without
        the heart icon) are redone: previous versions did not protect the codes."""
        if not self.codici:
            return
        schema = re.compile(r"(?<![^\W\d_])(?:%s)(?![^\W\d_])" % "|".join(map(re.escape, self.codici)))
        tolti = 0
        for t in set(testi):
            v = self.memoria.get(t)
            if isinstance(v, str) and sorted(schema.findall(t)) != sorted(schema.findall(v)):
                del self.memoria[t]
                tolti += 1
        if tolti:
            self.log("  %d traduzioni salvate avevano perso un'icona del gioco (%s): le rifaccio"
                     % (tolti, ", ".join(self.codici)))
            self._salva_memoria()

    def _contesti(self, voce, unita, dest):
        """{text: context} for the local translator: the key of the text in the file (without the final number: it is a
        hint, not a text to translate), the game's official translations (at most 3 languages) and the space
        they take (for short labels)."""
        # for Italian, Spanish and Portuguese together are too close: the model copied them ('saque', 'crânio')
        preferite = ("fr", "de", "es", "pl", "ru", "pt", "tr")
        if voce.get("formato") == "csv":
            return self._contesti_csv(voce, unita, dest, preferite)
        riferimenti = voce.get("riferimenti") or {}
        scelte = [c for c in preferite if c in riferimenti and c.split("-")[0] != dest.split("-")[0]]
        scelte += [c for c in sorted(riferimenti) if c not in scelte and c.split("-")[0] != dest.split("-")[0]
                   and lingua.endonimo(c)]
        dati = {}
        for c in scelte[:3]:
            try:
                dati[c] = adapter_gamemaker.leggi_originale(self._percorso_gioco(riferimenti[c]),
                                                            adapter_gamemaker._leggi_json)
            except (OSError, ValueError):
                continue
        limiti = voce.get("limiti") or {}
        uscita = {}
        for pos, testo in unita:
            if testo in uscita or not isinstance(pos, tuple) or not pos:
                continue
            chiave = adapter_gamemaker.chiave_di(pos)
            rif = {}
            for c, d in dati.items():
                v = adapter_gamemaker.al_percorso(d, pos)  # also inside the sections ('menu', '_play')
                if isinstance(v, str) and v.strip():
                    rif[c] = adapter_gamemaker.normalizza_a_capo(v, voce.get("a_capo"), voce.get("punto_elenco")) \
                        if voce.get("a_capo") or voce.get("punto_elenco") else v
            limite = limiti.get(chiave)
            # 'spazio': the longest line of the game's versions, for one-line labels (see _accorcia)
            uscita[testo] = {"chiave": re.sub(r"[_\-.]?\d+$", "", chiave), "riferimenti": rif,
                             "spazio": limite[0] if limite and limite[1] == 1 else None}
        return uscita

    def _contesti_csv(self, voce, unita, dest, preferite):
        """Like _contesti, for a CSV table: the key is the cell of the keys column, the official translations
        are the other columns of the same row (at most 3 languages)."""
        f = adapter_csv.leggi(self._originale(self._percorso_gioco(voce["percorso"]), aggiorna=False, voce=voce))
        codici = [c for i, c in f.lingue.items() if c.split("-")[0] != dest.split("-")[0]
                  and i != self._colonna(f, voce)]
        scelte = [c for c in preferite if c in codici] + [c for c in codici if c not in preferite]
        scelte = list(dict.fromkeys(scelte))[:3]
        uscita = {}
        for pos, testo in unita:
            if testo not in uscita:
                chiave = f.chiave_di(pos)
                uscita[testo] = {"chiave": re.sub(r"[_\-.]?\d+$", "", chiave), "spazio": None,
                                 "riferimenti": f.riferimenti(pos, scelte)}
        return uscita

    def traduci(self, backend):
        orig = self.profilo.get("lingua_origine", lingua.AUTO)
        dest = self.profilo["lingua_destinazione"]
        testi, contesti = [], {}
        for voce in self.profilo["file"]:
            _, unita = self._carica(voce)
            testi += [t for _, t in unita or []]
            if unita and voce.get("localizzazione"):
                # key and official translations: to the local translator as context, and to recognize a copied
                # official translation among the saved ones (see _da_rifare)
                for t, c in self._contesti(voce, unita, dest).items():
                    contesti.setdefault(t, c)
        if orig == lingua.AUTO:
            orig, motivo = lingua.rileva(testi)
            self.log("Lingua del gioco rilevata: %s" % lingua.etichetta(orig))
            self.log(DETTAGLIO + "  (lingua riconosciuta da: %s)" % motivo)
        if orig.split("-")[0] == dest.split("-")[0]:
            self.log("Il gioco e' gia' in %s: niente da tradurre." % lingua.etichetta(dest))
            self.esito_traduzione = "NULLA_DA_FARE"
            self.motivo_traduzione = "Il gioco e' gia' in %s: non c'e' niente da tradurre." % lingua.etichetta(dest)
            interruzione.controlla()
            return self.applica()
        presenti = set(testi)
        vecchi = [t for t in self._problemi if t not in presenti]  # texts the game no longer has
        for t in vecchi:
            del self._problemi[t]
        if vecchi:
            self._salva_problemi()
        falliti = self.traduci_testi(testi, backend, orig, dest, contesti)
        interruzione.controlla()  # stopped during the last request: nothing is written to the game
        fatto = self.applica()
        # machine translations the game cannot show (different form, characters the fonts lack): another round
        # right away with the right instruction, only once
        rifare = [t for t in self._respinte if self.automatica(t)
                  and (self._problemi.get(t) or {}).get("tentativi", 0) < MAX_TENTATIVI]
        if rifare and self.recupero and self.esito_traduzione is None:
            self.log("  %d traduzioni non si possono scrivere nel gioco: le rifaccio subito con l'istruzione giusta"
                     % len(rifare))
            primi, rifatte = list(self.ultimi_falliti), self.rifatte
            falliti += self.traduci_testi(rifare, backend, orig, dest, contesti)
            self.ultimi_falliti = list(dict.fromkeys(primi + self.ultimi_falliti))
            self.rifatte += rifatte
            interruzione.controlla()
            fatto = self.applica()
        return fatto and not falliti

    def nomi_attivi(self, testi=()):
        """The game names that stay as they are in the translation of these texts (see _prepara_nomi)."""
        self._prepara_nomi(testi)
        return self._nomi

    # --- fixes made with "Fix translations" (see corrections.py) ---------------------------------------

    def lingua_origine(self, testi=None):
        """The game's language: that of the game profile, or detected from the texts (those given or those in the
        translation memory)."""
        orig = self.profilo.get("lingua_origine") or lingua.AUTO
        if orig == lingua.AUTO:
            campione = list(testi) if testi is not None else list(self.memoria)
            orig = lingua.rileva(campione[:5000])[0] if campione else "en"
        return orig

    def correggi(self, modifiche):
        """The translations fixed by hand with "Fix translations" ({text: translation}):
        - they go into the translation memory (saved with the lock, see file_condiviso: the sentences added in the
          meantime by the translator started by the game stay);
        - they leave problemi.json if they were left untranslated or suspect (now there is yours); those the game could
          not show stay until "Reapply" manages to write them;
        - the short texts go into the shared translation memory as fixes made by hand (see _memoria_comune), even those
          that had no machine translation (e.g. left untranslated and translated by you).
        The machine translation stays in .automatiche.json: the fix is always recognized and never touched again. A
        text that was not there (left untranslated, or translated by a version that did not record the machine
        translations: for PatchSmith an entry without a record is a machine translation) enters it with the previous
        translation (empty if there was none), so it too counts as fixed by hand.
        Returns the texts whose translation in the translation memory changed."""
        cambiate = [t for t, v in modifiche.items() if isinstance(t, str) and isinstance(v, str) and v.strip()
                    and self.memoria.get(t) != v]
        prima = {t: self.memoria.get(t) for t in cambiate}
        for t in cambiate:
            self.memoria[t] = modifiche[t]
        if cambiate:
            self._salva_memoria()
        if cambiate and not self.prova:
            automatiche = self._leggi_automatiche()
            annotate = {t: prima[t] if isinstance(prima[t], str) else "" for t in cambiate if t not in automatiche}
            if annotate:
                automatiche.update(annotate)
                try:
                    self._file_automatiche.salva(automatiche)
                except OSError as e:
                    self.log("  ! Traduzioni automatiche non annotate (%s)" % e)
        tolti = [t for t in modifiche if (self._problemi.get(t) or {}).get("dove") in ("traduzione", "memoria")]
        for t in tolti:
            del self._problemi[t]
        if tolti:
            self._salva_problemi()
        if self.prova or not cambiate:
            return cambiate
        dest = self.profilo["lingua_destinazione"]
        orig = self.lingua_origine()
        if orig.split("-")[0] == dest.split("-")[0]:
            return cambiate
        comune = self._memoria_comune(orig, dest)  # (also this game's earlier fixes made by hand)
        nome, aggiunte = self.profilo.get("nome"), 0
        for t in cambiate:
            v = self.memoria[t]
            if not _breve(t) or v.strip() == t.strip() or comune.get(t) == v:
                continue
            gia = self._origine_comune.get(t) or {}
            if gia.get("mano") and gia.get("gioco") != nome:  # fixed by hand in another game: that one stays
                continue
            comune[t] = v
            self._origine_comune[t] = {"mano": True, "gioco": nome, "versione": __version__}
            aggiunte += 1
        if aggiunte:
            try:
                self._file_comune.salva(comune)
            except OSError as e:
                self.log("  ! Memoria comune non aggiornata (%s)" % e)
                return cambiate
            self._salva_origine()
        return cambiate

    def ritraduci(self, testi, backend, orig, dest, contesti=None, giro=1):
        """Re-translates the given texts now, as requested with "Fix translations": also those already in the
        translation memory and those fixed by hand (the choice is the user's). If the new translation fails the previous
        one stays. giro: which round the local translator's seeds start from (1 or more = new seeds and slightly higher
        temperatures: the same question would give the same answer; 0 = the questions of the first round, for example
        with the other model). The new translations become machine translations (.automatiche.json) and the short texts
        go into the shared translation memory as in a normal translation. Returns (texts with a new translation, texts
        left as they were)."""
        unici = [t for t in dict.fromkeys(testi) if isinstance(t, str) and _da_tradurre(t)]
        self._prepara_nomi(unici)
        self._backend_attuale = backend
        self.rifatte = 0
        self.ultimi_falliti = []
        self._riusciti = set()
        comune = None
        if not self.prova and orig != lingua.AUTO:
            comune = self._memoria_comune(orig, dest)
        else:
            self._leggi_automatiche()
        prima = {t: self.memoria.get(t) for t in unici}
        self._giro_minimo = dict.fromkeys(unici, giro) if giro else {}
        contesti = self._con_correzioni(unici, contesti)
        self.log("Ritraduco %d testi (%d caratteri)%s" % (len(unici), sum(len(t) for t in unici),
                                                         " con domande nuove" if giro else ""))
        try:
            self._traduci_mancanti(unici, backend, orig, dest, contesti)
        finally:
            riusciti = [t for t in unici if t in self._riusciti]
            nuove = [t for t in riusciti if self.memoria.get(t) != prima.get(t)]
            if not self.prova:
                self._aggiorna_automatiche(riusciti)
            if comune is not None:
                self._aggiorna_comune(comune, orig, dest, nuove, prima)
            falliti, cambiato = set(self.ultimi_falliti), False
            for t in unici:
                voce = self._problemi.get(t)
                if t in self._riusciti:
                    if voce and voce.get("dove") != "gioco":  # those unfit for the game are judged by applica()
                        del self._problemi[t]
                        cambiato = True
                elif t in falliti and not isinstance(self.memoria.get(t), str):
                    # still without a translation: as in a normal translation (new seeds next time); a saved
                    # translation that could not be redone stays as it was, without being noted as suspect
                    # (the re-translation counts as one round, even with the seeds of a later round: see
                    # correzioni.ritraduci)
                    self._segna_problema(t, motivo=self._motivi_falliti.get(t) or "nessuna traduzione valida",
                                         dove="traduzione", tentativi=max((voce or {}).get("tentativi", 0),
                                                                          min(giro, 1)) + getattr(self, "_passi", 1))
                    cambiato = True
            if cambiato:
                self._salva_problemi()
            self._giro_minimo = {}
        return nuove, [t for t in unici if t not in nuove]

    def testi_del_gioco(self):
        """[(game profile entry, position, text to translate, text as it is in the file)] of the game files in the game
        profile, from the originals and without writing anything (for "Fix translations": the texts left untranslated
        and the space they have in the game). A file that cannot be read is skipped, with a line in the log."""
        uscita = []
        for voce in self.profilo.get("file", []):
            try:
                f, unita = self._carica(voce, aggiorna=False)
                grezzi = dict(self._grezzi_json(f.dati, voce)) if unita and isinstance(f, adapter_webjson.FileJSON) \
                    else {}
            except (OSError, ValueError, KeyError, RuntimeError, UnicodeDecodeError) as e:
                self.log("  ! %s: non riesco a leggerlo (%s)" % (voce.get("percorso"), e))
                continue
            for pos, t in unita or []:
                if isinstance(t, str):
                    uscita.append((voce, pos, t, grezzi.get(pos, t)))
        return uscita

    def traduci_testi(self, testi, backend, orig, dest, contesti=None, rifare=None):
        """Translates the texts not yet in the translation memory and redoes the saved translations that are not good
        (see _da_rifare; in 'rifare' {text: reason} others to redo, if machine translations: e.g. the wrong lines of the
        XUnity cache), in groups, saving after each group. Returns how many texts are left without a translation.
        - Short texts already translated for other games come from the shared translation memory (see
          _memoria_comune).
        - Texts rejected by the checks are retried right away in another way (see _recupera); those left
          without a translation go into problemi.json (see percorso_problemi) and next time they are tried with
          new parameters.
        - The game names (profilo["non_tradurre"]) stay as they are, like the codes."""
        rifare_extra = rifare
        unici = list(dict.fromkeys(testi))
        self._prepara_nomi(unici)
        self._dimentica_senza_codici(unici)
        self._backend_attuale = backend
        self.rifatte = 0
        self.ultimi_falliti = []
        self._riusciti = set()
        self._testi_attuali = unici  # for labels that are too long (see _accorcia)
        self._lunghe = {}
        dalla_comune, comune = 0, None
        if not getattr(self, "prova", False) and orig != lingua.AUTO:
            comune = self._memoria_comune(orig, dest)
        else:
            self._leggi_automatiche()
        rifare = self._da_rifare(unici, dest, contesti)
        if self.recupero and rifare_extra:
            insieme = set(unici)
            for t, motivo in rifare_extra.items():
                if t in insieme and t not in rifare and self.automatica(t) and \
                        (self._problemi.get(t) or {}).get("tentativi", 0) < MAX_TENTATIVI:
                    rifare[t] = motivo
        if comune is not None:
            schema = schema_protetti(self._protetti())
            tolti_problemi = False
            for t in unici:
                if (t in self.memoria and t not in rifare) or not _breve(t) or t not in comune:
                    continue
                v = comune[t]
                if v == self.memoria.get(t):
                    continue
                if not voce_comune_valida(t, v, dest, self._origine_comune.get(t), schema):
                    self._comune_saltate.add(t)  # old entry that does not pass the checks: translated again
                    continue
                self.memoria[t] = self._automatiche[t] = v
                if t in rifare:
                    del rifare[t]
                    self.rifatte += 1
                tolti_problemi = self._problemi.pop(t, None) is not None or tolti_problemi
                dalla_comune += 1
            if dalla_comune:
                self._salva_memoria()
            if tolti_problemi:
                self._salva_problemi()
        mancanti = [t for t in unici if t not in self.memoria or t in rifare]
        prima = {t: self.memoria.get(t) for t in rifare}  # the translations to redo, as they were
        # the saved translation is already one round: redoing it with the same questions (seeds 42, 7, 11) would give
        # the same answer, so it starts from the new seeds
        self._giro_minimo = dict.fromkeys(rifare, 1)
        contesti_rifare = contesti or {}
        contesti = self._con_correzioni(mancanti, contesti)
        self.log("Testi del gioco: %d, gia' tradotti: %d%s, da tradurre: %d" % (
            len(unici), len(unici) - len(mancanti),
            " (%d da altri giochi)" % dalla_comune if dalla_comune else "", len(mancanti)))
        self.log(DETTAGLIO + "  (%d caratteri da tradurre)" % sum(len(t) for t in mancanti))
        try:
            return self._traduci_mancanti(mancanti, backend, orig, dest, contesti)
        finally:
            ancora = self._ancora_da_rifare(rifare, prima, dest, contesti_rifare)
            self.rifatte += sum(1 for t in rifare if t in self._riusciti and t not in ancora
                                and self.memoria.get(t) != prima[t])
            if not getattr(self, "prova", False):
                self._aggiorna_automatiche(mancanti)
            if comune is not None:
                self._aggiorna_comune(comune, orig, dest, [t for t in mancanti if t not in ancora], prima)
            self._aggiorna_problemi(mancanti, rifare, ancora)
            self._registra_lunghe()
            self._giro_minimo = {}

    def _ancora_da_rifare(self, rifare, prima, dest, contesti):
        """{text: reasons} of the saved translations redone now that are still not good: equal to the previous one or
        not passing the current checks (problemi_voce: the local translator checks less, for example not the
        language). They count as a failed attempt (problemi.json, 'memoria'), so they are not redone at every run
        beyond MAX_TENTATIVI. Those the game could not show are judged by applica()."""
        ancora = {}
        if not self.recupero or not rifare:
            return ancora
        schema = schema_protetti(self._protetti())
        for t in rifare:
            v = self.memoria.get(t)
            if t not in self._riusciti or not isinstance(v, str) or \
                    (self._problemi.get(t) or {}).get("dove") == "gioco":
                continue
            riferimenti = ((contesti or {}).get(t) or {}).get("riferimenti") or {}
            motivi = problemi_voce(t, v, dest, schema, list(riferimenti.values()))
            if not motivi and v == prima.get(t):
                motivi = ["uguale a quella di prima"]
            if motivi:
                ancora[t] = motivi
        return ancora

    def _percorso_automatiche(self):
        return self.percorso_memoria[:-len(".json")] + ".automatiche.json"

    def _memoria_comune(self, orig, dest):
        """The shared translation memory of the short texts, updated with the fixes made by hand in this game: a short
        text whose translation in the translation memory differs from the machine translation was fixed with
        'Fix translations', and the fix also holds for the other games (except an entry fixed by hand in another
        game: the first fix stays). The machine translation stays in .automatiche.json: so it is always known
        which entries were fixed by hand (and they are not touched)."""
        comune = self._leggi_json(percorso_comune(orig, dest), {}, "file della memoria comune")
        self._automatiche = None  # read again: the translator started by the game writes it too
        self._leggi_automatiche()
        try:
            origine = file_condiviso.leggi_json(percorso_origine_comune(orig, dest))
        except FileNotFoundError:
            origine = {}
        except (OSError, ValueError):  # damaged service file: start over (on save it goes to .illeggibile)
            origine = {}
        self._origine_comune = {k: v for k, v in origine.items() if isinstance(v, dict)} \
            if isinstance(origine, dict) else {}
        self._comune_saltate = set()
        # files also written by the other open programs (e.g. the translator started by a game): see file_condiviso
        self._file_comune = file_condiviso.FileUnito(percorso_comune(orig, dest), comune)
        self._file_origine = file_condiviso.FileUnito(percorso_origine_comune(orig, dest), self._origine_comune)
        cambiate = 0
        for t, v in self.memoria.items():
            if not (isinstance(v, str) and v and _breve(t)):
                continue
            auto = self._automatiche.get(t)
            if auto is None or v == auto:  # machine translation, or from before they were recorded: not a fix
                continue
            if v.strip() == t.strip():  # left as it is (e.g. a name): holds only for this game
                continue
            gia = self._origine_comune.get(t) or {}
            if comune.get(t) == v or (gia.get("mano") and gia.get("gioco") != self.profilo.get("nome")):
                continue
            comune[t] = v
            self._origine_comune[t] = {"mano": True, "gioco": self.profilo.get("nome"), "versione": __version__}
            cambiate += 1
        if cambiate:
            self.log("  Memoria comune: %d correzioni fatte a mano in questo gioco valgono ora per tutti" % cambiate)
            self._file_comune.salva(comune)
            self._salva_origine()
        return comune

    def _salva_origine(self):
        try:
            self._file_origine.salva(self._origine_comune)
        except OSError as e:
            self.log("  ! Provenienza della memoria comune non salvata (%s)" % e)

    def _provenienza(self):
        """Where a translation made now comes from: translator, model, PatchSmith version, game."""
        b = getattr(self, "_backend_attuale", None)
        b = getattr(b, "_backend", b)  # the real translator inside the wrapper of the live translator
        return {"backend": getattr(b, "nome", None), "modello": getattr(b, "modello", None), "versione": __version__,
                "gioco": self.profilo.get("nome")}

    def _aggiorna_automatiche(self, mancanti):
        """The machine translations just written (redone ones too) go into .automatiche.json."""
        automatiche = self._leggi_automatiche()
        for t in mancanti:
            if isinstance(self.memoria.get(t), str):
                automatiche[t] = self.memoria[t]
        try:
            self._file_automatiche.salva(automatiche)
        except OSError as e:
            self.log("  ! Traduzioni automatiche non annotate (%s)" % e)

    def _aggiorna_comune(self, comune, orig, dest, mancanti, prima=None):
        """The short texts translated now go into the shared translation memory, with their origin
        (percorso_origine_comune). Only those: the old entries of the game's translation memory may come from versions
        without today's checks. Not those left equal to the original (names), nor those that do not pass the current
        checks (problemi_voce: an entry with its origin holds for all games without further checks). An entry of the
        shared memory is replaced only if it had been skipped because it did not pass the checks, or if it was this
        game's translation just redone ('prima')."""
        prima = prima or {}
        origine = getattr(self, "_origine_comune", None)
        if origine is None:
            origine = self._origine_comune = {}
        saltate = getattr(self, "_comune_saltate", set())
        provenienza = self._provenienza()
        schema = schema_protetti(self._protetti())
        nuovi = 0
        for t in mancanti:
            v = self.memoria.get(t)
            if not (isinstance(v, str) and v.strip() and _breve(t) and v.strip() != t.strip()) or v == prima.get(t):
                continue
            if t in comune and t not in saltate and comune[t] != prima.get(t):
                continue
            if problemi_voce(t, v, dest, schema):
                continue
            comune[t] = v
            origine[t] = dict(provenienza)
            nuovi += 1
        if not nuovi:
            return
        try:
            self._file_comune.salva(comune)
        except OSError as e:
            self.log("  ! Memoria comune non aggiornata (%s)" % e)
            return
        if hasattr(self, "_file_origine"):
            self._salva_origine()

    def _aggiorna_problemi(self, mancanti, rifare, ancora=None):
        """problemi.json after a translation: the translated texts leave the list (those that could not be written
        to the game stay until applica() manages to write them); those left without a translation enter it,
        with the reason and the passes made (next time the local translator uses new seeds). A saved translation
        to redo that did not succeed stays the previous one, noted; a redone one that is still not good
        ('ancora', see _ancora_da_rifare) stays noted with the passes made."""
        if not self.recupero:
            return
        ancora = ancora or {}
        falliti = set(self.ultimi_falliti)
        passi = getattr(self, "_passi", 1)
        cambiato = False
        for t in mancanti:
            voce = self._problemi.get(t)
            # passes already made: those noted, and at least one for a saved translation to redo (see _giri)
            fatti = max((voce or {}).get("tentativi", 0), getattr(self, "_giro_minimo", {}).get(t, 0)) + passi
            if t in self._riusciti:
                if voce and voce.get("dove") == "gioco":  # the new translation is judged by applica()
                    self._segna_problema(t, tentativi=fatti)
                    cambiato = True
                elif t in ancora:
                    self._segna_problema(t, motivo="%s; rifatta, ma non va ancora (%s)" % (
                        rifare.get(t) or "traduzione salvata sospetta", ", ".join(ancora[t])), dove="memoria",
                        tentativi=fatti, valore=self.memoria.get(t))
                    cambiato = True
                elif voce:
                    del self._problemi[t]
                    cambiato = True
                continue
            if t not in falliti:  # not attempted (translator stopped or paused): stays as it was
                continue
            if t in rifare and voce and voce.get("dove") == "gioco":
                self._segna_problema(t, tentativi=fatti)
            elif t in rifare:
                self._segna_problema(t, motivo="%s; rifarla non e' riuscito, resta quella di prima" % rifare[t],
                                     dove="memoria", tentativi=fatti, valore=self.memoria.get(t))
            else:
                self._segna_problema(t, motivo=self._motivi_falliti.get(t) or "nessuna traduzione valida",
                                     dove="traduzione", tentativi=fatti)
            cambiato = True
        if cambiato:
            self._salva_problemi()

    # --- labels too long for their space ------------------------------------------------------------------------

    def _etichette_lunghe(self, contesti=None, backend=None):
        """{text: space} of the short labels of this translation (etichetta_corta) whose translation is longer than
        their space (spazio_etichetta): only machine translations (now too), not the fixes made by hand,
        and not those already noted with this same translation (problemi.json, 'lunghezza'): asked shorter once,
        or noted with an online translator and now again with an online one. Those noted with an online translator
        and now with the local one (backend.usa_contesto) are asked shorter, once."""
        if not self.recupero or getattr(self, "prova", False):
            return {}
        locale = getattr(backend, "usa_contesto", False)
        uscita = {}
        riusciti = getattr(self, "_riusciti", set())
        for t in getattr(self, "_testi_attuali", ()):
            v = self.memoria.get(t)
            if not isinstance(v, str) or not v.strip() or not etichetta_corta(t) or _uguale(t, v):
                continue
            if t not in riusciti and not self.automatica(t):
                continue  # fixed by hand, or from a version that did not record the machine translations
            problema = self._problemi.get(t) or {}
            if problema.get("dove") == "lunghezza" and problema.get("valore") == v and \
                    (problema.get("chiesta", True) or not locale):
                continue  # already asked shorter once (or already noted, and the translator cannot shorten it)
            if problema.get("dove") in ("gioco", "memoria"):
                continue  # the other checks redo it
            contesto = (contesti or {}).get(t) or {}
            spazio = spazio_etichetta(t, (contesto.get("riferimenti") or {}).values(), contesto.get("spazio"))
            if _peso(v) > spazio + 0.01:
                uscita[t] = spazio
        return uscita

    def _accorcia(self, backend, orig, dest, contesti=None):
        """The short labels too long for their space (e.g. 'Credits' -> 'Riconoscimenti'): with the local translator
        they are asked again once, with the maximum number of characters (context 'larghezza'), and the shortest
        translation that passes the checks stays. Those still too long, and with the other translators (which do not
        know about a maximum space) all of them, go into problemi.json ('lunghezza', with the original text as key):
        they are shown in "Fix translations", list "Too long"."""
        try:
            self._chiedi_piu_corte(backend, orig, dest, contesti)
        except Exception as e:  # noqa: BLE001 - it is an extra: the translation just made stays valid and is written
            self.log("  ! etichette piu' corte non riuscite (%s: %s)" % (type(e).__name__, e))

    def _chiedi_piu_corte(self, backend, orig, dest, contesti=None):
        if self.esito_traduzione is not None:  # translator blocked: next time
            return
        lunghe = self._etichette_lunghe(contesti, backend)
        if not lunghe:
            return
        if not getattr(backend, "usa_contesto", False):
            for t, spazio in lunghe.items():
                self._lunghe[t] = (spazio, self.memoria[t], False)
            self.log("  %d etichette corte sono piu' lunghe dello spazio che hanno di solito (es. %r -> %r): le vedi "
                     "in 'Correggi traduzioni' (elenco 'Troppo lunghi'); il traduttore locale sa farle piu' corte" % (
                         len(lunghe), next(iter(lunghe))[:30], self.memoria[next(iter(lunghe))][:40]))
            return
        if hasattr(backend, "pausa") and backend.pausa():
            return
        self.log("  %d etichette corte sono piu' lunghe dello spazio che hanno di solito: le chiedo piu' corte"
                 % len(lunghe))
        schema = schema_protetti(self._protetti())
        accorciate = []
        for gruppo in _gruppi(list(lunghe), getattr(backend, "max_testi", TESTI_PER_GRUPPO)):
            interruzione.controlla()
            protetti = {t: proteggi(t, self._protetti()) for t in gruppo}
            richieste = []
            for t in gruppo:
                c = (contesti or {}).get(t) or {}
                richieste.append({"chiave": c.get("chiave"), "riferimenti": c.get("riferimenti") or {},
                                  "non_usare": c.get("non_usare"), "larghezza": max(1, int(lunghe[t]))})
            try:
                risposte = backend.traduci_righe([protetti[t][0] for t in gruppo], orig, dest, contesti=richieste)
            except ErroreBackend as e:  # the others are asked next time
                self.log("  ! etichette piu' corte non chieste: %s" % e)
                break
            except Exception as e:  # noqa: BLE001 - it is an extra: the previous translations stay
                self.log("  ! etichette piu' corte non riuscite (%s: %s)" % (type(e).__name__, e))
                break
            for t, risposta in zip(gruppo, risposte):
                vecchia = self.memoria[t]
                nuova = ripristina_segnaposto(risposta, protetti[t][1]) if risposta else None
                nuova = maiuscola_come(t, nuova) if nuova else None
                riferimenti = list((((contesti or {}).get(t) or {}).get("riferimenti") or {}).values())
                if nuova and nuova.strip() and not _uguale(t, nuova) and _peso(nuova) < _peso(vecchia) \
                        and not problemi_voce(t, nuova, dest, schema, riferimenti):
                    self.memoria[t] = nuova
                    accorciate.append(t)
                # still too long: it stays noted (the shorter one too); if it fits, it leaves the list
                ancora = _peso(self.memoria[t]) > lunghe[t] + 0.01
                self._lunghe[t] = (lunghe[t], self.memoria[t], True) if ancora else None
            self._salva_memoria()
        if accorciate:
            self._aggiorna_automatiche(accorciate)
            self.accorciate += len(accorciate)
        restano = sum(1 for v in self._lunghe.values() if v)
        self.log("  Etichette: %d accorciate%s" % (len(accorciate), ", %d ancora piu' lunghe del solito (le vedi in "
                                                    "'Correggi traduzioni', elenco 'Troppo lunghi')" % restano
                                                    if restano else ""))

    def _registra_lunghe(self):
        """The labels still too long go into problemi.json ('lunghezza'), with the space and the translation; those
        that fit now leave the list."""
        lunghe = getattr(self, "_lunghe", None)
        if not lunghe or not self.recupero:
            return
        for t, dati in lunghe.items():
            if dati is None:
                if (self._problemi.get(t) or {}).get("dove") == "lunghezza":
                    del self._problemi[t]
                continue
            spazio, valore, chiesta = dati
            if (self._problemi.get(t) or {}).get("dove") not in (None, "lunghezza"):
                continue  # a more important problem (not translated, unfit for the game)
            # chiesta: already asked shorter to the local translator (if not, it is asked with that one:
            # _etichette_lunghe)
            self._segna_problema(t, motivo="%d caratteri, di solito ce ne stanno circa %d" % (
                round(_peso(valore)), int(spazio)), dove="lunghezza", valore=valore, spazio=int(spazio),
                chiesta=chiesta)
            self.lunghe_annotate += 1
        self._salva_problemi()
        self._lunghe = {}

    def _traduci_mancanti(self, mancanti, backend, orig, dest, contesti=None):
        self._nuclei_ok, self._passi, self._scarti_visti, self._motivi_falliti = {}, 1, {}, {}
        if not mancanti:
            # (only the saved translations: the translator is started if needed)
            if self._etichette_lunghe(contesti, backend):
                try:
                    self._accorcia(backend, orig, dest, contesti)
                finally:
                    if hasattr(backend, "chiudi"):
                        backend.chiudi()
            return 0
        pausa = backend.pausa() if hasattr(backend, "pausa") else None
        if pausa:  # service paused because of a previous block: no requests, so as not to extend it
            self.log("  ! Traduzione non avviata: %s" % pausa)
            self.esito_traduzione, self.motivo_traduzione = "TRADUTTORE_BLOCCATO", pausa
            return len(mancanti)
        gruppi = _gruppi(mancanti, min(TESTI_PER_GRUPPO, getattr(backend, "max_testi", TESTI_PER_GRUPPO)),
                         getattr(backend, "max_caratteri", None))
        if hasattr(backend, "stima"):
            from .limiter import NOMI, descrivi_durata
            richieste = backend.richieste(mancanti) if hasattr(backend, "richieste") else \
                [sum(len(t) + 1 for t in g) for g in gruppi]
            if getattr(backend, "limitato", True):
                self.log("  Richieste previste: circa %d, tempo stimato: %s (PatchSmith va piano apposta con %s, per "
                         "ridurre il rischio di blocchi)" % (len(richieste), descrivi_durata(backend.stima(richieste)),
                                                             NOMI.get(backend.nome, backend.nome)))
            else:
                self.log("  Traduttore locale: %d testi, tempo stimato circa %s. Non avviare il gioco mentre traduce "
                         "(la scheda video e' occupata)." % (len(richieste), descrivi_durata(backend.stima(richieste))))
            if hasattr(backend, "quota") and sum(richieste) > backend.quota():
                self.log("  ! Servono circa %d caratteri, piu' dei %d al giorno di %s: oggi se ne traduce una parte, "
                         "il resto nei giorni successivi (si riparte da dove ci si era fermati)."
                         % (sum(richieste), backend.quota(), NOMI.get(backend.nome, backend.nome)))
        # the rejected proposals of the local translator say why a text did not pass (for problemi.json); in the
        # live translator it prepares them itself (servizio_xunity)
        scarti_propri = self.recupero and getattr(backend, "scarti", False) is None
        if scarti_propri:
            backend.scarti = {}
        try:
            falliti, non_tentati = self._traduci_gruppi(backend, gruppi, mancanti, orig, dest, contesti)
            if falliti and self.recupero and self.esito_traduzione is None:
                falliti = self._recupera(backend, falliti, orig, dest, contesti)
            if falliti and self.recupero:
                self._scarti_visti.update(getattr(backend, "scarti", None) or {})
                self._motivi_falliti = self._motivi(falliti)
            self._accorcia(backend, orig, dest, contesti)  # before closing the local translator
        finally:
            if hasattr(backend, "chiudi"):  # local translator: frees the graphics card and closes the server
                backend.chiudi()
            if scarti_propri:
                backend.scarti = None
        self.ultimi_falliti = falliti
        totale = len(falliti) + non_tentati
        self.log(DETTAGLIO + "Memoria: %d voci (%s)%s" % (
            len(self.memoria), self.percorso_memoria, ", non tradotti: %d" % totale if totale else ""))
        return totale

    def _traduci_gruppi(self, backend, gruppi, mancanti, orig, dest, contesti=None):
        """([texts attempted and left without a valid translation], how many were not attempted because the translator
        stopped or the group was skipped because of an error)."""
        falliti, non_tentati = [], 0
        fatti = 0
        for gruppo in gruppi:
            inizio = fatti
            fatti += len(gruppo)
            self.log(DETTAGLIO + "  testi %d-%d di %d" % (inizio + 1, inizio + len(gruppo), len(mancanti)))
            # in the window the progress every 10% (not every group of texts)
            if inizio and len(gruppi) > 2 and inizio * 10 // len(mancanti) > (inizio - len(gruppo)) * 10 // len(mancanti):
                self.log("  Tradotti %d di %d (%d%%)..." % (inizio, len(mancanti), inizio * 100 // len(mancanti)))
            try:
                interruzione.controlla()
                falliti += self._traduci_gruppo(backend, gruppo, orig, dest, contesti)
            except interruzione.Interrotto:
                self.log("  Fermato: le traduzioni fatte finora sono salvate (%d in memoria) e la prossima volta si "
                         "riparte da li'." % len(self.memoria))
                raise
            except ErroreBackend as e:  # service unreachable or blocked: resumes at the next run
                self.log("  ! traduzione interrotta: %s" % e)
                self.esito_traduzione, self.motivo_traduzione = "TRADUTTORE_BLOCCATO", str(e)
                self.ultimo_errore = e
                non_tentati += len(mancanti) - inizio
                break
            except Exception as e:  # problem with this group: skip it and continue with the next ones
                self.log("  ! gruppo saltato (%s: %s)" % (type(e).__name__, e))
                self.ultimo_errore = e
                non_tentati += len(gruppo)
            finally:
                self._salva_memoria()
        return falliti, non_tentati

    def _giri(self, divisi):
        """{line: passes already made} from the attempts noted in problemi.json for the texts the line comes from (at
        least one for a saved translation to redo: see traduci_testi)."""
        giri = {}
        if not self.recupero:
            return giri
        minimo = getattr(self, "_giro_minimo", {})
        for t, parti in divisi.items():
            g = max((self._problemi.get(t) or {}).get("tentativi", 0), minimo.get(t, 0))
            if g:
                for _, n, _ in parti:
                    if n:
                        giri[n] = max(giri.get(n, 0), g)
        return giri

    def _traduci_gruppo(self, backend, gruppo, orig, dest, contesti=None, passo=0):
        """Translates a group of texts line by line, with the game's codes and names protected, and saves in the
        translation memory those with all lines translated; returns the others. passo: the rounds already made in this
        translation (0 = the first; after that only the missing lines are asked, with new seeds: see _recupera)."""
        divisi = {t: dividi_righe(t) for t in gruppo}
        nuclei_ok = getattr(self, "_nuclei_ok", {})
        nuclei = list(dict.fromkeys(n for parti in divisi.values() for _, n, _ in parti
                                    if _da_tradurre(n) and not (passo and n in nuclei_ok)))
        protetti = {n: proteggi(n, self._protetti()) for n in nuclei}
        # a line made only of the game's codes and names ('Cup Dog Games') stays as it is, without asking for it
        fissi = {n: n for n in nuclei if protetti[n][1] and not _da_tradurre(_TOKEN.sub("", protetti[n][0]))}
        chiesti = [n for n in nuclei if n not in fissi]
        extra = {}
        if contesti and getattr(backend, "usa_contesto", False):
            extra["contesti"] = _contesti_nuclei(divisi, chiesti, contesti)
        giri = {}
        if getattr(backend, "semi_variabili", False) and getattr(self, "recupero", True):
            gia = self._giri(divisi)
            giri = {n: gia.get(n, 0) + passo for n in chiesti}
            if any(giri.values()):
                extra["giri"] = [giri[n] for n in chiesti]
        try:
            tradotti = backend.traduci_righe([protetti[n][0] for n in chiesti], orig, dest, **extra) if chiesti else []
        except BaseException as e:  # block, quota used up or "Stop" halfway: what arrived is saved
            parziali = getattr(e, "parziali", None)
            if parziali and len(parziali) == len(chiesti):
                mappa, _ = self._mappa(chiesti, protetti, parziali)
                mappa.update(fissi)
                self._in_memoria(divisi, mappa)
            raise
        mappa, da_ripiegare = self._mappa(chiesti, protetti, tradotti)
        mappa.update(fissi)
        if da_ripiegare:  # the pieces between the placeholders of the whole group, in a single request
            try:
                ripiego = self._traduci_a_pezzi(backend, da_ripiegare, orig, dest,
                                                giri={n: giri.get(n, 0) for n in da_ripiegare} if giri else None)
            except BaseException:  # Stop or block during the fallback: the group's already good translations stay
                self._in_memoria(divisi, mappa)
                raise
            for n, ripristinato in ripiego.items():
                if ripristinato:
                    mappa[n] = maiuscola_come(n, ripristinato)
        self.ultimo_gruppo = {"divisi": divisi, "protetti": protetti, "mappa": mappa, "ripiegati": da_ripiegare}
        return self._in_memoria(divisi, mappa)

    @staticmethod
    def _mappa(nuclei, protetti, tradotti):
        """({core: valid translation}, [cores with damaged placeholders to re-translate in pieces])."""
        mappa, da_ripiegare = {}, []
        for n, t in zip(nuclei, tradotti):
            ripristinato = ripristina_segnaposto(t, protetti[n][1]) if t else None
            if ripristinato is None and protetti[n][1] and t:
                da_ripiegare.append(n)
            elif ripristinato:
                mappa[n] = maiuscola_come(n, ripristinato)
        return mappa, da_ripiegare

    def _in_memoria(self, divisi, mappa):
        """Saves in the translation memory the texts with all lines translated (also in a group or round before this
        one, in the same translation). Returns the texts left without a translation."""
        if not hasattr(self, "_nuclei_ok"):
            self._nuclei_ok = {}
        self._nuclei_ok.update(mappa)
        riusciti = getattr(self, "_riusciti", None)
        falliti = []
        for testo, parti in divisi.items():
            if all(n in self._nuclei_ok or not _da_tradurre(n) for _, n, _ in parti):
                self.memoria[testo] = ricomponi_righe(parti, self._nuclei_ok)
                if riusciti is not None:
                    riusciti.add(testo)
            else:
                falliti.append(testo)
        return falliti

    def _recupera(self, backend, falliti, orig, dest, contesti=None):
        """New automatic rounds, right away, for the texts rejected by the checks (before, the same question was
        repeated with the same parameters, and the answer was the same at every run):
        1. the missing lines split into sentences and into the pieces between the codes, each translated on its own, in
           a single request; the local translator uses new seeds, derived from the text and the round;
        2. the model stays the chosen one: the texts still rejected go into problemi.json ('Fix translations').
        Returns the texts still without a translation."""
        self.log("  %d testi scartati dai controlli: li riprovo subito in un altro modo (frasi separate, parametri "
                 "diversi)" % len(falliti))
        divisi = {t: dividi_righe(t) for t in falliti}
        giri = self._giri(divisi)
        righe = list(dict.fromkeys(n for t in falliti for _, n, _ in divisi[t]
                                   if _da_tradurre(n) and n not in self._nuclei_ok))
        self._passi = 2
        fermato = False
        try:
            ripiego = self._traduci_a_pezzi(backend, righe, orig, dest, frasi=True,
                                            giri={n: giri.get(n, 0) + 1 for n in righe})
        except ErroreBackend as e:  # translator stopped or blocked: resumes at the next run
            self.log("  ! nuovo giro interrotto: %s" % e)
            self.esito_traduzione, self.motivo_traduzione, self.ultimo_errore = "TRADUTTORE_BLOCCATO", str(e), e
            ripiego, fermato = {}, True
        except Exception as e:  # noqa: BLE001 - the new rounds are an extra: the translations made are written anyway
            self.log("  ! nuovo giro non riuscito (%s: %s)" % (type(e).__name__, e))
            self.ultimo_errore = e
            ripiego = {}
        for n, r in ripiego.items():
            if r:
                self._nuclei_ok[n] = maiuscola_come(n, r)
        rimasti = self._in_memoria(divisi, {})
        self._salva_memoria()
        # no automatic model switch: the one chosen by the user is used (the Quality 12B instead of the Fast one
        # would take minutes and the graphics card without being asked); the remaining ones can be re-translated with
        # the 12B from 'Fix translations', if wanted
        recuperati = len(falliti) - len(rimasti)
        self.log("  Nuovi giri: %d testi tradotti, %d ancora in originale" % (recuperati, len(rimasti)))
        return rimasti

    def _motivi(self, falliti):
        """{text: why it was left without a translation} for problemi.json, from the rejected proposals of the local
        translator (the checks they did not pass)."""
        uscita = {}
        for t in falliti:
            motivi = []
            for _, n, _ in dividi_righe(t):
                if not n or not _da_tradurre(n) or n in self._nuclei_ok:
                    continue
                proposta = self._scarti_visti.get(proteggi(n, self._protetti())[0])
                if proposta:
                    motivi += [m for m in proposta[1] if m not in motivi]
            uscita[t] = ("scartata dai controlli (%s)" % ", ".join(motivi)) if motivi else \
                "il traduttore non ha dato una traduzione valida"
        return uscita

    def _traduci_a_pezzi(self, backend, nuclei, orig, dest, frasi=False, giri=None):
        """Fallback: translates separately the pieces of text between the placeholders (frasi=True: also each sentence
        on its own), of all the cores together with a single call to the translator. giri: {core: round} for the seeds
        of the local translator. Returns {core: translation or None}."""
        preparati, pezzi_unici, giro_pezzo = {}, [], {}
        codice = re.compile(r"\[\[\d+\]\]")
        for nucleo in nuclei:
            testo, segnaposto = proteggi(nucleo, self._protetti())
            pezzi = re.split(r"(\[\[\d+\]\])", testo)
            if frasi:  # also between one sentence and the next (the spaces between sentences stay as they are)
                pezzi = [x for p in pezzi for x in
                         ([p] if codice.fullmatch(p) else re.split(r"((?<=[.!?…。！？])\s+)", p))]
            preparati[nucleo] = (pezzi, segnaposto)
            for p in pezzi:
                if p.strip() and not codice.fullmatch(p) and _da_tradurre(p):
                    pezzi_unici.append(p.strip())
                    giro_pezzo[p.strip()] = max(giro_pezzo.get(p.strip(), 0), (giri or {}).get(nucleo, 0))
        pezzi_unici = list(dict.fromkeys(pezzi_unici))
        extra = {}
        if giri and getattr(backend, "semi_variabili", False) and any(giro_pezzo.values()):
            extra["giri"] = [giro_pezzo[p] for p in pezzi_unici]
        tradotti = dict(zip(pezzi_unici, backend.traduci_righe(pezzi_unici, orig, dest, **extra))) \
            if pezzi_unici else {}
        uscita = {}
        for nucleo, (pezzi, segnaposto) in preparati.items():
            da_tradurre = [p for p in pezzi if p.strip() and not codice.fullmatch(p) and _da_tradurre(p)]
            if any(not tradotti.get(p.strip()) for p in da_tradurre):
                uscita[nucleo] = None
                continue
            ricomposto = "".join(
                (p[:len(p) - len(p.lstrip())] + tradotti[p.strip()] + p[len(p.rstrip()):])
                if p in da_tradurre else p
                for p in pezzi
            )
            uscita[nucleo] = ripristina_segnaposto(ricomposto, segnaposto)
        return uscita

    def applica(self):
        """Writes the translations in the translation memory to the game's files (does not use the network). The account
        stays in self.ultimo_applica: texts translated (ok) and left untranslated (mancanti), the first originals left
        (esempi), translations rejected because the game would not show them well, files written, game open.
        The rejected translations go into problemi.json with the reason (see _registra_applica)."""
        self.ultimo_applica = {"ok": 0, "mancanti": 0, "esempi": [], "scartate": 0, "scritti": 0, "aperto": False}
        self._respinte, self._applicati = {}, set()
        if self.profilo.get("motore") == "gamemaker" and gioco_aperto(self.profilo["cartella_gioco"]):
            self.log("  ! Il gioco e' aperto: chiudilo e riprova (alcuni testi li rilegge solo al riavvio).")
            self.ultimo_applica["aperto"] = True
            return False
        totale_ok = totale_mancanti = 0
        self._lasciati = 0  # labels left like the original on purpose (as the game does in its translations)
        self._esempi, self._scartate, self._scritti = {}, 0, 0
        for voce in self.profilo["file"]:
            try:
                if voce.get("formato", "json") == "web-ui":
                    ok, mancanti = self._applica_webui(voce)
                elif voce.get("formato") == "righe":
                    ok, mancanti = self._applica_righe(voce)
                elif voce.get("formato") == "csv":
                    ok, mancanti = self._applica_csv(voce)
                elif voce.get("formato") == "unity-tabelle":
                    ok, mancanti = self._applica_unity_tabelle(voce)
                else:
                    ok, mancanti = self._applica_json(voce)
            except (OSError, ValueError, UnicodeDecodeError) as e:
                self.log("  ! %s saltato: %s" % (voce["percorso"], e))
                ok, mancanti = 0, 1
            totale_ok += ok
            totale_mancanti += mancanti
        self.log("Applicato: %d testi tradotti%s, %d ancora in originale." % (
            totale_ok, ", %d lasciati in originale come nelle traduzioni del gioco" % self._lasciati
            if self._lasciati else "", totale_mancanti))
        self.ultimo_applica.update(ok=totale_ok, mancanti=totale_mancanti, esempi=list(self._esempi),
                                   scartate=self._scartate, scritti=self._scritti)
        self._registra_applica()
        return totale_mancanti == 0

    def _respingi(self, testo, motivo, voce, non_usare=""):
        """A saved translation that cannot be written to the game (the original stays): noted for problemi.json."""
        if isinstance(testo, str) and testo not in self._respinte:
            self._respinte[testo] = {"motivo": motivo, "file": voce.get("percorso"),
                                     "non_usare": "".join(dict.fromkeys(non_usare or ""))}

    def _registra_applica(self):
        """After applica(): the rejected translations go into problemi.json (dove 'gioco', with the reason and the
        characters not to use); the machine translations will be redone at the next translation with the right
        instruction (see _da_rifare), those fixed by hand stay (they are shown in 'Show remaining'). The texts written
        to the game leave the list, except the suspect saved translations ('memoria'): they can be written, but stay
        noted (with the passes made, which stop them after MAX_TENTATIVI) as long as that translation is in the
        translation memory."""
        if not self.recupero:
            return
        cambiato = False
        for t in self._applicati:
            voce = self._problemi.get(t)
            if voce is None or t in self._respinte:
                continue
            if voce.get("dove") in ("memoria", "lunghezza") and voce.get("valore") == self.memoria.get(t):
                continue  # (suspect and too long: they are written, but stay noted until they change)
            del self._problemi[t]
            cambiato = True
        automatiche = 0
        for t, info in self._respinte.items():
            prima = self._problemi.get(t) or {}
            automatica = self.automatica(t)
            automatiche += automatica
            self._segna_problema(t, motivo=info["motivo"], dove="gioco", file=info.get("file"),
                                 valore=self.memoria.get(t), automatica=automatica,
                                 non_usare="".join(dict.fromkeys((prima.get("non_usare") or "") + info["non_usare"]))
                                 or None,
                                 tentativi=max(prima.get("tentativi", 0), 1))  # redone: new seeds
            cambiato = True
        if automatiche:
            self.log("  %d traduzioni automatiche non si possono scrivere nel gioco: alla prossima traduzione le "
                     "rifaccio con l'istruzione giusta" % automatiche)
        if cambiato:
            self._salva_problemi()

    def _rimasti(self, testi):
        """Notes the first originals left without a translation (to show them to the user)."""
        for t in testi:
            if len(self._esempi) >= 20:
                return
            if isinstance(t, str) and t.strip():
                self._esempi[t] = True

    def _applica_json(self, voce):
        f, unita = self._carica(voce)
        if unita is None:
            self.log("  %s: il file non esiste piu' nel gioco, lo salto" % voce["percorso"])
            return 0, 0
        speciale = voce.get("a_capo") or voce.get("punto_elenco")
        originali = 0  # labels left like the original on purpose (they are not missing translations)
        lasciate = set()  # their positions
        if speciale or self._font_del_gioco():
            grezzi = dict(self._grezzi_json(f.dati, voce))
            sostituzioni, adattate, scartate, larghe = {}, 0, [], []
            limiti = voce.get("limiti") or {}
            non_disegnabili = []
            for p, t in unita:
                tradotto = self.memoria.get(t)
                if not tradotto:
                    continue
                # lowercase game (sprite font): holds for the texts written in lowercase; those with normal
                # capitals (system messages) the game draws with another font
                minuscolo = voce.get("maiuscole") == "minuscolo" and not adapter_gamemaker._parole_miste(
                    adapter_gamemaker._senza_segnaposto(grezzi[p]))
                if minuscolo:  # the game writes in lowercase: capitals would be icons or missing letters
                    tradotto = adapter_gamemaker.in_minuscolo(tradotto, grezzi[p], voce.get("codici", []),
                                                              self.profilo["lingua_destinazione"])
                limite = limiti.get(adapter_gamemaker.chiave_di(p)) if isinstance(p, tuple) and p else None
                if speciale:  # again with the game's line breaks and symbols, and in the space the game gives the text
                    ricomposto = adapter_gamemaker.ricomponi_a_capo(
                        grezzi[p], tradotto, voce.get("a_capo"), voce.get("punto_elenco"),
                        limite[0] if limite else None, limite[1] if limite else None)
                    if ricomposto is None:
                        scartate.append(p)
                        # the game's signs (line break, symbol) written inside the translation: not next time
                        segni = "".join(s for s in (voce.get("a_capo") or "", voce.get("punto_elenco") or "")
                                        if s and not s.isalnum() and s in tradotto)
                        self._respingi(t, "forma diversa dall'originale (paragrafi diversi o il segno di a capo del "
                                          "gioco dentro la frase)", voce, segni)
                        continue
                    tradotto = ricomposto
                if limite and voce.get("a_capo"):
                    lunga = adapter_gamemaker._riga_piu_lunga(tradotto, voce["a_capo"])
                    corto = len(grezzi[p].split()) <= 2 and voce["a_capo"] not in grezzi[p]
                    if lunga > 1.25 * limite[0] and corto and len(limite) > 2 and limite[2] >= 2:
                        originali += 1  # like the official translations that leave it as it is (e.g. 'vsync:')
                        lasciate.add(p)
                        continue
                    if lunga > max(limite[0] + 2, 1.1 * limite[0]):
                        larghe.append((" ".join(t.split())[:30], lunga, limite[0]))
                pronto = self._adatta(tradotto, grezzi[p], ripara=False, minuscolo=minuscolo)
                if pronto is None:
                    non_disegnabili.append(tradotto)
                    mancano = self._caratteri_mancanti(tradotto, grezzi[p], minuscolo)
                    self._respingi(t, "caratteri che i font del gioco non sanno disegnare: %s" % " ".join(mancano),
                                   voce, mancano)
                    continue
                adattate += pronto != tradotto
                sostituzioni[p] = pronto
            if adattate:
                self.log("  %s: %d testi adattati ai caratteri dei font del gioco" % (voce["percorso"], adattate))
            if originali:
                self.log("  %s: %d etichette corte lasciate in originale come nelle traduzioni ufficiali del gioco "
                         "(la traduzione non ci stava)" % (voce["percorso"], originali))
            if non_disegnabili:
                self.log("  ! %s: %d traduzioni hanno caratteri che i font del gioco non sanno disegnare e restano in "
                         "originale, es. %r (quelle scritte da PatchSmith le rifaccio senza quei caratteri; quelle "
                         "corrette a mano vanno sistemate con 'Correggi traduzioni')"
                         % (voce["percorso"], len(non_disegnabili), non_disegnabili[0][:60]))
            if larghe:
                larghe.sort(key=lambda x: x[2] - x[1])
                self.log("  ! %s: %d testi potrebbero uscire dal loro spazio (piu' lunghi di tutte le versioni del "
                         "gioco), es. %s: se serve accorciali con 'Correggi traduzioni' (elenco 'Troppo lunghi')" % (
                             voce["percorso"], len(larghe),
                             ", ".join("'%s' %d/%d caratteri" % x for x in larghe[:4])))
            if scartate:
                self.log("  ! %s: %d traduzioni lasciate in originale perche' non hanno la forma del testo originale "
                         "(paragrafi diversi o un '%s' scritto a mano), es. %s (quelle scritte da PatchSmith le "
                         "rifaccio; quelle corrette a mano vanno sistemate con 'Correggi traduzioni')"
                         % (voce["percorso"], len(scartate), voce.get("a_capo") or "*",
                            ", ".join(str(p[-1]) for p in scartate[:3])))
            self._scartate += len(scartate) + len(non_disegnabili)
        else:
            sostituzioni = {p: self.memoria[t] for p, t in unita if t in self.memoria}
        mancanti = len(unita) - len(sostituzioni) - originali
        nuovi = adapter_webjson.applica(f.dati, sostituzioni)
        problemi = adapter_webjson.verifica_struttura(f.dati, nuovi, set(sostituzioni))
        if problemi:
            self.log("  ! %s NON scritto, controllo struttura fallito: %s" % (voce["percorso"], problemi[:3]))
            self._rimasti(t for _, t in unita)
            return 0, len(unita)
        self._rimasti(t for p, t in unita if p not in sostituzioni and p not in lasciate)
        dati_byte = f.serializza(nuovi)  # raises ValueError if the JSON cannot be represented
        adapter_webjson.rileggi_rigoroso(dati_byte, f.tollerante)
        percorso = self._percorso_gioco(voce["percorso"])
        if voce.get("lingua_nuova"):  # one more language in the game: the original file stays as it is
            nuovo = self._prepara_lingua_nuova(percorso, voce["lingua_nuova"], voce, dati_byte)
            adapter_webjson.scrivi_atomico(nuovo, dati_byte)
            self._scritti += 1
            self._applicati.update(t for p, t in unita if p in sostituzioni)
            self.log("  %s: %d testi tradotti, %d ancora in originale (lingua aggiunta al gioco; %s resta originale)"
                     % (voce["lingua_nuova"], len(sostituzioni), mancanti, voce["percorso"]))
            self._lasciati += originali
            return len(sostituzioni), mancanti
        self._prepara_scrittura(percorso, dati_byte)
        adapter_webjson.scrivi_atomico(percorso, dati_byte)
        self._scritti += 1
        self._applicati.update(t for p, t in unita if p in sostituzioni)
        self.log("  %s: %d testi tradotti, %d ancora in originale" % (voce["percorso"], len(sostituzioni), mancanti))
        self._lasciati += originali
        return len(sostituzioni), mancanti

    def _prepara_lingua_nuova(self, sorgente, relativo, voce, dati_byte):
        """Before writing the added language (e.g. local/italiano.json):
        - the file of the original language goes back to the original if a previous version had translated it in place
          (even if it had then been fixed by hand: the fixed version is set aside, .patchsmith-modificato);
        - a file with that name that does not belong to PatchSmith (e.g. a translation made by others) is not touched;
        - an added language fixed by hand is set aside before rewriting it;
        - the added language is recorded (with the fingerprint of what is written) among the files created in the info
          of the original file's backup, so 'Restore originals' removes it."""
        nuovo = self._percorso_gioco(relativo)
        if os.path.normcase(os.path.dirname(nuovo)) != os.path.normcase(os.path.dirname(sorgente)):
            raise ValueError("la lingua aggiunta deve stare accanto al file originale")
        nome = os.path.basename(nuovo)
        backup = sorgente + SUFFISSO_BACKUP
        info = leggi_info(sorgente) if os.path.exists(backup) else None
        if os.path.exists(nuovo) and (info is None or nome not in info.get("creati", [])):
            raise ValueError("%s c'e' gia' e non l'ha creato PatchSmith (una traduzione di qualcun altro?): non lo "
                             "tocco. Spostalo o rinominalo se vuoi la traduzione di PatchSmith" % relativo)
        if info is None:
            info = crea_backup(sorgente, {"solo_creati": True})
        else:
            attuale = _hash_file(sorgente)
            if attuale != info["hash_originale"] and (attuale in info.get("hash_scritti", [])
                                                      or self._sembra_tradotto(sorgente, voce)):
                if attuale not in info.get("hash_scritti", []):
                    shutil.copyfile(sorgente, sorgente + ".patchsmith-modificato")
                    self.log("  ! %s era tradotto e corretto a mano: la versione corretta resta in %s"
                             % (os.path.basename(sorgente), os.path.basename(sorgente) + ".patchsmith-modificato"))
                with open(backup, "rb") as f:
                    adapter_webjson.scrivi_atomico(sorgente, f.read())
                if _hash_file(sorgente) != info["hash_originale"]:
                    raise RuntimeError("non riesco a rimettere l'originale di %s" % sorgente)
                self.log("  %s: rimesso originale (la traduzione ora e' una lingua a parte)"
                         % os.path.basename(sorgente))
            info["solo_creati"] = True
        atteso = (info.get("hash_creati") or {}).get(nome)
        if os.path.exists(nuovo) and atteso and _hash_file(nuovo) != atteso and not self._sembra_tradotto(nuovo, voce):
            # it is no longer ours (e.g. an official italiano.json arrived with an update): it is not touched
            info["creati"] = [c for c in info.get("creati", []) if c != nome]
            info["hash_creati"] = {k: v for k, v in (info.get("hash_creati") or {}).items() if k != nome}
            salva_info(sorgente, info)
            raise ValueError("%s e' stato sostituito da un file che non e' di PatchSmith (un aggiornamento del gioco?): "
                             "non lo tocco. Se il gioco ha ora la sua traduzione, sceglila nel menu" % relativo)
        if os.path.exists(nuovo) and atteso and _hash_file(nuovo) != atteso:
            shutil.copyfile(nuovo, nuovo + ".patchsmith-modificato")
            self.log("  ! %s era stato corretto a mano: lo riscrivo, la versione corretta resta in %s (per "
                     "correggere una traduzione usa 'Correggi traduzioni')" % (nome, nome + ".patchsmith-modificato"))
        if nome not in info.get("creati", []):
            info["creati"] = info.get("creati", []) + [nome]
        info["hash_creati"] = dict(info.get("hash_creati") or {}, **{nome: _hash_byte(dati_byte)})
        info["patchsmith_versione"] = __version__
        salva_info(sorgente, info)  # before writing, and also to move to the new name of the info file
        return nuovo

    def _font_del_gioco(self):
        """'gamemaker' or 'xna' if the translations must be adapted to the characters of the game's fonts (in GameMaker
        a character the font lacks becomes a small square, in XNA it can make the game close), otherwise None.
        Also holds for the game profiles of the generic search, with the engine detected in profilo["motore_gioco"]."""
        if self.profilo.get("motore") == "gamemaker":
            return "gamemaker"
        return self.profilo.get("motore_gioco") if self.profilo.get("motore_gioco") in ("gamemaker", "xna") else None

    def _adatta(self, tradotto, originale, ripara=True, minuscolo=False):
        """Translation with only the characters the game's fonts can draw (None: impossible). 'ripara':
        also the rules of the codes between slashes of line-based files (e.g. /name/ in Dokimon). 'minuscolo': text of
        a game that writes in lowercase with a sprite font (Forager): the characters of that font apply."""
        font = self._font_del_gioco()
        gamemaker = font == "gamemaker"
        if not hasattr(self, "_ammessi"):
            self._ammessi = None
            if gamemaker:
                self._ammessi = adapter_gamemaker.caratteri_font(self.profilo["cartella_gioco"], self.log,
                                                                 self.profilo.get("lettere_ufficiali"),
                                                                 self.profilo.get("lingua_file"))
                if self._ammessi is None:
                    self.log("  ! Non trovo i font del gioco (data.win): per sicurezza uso solo caratteri ASCII")
                    self._ammessi = set(ASCII)
            elif font == "xna":  # None if the game has no XNB fonts: no limit
                self._ammessi = adapter_xna.caratteri_font(self.profilo["cartella_gioco"], self.log)
        pronto = tradotto
        ammessi = self._ammessi
        if minuscolo and gamemaker:
            if not hasattr(self, "_ammessi_sprite"):
                mappa = adapter_gamemaker.caratteri_font_sprite(self.profilo["cartella_gioco"])
                self._ammessi_sprite = set(mappa) | {" ", "*", "\n"} if mappa else None
            ammessi = self._ammessi_sprite or ammessi
        if ammessi is not None:
            pronto = adatta_caratteri(tradotto, ammessi | set(originale), self.profilo["lingua_destinazione"])
        if pronto is not None and gamemaker and ripara:
            pronto = adapter_gamemaker.ripara(originale, pronto)
        return pronto

    def _caratteri_mancanti(self, tradotto, originale, minuscolo=False):
        """The characters of the translation the game's fonts cannot draw, not even adapted (see _adatta)."""
        return "".join(c for c in dict.fromkeys(tradotto)
                       if not c.isspace() and self._adatta(c, originale, ripara=False, minuscolo=minuscolo) is None)

    MAX_SCARTI_TABELLE = 5  # translations removed one at a time until the bundle read back matches

    def _togli_crc(self, voce):
        """Unity Localization with the Addressables catalog (catalog.json) that checks the fingerprint (CRC) of the
        bundles: before rewriting a bundle, the CRC of this game profile's bundles (only those) is set to 0 in the
        catalog. The catalog has its backup and its info like the other files: 'Restore originals' puts it
        back; if a game update changes it, the new one becomes the original. If the bundle is not found
        in the catalog it stops (ValueError): the bundle is not written."""
        rel = voce["catalogo"]
        catalogo = self._percorso_gioco(rel)
        sorgente = self._originale(catalogo)
        if sorgente is None:
            raise ValueError("non trovo il catalogo dei bundle (%s)" % rel)
        nomi = [os.path.basename(v["percorso"]) for v in self.profilo.get("file", [])
                if v.get("catalogo") == rel and v.get("percorso")]
        with open(sorgente, "rb") as f:
            originale = f.read()
        nuovo = adapter_unity_loc.togli_crc(originale, nomi)
        if nuovo is None:
            raise ValueError("non riesco a togliere il controllo dell'impronta (CRC) di %s dal catalogo del gioco: "
                             "non lo modifico" % ", ".join(nomi))
        with open(catalogo, "rb") as f:
            attuale = f.read()
        if attuale != nuovo:
            self._prepara_scrittura(catalogo, nuovo)
            adapter_webjson.scrivi_atomico(catalogo, nuovo)
            self.log(DETTAGLIO + "  %s: tolto il controllo dell'impronta (CRC) di %s" % (os.path.basename(catalogo),
                                                                            ", ".join(nomi)))

    def _caratteri_tmp(self, voce):
        """The characters the game's TextMeshPro fonts can draw (font_unity), or None if unknown (no
        limit). Read once per translation; the result stays in memory/<gioco>.unity/font.json until the game's files
        change."""
        if not hasattr(self, "_ammessi_tmp"):
            self._ammessi_tmp = None
            dati = font_unity.dati_del_bundle(self.profilo["cartella_gioco"], voce["percorso"])
            if dati and os.path.isdir(dati):
                cache = cartella_dati("memory", "%s.unity" % self.profilo["nome"], "font.json")
                try:
                    self._ammessi_tmp, motivo = font_unity.caratteri_ammessi(dati, self.log, cache=cache)
                except Exception as e:  # noqa: BLE001 - the fonts are an extra check: an odd file must not
                    # stop the writing of the translations (it goes on without limits, as before)
                    self._ammessi_tmp, motivo = None, "%s: %s" % (type(e).__name__, e)
                dest = self.profilo["lingua_destinazione"]
                if self._ammessi_tmp is None:
                    self.log("  Font del gioco: non so quali caratteri hanno (%s), non li limito" % motivo)
                else:
                    assenti = [c for c in lettere_tipiche(dest) if c not in self._ammessi_tmp]
                    self.log(DETTAGLIO + "  Font del gioco: %s%s" % (motivo, "; non hanno %s: le adatto (es. e')" % " ".join(
                        assenti) if assenti else ""))
        return self._ammessi_tmp

    def _applica_unity_tabelle(self, voce):
        """Rewrites the bundle of the text tables with the translations, first checking that, read back, it has the
        same texts in the same order. A translation without letters would disappear from the table read back: the
        original stays. If the check fails because of another translation, that one is removed (up to
        MAX_SCARTI_TABELLE); the removed ones go into problemi.json. The characters no TextMeshPro font of the game can
        draw are adapted (caratteri.adatta_caratteri: è -> e'); a translation that cannot be adapted stays untranslated
        and goes into problemi.json (the next translation redoes it without those characters)."""
        f, unita = self._carica(voce)
        if unita is None:
            self.log("  %s: il file non esiste piu' nel gioco, lo salto" % voce["percorso"])
            return 0, 0
        ammessi = self._caratteri_tmp(voce)
        # characters the game said it cannot draw (Unity log, see checks.py)
        esclusi = set(self.profilo.get("caratteri_mancanti") or "")
        dest = self.profilo["lingua_destinazione"]
        sostituzioni, adattate, scartate_font = {}, [], 0
        for pos, t in unita:
            v = self.memoria.get(t)
            if not isinstance(v, str):
                continue
            if not any(c.isalpha() for c in v):
                self._respingi(t, "traduzione senza lettere: nella tabella dei testi non si puo' scrivere", voce)
                continue
            pronto, mancano = _adatta_tmp(v, t, ammessi, esclusi, dest)
            if pronto is None:
                self._respingi(t, "caratteri che i font del gioco non sanno disegnare: %s" % " ".join(mancano),
                               voce, mancano)
                scartate_font += 1
                continue
            if pronto != v:
                adattate.append((v, pronto))
            sostituzioni[pos] = pronto
        if adattate:
            self.log("  %s: %d testi adattati ai caratteri dei font del gioco (es. %r -> %r)" % (
                os.path.basename(voce["percorso"]), len(adattate), adattate[0][0][:40], adattate[0][1][:40]))
            self._adattate_font = getattr(self, "_adattate_font", 0) + len(adattate)
        if scartate_font:
            self.log("  ! %s: %d traduzioni hanno caratteri che i font del gioco non sanno disegnare e restano in "
                     "originale (quelle scritte da PatchSmith le rifaccio senza quei caratteri; quelle corrette a mano "
                     "vanno sistemate con 'Correggi traduzioni')" % (os.path.basename(voce["percorso"]),
                                                                     scartate_font))
        for _ in range(self.MAX_SCARTI_TABELLE + 1):
            dati_byte = f.serializza(sostituzioni)
            attesi = [sostituzioni.get(pos, t) for pos, t in unita]
            riletti = [t for _, t in adapter_unity_loc.FileTabelle(dati_byte).testi()]
            if attesi == riletti:
                break
            # the first entry that does not match: if it is a translation it is removed and it tries again
            diversa = next((i for i, (a, r) in enumerate(zip(attesi, riletti)) if a != r), min(len(attesi),
                                                                                                len(riletti)))
            pos, testo = unita[diversa] if diversa < len(unita) else (None, None)
            if pos not in sostituzioni:
                dati_byte = None
                break
            del sostituzioni[pos]
            self._respingi(testo, "la tabella dei testi riletta non torna con questa traduzione", voce)
        else:
            dati_byte = None
        if dati_byte is None:
            self.log("  ! %s NON scritto: il controllo dopo la riscrittura non torna" % voce["percorso"])
            self._rimasti(t for _, t in unita)
            return 0, len(unita)
        percorso = self._percorso_gioco(voce["percorso"])
        if voce.get("catalogo"):  # the catalog first: a rewritten bundle with the CRC still there would not load
            self._togli_crc(voce)
        self._prepara_scrittura(percorso, dati_byte)
        adapter_webjson.scrivi_atomico(percorso, dati_byte)
        self._scritti += 1
        self._applicati.update(t for pos, t in unita if pos in sostituzioni)
        self._scartate += len({t for t in self._respinte if self._respinte[t].get("file") == voce["percorso"]})
        self._rimasti(t for pos, t in unita if pos not in sostituzioni)
        mancanti = len(unita) - len(sostituzioni)
        self.log("  %s: %d testi tradotti, %d ancora in originale" % (os.path.basename(voce["percorso"]),
                                                                       len(sostituzioni), mancanti))
        return len(sostituzioni), mancanti

    def _applica_righe(self, voce):
        f, unita = self._carica(voce)
        if unita is None:
            self.log("  %s: il file non esiste piu' nel gioco, lo salto" % voce["percorso"])
            return 0, 0
        sostituzioni, adattate = {}, 0
        for pos, testo in unita:
            tradotto = self.memoria.get(testo)
            if not tradotto:
                continue
            if tradotto.count("\n") != testo.count("\n"):
                self._scartate += 1  # different number of lines (e.g. badly fixed by hand)
                self._respingi(testo, "righe diverse dall'originale (%d invece di %d)" % (
                    tradotto.count("\n") + 1, testo.count("\n") + 1), voce)
                continue
            pronto = self._adatta(tradotto, testo)
            if pronto is None:
                self._scartate += 1
                mancano = self._caratteri_mancanti(tradotto, testo)
                self._respingi(testo, "caratteri che i font del gioco non sanno disegnare: %s" % " ".join(mancano),
                               voce, mancano)
                continue
            adattate += pronto != tradotto
            sostituzioni[pos] = pronto
        # each translation that would change how the game reads the file stays untranslated (on its own)
        dati_byte, scartate = f.serializza_robusto(sostituzioni)
        for pos in scartate:
            del sostituzioni[pos]
        if scartate:
            originali = dict(unita)
            self.log("  ! %s: %d traduzioni lasciate in originale perche' il gioco le leggerebbe male (es. %r)"
                     % (voce["percorso"], len(scartate), originali.get(scartate[0], "")[:60]))
            self._scartate += len(scartate)
            for pos in scartate:
                self._respingi(originali.get(pos), "il gioco leggerebbe male il file con questa traduzione (una riga "
                                                   "vuota, un numero o un segno del file dove va il testo)", voce)
        mancanti = len(unita) - len(sostituzioni)
        self._rimasti(t for pos, t in unita if pos not in sostituzioni)
        percorso = self._percorso_gioco(voce["percorso"])
        self._prepara_scrittura(percorso, dati_byte)
        adapter_webjson.scrivi_atomico(percorso, dati_byte)
        self._scritti += 1
        self._applicati.update(t for pos, t in unita if pos in sostituzioni)
        self.log("  %s: %d testi tradotti, %d ancora in originale%s" % (
            voce["percorso"], len(sostituzioni), mancanti,
            ", %d adattati ai caratteri dei font" % adattate if adattate else ""))
        return len(sostituzioni), mancanti

    def _applica_csv(self, voce):
        """CSV table with one column per language (adapter_csv): only the cells of the original column are rewritten.
        The separator and the quotes in a file that the game splits by hand are replaced (FileCSV.adatta); a
        translation with a different number of lines, or with characters the fonts cannot draw, stays
        untranslated."""
        f, unita = self._carica(voce)
        if unita is None:
            self.log("  %s: il file non esiste piu' nel gioco, lo salto" % voce["percorso"])
            return 0, 0
        sostituzioni, adattate, scartate = {}, 0, []
        for pos, testo in unita:
            tradotto = self.memoria.get(testo)
            if not tradotto:
                continue
            if tradotto.count("\n") != testo.count("\n"):
                scartate.append(testo)
                self._respingi(testo, "righe diverse dall'originale (%d invece di %d)" % (
                    tradotto.count("\n") + 1, testo.count("\n") + 1), voce)
                continue
            pronto = self._adatta(tradotto, testo, ripara=False)
            if pronto is None:
                scartate.append(testo)
                mancano = self._caratteri_mancanti(tradotto, testo)
                self._respingi(testo, "caratteri che i font del gioco non sanno disegnare: %s" % " ".join(mancano),
                               voce, mancano)
                continue
            pronto = f.adatta(pronto)  # e.g. ',' -> ';' in a table that the game splits on commas
            vietati = f.non_scrivibile(pronto)
            if vietati:
                scartate.append(testo)
                self._respingi(testo, "caratteri che la tabella del gioco non permette nei testi: %s" % " ".join(
                    repr(c) if c.isspace() else c for c in vietati), voce, vietati.replace("\n", "").replace("\r", ""))
                continue
            adattate += pronto != tradotto
            sostituzioni[pos] = pronto
        if scartate:
            self.log("  ! %s: %d traduzioni lasciate in originale perche' il gioco non le leggerebbe bene, es. %r "
                     "(quelle scritte da PatchSmith le rifaccio; quelle corrette a mano vanno sistemate con 'Correggi "
                     "traduzioni')" % (voce["percorso"], len(scartate), scartate[0][:60]))
            self._scartate += len(scartate)
        dati_byte = f.serializza(sostituzioni)  # ErroreCSV (ValueError) if the file read back differs: not written
        mancanti = len(unita) - len(sostituzioni)
        self._rimasti(t for pos, t in unita if pos not in sostituzioni)
        percorso = self._percorso_gioco(voce["percorso"])
        self._prepara_scrittura(percorso, dati_byte)
        adapter_webjson.scrivi_atomico(percorso, dati_byte)
        self._scritti += 1
        self._applicati.update(t for pos, t in unita if pos in sostituzioni)
        self.log("  %s: %d testi tradotti, %d ancora in originale%s" % (
            voce["percorso"], len(sostituzioni), mancanti,
            ", %d adattati ai caratteri dei font" % adattate if adattate else ""))
        return len(sostituzioni), mancanti

    def _applica_webui(self, voce):
        index = self._percorso_gioco(voce["percorso"])
        _, unita = self._carica(voce)
        if unita is None:
            self.log("  %s: il file non esiste piu' nel gioco, lo salto" % voce["percorso"])
            return 0, 0
        dizionario = {t: self.memoria[t] for _, t in unita if t in self.memoria}
        mancanti = len(unita) - len(dizionario)
        self._rimasti(t for _, t in unita if t not in dizionario)

        with open(self._originale(index), "rb") as f:
            html, codifica = adapter_webui.decodifica_html(f.read())
        nuovo_html = adapter_webui.inserisci_tag(html)
        if nuovo_html.count(adapter_webui.NOME_SCRIPT) != 1:
            self.log("  ! %s: inserimento del tag non riuscito, interfaccia NON modificata" % voce["percorso"])
            return 0, len(unita)
        html_byte = adapter_webui.codifica_html(nuovo_html, codifica)
        script_byte = adapter_webui.genera_script(dizionario).encode("utf-8")
        script = os.path.join(os.path.dirname(index), adapter_webui.NOME_SCRIPT)

        # 1) backup and record (fingerprint and created file) before any writing
        info = self._prepara_scrittura(index, html_byte)
        if adapter_webui.NOME_SCRIPT not in info.get("creati", []):
            info["creati"] = info.get("creati", []) + [adapter_webui.NOME_SCRIPT]
            salva_info(index, info)
        # 2) script, checked before hooking it to the page
        adapter_webjson.scrivi_atomico(script, script_byte)
        sintassi = adapter_webui.controlla_script(script)
        if sintassi is False:
            self.log("  ! %s: lo script generato non e' valido, interfaccia NON modificata" % voce["percorso"])
            return 0, len(unita)
        # 3) page with the tag
        adapter_webjson.scrivi_atomico(index, html_byte)
        self._scritti += 1
        self._applicati.update(dizionario)
        self.log("  %s: interfaccia, %d testi tradotti, %d ancora in originale%s" % (
            voce["percorso"], len(dizionario), mancanti,
            "" if sintassi else " (sintassi dello script non verificata: Node.js assente)"))
        return len(dizionario), mancanti

    def ripristina(self):
        """Puts back the originals of ALL the changed files in the game's folder."""
        percorsi = trova_info(self.profilo["cartella_gioco"])
        if not percorsi:
            self.log("  Nessun file modificato: il gioco e' gia' originale.")
            return False
        for percorso in percorsi:
            ripristina_file(percorso, self.log)
        return True

    def mostra_stato(self, esempi=8):
        unici = {}
        for voce in self.profilo["file"]:
            _, unita = self._carica(voce, aggiorna=False)
            if unita is None:
                self.log("  %s: non esiste piu' nel gioco" % voce["percorso"])
                continue
            for _, t in unita:
                unici[t] = t in self.memoria
            modificato = os.path.exists(self._percorso_gioco(voce["percorso"]) + SUFFISSO_BACKUP)
            tipi = {"web-ui": "interfaccia", "righe": "testi", "csv": "tabella dei testi"}
            tipo = tipi.get(voce.get("formato"), "dati")
            self.log("  %s (%s): %d testi, %s" % (voce["percorso"], tipo, len(unita),
                                                 "tradotto (backup presente)" if modificato else "originale"))
        tradotti = sum(unici.values())
        self.log("Testi unici: %d, tradotti in memoria: %d, mancanti: %d" % (len(unici), tradotti, len(unici) - tradotti))
        for t in [t for t, ok in unici.items() if ok][:esempi]:
            self.log("  EN: %s\n  %s: %s" % (t[:160].replace("\n", " / "), self.profilo["lingua_destinazione"].upper(),
                                              self.memoria[t][:160].replace("\n", " / ")))
