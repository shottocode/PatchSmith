"""Fix translations: the list of a game's translations with the problems found (for the gui_correzioni window and for
the tests) and what is done from there: saving the fixes, re-translating the chosen texts.

The rows of the list come from:
- the game's translation memory (memory/<gioco>.<lingua>.json) and the texts with a problem (problemi.json: left
  untranslated, not suitable for the game, suspicious);
- games with translated files (web, GameMaker, Unity Localization...): also the texts of the game's files still
  without a translation, and the space each text has in the game (the longest line among the game's versions, see
  adapter_gamemaker.limiti_righe);
- Unity (XUnity): also the XUnity cache, that is what the game really shows (lines written by the live translator, by
  XUnity with Google, lines left identical to the original), and the journal of the sentences translated while you
  played.

The fixes go into the translation memory with Progetto.correggi (with the lock of the shared memory; short texts also
into the shared translation memory). For Unity the lines to change in the XUnity cache are noted in
memory/<gioco>.<lingua>.da_scrivere.json: the next "Reapply" (or "Analyze and translate") writes them with the game
closed, see automatico.unity_completo. After saving, the interface starts "Reapply" on its own.
"""
import collections
import datetime
import os
import random
import re

from . import adapter_gamemaker, adapter_unity, shared_file as file_condiviso, language as lingua
from .backend import _numeri, _numeri_in_lettere, _uguale, differenza_numeri
from .project import Progetto, percorso_memoria, problemi_voce, schema_protetti
from .text import proteggi

# (key, label) of the window's filters, in menu order
FILTRI = (("tutte", "Tutte"), ("rimasti", "Rimasti in originale"), ("lunghi", "Troppo lunghi"),
          ("gioco", "Non scritte nel gioco"), ("segnaposto", "Con numeri o segnaposto"),
          ("lingua", "In un'altra lingua"), ("sospetti", "Sospetti"), ("mano", "Corrette a mano"))
NOMI_FILTRI = dict(FILTRI)
# the reasons from the Progetto checks (problemi_voce) -> filter and words for the player
_MOTIVI = {"non tradotta": ("rimasti", "uguale all'originale"),
           "in un'altra lingua": ("lingua", "sembra in un'altra lingua"),
           "riferimento copiato": ("lingua", "uguale alla traduzione ufficiale di un'altra lingua"),
           "lettere di un'altra lingua": ("lingua", "ha lettere di un'altra lingua"),
           "scrittura sbagliata": ("lingua", "alfabeto sbagliato"),
           "numeri diversi": ("segnaposto", "numeri diversi dall'originale"),
           "a capo diversi": ("sospetti", "righe diverse dall'originale"),
           "a capo aggiunto": ("sospetti", "righe diverse dall'originale"),
           "lunghezza anomala": ("sospetti", "lunghezza strana rispetto all'originale"),
           "ripetizioni": ("sospetti", "parole ripetute"),
           "testo di contorno": ("sospetti", "testo in più del traduttore"),
           "rifiuto del modello": ("sospetti", "il traduttore ha risposto con un rifiuto"),
           "nome tradotto": ("sospetti", "un nome del gioco tradotto")}
# verbs that in Italian menus and buttons are usually written in the imperative ("Salva", not "Salvare")
_VERBI_UI = set("save load export import continue quit exit start restart resume retry reset delete apply cancel "
                "confirm select play open close buy sell equip unequip use drop craft upgrade join leave create edit "
                "rename copy paste undo redo accept decline skip pause show hide enable disable back backup "
                "download upload install uninstall connect disconnect invite search sort filter refresh "
                "update share print send submit".split())
_INFINITO = re.compile(r"^[^\W\d_]{3,}(?:are|ere|ire)$", re.I)
_NON_INFINITO = re.compile(r"(?:iere|ore)$", re.I)  # forziere, cavaliere, colore: nouns, not verbs
_NUMERI = re.compile(r"\d+")


def _adesso():
    return datetime.datetime.now().isoformat(timespec="seconds")


class Voce:
    """A row of the list. testo: the original (the translation memory key); memoria: the saved translation (or None);
    cache: for Unity the line of the XUnity cache (or None); spazio: (text as it is in the file, line break mark,
    paragraph start symbol, width, lines, official translations identical to the original) for texts with the space
    measured in the game; problemi: {filtro: motivo} (empty reason = only to look at, e.g. a text with placeholders);
    a_mano: fixed by hand (different from the machine translation)."""

    __slots__ = ("testo", "memoria", "cache", "spazio", "problemi", "a_mano")

    def __init__(self, testo):
        self.testo = testo
        self.memoria = self.cache = self.spazio = None
        self.problemi = {}
        self.a_mano = False

    @property
    def traduzione(self):
        """The one that counts now: for Unity the cache line (it is what the game shows), if there is one."""
        if self.cache is not None:
            return self.cache
        return self.memoria or ""

    def motivi(self):
        """The problems on one line, for the "Problem" column."""
        testo = "; ".join(dict.fromkeys(m for k, m in self.problemi.items() if m and k != "mano"))
        return testo or ("corretta a mano" if self.a_mano else "")


class Elenco:
    """A game's translations for the "Fix translations" window (see carica)."""

    def __init__(self, percorso_profilo, profilo, voci, prova, orig, protetti, automatiche_note):
        self.percorso_profilo = percorso_profilo
        self.profilo = profilo
        self.voci = voci
        self.prova = prova
        self.unity = profilo.get("motore") == "unity"
        self.dest = profilo["lingua_destinazione"]
        self.orig = orig
        self.protetti = protetti
        # False: the game was translated before PatchSmith remembered the machine translations
        # (.automatiche.json): fixes made by hand cannot be recognized
        self.automatiche_note = automatiche_note
        # translations in memory without a note in .automatiche.json (made by a version that did not note them):
        # for PatchSmith they are machine translations, but they might have been fixed by hand in the file (see carica)
        self.non_annotate = 0

    @property
    def nome(self):
        return self.profilo.get("nome", "")

    def conteggi(self):
        conti = collections.Counter(k for x in self.voci for k in x.problemi)
        conti["tutte"] = len(self.voci)
        return {k: conti.get(k, 0) for k, _ in FILTRI}

    def filtra(self, filtro="tutte", cerca=""):
        """The rows of the filter that contain 'cerca' (in the original or in the translation, case-insensitive). In a
        filter, first those with a written problem (e.g. among placeholders, first those that do not match)."""
        cerca = (cerca or "").casefold().strip()
        uscita = [x for x in self.voci if filtro in ("tutte", None, "") or filtro in x.problemi]
        if cerca:
            uscita = [x for x in uscita if cerca in x.testo.casefold() or cerca in x.traduzione.casefold()]
        if filtro not in ("tutte", None, ""):
            uscita.sort(key=lambda x: not x.problemi.get(filtro))
        return uscita


def _cache_del_gioco(profilo):
    """{originale: traduzione} of the XUnity cache: the one in the game, or the one saved by "Restore originals"."""
    cartella, a = profilo.get("cartella_gioco") or "", profilo["lingua_destinazione"]
    percorso = adapter_unity._percorso_cache(cartella, a)
    if not os.path.exists(percorso):
        percorso = adapter_unity.percorso_cache_salvata(profilo["nome"], a)
    return adapter_unity.voci_cache_xunity(cartella, a, percorso)


def carica(percorso_profilo, prova=False, log=None):
    """The list of the game's translations (Elenco), with the problems of each row. Reads only (Progetto cleans up the
    invisible characters left by old versions, as always). prova: the translation memory of the test translator."""
    log = log or (lambda _m: None)
    p = Progetto(percorso_profilo, log, prova=prova)
    profilo = p.profilo
    dest = profilo["lingua_destinazione"]
    voci = {}

    def voce(t):
        if t not in voci:
            voci[t] = Voce(t)
        return voci[t]

    # 1. the texts of the game's files, in their order, with the space they have
    gioco = p.testi_del_gioco() if profilo.get("file") else []
    for v, pos, t, grezzo in gioco:
        x = voce(t)
        limite = (v.get("limiti") or {}).get(adapter_gamemaker.chiave_di(pos)) if isinstance(pos, tuple) and pos \
            and v.get("formato", "json") == "json" else None
        if limite and v.get("a_capo") and (x.spazio is None or limite[0] < x.spazio[3]):
            x.spazio = (grezzo, v.get("a_capo"), v.get("punto_elenco"), limite[0], limite[1],
                        limite[2] if len(limite) > 2 else 0)
    # 2. the translation memory; 3. for Unity the XUnity cache (what the game shows)
    for t, v in p.memoria.items():
        if isinstance(v, str):
            voce(t).memoria = v
    unity = profilo.get("motore") == "unity"
    cache = _cache_del_gioco(profilo) if unity and not prova else {}
    for t, v in cache.items():
        if t not in lingua.ENDONIMI and any(c.isalpha() for c in t):
            voce(t).cache = v
    problemi = p.problemi()
    for t in problemi:
        voce(t)
    testi_gioco = [t for _, _, t, _ in gioco]
    orig = p.lingua_origine(testi_gioco or None)
    protetti = tuple(sorted(set(p.codici) | set(p.nomi_attivi(list(voci)))))
    schema = schema_protetti(protetti)
    # official translations into other languages (language files with the game's translations): for
    # "riferimento copiato"
    riferimenti = {t: list((c.get("riferimenti") or {}).values()) for t, c in contesti_del_gioco(p, gioco).items()}
    automatiche = p._leggi_automatiche()
    provvisorie = _provvisorie(profilo["nome"]) if unity and not prova else {}
    cache_segnalate = adapter_unity.controlla_cache_xunity(cache, orig, dest, p.nomi_attivi(list(cache))) \
        if cache else {}
    for x in voci.values():
        _problemi_di(x, problemi.get(x.testo) or {}, dest, schema, protetti, riferimenti.get(x.testo) or (),
                     automatiche, provvisorie, cache_segnalate)
    elenco = Elenco(os.path.abspath(percorso_profilo), profilo, list(voci.values()), prova, orig, protetti,
                    bool(automatiche))
    if automatiche:
        elenco.non_annotate = sum(1 for x in elenco.voci if isinstance(x.memoria, str) and x.testo not in automatiche)
    return elenco


def _provvisorie(nome):
    """{testo: risposta} of the sentences translated while you played without all the checks (journal of the live
    translator: 'rilassata', 'rifiutata', 'rinviata'...), if they were not translated properly afterwards."""
    from .xunity_service import leggi_diario
    uscita = {}
    for riga in leggi_diario(nome):
        risposta = riga.get("risposta")
        if not isinstance(risposta, str):
            continue
        if riga.get("esito") == "ok":
            uscita.pop(riga["testo"], None)
        else:
            uscita[riga["testo"]] = risposta
    return uscita


def _problemi_di(x, problema, dest, schema, protetti, riferimenti, automatiche, provvisorie, cache_segnalate):
    """The problems of a row ({filtro: motivo} in x.problemi): see FILTRI."""
    t, tr = x.testo, x.traduzione
    # fixed by hand: different from the noted machine translation. Without a note (translation memories of the
    # versions that did not keep them) it is a machine translation, as for Progetto._memoria_comune and _da_rifare;
    # the fixes made here to texts without a note get one (Progetto.correggi)
    if isinstance(x.memoria, str) and t in automatiche and automatiche[t] != x.memoria:
        x.a_mano = True
        x.problemi["mano"] = "corretta a mano"
    dove, valore = problema.get("dove"), problema.get("valore")
    if not tr.strip():
        motivo = problema.get("motivo") if dove == "traduzione" else ""
        x.problemi["rimasti"] = "senza traduzione" + (" (%s)" % motivo if motivo else "")
    if dove == "gioco" and (valore is None or valore == x.memoria):
        x.problemi["gioco"] = problema.get("motivo") or "il gioco non la saprebbe mostrare"
    if dove == "memoria" and (valore is None or valore == x.memoria):
        x.problemi["sospetti"] = "sospetta: %s" % (problema.get("motivo") or "non passa i controlli")
    if dove == "lunghezza" and (valore is None or valore == x.memoria):  # (see Progetto._accorcia)
        x.problemi["lunghi"] = "troppo lunga: %s" % (problema.get("motivo") or "più lunga del suo spazio")
    if not tr.strip():
        return
    for m in problemi_voce(t, tr, dest, schema, riferimenti):
        if m in _MOTIVI:
            filtro, parole = _MOTIVI[m]
            x.problemi.setdefault(filtro, parole)
    if x.cache is not None and t in cache_segnalate:
        for m in cache_segnalate[t][1]:
            if m == "uguale all'originale":
                x.problemi.setdefault("rimasti", "uguale all'originale")
            elif m == "in un'altra lingua":
                x.problemi.setdefault("lingua", "sembra in un'altra lingua")
            elif m == "segnaposto diversi":
                x.problemi.setdefault("segnaposto", "segnaposto diversi dall'originale")
    if x.cache is not None and provvisorie.get(t) == x.cache:
        x.problemi.setdefault("sospetti", "tradotta mentre giocavi senza tutti i controlli")
    # numbers and placeholders: the filter has all the texts that contain them (to look at), with the reason if they
    # do not match
    avvisi_segni = [a for a in avvisi(t, tr, protetti) if a.startswith(("manca", "in più", "numeri"))]
    if avvisi_segni:
        x.problemi["segnaposto"] = "; ".join(avvisi_segni)
    elif proteggi(t, protetti)[1] or _NUMERI.search(t):
        x.problemi.setdefault("segnaposto", "")
    # length: with the space measured in the game; without it, the short labels that became much longer
    misurata = misura(x, tr)
    if misurata and misurata[0] is not None and troppo_lunga(*misurata):
        x.problemi["lunghi"] = "riga più lunga: %d caratteri, nel gioco ce ne stanno circa %d" % misurata
    elif x.spazio is None and len(t.split()) <= 3 and len(t) <= 25 and len(tr) >= 2 * len(t) and \
            len(tr) - len(t) >= 8:  # (thresholds tested on real memories: about 2-5% of texts, the obvious cases)
        x.problemi.setdefault("lunghi", "%d caratteri invece di %d: potrebbe non starci" % (len(tr), len(t)))
    # one-word labels: turned into a sentence, or an infinitive verb where the imperative is used
    parole = re.findall(r"[^\W\d_]+", t)
    if len(parole) == 1 and len(parole[0]) >= 3 and len(re.findall(r"[^\W\d_]+", tr)) >= 3:
        x.problemi.setdefault("sospetti", "una parola tradotta con una frase")
    if dest.split("-")[0] == "it" and 1 <= len(parole) <= 2 and "".join(parole).lower() in _VERBI_UI:
        primo = tr.split()[0] if tr.split() else ""
        if _INFINITO.match(primo) and not _NON_INFINITO.search(primo):
            forma = _imperativo(primo)
            x.problemi.setdefault("sospetti", "verbo all'infinito: nei pulsanti di solito si usa %s" % (
                "\"%s\", non \"%s\"" % (forma, primo) if forma else "l'imperativo (\"%s\" è all'infinito)" % primo))


def _imperativo(infinito):
    """The imperative for a button, for regular verbs: 'Salvare' -> 'Salva', 'Esportare' -> 'Esporta', 'Chiudere'
    -> 'Chiudi'. None if it is not certain: verbs in -ire ('Inserire' -> 'Inserisci', 'Uscire' -> 'Esci') and irregular
    ones ('Rifare' -> 'Rifai', 'Ottenere' -> 'Ottieni')."""
    minuscolo = infinito.lower()
    if minuscolo.endswith(("fare", "dare", "stare", "andare", "tenere", "ire")):
        return None
    return infinito[:-3] + ("a" if minuscolo.endswith("are") else "i")


def avvisi(originale, tradotto, protetti=()):
    """What is wrong in a translation written by hand, in words for the player (empty: it is fine): placeholders and
    game codes that are missing or extra (they must be copied as they are), different numbers, different lines."""
    if not tradotto.strip():
        return ["la traduzione è vuota"]
    uscita = []
    so, segni_o = proteggi(originale, protetti)
    st, segni_t = proteggi(tradotto, protetti)
    mancano = collections.Counter(segni_o) - collections.Counter(segni_t)
    in_piu = collections.Counter(segni_t) - collections.Counter(segni_o)
    if mancano:
        uscita.append("manca %s (va copiato così com'è)" % ", ".join(sorted(mancano)))
    if in_piu:
        uscita.append("in più: %s" % ", ".join(sorted(in_piu)))
    senza_o, senza_t = re.sub(r"\[\[\d+\]\]", " ", so), re.sub(r"\[\[\d+\]\]", " ", st)
    if _numeri(senza_o) != _numeri(senza_t) and not _numeri_in_lettere(senza_o, senza_t, "it"):
        mancano, in_piu = differenza_numeri(senza_o, senza_t)
        dettagli = (["manca %s" % ", ".join(mancano)] if mancano else []) + \
            (["in più %s" % ", ".join(in_piu)] if in_piu else [])
        uscita.append("numeri diversi dall'originale%s" % (" (%s)" % "; ".join(dettagli) if dettagli else ""))
    if originale.count("\n") != tradotto.count("\n"):
        uscita.append("righe: %d invece di %d" % (tradotto.count("\n") + 1, originale.count("\n") + 1))
    return uscita


def misura(x, tradotto):
    """(longest line, width) of the translation as the game writes it, for texts with the measured space (x.spazio);
    (None, larghezza) if it does not have the shape of the original; None without a measured space."""
    if not x.spazio:
        return None
    grezzo, segno, punto, larghezza, righe, _ = x.spazio
    scritto = adapter_gamemaker.ricomponi_a_capo(grezzo, tradotto, segno, punto, larghezza, righe)
    if scritto is None:
        return None, larghezza
    return adapter_gamemaker._riga_piu_lunga(scritto, segno), larghezza


def troppo_lunga(lunga, larghezza):
    """As in Progetto._applica_json: over 10% (and 2 characters) beyond the longest line among the game's versions."""
    return lunga > max(larghezza + 2, 1.1 * larghezza)


# --- fixes to write into the XUnity cache (Unity) ------------------------------------------------------------

def percorso_da_scrivere(profilo):
    """memory/<gioco>.<lingua>.da_scrivere.json: {testo: {prima, valore, quando}}, the lines of the XUnity cache to
    change with a fix (or a re-translation) made in "Fix translations". 'prima': the translation the line had (None:
    no line, or any line from the translator). automatico.unity_completo writes them."""
    return percorso_memoria(profilo)[:-len(".json")] + ".da_scrivere.json"


def _leggi_da_scrivere(percorso):
    try:
        dati = file_condiviso.leggi_json(percorso)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}
    return dati if isinstance(dati, dict) else {}


def segna_da_scrivere(profilo, righe):
    """righe: {testo: (translation the cache line has now or None, new translation)}. A fix already waiting for the
    same text keeps its 'prima' (the line has not changed yet)."""
    if not righe:
        return
    percorso = percorso_da_scrivere(profilo)
    dati = _leggi_da_scrivere(percorso)
    base = dict(dati)
    for t, (prima, valore) in righe.items():
        vecchia = dati.get(t)
        if isinstance(vecchia, dict) and "prima" in vecchia:
            prima = vecchia["prima"]
        dati[t] = {"prima": prima, "valore": valore, "quando": _adesso()}
    file_condiviso.salva_unendo(percorso, dati, base)


def da_scrivere(profilo):
    """{testo: {'prima', 'valore'}} of the XUnity cache lines to change (see percorso_da_scrivere)."""
    return {t: d for t, d in _leggi_da_scrivere(percorso_da_scrivere(profilo)).items()
            if isinstance(t, str) and isinstance(d, dict) and isinstance(d.get("valore"), str) and d["valore"].strip()
            and (d.get("prima") is None or isinstance(d.get("prima"), str))}


def scritte(profilo, testi):
    """Removes from the list of lines to write the ones already written (or left on purpose)."""
    percorso = percorso_da_scrivere(profilo)
    dati = _leggi_da_scrivere(percorso)
    if not any(t in dati for t in testi):
        return
    base = dict(dati)
    for t in testi:
        dati.pop(t, None)
    file_condiviso.salva_unendo(percorso, dati, base)


# --- saving and re-translating --------------------------------------------------------------------------

def salva(percorso_profilo, modifiche, viste=None, prova=False, log=print):
    """Saves the fixes ({testo: traduzione}) made in the window: into the translation memory (Progetto.correggi) and,
    for Unity, among the lines to write into the XUnity cache (viste: {testo: the cache line the window showed, or
    None}). Returns (texts changed in the translation memory, cache lines to write)."""
    p = Progetto(percorso_profilo, log, prova=prova)
    modifiche = {t: v for t, v in modifiche.items() if isinstance(v, str) and v.strip()}
    cambiate = p.correggi(modifiche)
    righe = {}
    if p.profilo.get("motore") == "unity" and not prova:
        viste = viste or {}
        righe = {t: (viste.get(t), v) for t, v in modifiche.items() if viste.get(t) != v}
        segna_da_scrivere(p.profilo, righe)
    if cambiate or righe:
        log("Correggi traduzioni: %d correzioni salvate%s" % (
            len(set(cambiate) | set(righe)), " (le righe della cache di XUnity si scrivono a gioco chiuso)"
            if righe else ""))
    return cambiate, righe


def testi_da_ritradurre(elenco):
    """(texts, fixes made by hand that remain) for "Re-translate everything": the machine translations (also those
    without a note from previous versions: see Elenco.automatiche_note and non_annotate) and the texts without a
    translation or left untranslated. For Unity, the cache lines that are not in the translation memory only if left
    untranslated or provisional (the others might be fixes made in the file)."""
    testi, a_mano = [], 0
    for x in elenco.voci:
        if x.a_mano:
            a_mano += 1
            continue
        provvisoria = "tradotta mentre giocavi" in (x.problemi.get("sospetti") or "")
        if x.memoria is not None or "rimasti" in x.problemi or provvisoria:
            testi.append(x.testo)
    return testi, a_mano


def contesti_del_gioco(p, gioco):
    """{testo: contesto} (key and official translations) of the game's language files, for the local translator.
    gioco: Progetto.testi_del_gioco()."""
    contesti, per_file = {}, collections.defaultdict(list)
    for v, pos, t, _ in gioco:
        per_file[id(v)].append((v, pos, t))
    for gruppo in per_file.values():
        if gruppo[0][0].get("localizzazione"):
            try:
                for t, c in p._contesti(gruppo[0][0], [(pos, t) for _, pos, t in gruppo],
                                        p.profilo["lingua_destinazione"]).items():
                    contesti.setdefault(t, c)
            except (OSError, ValueError, KeyError):
                pass
    return contesti


def crea_traduttore(nome, modello=None, log=print):
    """The translator for re-translating: the chosen one (backend.crea_backend) or, with 'modello', the local
    translator with that model (Fast or Quality), which must be installed."""
    from . import backend, ollama
    if nome == "locale" and modello:
        if ollama.stato() != "pronto":
            raise backend.ErroreBackend(backend.NON_INSTALLATO, "TRADUTTORE_ASSENTE", comando=backend.COMANDO_INSTALLA)
        if not ollama.modello_presente(modello):
            raise backend.ErroreBackend("Il modello %s non e' scaricato: sceglilo accanto al traduttore locale e premi "
                                        "'Installa...'." % ollama.MODELLI[modello]["etichetta"], "TRADUTTORE_ASSENTE",
                                        comando="python -I smith.py local --install %s" % modello)
        return backend.Locale(modello, log=log)
    return backend.crea_backend(nome, log)


def _giro_nuovo():
    """The round (the local translator's seeds) of a "Re-translate selected": random, never 0, so that even pressing
    again on the same line the requests are new (with a fixed round the second time would return the answer of the
    first)."""
    return random.randint(1, 99_999)


def ritraduci(percorso_profilo, testi, traduttore, log=print, prova=False):
    """Re-translates the texts chosen in the window (testi None: everything, see testi_da_ritradurre). The chosen
    texts are redone with new requests every time (also those fixed by hand: you chose them; see _giro_nuovo);
    "everything" with the normal requests (useful with the other model: Quality gives other answers). For Unity the
    changed lines go among those to write into the XUnity cache: the chosen ones always, with "everything" only those
    that still have PatchSmith's translation.
    Returns {ritradotte, rimaste, testi, interrotta (the reason, if the translator stopped)}."""
    scelti = testi is not None
    if not scelti:
        testi, _ = testi_da_ritradurre(carica(percorso_profilo, prova, log))
    p = Progetto(percorso_profilo, log, prova=prova)
    dest = p.profilo["lingua_destinazione"]
    gioco = p.testi_del_gioco() if p.profilo.get("file") else []
    orig = p.lingua_origine([t for _, _, t, _ in gioco] or None)
    automatiche = dict(p._leggi_automatiche())
    prima = {t: p.memoria.get(t) for t in testi}
    nuove = []
    try:
        nuove, _ = p.ritraduci(testi, traduttore, orig, dest, contesti_del_gioco(p, gioco),
                               giro=_giro_nuovo() if scelti else 0)
    finally:
        cambiate = [t for t in prima if isinstance(p.memoria.get(t), str) and p.memoria.get(t) != prima[t]]
        if p.profilo.get("motore") == "unity" and not prova and cambiate:
            cache = _cache_del_gioco(p.profilo)
            righe = {}
            for t in cambiate:
                c = cache.get(t)
                if scelti or c is None or c in (prima[t], automatiche.get(t)) or _uguale(c, t):
                    righe[t] = (c, p.memoria[t])
            segna_da_scrivere(p.profilo, righe)
    unici = list(dict.fromkeys(testi))
    log("Ritradotti: %d testi su %d%s" % (len(nuove), len(unici), " (per gli altri resta la traduzione di prima)"
                                         if len(nuove) < len(unici) else ""))
    return {"ritradotte": len(nuove), "rimaste": len(unici) - len(nuove), "testi": len(unici),
            "interrotta": p.motivo_traduzione if p.esito_traduzione == "TRADUTTORE_BLOCCATO" else ""}
