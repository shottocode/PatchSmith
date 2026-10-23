"""Flusso di lavoro su un profilo: traduci, applica, ripristina, stato.

Sicurezza dei file del gioco:
- prima della prima modifica l'originale viene copiato accanto come <file>.patchsmith-originale
  (copia su file temporaneo, controllo dell'impronta, poi rinomina: mai backup a meta');
- accanto al backup c'e' <file>.patchsmith-info con l'impronta dell'originale, le impronte di tutto
  quello che abbiamo scritto e i file creati. Vive nella cartella del gioco, quindi vale per
  qualsiasi profilo e anche se lo strumento viene spostato;
- l'impronta di cio' che stiamo per scrivere viene registrata PRIMA di scrivere;
- se il file del gioco cambia per mano d'altri (aggiornamento), diventa il nuovo originale e il
  vecchio originale resta conservato con un numero di versione;
- i testi si estraggono sempre dall'originale.
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

# Il programma: codice, componenti scaricati (componenti/), regole (dati/), smith.py. Resta sempre qui.
CARTELLA_PROGETTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# I dati dell'utente (DATI_UTENTE): di norma accanto al programma (None = CARTELLA_PROGETTO, letta al momento).
# La variabile d'ambiente PATCHSMITH_DATI li sposta in un'altra cartella: le prove automatiche la usano per non
# toccare mai i dati veri. Il traduttore per XUnity lo avvia il gioco, che di norma non ha la variabile: usa i dati
# accanto al programma.
RADICE_DATI = os.path.abspath(os.environ["PATCHSMITH_DATI"]) if os.environ.get("PATCHSMITH_DATI") else None
DATI_UTENTE = ("profiles", "memory", "logs", "languages", "configurations", "save-backups", "settings.json",
               "services.json")
# i nomi italiani di prima della 0.1.0: migra_dati li porta a quelli inglesi; finche' non ci riesce (una cartella in
# uso) cartella_dati e cartella_componenti trovano ancora i vecchi
NOMI_ITALIANI = {"profiles": "profili", "memory": "memoria", "logs": "registri", "languages": "lingue",
                 "configurations": "configurazioni", "save-backups": "salvataggi", "settings.json": "impostazioni.json",
                 "services.json": "stato_servizi.json", "components": "componenti"}
DATI_UTENTE_PRIMA = tuple(NOMI_ITALIANI[n] for n in DATI_UTENTE)
SUFFISSO_BACKUP = adapter_webui.SUFFISSO_BACKUP  # ".patchsmith-originale"
# senza ".json" finale: Forager (e altri giochi GameMaker) caricano come lingua ogni local/*.json, anche il nostro
SUFFISSO_INFO = ".patchsmith-info"
SUFFISSO_INFO_VECCHIO = ".patchsmith-info.json"  # fino alla 0.12: si legge ancora e si rinomina alla prima scrittura
SUFFISSI_INFO = (SUFFISSO_INFO, SUFFISSO_INFO_VECCHIO)
TESTI_PER_GRUPPO = 150  # la memoria si salva dopo ogni gruppo
MAX_IMPRONTE = 50
# passaggi del traduttore per lo stesso testo (2 o 3 per ogni "Analizza e traduci"): oltre, una traduzione salvata
# sospetta o non adatta al gioco resta com'e' (non si ritraduce a ogni avvio)
MAX_TENTATIVI = 6


def cartella_dati(*parti):
    """Percorso tra i dati dell'utente (profiles, memory, settings.json...): vedi RADICE_DATI. Una cartella con il nome
    italiano di prima non ancora spostata (migra_dati) si usa finche' quella nuova non c'e'."""
    return _con_ripiego(RADICE_DATI or CARTELLA_PROGETTO, parti)


def cartella_componenti(*parti):
    """I componenti scaricati (Ollama e i modelli, BepInEx, XUnity) e i sorgenti dei plugin: components/ accanto al
    programma (o componenti/, il nome di prima, finche' non e' stata spostata)."""
    return _con_ripiego(CARTELLA_PROGETTO, ("components",) + parti)


def _con_ripiego(base, parti):
    nuovo = os.path.join(base, *parti)
    if parti and parti[0] in NOMI_ITALIANI and not os.path.exists(nuovo):
        vecchio = os.path.join(base, NOMI_ITALIANI[parti[0]], *parti[1:])
        if os.path.exists(vecchio):
            return vecchio
    return nuovo


def migra_dati():
    """Porta i dati con i nomi italiani di prima della 0.1.0 (profili, memoria, registri...) ai nomi inglesi: li
    sposta, senza copiare ne' cancellare nulla. Se la cartella nuova c'e' gia' ci sposta dentro le voci che mancano
    (anche nelle sottocartelle); quelle con lo stesso nome restano nella vecchia. Una cartella in uso resta dov'e':
    cartella_dati la trova ancora. La chiamano la finestra e la riga di comando all'avvio; restituisce i nomi spostati."""
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
            os.rmdir(da)  # (vuota: tutto e' gia' nella nuova)
        return True
    except OSError:  # in uso (es. il traduttore locale acceso): riprova al prossimo avvio
        return False


def maiuscola_come(originale, tradotto):
    """Se l'originale comincia con la maiuscola, anche la traduzione (Google scrive 'francese' per 'French')."""
    if originale[:1].isupper() and tradotto[:1].islower():
        return tradotto[:1].upper() + tradotto[1:]
    return tradotto


def _impronta_testo(testo):
    """Il testo ridotto a lettere e cifre minuscole senza accenti (per riconoscere una traduzione adattata)."""
    semplice = unicodedata.normalize("NFKD", testo).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[\W_]+", "", semplice).casefold()


_PAROLA = re.compile(r"[^\W\d_]{3,}")


def _da_tradurre(riga):
    """Una riga va tradotta se contiene almeno una lettera; non i numeri con simboli e unita' di misura (nessuna
    parola di almeno 3 lettere: "$5k", "+$0,01/s", "x 2", "1920 x 1080", "v1.0"), che il traduttore rovinerebbe
    ("+0,01/secondo", senza il dollaro)."""
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
    if os.path.exists(percorso + SUFFISSO_INFO_VECCHIO):  # nome vecchio: via (il gioco lo caricava come lingua)
        os.remove(percorso + SUFFISSO_INFO_VECCHIO)


def _togli_info(percorso):
    for suffisso in SUFFISSI_INFO:
        if os.path.exists(percorso + suffisso):
            os.remove(percorso + suffisso)


def crea_backup(percorso, extra=None):
    """Backup verificato e atomico dell'originale. Restituisce le info appena salvate ('extra' vi si aggiunge
    prima dell'unico salvataggio). Se qualcosa va storto non resta nessun file temporaneo."""
    backup = percorso + SUFFISSO_BACKUP
    temporaneo = backup + adapter_webjson.SUFFISSO_TMP
    info_salvate = False
    prima = None  # informazioni che c'erano gia' (es. un aggiornamento del gioco): si rimettono se va storto
    for suffisso in SUFFISSI_INFO:
        if os.path.exists(percorso + suffisso):
            with open(percorso + suffisso, "rb") as f:
                prima = (suffisso, f.read())
            break
    try:
        shutil.copyfile(percorso, temporaneo)  # non copy2: la sola lettura del file non deve passare al backup
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
    """Riporta un file del gioco all'originale usando backup e info. Restituisce True se ha agito."""
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
                # file sostituito da un aggiornamento: la modifica del configuratore non c'e' piu' e il backup
                # (di una versione vecchia) non serve: il file resta quello nuovo
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
    """Toglie un file aggiunto da PatchSmith (es. local/italiano.json). Se nel frattempo e' stato cambiato (a mano,
    o e' diventato di qualcun altro) non si perde: va da parte come <nome>.patchsmith-modificato, che il gioco non
    carica."""
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
    """Tutti i file del gioco che hanno un .patchsmith-info (cioe' modificati da noi)."""
    trovati = set()
    for radice, dirs, files in os.walk(cartella):
        dirs[:] = [d for d in dirs if d.lower() not in ("node_modules", ".git")]
        for f in files:
            for suffisso in SUFFISSI_INFO:
                if f.endswith(suffisso):
                    trovati.add(os.path.join(radice, f[: -len(suffisso)]))
    return sorted(trovati)


def _gruppi(testi, massimo_testi, massimo_caratteri=None):
    """Testi divisi in gruppi (una richiesta al traduttore ciascuno, la memoria si salva dopo ogni gruppo)."""
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
    """Il contesto di ogni riga da tradurre: quello del testo da cui viene; delle traduzioni ufficiali si da' la riga
    corrispondente se hanno lo stesso numero di righe, il testo intero se la riga e' una sola, niente se non si sa
    quale pezzo corrisponde (con il testo intero il traduttore aggiungerebbe frasi che non ci sono)."""
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
    """Memoria comune a tutti i giochi per i testi brevi (menu e pulsanti: 'options', 'back', 'press any key')."""
    return cartella_dati("memory", "comune.%s-%s.json" % (orig, dest))


def _breve(testo):
    """Testo da memoria comune: una riga, da 1 a 4 parole, al massimo 40 caratteri, con almeno una lettera. Le frasi
    lunghe quasi mai si ripetono uguali tra giochi diversi."""
    return "\n" not in testo and len(testo) <= 40 and 1 <= len(testo.split()) <= 4 and any(c.isalpha() for c in testo)


MAX_PAROLE_ETICHETTA = 3  # etichette corte (pulsanti, voci dei menu): fino a 3 parole...
MAX_CARATTERI_ETICHETTA = 30  # ...e 30 caratteri


def etichetta_corta(testo):
    """True per un testo da pulsante o da menu: una riga, da 1 a 3 parole, al massimo 30 caratteri, con lettere."""
    t = (testo or "").strip()
    return bool(t) and "\n" not in t and len(t) <= MAX_CARATTERI_ETICHETTA and \
        1 <= len(t.split()) <= MAX_PAROLE_ETICHETTA and any(c.isalpha() for c in t)


def spazio_etichetta(originale, riferimenti=(), spazio=None):
    """Lo spazio di un'etichetta corta, in caratteri 'a vista' (backend._peso: un carattere cinese ne vale due e
    mezzo), arrotondato per eccesso: 1,3 volte l'originale piu' 2, oppure la traduzione ufficiale piu' lunga del gioco
    (di una riga) o la riga piu' lunga delle sue versioni ('spazio', GameMaker), se sono di piu'.
    Se il gioco non dice nulla del suo spazio (nessuna traduzione ufficiale): solo le traduzioni lunghe almeno il
    doppio e 7 caratteri in piu' ('Credits' -> 'Ringraziamenti', 'Slider' -> 'Dispositivo di scorrimento'). Sulle
    memorie vere la sola regola dell'1,3 segnalava il 15-30% delle etichette, anche le normali 'Statistiche' e
    'Combattimento'; cosi' sono il 3-8%, i casi evidenti."""
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
    """I testi con un problema di un gioco (memory/<gioco>.<lingua>.problemi.json): {testo: {motivo, dove,
    tentativi, quando, ...}}. 'dove': 'traduzione' (nessuna traduzione valida, neanche dopo i nuovi tentativi),
    'gioco' (la traduzione salvata non si puo' scrivere nel gioco: forma diversa, caratteri che i font non hanno),
    'memoria' (la traduzione salvata non passa i controlli di adesso e rifarla non e' riuscito), 'lunghezza' (etichetta
    corta piu' lunga del suo spazio anche dopo averla chiesta piu' corta, vedi Progetto._accorcia: si vede in "Correggi
    traduzioni", elenco "Troppo lunghi"). 'tentativi': i passaggi del traduttore gia' fatti per quel testo; decidono i
    semi del traduttore locale (vedi backend.Locale._tentativi): ogni giro, anche in un altro avvio, fa domande nuove."""
    return percorso_memoria(profilo, prova)[:-len(".json")] + ".problemi.json"


def percorso_origine_comune(orig, dest):
    """Da dove viene ogni voce della memoria comune: {testo: {backend, modello, versione, gioco, mano}}. Le voci senza
    (messe dalle versioni precedenti, che ci copiavano tutti i testi brevi) si usano solo se passano i controlli di
    adesso (vedi voce_comune_valida)."""
    return percorso_comune(orig, dest)[:-len(".json")] + ".origine.json"


def schema_protetti(termini):
    """L'espressione che trova i codici e i nomi del gioco in un testo (come testo.proteggi), oppure None."""
    termini = [t for t in termini or () if t]
    if not termini:
        return None
    return re.compile(r"(?<![^\W\d_])(?:%s)(?![^\W\d_])" % "|".join(
        re.escape(t) for t in sorted(termini, key=len, reverse=True)))


def nomi_persi(originale, tradotto, schema):
    """I nomi (o codici) dell'originale che nella traduzione non ci sono piu' (es. 'Cup Dog Games' tradotto)."""
    if schema is None:
        return []
    return sorted(collections.Counter(schema.findall(originale)) - collections.Counter(schema.findall(tradotto)))


def problemi_voce(originale, tradotto, dest, schema=None, riferimenti=()):
    """Perche' una traduzione salvata non va (vuoto = va bene): i controlli del traduttore locale
    (backend.problemi_traduzione: lettere di un'altra lingua, numeri, a capo...), un nome del gioco tradotto, una
    traduzione ufficiale di un'altra lingua copiata."""
    if not isinstance(tradotto, str) or not tradotto.strip():
        return ["vuota"]
    if schema is not None and _uguale(originale, tradotto) and \
            not _da_tradurre(schema.sub("", proteggi(originale)[0]).replace("[[", "").replace("]]", "")):
        return []  # solo nomi e codici del gioco ('Cult Of The Lamb'): restano com'erano apposta
    problemi = problemi_traduzione(originale, tradotto, dest)
    if dest.split("-")[0] in lingua._PAROLE and not _uguale(originale, tradotto) and \
            lingua.lingua_evidente(solo_parole(tradotto), dest):  # es. rimasta in inglese o venuta in spagnolo
        problemi.append("in un'altra lingua")
    if nomi_persi(originale, tradotto, schema):
        problemi.append("nome tradotto")
    # solo per le frasi: una parola uguale nelle due lingue e' normale ('Oro' in spagnolo e in italiano, 'Ferro' in
    # portoghese), e il traduttore locale l'accetta se la ridice senza le traduzioni ufficiali davanti
    if riferimenti and len(re.findall(r"[^\W\d_]+", tradotto)) >= 3 and not _uguale(originale, tradotto) and \
            any(r and _uguale(r, tradotto) for r in riferimenti):
        problemi.append("riferimento copiato")
    return problemi


def voce_comune_valida(testo, tradotto, dest, origine=None, schema=None):
    """True se una voce della memoria comune si puo' usare per un gioco: con la provenienza (messa da questa
    versione, o corretta a mano) basta che non traduca un nome del gioco; senza, deve passare anche i controlli di
    adesso (problemi_voce)."""
    if not isinstance(tradotto, str) or not tradotto.strip():
        return False
    if origine:
        return not nomi_persi(testo, tradotto, schema)
    return not problemi_voce(testo, tradotto, dest, schema)


def _adesso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _adatta_tmp(tradotto, originale, ammessi, esclusi, dest):
    """(traduzione con i soli caratteri che i font TextMeshPro sanno disegnare, '') oppure (None, caratteri che non
    si possono sostituire). ammessi: i caratteri dei font (font_unity; None se non si sa); esclusi: quelli che il gioco
    ha detto di non avere (registro di Unity): sostituiti solo con caratteri ASCII se dei font non si sa nulla."""
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
        # la memoria la scrive anche il traduttore avviato dal gioco: si salvano solo le proprie modifiche
        self._file_memoria = file_condiviso.FileUnito(self.percorso_memoria, self.memoria)
        # per l'esito dell'operazione (vedi outcome.py): perche' la traduzione si e' fermata e cosa ha scritto applica()
        self.esito_traduzione = None  # None, "TRADUTTORE_BLOCCATO" o "NULLA_DA_FARE"
        self.motivo_traduzione = ""
        self.ultimo_applica = None  # {ok, mancanti, esempi, scartate, scritti, aperto}
        # per il traduttore dal vivo (servizio_xunity): l'errore del traduttore che ha fermato l'ultima traduzione e
        # l'ultimo gruppo tradotto ({divisi, protetti, mappa, ripiegati}), per costruire una risposta di ripiego
        self.ultimo_errore = None
        self.ultimo_gruppo = None
        # False nel traduttore dal vivo (servizio_xunity): una frase alla volta mentre si gioca, niente secondi giri,
        # niente rivalidazione ne' problemi.json (le frasi scartate le annota il suo diario)
        self.recupero = True
        self._giro_minimo = {}  # {testo: giro}: le traduzioni salvate da rifare contano gia' un giro (vedi _giri)
        self.ultimi_falliti = []  # i testi dell'ultima traduzione rimasti senza traduzione valida (dopo i nuovi giri)
        self.rifatte = 0  # traduzioni salvate rifatte nell'ultima traduzione (sospette o non adatte al gioco)
        # etichette chieste piu' corte e accorciate, e quelle annotate come troppo lunghe (vedi _accorcia), in tutta
        # l'operazione (traduci() puo' tradurre due volte)
        self.accorciate = self.lunghe_annotate = 0
        self.percorso_problemi = percorso_problemi(self.profilo, prova)
        self._problemi = self._leggi_problemi()
        self._file_problemi = file_condiviso.FileUnito(self.percorso_problemi, self._problemi)
        self._automatiche = None  # letto quando serve (_leggi_automatiche)
        self._nomi = ()  # nomi del gioco da non tradurre, per i testi di questa traduzione (_prepara_nomi)
        self._parole_comuni = None
        self._migra_stato_vecchio()
        # caratteri invisibili inseriti da Google in traduzioni di versioni precedenti: via
        pulite = {k: pulisci(v) for k, v in self.memoria.items() if isinstance(v, str) and pulisci(v) != v}
        if pulite:
            self.memoria.update(pulite)
            self._salva_memoria()

    @staticmethod
    def _leggi_json(percorso, predefinito, descrizione):
        try:  # (riprova se un altro programma lo sta salvando proprio adesso: vedi file_condiviso)
            return file_condiviso.leggi_json(percorso)
        except FileNotFoundError:
            if predefinito is None:
                raise
            return predefinito
        except ValueError as e:
            raise RuntimeError("Il %s non e' un JSON valido: %s (%s). Correggilo e riprova." % (
                descrizione, percorso, e)) from e

    def _migra_stato_vecchio(self):
        """Le versioni 0.1-0.2 tenevano le impronte in profili/<nome>.stato.json: le sposto nel gioco."""
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
        """Salva le traduzioni nuove (o cambiate o tolte) e prende quelle salvate nel frattempo da altri (es. il
        traduttore avviato dal gioco): vedi file_condiviso."""
        self._file_memoria.salva(self.memoria)

    def _leggi_problemi(self):
        """I testi con un problema (vedi percorso_problemi). Il file non lo scrive mai l'utente: se e' rovinato si
        riparte da zero (al salvataggio la copia rovinata resta in .illeggibile)."""
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
        """Copia dei testi con un problema: {testo: {motivo, dove, tentativi, ...}} (vedi percorso_problemi)."""
        return {k: dict(v) for k, v in self._problemi.items()}

    def _segna_problema(self, testo, **campi):
        """Registra (o aggiorna) il problema di un testo. Sempre un dizionario nuovo: FileUnito confronta le voci con
        com'erano, e un dizionario cambiato sul posto non risulterebbe cambiato."""
        voce = dict(self._problemi.get(testo) or {})
        voce.update({k: v for k, v in campi.items() if v is not None})
        voce["quando"] = _adesso()
        self._problemi[testo] = voce

    def _leggi_automatiche(self):
        """Le traduzioni scritte in automatico (<memoria>.automatiche.json): una voce della memoria uguale a quella
        e' automatica, una diversa e' stata corretta a mano (e non si tocca). Nella memoria di prova non si tengono."""
        if self._automatiche is None:
            self._automatiche = {} if self.prova else \
                self._leggi_json(self._percorso_automatiche(), {}, "file delle traduzioni automatiche")
            self._file_automatiche = file_condiviso.FileUnito(self._percorso_automatiche(), self._automatiche)
        return self._automatiche

    def automatica(self, testo):
        """True se la traduzione salvata del testo e' quella scritta in automatico (non corretta a mano)."""
        v = self.memoria.get(testo)
        return isinstance(v, str) and self._leggi_automatiche().get(testo) == v

    def _percorso_gioco(self, rel):
        return os.path.join(self.profilo["cartella_gioco"], *rel.split("/"))

    # --- originali e aggiornamenti ------------------------------------------------------------

    def _sembra_tradotto(self, percorso, voce):
        """True se il file contiene soprattutto nostre traduzioni (cioe' e' il file tradotto, cambiato a mano)."""
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
        # nel file le traduzioni possono essere adattate al gioco (minuscole, caratteri del font, a capo): si
        # confrontano solo lettere e cifre, senza accenti e maiuscole
        tradotti = {_impronta_testo(v) for v in self.memoria.values() if isinstance(v, str)}
        originali = {_impronta_testo(k) for k in self.memoria}
        nostri = sum(1 for t in testi if _impronta_testo(t) in tradotti and _impronta_testo(t) not in originali)
        return len(testi) >= 3 and nostri >= 0.3 * len(testi)

    def _originale(self, percorso, aggiorna=True, voce=None):
        """Percorso del file da cui estrarre i testi originali (None se il file non esiste piu')."""
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
        # Il file tradotto e' stato cambiato a mano: l'originale resta quello del backup (non si perde)
        if self._sembra_tradotto(percorso, voce):
            if aggiorna:
                self.log("  ! %s e' stato modificato a mano dopo la traduzione: lo riscrivo dall'originale "
                         "(per correggere una traduzione usa 'Correggi traduzioni')" % os.path.basename(percorso))
            return backup
        # Il file e' cambiato per mano d'altri: aggiornamento del gioco (o di un'altra mod).
        if not aggiorna:
            return percorso
        n = len(info.get("precedenti", [])) + 1
        vecchio = "%s.%d" % (os.path.basename(backup), n)
        os.replace(backup, os.path.join(os.path.dirname(backup), vecchio))
        tenere = {k: info[k] for k in ("creati", "hash_creati", "solo_creati") if k in info}
        try:
            crea_backup(percorso, dict(tenere, precedenti=info.get("precedenti", []) + [vecchio]))
        except BaseException:
            os.replace(os.path.join(os.path.dirname(backup), vecchio), backup)  # tutto com'era
            raise
        self.log("  ! %s e' cambiato (aggiornamento del gioco?): e' il nuovo originale, il precedente e' in %s"
                 % (os.path.basename(percorso), vecchio))
        return backup

    def _carica(self, voce, aggiorna=True):
        """(oggetto, [(posizione, testo)]) per la voce; (None, None) se il file non esiste piu'."""
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
        """Scrive nel registro un messaggio una volta sola (i file si rileggono piu' volte: traduzione, scrittura)."""
        visti = self.__dict__.setdefault("_detti", set())
        if messaggio not in visti:
            visti.add(messaggio)
            self.log(messaggio)

    @staticmethod
    def _colonna(f, voce):
        """L'indice della colonna da tradurre di una tabella CSV (voce["colonna"]: il suo nome nell'intestazione)."""
        i = f.colonna(voce.get("colonna"))
        if i is None:
            raise adapter_csv.ErroreCSV("la colonna '%s' non c'e' piu' nella tabella" % voce.get("colonna"))
        return i

    @staticmethod
    def _grezzi_json(dati, voce):
        """[(posizione, testo come sta nel file)]: nei file di localizzazione ogni valore con lettere e' un testo."""
        leggibile = adapter_gamemaker.testo_localizzazione if voce.get("localizzazione") else e_testo_leggibile
        return adapter_webjson.estrai(dati, voce["includi"], leggibile)

    @classmethod
    def _estrai_json(cls, dati, voce):
        """[(posizione, testo da tradurre)]: con le convenzioni del gioco (a capo '*', simbolo a inizio paragrafo)
        il testo e' normalizzato (frasi intere, paragrafi su righe separate)."""
        grezzi = cls._grezzi_json(dati, voce)
        if voce.get("a_capo") or voce.get("punto_elenco"):
            return [(p, adapter_gamemaker.normalizza_a_capo(t, voce.get("a_capo"), voce.get("punto_elenco")))
                    for p, t in grezzi]
        return grezzi

    def _prepara_scrittura(self, percorso, dati_byte):
        """Backup (una volta) e registrazione dell'impronta PRIMA di scrivere."""
        info = leggi_info(percorso) if os.path.exists(percorso + SUFFISSO_BACKUP) else None
        if info is None:
            info = crea_backup(percorso)
        impronta = _hash_byte(dati_byte)
        scritti = [h for h in info.get("hash_scritti", []) if h != impronta] + [impronta]
        info["hash_scritti"] = scritti[-MAX_IMPRONTE:]
        info["patchsmith_versione"] = __version__  # con quale versione e' stata scritta (per i controlli futuri)
        salva_info(percorso, info)
        return info

    # --- comandi --------------------------------------------------------------------------------

    @property
    def codici(self):
        """Parole del gioco da non tradurre (icone del font, es. 'EP' e 'H' in Forager), da tutte le voci."""
        return sorted({c for v in self.profilo.get("file", []) for c in v.get("codici", [])})

    def _prepara_nomi(self, testi=()):
        """I nomi del gioco da non tradurre per questa traduzione (profilo["non_tradurre"]: studio e titolo, vedi
        names.py). Un nome di una sola parola che nei testi del gioco c'e' anche in minuscolo ('Control') e' una parola
        normale e si traduce: si guardano i testi da tradurre e quelli gia' in memoria."""
        elenco = self.profilo.get("non_tradurre") or []
        if not elenco:
            self._nomi = ()
            return
        if self._parole_comuni is None:
            self._parole_comuni = nomi_gioco.parole_comuni(self.memoria)
        self._nomi = tuple(nomi_gioco.da_proteggere(elenco, self._parole_comuni | nomi_gioco.parole_comuni(testi)))

    def _protetti(self):
        """Codici e nomi del gioco da proteggere nei testi da tradurre (testo.proteggi)."""
        codici = self.codici if hasattr(self, "profilo") else []
        return tuple(sorted(set(codici) | set(getattr(self, "_nomi", ()))))

    def _da_rifare(self, testi, dest, contesti=None):
        """Le traduzioni salvate da rifare, {testo: motivo}: restano in memoria finche' non ne arriva una migliore
        (se la nuova traduzione non riesce, resta quella di prima). Sono solo quelle scritte in automatico (le
        correzioni a mano non si toccano):
        - che non passano i controlli di adesso (problemi_voce: lettere di un'altra lingua, numeri, a capo diversi, un
          nome del gioco tradotto, una traduzione ufficiale di un'altra lingua copiata). Le versioni precedenti, e
          Google, non avevano questi controlli;
        - che non si sono potute scrivere nel gioco (problemi.json, dove 'gioco': forma diversa dall'originale,
          caratteri che i font del gioco non hanno): si rifanno con l'istruzione giusta (vedi _con_correzioni).
        In piu' un testo che e' solo un nome del gioco ('Cup Dog Games') e che in memoria e' tradotto, anche senza
        traccia della traduzione automatica (memorie delle versioni precedenti). Dopo MAX_TENTATIVI passaggi una
        traduzione si lascia com'e'."""
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
                    (automatiche.get(t) == v or t not in automatiche):  # automatica o di una versione precedente
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
        """I contesti per il traduttore locale con le istruzioni per i testi la cui traduzione non si poteva scrivere
        nel gioco: i caratteri da non usare (quelli che i font del gioco non sanno disegnare, o il segno di a capo
        del gioco scritto dentro la frase). Le etichette annotate come troppo lunghe ('lunghezza', vedi _accorcia), se
        si ritraducono (es. "Ritraduci" nell'elenco "Troppo lunghi"), si chiedono con il loro spazio ('larghezza')."""
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
        """Le traduzioni salvate che hanno perso un codice dell'originale (es. 'max H' tradotto 'massimo', senza
        l'icona del cuore) si rifanno: le versioni precedenti non proteggevano i codici."""
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
        """{testo: contesto} per il traduttore locale: la chiave del testo nel file (senza il numero finale: e' un
        indizio, non un testo da tradurre), le traduzioni ufficiali del gioco (al massimo 3 lingue) e lo spazio
        che hanno (per le etichette corte)."""
        # per l'italiano spagnolo e portoghese insieme sono troppo vicini: il modello li copiava ('saque', 'crânio')
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
                v = adapter_gamemaker.al_percorso(d, pos)  # anche dentro le sezioni ('menu', '_play')
                if isinstance(v, str) and v.strip():
                    rif[c] = adapter_gamemaker.normalizza_a_capo(v, voce.get("a_capo"), voce.get("punto_elenco")) \
                        if voce.get("a_capo") or voce.get("punto_elenco") else v
            limite = limiti.get(chiave)
            # 'spazio': la riga piu' lunga delle versioni del gioco, per le etichette di una riga (vedi _accorcia)
            uscita[testo] = {"chiave": re.sub(r"[_\-.]?\d+$", "", chiave), "riferimenti": rif,
                             "spazio": limite[0] if limite and limite[1] == 1 else None}
        return uscita

    def _contesti_csv(self, voce, unita, dest, preferite):
        """Come _contesti, per una tabella CSV: la chiave e' la cella della colonna delle chiavi, le traduzioni
        ufficiali sono le altre colonne della stessa riga (al massimo 3 lingue)."""
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
                # chiave e traduzioni ufficiali: al traduttore locale come contesto, e per riconoscere una traduzione
                # ufficiale copiata tra quelle salvate (vedi _da_rifare)
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
        vecchi = [t for t in self._problemi if t not in presenti]  # testi che il gioco non ha piu'
        for t in vecchi:
            del self._problemi[t]
        if vecchi:
            self._salva_problemi()
        falliti = self.traduci_testi(testi, backend, orig, dest, contesti)
        interruzione.controlla()  # fermato durante l'ultima richiesta: non si scrive nel gioco
        fatto = self.applica()
        # traduzioni scritte in automatico che il gioco non puo' mostrare (forma diversa, caratteri che i font non
        # hanno): subito un altro giro con l'istruzione giusta, una volta sola
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
        """I nomi del gioco che restano come sono nella traduzione di questi testi (vedi _prepara_nomi)."""
        self._prepara_nomi(testi)
        return self._nomi

    # --- correzioni fatte con "Correggi traduzioni" (vedi corrections.py) ------------------------------

    def lingua_origine(self, testi=None):
        """La lingua del gioco: quella del profilo, oppure riconosciuta dai testi (quelli dati o quelli in memoria)."""
        orig = self.profilo.get("lingua_origine") or lingua.AUTO
        if orig == lingua.AUTO:
            campione = list(testi) if testi is not None else list(self.memoria)
            orig = lingua.rileva(campione[:5000])[0] if campione else "en"
        return orig

    def correggi(self, modifiche):
        """Le traduzioni corrette a mano con "Correggi traduzioni" ({testo: traduzione}):
        - vanno in memoria (salvata con il lucchetto, vedi file_condiviso: le frasi aggiunte nel frattempo dal
          traduttore avviato dal gioco restano);
        - escono da problemi.json se erano rimaste in originale o sospette (adesso c'e' la tua); quelle che il gioco non
          sapeva mostrare restano finche' "Riapplica" non riesce a scriverle;
        - i testi brevi passano nella memoria comune come correzioni a mano (vedi _memoria_comune), anche quelli
          che non avevano una traduzione automatica (es. rimasti in originale e tradotti da te).
        In .automatiche.json resta la traduzione automatica: la correzione si riconosce sempre e non si tocca piu'. Un
        testo che li' non c'era (rimasto in originale, o tradotto da una versione che non annotava le traduzioni
        automatiche: per PatchSmith una voce senza annotazione e' automatica) ci entra con la traduzione di prima
        (vuota se non ce n'era una), cosi' anche lui risulta corretto a mano.
        Restituisce i testi la cui traduzione in memoria e' cambiata."""
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
        comune = self._memoria_comune(orig, dest)  # (anche le correzioni a mano di prima di questo gioco)
        nome, aggiunte = self.profilo.get("nome"), 0
        for t in cambiate:
            v = self.memoria[t]
            if not _breve(t) or v.strip() == t.strip() or comune.get(t) == v:
                continue
            gia = self._origine_comune.get(t) or {}
            if gia.get("mano") and gia.get("gioco") != nome:  # corretta a mano in un altro gioco: resta quella
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
        """Ritraduce adesso i testi dati, chiesti con "Correggi traduzioni": anche quelli gia' in memoria e quelli
        corretti a mano (la scelta e' dell'utente). Se la traduzione nuova non riesce resta quella di prima.
        giro: da quale giro partono i semi del traduttore locale (1 o piu' = semi nuovi e temperature un po' piu' alte:
        la stessa domanda darebbe la stessa risposta; 0 = le domande del primo giro, per esempio con l'altro modello).
        Le traduzioni nuove diventano automatiche (.automatiche.json) e i testi brevi vanno nella memoria comune come
        in una traduzione normale. Restituisce (testi con una traduzione nuova, testi rimasti com'erano)."""
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
                    if voce and voce.get("dove") != "gioco":  # quelle non adatte al gioco le giudica applica()
                        del self._problemi[t]
                        cambiato = True
                elif t in falliti and not isinstance(self.memoria.get(t), str):
                    # ancora senza traduzione: come in una traduzione normale (la prossima volta semi nuovi); una
                    # traduzione salvata che non si e' riusciti a rifare resta com'era, senza annotarla come sospetta
                    # (la ritraduzione conta come un giro, anche con i semi di un giro piu' avanti: vedi
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
        """[(voce del profilo, posizione, testo da tradurre, testo come sta nel file)] dei file del gioco nel profilo,
        dagli originali e senza scrivere nulla (per "Correggi traduzioni": i testi rimasti in originale e lo spazio
        che hanno nel gioco). Un file che non si legge si salta, con una riga nel registro."""
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
        """Traduce i testi non ancora in memoria e rifa' le traduzioni salvate che non vanno (vedi _da_rifare; in
        'rifare' {testo: motivo} altre da rifare, se scritte in automatico: es. le righe sbagliate della cache di
        XUnity), a gruppi, salvando dopo ogni gruppo. Restituisce quanti testi restano senza traduzione.
        - I testi brevi gia' tradotti per altri giochi vengono dalla memoria comune (vedi _memoria_comune).
        - I testi scartati dai controlli si riprovano subito in un altro modo (vedi _recupera); quelli che restano
          senza traduzione vanno in problemi.json (vedi percorso_problemi) e la prossima volta si provano con
          parametri nuovi.
        - I nomi del gioco (profilo["non_tradurre"]) restano come sono, come i codici."""
        rifare_extra = rifare
        unici = list(dict.fromkeys(testi))
        self._prepara_nomi(unici)
        self._dimentica_senza_codici(unici)
        self._backend_attuale = backend
        self.rifatte = 0
        self.ultimi_falliti = []
        self._riusciti = set()
        self._testi_attuali = unici  # per le etichette troppo lunghe (vedi _accorcia)
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
                    self._comune_saltate.add(t)  # voce vecchia che non passa i controlli: si traduce di nuovo
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
        prima = {t: self.memoria.get(t) for t in rifare}  # le traduzioni da rifare, com'erano
        # la traduzione salvata e' gia' un giro: rifarla con le stesse domande (semi 42, 7, 11) darebbe la stessa
        # risposta, quindi si parte dai semi nuovi
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
        """{testo: motivi} delle traduzioni salvate rifatte adesso che non vanno ancora: uguali a quella di prima o
        che non passano i controlli di adesso (problemi_voce: il traduttore locale controlla meno, per esempio non la
        lingua). Contano come un tentativo non riuscito (problemi.json, 'memoria'), cosi' non si rifanno a ogni avvio
        oltre MAX_TENTATIVI. Quelle che il gioco non sapeva mostrare le giudica applica()."""
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
        """La memoria comune dei testi brevi, aggiornata con le correzioni fatte a mano in questo gioco: un testo
        breve la cui traduzione in memoria e' diversa da quella scritta in automatico e' stato corretto con
        'Correggi traduzioni', e la correzione vale anche per gli altri giochi (tranne una voce corretta a mano in un
        altro gioco: la prima correzione resta). In .automatiche.json resta la traduzione automatica: cosi' si sa
        sempre quali voci sono state corrette a mano (e non si toccano)."""
        comune = self._leggi_json(percorso_comune(orig, dest), {}, "file della memoria comune")
        self._automatiche = None  # riletto: lo scrive anche il traduttore avviato dal gioco
        self._leggi_automatiche()
        try:
            origine = file_condiviso.leggi_json(percorso_origine_comune(orig, dest))
        except FileNotFoundError:
            origine = {}
        except (OSError, ValueError):  # file di servizio rovinato: si riparte (al salvataggio va in .illeggibile)
            origine = {}
        self._origine_comune = {k: v for k, v in origine.items() if isinstance(v, dict)} \
            if isinstance(origine, dict) else {}
        self._comune_saltate = set()
        # file scritti anche dagli altri programmi aperti (es. il traduttore avviato da un gioco): vedi file_condiviso
        self._file_comune = file_condiviso.FileUnito(percorso_comune(orig, dest), comune)
        self._file_origine = file_condiviso.FileUnito(percorso_origine_comune(orig, dest), self._origine_comune)
        cambiate = 0
        for t, v in self.memoria.items():
            if not (isinstance(v, str) and v and _breve(t)):
                continue
            auto = self._automatiche.get(t)
            if auto is None or v == auto:  # automatica, o di prima che si tenessero le automatiche: non e' corretta
                continue
            if v.strip() == t.strip():  # lasciata com'e' (es. un nome): vale solo per questo gioco
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
        """Da dove viene una traduzione fatta adesso: traduttore, modello, versione di PatchSmith, gioco."""
        b = getattr(self, "_backend_attuale", None)
        b = getattr(b, "_backend", b)  # il traduttore vero dentro l'involucro del traduttore dal vivo
        return {"backend": getattr(b, "nome", None), "modello": getattr(b, "modello", None), "versione": __version__,
                "gioco": self.profilo.get("nome")}

    def _aggiorna_automatiche(self, mancanti):
        """Le traduzioni appena scritte in automatico (anche quelle rifatte) vanno in .automatiche.json."""
        automatiche = self._leggi_automatiche()
        for t in mancanti:
            if isinstance(self.memoria.get(t), str):
                automatiche[t] = self.memoria[t]
        try:
            self._file_automatiche.salva(automatiche)
        except OSError as e:
            self.log("  ! Traduzioni automatiche non annotate (%s)" % e)

    def _aggiorna_comune(self, comune, orig, dest, mancanti, prima=None):
        """I testi brevi tradotti adesso vanno nella memoria comune, con la loro provenienza (percorso_origine_comune).
        Solo quelli: le voci vecchie della memoria del gioco potevano venire da versioni senza i controlli di oggi.
        Quelli rimasti uguali all'originale (nomi) no, e nemmeno quelli che non passano i controlli di adesso
        (problemi_voce: una voce con la provenienza vale per tutti i giochi senza altri controlli). Una voce della
        comune si sostituisce solo se era stata saltata perche' non passava i controlli, o se era la traduzione di
        questo gioco appena rifatta ('prima')."""
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
        """problemi.json dopo una traduzione: i testi tradotti escono dall'elenco (quelli che non si potevano scrivere
        nel gioco ci restano finche' applica() non riesce a scriverli); quelli rimasti senza traduzione ci entrano,
        con il motivo e i passaggi fatti (la prossima volta il traduttore locale usa semi nuovi). Una traduzione
        salvata da rifare che non e' riuscita resta quella di prima, annotata; una rifatta che non va ancora
        ('ancora', vedi _ancora_da_rifare) resta annotata con i passaggi fatti."""
        if not self.recupero:
            return
        ancora = ancora or {}
        falliti = set(self.ultimi_falliti)
        passi = getattr(self, "_passi", 1)
        cambiato = False
        for t in mancanti:
            voce = self._problemi.get(t)
            # passaggi gia' fatti: quelli annotati, e almeno uno per una traduzione salvata da rifare (vedi _giri)
            fatti = max((voce or {}).get("tentativi", 0), getattr(self, "_giro_minimo", {}).get(t, 0)) + passi
            if t in self._riusciti:
                if voce and voce.get("dove") == "gioco":  # la nuova traduzione la giudica applica()
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
            if t not in falliti:  # non tentato (traduttore fermato o in pausa): resta com'era
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

    # --- etichette troppo lunghe per il loro spazio -------------------------------------------------------------

    def _etichette_lunghe(self, contesti=None, backend=None):
        """{testo: spazio} delle etichette corte di questa traduzione (etichetta_corta) la cui traduzione e' piu' lunga
        del loro spazio (spazio_etichetta): solo quelle scritte in automatico (anche adesso), non le correzioni a mano,
        e non quelle gia' annotate con questa stessa traduzione (problemi.json, 'lunghezza'): chieste piu' corte una
        volta, o annotate con un traduttore online e adesso di nuovo con uno online. Annotate con un traduttore online
        e adesso con quello locale (backend.usa_contesto) si chiedono piu' corte, una volta."""
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
                continue  # corretta a mano, o di una versione che non annotava le traduzioni automatiche
            problema = self._problemi.get(t) or {}
            if problema.get("dove") == "lunghezza" and problema.get("valore") == v and \
                    (problema.get("chiesta", True) or not locale):
                continue  # gia' chiesta piu' corta una volta (o gia' annotata, e il traduttore non sa accorciarla)
            if problema.get("dove") in ("gioco", "memoria"):
                continue  # la rifanno gli altri controlli
            contesto = (contesti or {}).get(t) or {}
            spazio = spazio_etichetta(t, (contesto.get("riferimenti") or {}).values(), contesto.get("spazio"))
            if _peso(v) > spazio + 0.01:
                uscita[t] = spazio
        return uscita

    def _accorcia(self, backend, orig, dest, contesti=None):
        """Le etichette corte troppo lunghe per il loro spazio (es. 'Credits' -> 'Riconoscimenti'): con il traduttore
        locale si chiedono una volta di nuovo, con il numero massimo di caratteri (contesto 'larghezza'), e resta la
        traduzione piu' corta che passa i controlli. Quelle ancora troppo lunghe, e con gli altri traduttori (che non
        sanno di uno spazio massimo) tutte, vanno in problemi.json ('lunghezza', con il testo originale come chiave):
        si vedono in "Correggi traduzioni", elenco "Troppo lunghi"."""
        try:
            self._chiedi_piu_corte(backend, orig, dest, contesti)
        except Exception as e:  # noqa: BLE001 - e' un di piu': la traduzione appena fatta resta valida e si scrive
            self.log("  ! etichette piu' corte non riuscite (%s: %s)" % (type(e).__name__, e))

    def _chiedi_piu_corte(self, backend, orig, dest, contesti=None):
        if self.esito_traduzione is not None:  # traduttore bloccato: la prossima volta
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
            except ErroreBackend as e:  # le altre si chiedono la prossima volta
                self.log("  ! etichette piu' corte non chieste: %s" % e)
                break
            except Exception as e:  # noqa: BLE001 - e' un di piu': le traduzioni di prima restano
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
                # ancora troppo lunga: resta annotata (anche la piu' corta); se ci sta, esce dall'elenco
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
        """Le etichette ancora troppo lunghe in problemi.json ('lunghezza'), con lo spazio e la traduzione; quelle che
        adesso ci stanno escono dall'elenco."""
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
                continue  # un problema piu' importante (non tradotta, non adatta al gioco)
            # chiesta: gia' chiesta piu' corta al traduttore locale (se no, con quello si chiede: _etichette_lunghe)
            self._segna_problema(t, motivo="%d caratteri, di solito ce ne stanno circa %d" % (
                round(_peso(valore)), int(spazio)), dove="lunghezza", valore=valore, spazio=int(spazio),
                chiesta=chiesta)
            self.lunghe_annotate += 1
        self._salva_problemi()
        self._lunghe = {}

    def _traduci_mancanti(self, mancanti, backend, orig, dest, contesti=None):
        self._nuclei_ok, self._passi, self._scarti_visti, self._motivi_falliti = {}, 1, {}, {}
        if not mancanti:
            # (solo le traduzioni salvate: il traduttore si avvia se serve)
            if self._etichette_lunghe(contesti, backend):
                try:
                    self._accorcia(backend, orig, dest, contesti)
                finally:
                    if hasattr(backend, "chiudi"):
                        backend.chiudi()
            return 0
        pausa = backend.pausa() if hasattr(backend, "pausa") else None
        if pausa:  # servizio in pausa per un blocco precedente: nessuna richiesta, per non allungarlo
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
        # le proposte scartate del traduttore locale dicono perche' un testo non e' passato (per problemi.json); nel
        # traduttore dal vivo le prepara lui (servizio_xunity)
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
            self._accorcia(backend, orig, dest, contesti)  # prima di chiudere il traduttore locale
        finally:
            if hasattr(backend, "chiudi"):  # traduttore locale: libera la scheda video e chiude il server
                backend.chiudi()
            if scarti_propri:
                backend.scarti = None
        self.ultimi_falliti = falliti
        totale = len(falliti) + non_tentati
        self.log(DETTAGLIO + "Memoria: %d voci (%s)%s" % (
            len(self.memoria), self.percorso_memoria, ", non tradotti: %d" % totale if totale else ""))
        return totale

    def _traduci_gruppi(self, backend, gruppi, mancanti, orig, dest, contesti=None):
        """([testi tentati e rimasti senza traduzione valida], quanti non sono stati tentati perche' il traduttore si
        e' fermato o il gruppo e' saltato per un errore)."""
        falliti, non_tentati = [], 0
        fatti = 0
        for gruppo in gruppi:
            inizio = fatti
            fatti += len(gruppo)
            self.log(DETTAGLIO + "  testi %d-%d di %d" % (inizio + 1, inizio + len(gruppo), len(mancanti)))
            # nella finestra l'avanzamento ogni 10% (non ogni gruppo di testi)
            if inizio and len(gruppi) > 2 and inizio * 10 // len(mancanti) > (inizio - len(gruppo)) * 10 // len(mancanti):
                self.log("  Tradotti %d di %d (%d%%)..." % (inizio, len(mancanti), inizio * 100 // len(mancanti)))
            try:
                interruzione.controlla()
                falliti += self._traduci_gruppo(backend, gruppo, orig, dest, contesti)
            except interruzione.Interrotto:
                self.log("  Fermato: le traduzioni fatte finora sono salvate (%d in memoria) e la prossima volta si "
                         "riparte da li'." % len(self.memoria))
                raise
            except ErroreBackend as e:  # servizio irraggiungibile o bloccato: si riprende al prossimo avvio
                self.log("  ! traduzione interrotta: %s" % e)
                self.esito_traduzione, self.motivo_traduzione = "TRADUTTORE_BLOCCATO", str(e)
                self.ultimo_errore = e
                non_tentati += len(mancanti) - inizio
                break
            except Exception as e:  # problema su questo gruppo: lo salto e continuo con i successivi
                self.log("  ! gruppo saltato (%s: %s)" % (type(e).__name__, e))
                self.ultimo_errore = e
                non_tentati += len(gruppo)
            finally:
                self._salva_memoria()
        return falliti, non_tentati

    def _giri(self, divisi):
        """{riga: passaggi gia' fatti} dai tentativi annotati in problemi.json per i testi da cui viene la riga (almeno
        uno per una traduzione salvata da rifare: vedi traduci_testi)."""
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
        """Traduce un gruppo di testi riga per riga, con codici e nomi del gioco protetti, e salva in memoria quelli
        con tutte le righe tradotte; restituisce gli altri. passo: i giri gia' fatti in questa traduzione (0 = il
        primo; dopo si chiedono solo le righe che mancano, con semi nuovi: vedi _recupera)."""
        divisi = {t: dividi_righe(t) for t in gruppo}
        nuclei_ok = getattr(self, "_nuclei_ok", {})
        nuclei = list(dict.fromkeys(n for parti in divisi.values() for _, n, _ in parti
                                    if _da_tradurre(n) and not (passo and n in nuclei_ok)))
        protetti = {n: proteggi(n, self._protetti()) for n in nuclei}
        # una riga fatta solo di codici e nomi del gioco ('Cup Dog Games') resta com'e', senza chiederla
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
        except BaseException as e:  # blocco, quota finita o "Ferma" a meta': si salva quello che e' arrivato
            parziali = getattr(e, "parziali", None)
            if parziali and len(parziali) == len(chiesti):
                mappa, _ = self._mappa(chiesti, protetti, parziali)
                mappa.update(fissi)
                self._in_memoria(divisi, mappa)
            raise
        mappa, da_ripiegare = self._mappa(chiesti, protetti, tradotti)
        mappa.update(fissi)
        if da_ripiegare:  # i pezzi tra i segnaposto di tutto il gruppo, in una richiesta sola
            try:
                ripiego = self._traduci_a_pezzi(backend, da_ripiegare, orig, dest,
                                                giri={n: giri.get(n, 0) for n in da_ripiegare} if giri else None)
            except BaseException:  # Ferma o blocco durante il ripiego: le traduzioni gia' buone del gruppo restano
                self._in_memoria(divisi, mappa)
                raise
            for n, ripristinato in ripiego.items():
                if ripristinato:
                    mappa[n] = maiuscola_come(n, ripristinato)
        self.ultimo_gruppo = {"divisi": divisi, "protetti": protetti, "mappa": mappa, "ripiegati": da_ripiegare}
        return self._in_memoria(divisi, mappa)

    @staticmethod
    def _mappa(nuclei, protetti, tradotti):
        """({nucleo: traduzione valida}, [nuclei con i segnaposto rovinati da ritradurre a pezzi])."""
        mappa, da_ripiegare = {}, []
        for n, t in zip(nuclei, tradotti):
            ripristinato = ripristina_segnaposto(t, protetti[n][1]) if t else None
            if ripristinato is None and protetti[n][1] and t:
                da_ripiegare.append(n)
            elif ripristinato:
                mappa[n] = maiuscola_come(n, ripristinato)
        return mappa, da_ripiegare

    def _in_memoria(self, divisi, mappa):
        """Salva in memoria i testi con tutte le righe tradotte (anche in un gruppo o in un giro prima di questo, nella
        stessa traduzione). Restituisce i testi rimasti senza traduzione."""
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
        """Nuovi giri automatici, subito, per i testi scartati dai controlli (prima si ripeteva la stessa domanda con
        gli stessi parametri, e la risposta era la stessa a ogni avvio):
        1. le righe che mancano divise in frasi e nei pezzi tra i codici, ognuno tradotto da solo, in una richiesta
           sola; il traduttore locale usa semi nuovi, ricavati dal testo e dal giro;
        2. il modello resta quello scelto: i testi ancora scartati vanno in problemi.json ('Correggi traduzioni').
        Restituisce i testi ancora senza traduzione."""
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
        except ErroreBackend as e:  # traduttore fermato o bloccato: si riprende al prossimo avvio
            self.log("  ! nuovo giro interrotto: %s" % e)
            self.esito_traduzione, self.motivo_traduzione, self.ultimo_errore = "TRADUTTORE_BLOCCATO", str(e), e
            ripiego, fermato = {}, True
        except Exception as e:  # noqa: BLE001 - i nuovi giri sono un di piu': le traduzioni fatte si scrivono comunque
            self.log("  ! nuovo giro non riuscito (%s: %s)" % (type(e).__name__, e))
            self.ultimo_errore = e
            ripiego = {}
        for n, r in ripiego.items():
            if r:
                self._nuclei_ok[n] = maiuscola_come(n, r)
        rimasti = self._in_memoria(divisi, {})
        self._salva_memoria()
        # niente cambio di modello automatico: si usa quello scelto dall'utente (il Qualita' 12B al posto del Veloce
        # caricherebbe minuti e scheda video senza che l'abbia chiesto); i rimasti si ritraducono con il 12B da
        # 'Correggi traduzioni', se lo si vuole
        recuperati = len(falliti) - len(rimasti)
        self.log("  Nuovi giri: %d testi tradotti, %d ancora in originale" % (recuperati, len(rimasti)))
        return rimasti

    def _motivi(self, falliti):
        """{testo: perche' e' rimasto senza traduzione} per problemi.json, dalle proposte scartate del traduttore
        locale (i controlli che non hanno passato)."""
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
        """Ripiego: traduce separatamente i pezzi di testo tra i segnaposto (frasi=True: anche ogni frase da sola), di
        tutti i nuclei insieme con una sola chiamata al traduttore. giri: {nucleo: giro} per i semi del traduttore
        locale. Restituisce {nucleo: traduzione o None}."""
        preparati, pezzi_unici, giro_pezzo = {}, [], {}
        codice = re.compile(r"\[\[\d+\]\]")
        for nucleo in nuclei:
            testo, segnaposto = proteggi(nucleo, self._protetti())
            pezzi = re.split(r"(\[\[\d+\]\])", testo)
            if frasi:  # anche tra una frase e l'altra (gli spazi tra le frasi restano come sono)
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
        """Scrive nei file del gioco le traduzioni presenti in memoria (non usa la rete). Il resoconto resta in
        self.ultimo_applica: testi tradotti (ok) e rimasti in originale (mancanti), i primi originali rimasti
        (esempi), traduzioni scartate perche' il gioco non le mostrerebbe bene, file scritti, gioco aperto.
        Le traduzioni scartate vanno in problemi.json con il motivo (vedi _registra_applica)."""
        self.ultimo_applica = {"ok": 0, "mancanti": 0, "esempi": [], "scartate": 0, "scritti": 0, "aperto": False}
        self._respinte, self._applicati = {}, set()
        if self.profilo.get("motore") == "gamemaker" and gioco_aperto(self.profilo["cartella_gioco"]):
            self.log("  ! Il gioco e' aperto: chiudilo e riprova (alcuni testi li rilegge solo al riavvio).")
            self.ultimo_applica["aperto"] = True
            return False
        totale_ok = totale_mancanti = 0
        self._lasciati = 0  # etichette lasciate apposta come l'originale (come fa il gioco nelle sue traduzioni)
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
        """Una traduzione salvata che non si puo' scrivere nel gioco (resta l'originale): annotata per problemi.json."""
        if isinstance(testo, str) and testo not in self._respinte:
            self._respinte[testo] = {"motivo": motivo, "file": voce.get("percorso"),
                                     "non_usare": "".join(dict.fromkeys(non_usare or ""))}

    def _registra_applica(self):
        """Dopo applica(): le traduzioni scartate vanno in problemi.json (dove 'gioco', con il motivo e i caratteri da
        non usare); quelle scritte automaticamente si rifaranno alla prossima traduzione con l'istruzione giusta (vedi
        _da_rifare), quelle corrette a mano restano (si vedono in 'Mostra i rimasti'). I testi scritti nel gioco escono
        dall'elenco, tranne le traduzioni salvate sospette ('memoria'): si possono scrivere, ma restano annotate (con i
        passaggi fatti, che le fermano dopo MAX_TENTATIVI) finche' in memoria c'e' quella traduzione."""
        if not self.recupero:
            return
        cambiato = False
        for t in self._applicati:
            voce = self._problemi.get(t)
            if voce is None or t in self._respinte:
                continue
            if voce.get("dove") in ("memoria", "lunghezza") and voce.get("valore") == self.memoria.get(t):
                continue  # (sospette e troppo lunghe: si scrivono, ma restano annotate finche' non cambiano)
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
                                 tentativi=max(prima.get("tentativi", 0), 1))  # rifatta: semi nuovi
            cambiato = True
        if automatiche:
            self.log("  %d traduzioni automatiche non si possono scrivere nel gioco: alla prossima traduzione le "
                     "rifaccio con l'istruzione giusta" % automatiche)
        if cambiato:
            self._salva_problemi()

    def _rimasti(self, testi):
        """Annota i primi originali rimasti senza traduzione (per mostrarli all'utente)."""
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
        originali = 0  # etichette lasciate apposta come l'originale (non sono traduzioni mancanti)
        lasciate = set()  # le loro posizioni
        if speciale or self._font_del_gioco():
            grezzi = dict(self._grezzi_json(f.dati, voce))
            sostituzioni, adattate, scartate, larghe = {}, 0, [], []
            limiti = voce.get("limiti") or {}
            non_disegnabili = []
            for p, t in unita:
                tradotto = self.memoria.get(t)
                if not tradotto:
                    continue
                # gioco in minuscolo (font a sprite): vale per i testi scritti in minuscolo; quelli con le maiuscole
                # normali (messaggi di sistema) il gioco li disegna con un altro font
                minuscolo = voce.get("maiuscole") == "minuscolo" and not adapter_gamemaker._parole_miste(
                    adapter_gamemaker._senza_segnaposto(grezzi[p]))
                if minuscolo:  # il gioco scrive in minuscolo: le maiuscole sarebbero icone o lettere mancanti
                    tradotto = adapter_gamemaker.in_minuscolo(tradotto, grezzi[p], voce.get("codici", []),
                                                              self.profilo["lingua_destinazione"])
                limite = limiti.get(adapter_gamemaker.chiave_di(p)) if isinstance(p, tuple) and p else None
                if speciale:  # di nuovo con gli a capo e i simboli del gioco, e nello spazio che il gioco da' al testo
                    ricomposto = adapter_gamemaker.ricomponi_a_capo(
                        grezzi[p], tradotto, voce.get("a_capo"), voce.get("punto_elenco"),
                        limite[0] if limite else None, limite[1] if limite else None)
                    if ricomposto is None:
                        scartate.append(p)
                        # i segni del gioco (a capo, simbolo) scritti dentro la traduzione: la prossima volta no
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
                        originali += 1  # come le traduzioni ufficiali che lo lasciano com'e' (es. 'vsync:')
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
        dati_byte = f.serializza(nuovi)  # solleva ValueError se il JSON non e' rappresentabile
        adapter_webjson.rileggi_rigoroso(dati_byte, f.tollerante)
        percorso = self._percorso_gioco(voce["percorso"])
        if voce.get("lingua_nuova"):  # una lingua in piu' nel gioco: il file originale resta com'e'
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
        """Prima di scrivere la lingua aggiunta (es. local/italiano.json):
        - il file della lingua originale torna originale se una versione precedente l'aveva tradotto al suo posto
          (anche se poi era stato corretto a mano: la versione corretta resta da parte, .patchsmith-modificato);
        - un file con quel nome che non e' di PatchSmith (es. una traduzione fatta da altri) non si tocca;
        - una lingua aggiunta corretta a mano va da parte prima di riscriverla;
        - la lingua aggiunta si registra (con l'impronta di cio' che si scrive) tra i file creati nelle informazioni
          del backup del file originale, cosi' 'Ripristina originali' la toglie."""
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
            # non e' piu' il nostro (es. un italiano.json ufficiale arrivato con un aggiornamento): non si tocca
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
        salva_info(sorgente, info)  # prima di scrivere, e anche per passare al nome nuovo delle informazioni
        return nuovo

    def _font_del_gioco(self):
        """'gamemaker' o 'xna' se le traduzioni vanno adattate ai caratteri dei font del gioco (in GameMaker un
        carattere che il font non ha diventa un quadratino, in XNA puo' far chiudere il gioco), altrimenti None.
        Vale anche per i profili della ricerca generica, con il motore rilevato in profilo["motore_gioco"]."""
        if self.profilo.get("motore") == "gamemaker":
            return "gamemaker"
        return self.profilo.get("motore_gioco") if self.profilo.get("motore_gioco") in ("gamemaker", "xna") else None

    def _adatta(self, tradotto, originale, ripara=True, minuscolo=False):
        """Traduzione con i soli caratteri che i font del gioco sanno disegnare (None: impossibile). 'ripara':
        anche le regole dei codici tra barre dei file a righe (es. /name/ in Dokimon). 'minuscolo': testo di un
        gioco che scrive in minuscolo con un font a sprite (Forager): valgono i caratteri di quel font."""
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
            elif font == "xna":  # None se il gioco non ha font XNB: nessun limite
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
        """I caratteri della traduzione che i font del gioco non sanno disegnare, neanche adattati (vedi _adatta)."""
        return "".join(c for c in dict.fromkeys(tradotto)
                       if not c.isspace() and self._adatta(c, originale, ripara=False, minuscolo=minuscolo) is None)

    MAX_SCARTI_TABELLE = 5  # traduzioni tolte una alla volta finche' il bundle riletto non torna

    def _togli_crc(self, voce):
        """Unity Localization con il catalogo di Addressables (catalog.json) che controlla l'impronta (CRC) dei
        bundle: prima di riscrivere un bundle, nel catalogo si mette 0 al CRC dei bundle di questo profilo (solo
        quelli). Il catalogo ha il suo backup e le sue informazioni come gli altri file: 'Ripristina originali' lo
        rimette; se un aggiornamento del gioco lo cambia, il nuovo diventa l'originale. Se il bundle non si trova
        nel catalogo si ferma (ValueError): il bundle non si scrive."""
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
        """I caratteri che i font TextMeshPro del gioco sanno disegnare (font_unity), o None se non si sa (nessun
        limite). Letti una volta per traduzione; il risultato resta in memoria/<gioco>.unity/font.json finche' i file
        del gioco non cambiano."""
        if not hasattr(self, "_ammessi_tmp"):
            self._ammessi_tmp = None
            dati = font_unity.dati_del_bundle(self.profilo["cartella_gioco"], voce["percorso"])
            if dati and os.path.isdir(dati):
                cache = cartella_dati("memory", "%s.unity" % self.profilo["nome"], "font.json")
                try:
                    self._ammessi_tmp, motivo = font_unity.caratteri_ammessi(dati, self.log, cache=cache)
                except Exception as e:  # noqa: BLE001 - i font sono un controllo in piu': un file strano non
                    # deve fermare la scrittura delle traduzioni (si va avanti senza limiti, come prima)
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
        """Riscrive il bundle delle tabelle dei testi con le traduzioni, controllando prima che, riletto, abbia gli
        stessi testi nello stesso ordine. Una traduzione senza lettere sparirebbe dalla tabella riletta: resta
        l'originale. Se il controllo non torna per un'altra traduzione, si toglie quella (fino a MAX_SCARTI_TABELLE);
        le tolte vanno in problemi.json. I caratteri che nessun font TextMeshPro del gioco sa disegnare si adattano
        (caratteri.adatta_caratteri: è -> e'); una traduzione che non si puo' adattare resta in originale e va in
        problemi.json (la prossima traduzione la rifa' senza quei caratteri)."""
        f, unita = self._carica(voce)
        if unita is None:
            self.log("  %s: il file non esiste piu' nel gioco, lo salto" % voce["percorso"])
            return 0, 0
        ammessi = self._caratteri_tmp(voce)
        # caratteri che il gioco ha detto di non saper disegnare (registro di Unity, vedi checks.py)
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
            # la prima voce che non torna: se e' una traduzione la si toglie e si riprova
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
        if voce.get("catalogo"):  # prima il catalogo: un bundle riscritto con il CRC ancora li' non si caricherebbe
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
                self._scartate += 1  # numero di righe diverso (es. corretta a mano male)
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
        # ogni traduzione che cambierebbe come il gioco legge il file resta in originale (da sola)
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
        """Tabella CSV con una colonna per lingua (adapter_csv): si riscrivono solo le celle della colonna originale.
        Il separatore e le virgolette in un file che il gioco divide a mano si sostituiscono (FileCSV.adatta); una
        traduzione con un numero di righe diverso, o con caratteri che i font non sanno disegnare, resta in
        originale."""
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
            pronto = f.adatta(pronto)  # es. ',' -> ';' in una tabella che il gioco divide sulle virgole
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
        dati_byte = f.serializza(sostituzioni)  # ErroreCSV (ValueError) se il file riletto non torna: non si scrive
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

        # 1) backup e registrazione (impronta e file creato) prima di qualsiasi scrittura
        info = self._prepara_scrittura(index, html_byte)
        if adapter_webui.NOME_SCRIPT not in info.get("creati", []):
            info["creati"] = info.get("creati", []) + [adapter_webui.NOME_SCRIPT]
            salva_info(index, info)
        # 2) script, controllato prima di agganciarlo alla pagina
        adapter_webjson.scrivi_atomico(script, script_byte)
        sintassi = adapter_webui.controlla_script(script)
        if sintassi is False:
            self.log("  ! %s: lo script generato non e' valido, interfaccia NON modificata" % voce["percorso"])
            return 0, len(unita)
        # 3) pagina con il tag
        adapter_webjson.scrivi_atomico(index, html_byte)
        self._scritti += 1
        self._applicati.update(dizionario)
        self.log("  %s: interfaccia, %d testi tradotti, %d ancora in originale%s" % (
            voce["percorso"], len(dizionario), mancanti,
            "" if sintassi else " (sintassi dello script non verificata: Node.js assente)"))
        return len(dizionario), mancanti

    def ripristina(self):
        """Rimette gli originali di TUTTI i file modificati nella cartella del gioco."""
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
