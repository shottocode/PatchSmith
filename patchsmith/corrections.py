"""Correggi traduzioni: l'elenco delle traduzioni di un gioco con i problemi trovati (per la finestra di
gui_correzioni e per le prove) e quello che si fa da li': salvare le correzioni, ritradurre i testi scelti.

Le righe dell'elenco vengono da:
- la memoria del gioco (memory/<gioco>.<lingua>.json) e i testi con un problema (problemi.json: rimasti in
  originale, non adatti al gioco, sospetti);
- i giochi con file tradotti (web, GameMaker, Unity Localization...): anche i testi dei file del gioco ancora senza
  traduzione, e lo spazio che ogni testo ha nel gioco (la riga piu' lunga delle versioni del gioco, vedi
  adapter_gamemaker.limiti_righe);
- Unity (XUnity): anche la cache di XUnity, cioe' quello che il gioco mostra davvero (righe scritte dal traduttore dal
  vivo, da XUnity con Google, righe rimaste uguali all'originale), e il diario delle frasi tradotte mentre giocavi.

Le correzioni vanno in memoria con Progetto.correggi (con il lucchetto della memoria condivisa; i testi brevi anche
nella memoria comune). Per Unity le righe da cambiare nella cache di XUnity si annotano in
memory/<gioco>.<lingua>.da_scrivere.json: le scrive la prossima "Riapplica" (o "Analizza e traduci") a gioco chiuso,
vedi automatico.unity_completo. L'interfaccia, dopo il salvataggio, fa partire "Riapplica" da sola.
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

# (chiave, etichetta) dei filtri della finestra, nell'ordine del menu
FILTRI = (("tutte", "Tutte"), ("rimasti", "Rimasti in originale"), ("lunghi", "Troppo lunghi"),
          ("gioco", "Non scritte nel gioco"), ("segnaposto", "Con numeri o segnaposto"),
          ("lingua", "In un'altra lingua"), ("sospetti", "Sospetti"), ("mano", "Corrette a mano"))
NOMI_FILTRI = dict(FILTRI)
# i motivi dei controlli di Progetto (problemi_voce) -> filtro e parole per chi gioca
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
# verbi che nei menu e nei pulsanti in italiano si scrivono di solito all'imperativo ("Salva", non "Salvare")
_VERBI_UI = set("save load export import continue quit exit start restart resume retry reset delete apply cancel "
                "confirm select play open close buy sell equip unequip use drop craft upgrade join leave create edit "
                "rename copy paste undo redo accept decline skip pause show hide enable disable back backup "
                "download upload install uninstall connect disconnect invite search sort filter refresh "
                "update share print send submit".split())
_INFINITO = re.compile(r"^[^\W\d_]{3,}(?:are|ere|ire)$", re.I)
_NON_INFINITO = re.compile(r"(?:iere|ore)$", re.I)  # forziere, cavaliere, colore: nomi, non verbi
_NUMERI = re.compile(r"\d+")


def _adesso():
    return datetime.datetime.now().isoformat(timespec="seconds")


class Voce:
    """Una riga dell'elenco. testo: l'originale (la chiave della memoria); memoria: la traduzione salvata (o None);
    cache: per Unity la riga della cache di XUnity (o None); spazio: (testo come sta nel file, segno di a capo,
    simbolo di inizio paragrafo, larghezza, righe, traduzioni ufficiali uguali all'originale) per i testi con lo
    spazio misurato nel gioco; problemi: {filtro: motivo} (motivo vuoto = solo da guardare, es. un testo con dei
    segnaposto); a_mano: corretta a mano (diversa dalla traduzione automatica)."""

    __slots__ = ("testo", "memoria", "cache", "spazio", "problemi", "a_mano")

    def __init__(self, testo):
        self.testo = testo
        self.memoria = self.cache = self.spazio = None
        self.problemi = {}
        self.a_mano = False

    @property
    def traduzione(self):
        """Quella che vale adesso: per Unity la riga della cache (e' quella che il gioco mostra), se c'e'."""
        if self.cache is not None:
            return self.cache
        return self.memoria or ""

    def motivi(self):
        """I problemi in una riga, per la colonna "Problema"."""
        testo = "; ".join(dict.fromkeys(m for k, m in self.problemi.items() if m and k != "mano"))
        return testo or ("corretta a mano" if self.a_mano else "")


class Elenco:
    """Le traduzioni di un gioco per la finestra "Correggi traduzioni" (vedi carica)."""

    def __init__(self, percorso_profilo, profilo, voci, prova, orig, protetti, automatiche_note):
        self.percorso_profilo = percorso_profilo
        self.profilo = profilo
        self.voci = voci
        self.prova = prova
        self.unity = profilo.get("motore") == "unity"
        self.dest = profilo["lingua_destinazione"]
        self.orig = orig
        self.protetti = protetti
        # False: il gioco e' stato tradotto prima che PatchSmith ricordasse le traduzioni automatiche
        # (.automatiche.json): le correzioni a mano non si riconoscono
        self.automatiche_note = automatiche_note
        # traduzioni in memoria senza annotazione in .automatiche.json (fatte da una versione che non le annotava):
        # per PatchSmith sono automatiche, ma potrebbero essere state corrette a mano nel file (vedi carica)
        self.non_annotate = 0

    @property
    def nome(self):
        return self.profilo.get("nome", "")

    def conteggi(self):
        conti = collections.Counter(k for x in self.voci for k in x.problemi)
        conti["tutte"] = len(self.voci)
        return {k: conti.get(k, 0) for k, _ in FILTRI}

    def filtra(self, filtro="tutte", cerca=""):
        """Le righe del filtro che contengono 'cerca' (nell'originale o nella traduzione, senza maiuscole). In un
        filtro prima quelle con un problema scritto (es. tra i segnaposto prima quelli che non tornano)."""
        cerca = (cerca or "").casefold().strip()
        uscita = [x for x in self.voci if filtro in ("tutte", None, "") or filtro in x.problemi]
        if cerca:
            uscita = [x for x in uscita if cerca in x.testo.casefold() or cerca in x.traduzione.casefold()]
        if filtro not in ("tutte", None, ""):
            uscita.sort(key=lambda x: not x.problemi.get(filtro))
        return uscita


def _cache_del_gioco(profilo):
    """{originale: traduzione} della cache di XUnity: quella nel gioco, o quella salvata da "Ripristina originali"."""
    cartella, a = profilo.get("cartella_gioco") or "", profilo["lingua_destinazione"]
    percorso = adapter_unity._percorso_cache(cartella, a)
    if not os.path.exists(percorso):
        percorso = adapter_unity.percorso_cache_salvata(profilo["nome"], a)
    return adapter_unity.voci_cache_xunity(cartella, a, percorso)


def carica(percorso_profilo, prova=False, log=None):
    """L'elenco delle traduzioni del gioco (Elenco), con i problemi di ogni riga. Solo letture (Progetto pulisce i
    caratteri invisibili lasciati da versioni vecchie, come sempre). prova: la memoria del traduttore di prova."""
    log = log or (lambda _m: None)
    p = Progetto(percorso_profilo, log, prova=prova)
    profilo = p.profilo
    dest = profilo["lingua_destinazione"]
    voci = {}

    def voce(t):
        if t not in voci:
            voci[t] = Voce(t)
        return voci[t]

    # 1. i testi dei file del gioco, nel loro ordine, con lo spazio che hanno
    gioco = p.testi_del_gioco() if profilo.get("file") else []
    for v, pos, t, grezzo in gioco:
        x = voce(t)
        limite = (v.get("limiti") or {}).get(adapter_gamemaker.chiave_di(pos)) if isinstance(pos, tuple) and pos \
            and v.get("formato", "json") == "json" else None
        if limite and v.get("a_capo") and (x.spazio is None or limite[0] < x.spazio[3]):
            x.spazio = (grezzo, v.get("a_capo"), v.get("punto_elenco"), limite[0], limite[1],
                        limite[2] if len(limite) > 2 else 0)
    # 2. la memoria; 3. per Unity la cache di XUnity (quello che il gioco mostra)
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
    # traduzioni ufficiali di altre lingue (file di lingua con le traduzioni del gioco): per "riferimento copiato"
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
    """{testo: risposta} delle frasi tradotte mentre giocavi senza tutti i controlli (diario del traduttore dal vivo:
    'rilassata', 'rifiutata', 'rinviata'...), se dopo non sono state tradotte bene."""
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
    """I problemi di una riga ({filtro: motivo} in x.problemi): vedi FILTRI."""
    t, tr = x.testo, x.traduzione
    # corretta a mano: diversa dalla traduzione automatica annotata. Senza annotazione (memorie delle versioni che non
    # le tenevano) e' automatica, come per Progetto._memoria_comune e _da_rifare; le correzioni fatte qui di testi
    # senza annotazione la ricevono (Progetto.correggi)
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
    if dove == "lunghezza" and (valore is None or valore == x.memoria):  # (vedi Progetto._accorcia)
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
    # numeri e segnaposto: nel filtro tutti i testi che ne hanno (da guardare), con il motivo se non tornano
    avvisi_segni = [a for a in avvisi(t, tr, protetti) if a.startswith(("manca", "in più", "numeri"))]
    if avvisi_segni:
        x.problemi["segnaposto"] = "; ".join(avvisi_segni)
    elif proteggi(t, protetti)[1] or _NUMERI.search(t):
        x.problemi.setdefault("segnaposto", "")
    # lunghezza: con lo spazio misurato nel gioco; senza, le etichette corte diventate molto piu' lunghe
    misurata = misura(x, tr)
    if misurata and misurata[0] is not None and troppo_lunga(*misurata):
        x.problemi["lunghi"] = "riga più lunga: %d caratteri, nel gioco ce ne stanno circa %d" % misurata
    elif x.spazio is None and len(t.split()) <= 3 and len(t) <= 25 and len(tr) >= 2 * len(t) and \
            len(tr) - len(t) >= 8:  # (soglie provate sulle memorie vere: circa 2-5% dei testi, i casi evidenti)
        x.problemi.setdefault("lunghi", "%d caratteri invece di %d: potrebbe non starci" % (len(tr), len(t)))
    # etichette di una parola: diventate una frase, o un verbo all'infinito dove si usa l'imperativo
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
    """L'imperativo per un pulsante dei verbi regolari: 'Salvare' -> 'Salva', 'Esportare' -> 'Esporta', 'Chiudere'
    -> 'Chiudi'. None se non e' sicuro: i verbi in -ire ('Inserire' -> 'Inserisci', 'Uscire' -> 'Esci') e gli irregolari
    ('Rifare' -> 'Rifai', 'Ottenere' -> 'Ottieni')."""
    minuscolo = infinito.lower()
    if minuscolo.endswith(("fare", "dare", "stare", "andare", "tenere", "ire")):
        return None
    return infinito[:-3] + ("a" if minuscolo.endswith("are") else "i")


def avvisi(originale, tradotto, protetti=()):
    """Cosa non va in una traduzione scritta a mano, in parole per chi gioca (vuoto: va bene): segnaposto e codici del
    gioco che mancano o sono in piu' (vanno copiati cosi' come sono), numeri diversi, righe diverse."""
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
    """(riga piu' lunga, larghezza) della traduzione come la scrive il gioco, per i testi con lo spazio misurato
    (x.spazio); (None, larghezza) se non ha la forma dell'originale; None senza spazio misurato."""
    if not x.spazio:
        return None
    grezzo, segno, punto, larghezza, righe, _ = x.spazio
    scritto = adapter_gamemaker.ricomponi_a_capo(grezzo, tradotto, segno, punto, larghezza, righe)
    if scritto is None:
        return None, larghezza
    return adapter_gamemaker._riga_piu_lunga(scritto, segno), larghezza


def troppo_lunga(lunga, larghezza):
    """Come in Progetto._applica_json: oltre il 10% (e 2 caratteri) della riga piu' lunga delle versioni del gioco."""
    return lunga > max(larghezza + 2, 1.1 * larghezza)


# --- correzioni da scrivere nella cache di XUnity (Unity) ---------------------------------------------------

def percorso_da_scrivere(profilo):
    """memory/<gioco>.<lingua>.da_scrivere.json: {testo: {prima, valore, quando}}, le righe della cache di XUnity da
    cambiare con una correzione (o una ritraduzione) fatta in "Correggi traduzioni". 'prima': la traduzione che la
    riga aveva (None: nessuna riga, o una riga qualsiasi del traduttore). Le scrive automatico.unity_completo."""
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
    """righe: {testo: (traduzione che la riga della cache ha adesso o None, traduzione nuova)}. Una correzione gia' in
    attesa per lo stesso testo tiene il suo 'prima' (la riga non e' ancora cambiata)."""
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
    """{testo: {'prima', 'valore'}} delle righe della cache di XUnity da cambiare (vedi percorso_da_scrivere)."""
    return {t: d for t, d in _leggi_da_scrivere(percorso_da_scrivere(profilo)).items()
            if isinstance(t, str) and isinstance(d, dict) and isinstance(d.get("valore"), str) and d["valore"].strip()
            and (d.get("prima") is None or isinstance(d.get("prima"), str))}


def scritte(profilo, testi):
    """Toglie dall'elenco delle righe da scrivere quelle gia' scritte (o lasciate apposta)."""
    percorso = percorso_da_scrivere(profilo)
    dati = _leggi_da_scrivere(percorso)
    if not any(t in dati for t in testi):
        return
    base = dict(dati)
    for t in testi:
        dati.pop(t, None)
    file_condiviso.salva_unendo(percorso, dati, base)


# --- salvataggio e ritraduzione -------------------------------------------------------------------------

def salva(percorso_profilo, modifiche, viste=None, prova=False, log=print):
    """Salva le correzioni ({testo: traduzione}) fatte nella finestra: in memoria (Progetto.correggi) e, per Unity, tra
    le righe da scrivere nella cache di XUnity (viste: {testo: la riga della cache che la finestra mostrava, o None}).
    Restituisce (testi cambiati in memoria, righe della cache da scrivere)."""
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
    """(testi, correzioni a mano che restano) per "Ritraduci tutto": le traduzioni automatiche (anche quelle senza
    annotazione delle versioni precedenti: vedi Elenco.automatiche_note e non_annotate) e i testi senza traduzione o
    rimasti in originale. Per Unity le righe della cache che non sono in memoria solo se rimaste in originale o
    provvisorie (le altre potrebbero essere correzioni fatte nel file)."""
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
    """{testo: contesto} (chiave e traduzioni ufficiali) dei file di lingua del gioco, per il traduttore locale.
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
    """Il traduttore per ritradurre: quello scelto (backend.crea_backend) o, con 'modello', il traduttore locale con
    quel modello (Veloce o Qualita'), che deve essere installato."""
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
    """Il giro (i semi del traduttore locale) di un "Ritraduci selezionati": a caso, mai 0, cosi' anche premendo di
    nuovo sulla stessa riga le domande sono nuove (con un giro fisso la seconda volta tornerebbe la risposta della
    prima)."""
    return random.randint(1, 99_999)


def ritraduci(percorso_profilo, testi, traduttore, log=print, prova=False):
    """Ritraduce i testi scelti nella finestra (testi None: tutto, vedi testi_da_ritradurre). I testi scelti si
    rifanno con domande nuove ogni volta (anche quelli corretti a mano: li hai scelti tu; vedi _giro_nuovo); "tutto"
    con le domande normali (serve con l'altro modello: il Qualita' da' altre risposte). Per Unity le righe cambiate
    vanno tra quelle da scrivere nella cache di XUnity: le scelte sempre, con "tutto" solo quelle che hanno ancora la
    traduzione di PatchSmith.
    Restituisce {ritradotte, rimaste, testi, interrotta (il motivo, se il traduttore si e' fermato)}."""
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
