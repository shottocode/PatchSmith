"""Adapter GameMaker: traduce i file di localizzazione esterni del gioco (es. Dokimon: Localization/*.txt).

Molti giochi GameMaker leggono i testi da file inclusi accanto all'eseguibile, uno per lingua
(Localization/english_ui.txt, Localization/french/ui.txt...). PatchSmith:
1. trova i file della lingua originale nel formato "a righe con id" (adapter_righe);
2. li traduce (memoria + Google, con i codici come /name/ e /n/ protetti);
3. adatta i caratteri a quelli dei font del gioco (letti da data.win: in GameMaker un carattere che
   il font non ha diventa un quadratino), per l'italiano ì -> í, ò -> ó, ù -> ú se il font ha gli acuti;
4. ripara cio' che il gioco leggerebbe male (una "/" in piu' viene presa per l'inizio di un codice);
5. li riscrive al loro posto con il backup verificato degli originali (come per i giochi web).
Nel gioco resta selezionata la lingua dei file tradotti (di solito English).

Altri formati riconosciuti: file JSON per lingua (local/english.json, anche con sezioni, commenti // e virgole in
fondo, e con il codice attaccato al nome come localization/stringsEN.json) e tabelle CSV con una colonna per lingua
(translations.csv: id,english,french...; vedi adapter_csv).
Limiti: i testi scritti nel codice del gioco (data.win) restano in originale; altri formati di localizzazione non
sono coperti.
"""
import os
import re
import struct

from . import adapter_righe, gamemaker, lingua
from .caratteri import ASCII

# nomi completi delle lingue (valgono ovunque nel percorso) e codici brevi (valgono solo in posizioni sicure)
NOMI_LINGUE = {
    "en": {"english", "inglese"}, "fr": {"french", "francais", "français"}, "de": {"german", "deutsch"},
    "es": {"spanish", "espanol", "español"}, "it": {"italian", "italiano"},
    "pt": {"portuguese", "brazilian", "portugues", "português"}, "ru": {"russian"}, "ja": {"japanese"},
    "ko": {"korean", "koreana"}, "zh": {"chinese", "schinese", "tchinese"}, "pl": {"polish", "polski"},
    "tr": {"turkish"},
    "th": {"thai"}, "nl": {"dutch", "nederlands"}, "sv": {"swedish", "svenska"}, "da": {"danish", "dansk"},
    "fi": {"finnish", "suomi"}, "no": {"norwegian", "norsk"}, "cs": {"czech", "cestina"}, "hu": {"hungarian"},
    "ro": {"romanian"}, "el": {"greek"}, "uk": {"ukrainian"}, "ar": {"arabic"}, "vi": {"vietnamese"},
    "id": {"indonesian"},
}
_CINESE_TRADIZIONALE = re.compile(r"(?<![a-z])(?:traditional|tradition|tchinese|hant|cht|tw)(?![a-z])")
# parole che accompagnano la lingua nel nome di un file (chinese_traditional, portuguese_br): fanno parte della lingua
_QUALIFICATORI = {"traditional", "tradition", "simplified", "hant", "hans", "tw", "cn", "hk", "br", "pt", "latam",
                  "mx", "us", "uk", "gb", "eu", "es"}
CODICI_LINGUE = {
    "en": {"en", "eng", "en-us", "en_us"}, "fr": {"fr", "fra", "fre"}, "de": {"de", "ger", "deu"},
    "es": {"es", "spa"}, "it": {"it", "ita"}, "pt": {"pt", "ptbr", "pt-br", "pt_br", "por"}, "ru": {"ru", "rus"},
    "ja": {"ja", "jp", "jpn"}, "ko": {"ko", "kr", "kor"}, "zh": {"zh", "cn", "chs", "cht", "zh-cn", "zh_cn"},
    "pl": {"pl", "pol"}, "tr": {"tr", "tur"},
}
_ESTENSIONI = (".txt", ".lang", ".loc", ".csv")
_MASSIMO = 20 * 1024 * 1024
# file che non sono testi del gioco anche se ne hanno l'aspetto
_DA_SALTARE = re.compile(r"copia|copy|backup|\bbak\b|_bak|\.bak|_old|\bold\b|readme|license|licence|changelog|"
                         r"patch.?notes|credits?_?list|save|score|config|settings|\blog\b|debug", re.I)


def _codice_di(parola):
    for codice, nomi in NOMI_LINGUE.items():
        if parola in nomi:
            return codice
    return None


def _codice_breve(parola):
    for codice, codici in CODICI_LINGUE.items():
        if parola in codici:
            return codice
    return None


# codice della lingua attaccato in fondo al nome, in maiuscolo dopo una minuscola (Another Door: stringsEN.json,
# stringsIT.json, stringsJP.json, stringsBR.json, stringsSC.json): anche i codici usati solo cosi'
_ATTACCATO = re.compile(r"^(.*[a-z])([A-Z]{2,4})$")
_CODICI_ATTACCATI = {"br": "pt", "ptbr": "pt", "sc": "zh", "chs": "zh", "tc": "zh-TW", "cht": "zh-TW", "tw": "zh-TW"}


def _attaccato(parola):
    """(radice, codice) se la parola (con le maiuscole com'erano) finisce con il codice di una lingua attaccato
    (stringsEN -> ('strings', 'en')), altrimenti None."""
    m = _ATTACCATO.match(parola or "")
    if not m:
        return None
    codice = _CODICI_ATTACCATI.get(m.group(2).lower()) or _codice_breve(m.group(2).lower())
    return (m.group(1), codice) if codice else None


def _ultima_parola(rel):
    """L'ultima parte del nome del file (senza estensione), con le maiuscole com'erano: strings_menuEN -> menuEN."""
    nome = rel.split("/")[-1].rsplit(".", 1)[0]
    pezzi = [p for p in re.split(r"[\s_.\-]+", nome) if p]
    return pezzi[-1] if pezzi else ""


def lingua_del_percorso(rel):
    """Codice della lingua indicata dal percorso, oppure None.

    I nomi completi (english, french...) valgono ovunque; i codici brevi (en, it, pt...) solo se sono
    il nome di una cartella, il nome intero del file, o la prima/ultima parte del nome (ui_it, en_ui), anche
    attaccati in maiuscolo alla fine del nome (stringsEN)."""
    parti = rel.lower().split("/")
    cartelle, nome = parti[:-1], parti[-1].rsplit(".", 1)[0]
    trovate = set()
    for c in cartelle:
        codice = _codice_di(c) or _codice_breve(c)
        if codice:
            trovate.add(codice)
    pezzi = [p for p in re.split(r"[\s_.\-]+", nome) if p]
    for p in pezzi:
        if _codice_di(p):
            trovate.add(_codice_di(p))
    if len(pezzi) == 1 and _codice_breve(nome):
        trovate.add(_codice_breve(nome))
    elif len(pezzi) > 1:
        for p in (pezzi[0], pezzi[-1]):
            if _codice_breve(p):
                trovate.add(_codice_breve(p))
    attaccato = _attaccato(_ultima_parola(rel))
    if attaccato:
        trovate.add(attaccato[1])
    if trovate == {"zh"} and _CINESE_TRADIZIONALE.search(rel.lower()):
        return "zh-TW"
    return trovate.pop() if len(trovate) == 1 else None


def famiglia(rel):
    """Il percorso con la lingua al posto di '{L}': 'local/{L}.json', 'lang/{L}/ui.json', '{L}_credits.json'.
    I file della stessa famiglia in lingue diverse sono traduzioni l'uno dell'altro."""
    parti = rel.lower().split("/")
    cartelle = ["{L}" if (_codice_di(c) or _codice_breve(c)) else c for c in parti[:-1]]
    nome = parti[-1]
    radice, punto, estensione = nome.rpartition(".") if "." in nome else (nome, "", "")
    pezzi = re.split(r"([\s_.\-]+)", radice)
    parole = list(range(0, len(pezzi), 2))
    attaccato = _attaccato(_ultima_parola(rel))
    if attaccato:  # stringsEN, stringsIT -> strings{L}
        pezzi[parole[-1]] = attaccato[0].lower() + "{L}"
    for n, i in enumerate(parole):
        p = pezzi[i]
        if _codice_di(p) or (_codice_breve(p) and (len(parole) == 1 or n in (0, len(parole) - 1))):
            pezzi[i] = "{L}"
    for n, i in enumerate(parole):  # qualificatori accanto alla lingua: chinese_traditional, portuguese_br
        vicini = [pezzi[parole[m]] for m in (n - 1, n + 1) if 0 <= m < len(parole)]
        if pezzi[i] in _QUALIFICATORI and "{L}" in vicini:
            pezzi[i] = "{L}"
    nome = re.sub(r"\{L\}(?:[\s_.\-]+\{L\})+", "{L}", "".join(pezzi))
    return "/".join(cartelle + [nome + punto + estensione])


def leggi_originale(percorso, lettore):
    """I dati del file com'era in origine: dal backup di PatchSmith se c'e' ed e' valido (il file puo' essere gia'
    tradotto), altrimenti dal file stesso. 'lettore' legge un percorso."""
    from . import progetto
    backup = percorso + progetto.SUFFISSO_BACKUP
    if os.path.exists(backup):
        info = progetto.leggi_info(percorso)
        if info and progetto._hash_file(backup) == info["hash_originale"]:
            return lettore(backup)
    return lettore(percorso)


def file_localizzazione(cartella):
    """{relativo: lingua o None} dei file nel formato 'a righe con id' (sola lettura)."""
    trovati = {}
    for radice, dirs, files in os.walk(cartella):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for nome in files:
            if not nome.lower().endswith(_ESTENSIONI) or ".patchsmith-" in nome or _DA_SALTARE.search(nome):
                continue
            p = os.path.join(radice, nome)
            try:
                if os.path.getsize(p) > _MASSIMO:
                    continue
            except OSError:
                continue
            rel = os.path.relpath(p, cartella).replace(os.sep, "/")
            lingua_file = lingua_del_percorso(rel)
            # un file con la lingua nel nome (english_ui.txt) basta che abbia pochi testi
            if adapter_righe.sembra_localizzazione(p, minimo=2 if lingua_file else 5):
                trovati[rel] = lingua_file
    return trovati


def scegli_sorgenti(trovati, origine="auto", cartella=None):
    """(file da tradurre, lingue presenti, lingua dei file scelti).

    Si traducono i file della lingua originale; se sono sparsi in piu' cartelle si tiene quella che ne
    ha di piu' (es. Localization/), cosi' file isolati altrove non vengono toccati."""
    lingue = {l for l in trovati.values() if l}
    base = (origine or "auto").split("-")[0].lower()
    if base == "auto":
        base = "en" if "en" in lingue else None
    sorgenti = sorted(r for r, l in trovati.items() if l == base) if base else []
    if not sorgenti:
        # file senza lingua nel nome accanto alle traduzioni (es. ui.txt + french/ui.txt): sono l'originale
        senza = sorted(r for r, l in trovati.items() if l is None)
        if senza and cartella:
            testi = []
            for r in senza:
                try:
                    testi += [t for _, t in adapter_righe.leggi(os.path.join(cartella, *r.split("/"))).unita]
                except (adapter_righe.ErroreRighe, OSError):
                    continue
            rilevata = lingua.rileva(testi)[0].split("-")[0] if testi else None
            if base is None or rilevata == base:
                sorgenti, base = senza, rilevata
        elif senza and not lingue:
            sorgenti = senza
    if sorgenti:
        conta = {}
        for r in sorgenti:
            conta[os.path.dirname(r)] = conta.get(os.path.dirname(r), 0) + 1
        principale = max(conta, key=lambda d: (conta[d], -len(d)))
        sorgenti = [r for r in sorgenti if os.path.dirname(r) == principale]
    return sorgenti, lingue, base


# script diversi dal latino: un font con solo questi oltre all'ASCII serve ad altre lingue (es. coreano)
def _altro_alfabeto(c):
    o = ord(c)
    return 0x3040 <= o <= 0x30FF or 0x4E00 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7A3 or 0x0400 <= o <= 0x04FF \
        or 0x3400 <= o <= 0x4DBF or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F


def _latino_accentato(c):
    return 0xC0 <= ord(c) <= 0x24F and c.isalpha()


# font che una libreria aggiunge al gioco come ripiego (Scribble: scribble_fallback_font, solo ASCII)
_FONT_DI_RIPIEGO = re.compile(r"fallback", re.I)


def file_dati(cartella):
    """Percorso del data.win del gioco (quello meno profondo), oppure None."""
    radici = gamemaker.radici(cartella)
    for r in radici:
        for nome in gamemaker.NOMI_DATI:
            if os.path.exists(os.path.join(r, nome)):
                return os.path.join(r, nome)
    return None


def _font_della_lingua(nome_font, codice):
    """True se il nome del font dice che e' per la lingua 'codice' (es. fnt_english, font_en)."""
    if not codice or not nome_font:
        return False
    base = codice.split("-")[0].lower()
    pezzi = [p for p in re.split(r"[^a-z]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", nome_font).lower()) if p]
    return any(p in NOMI_LINGUE.get(base, ()) or p in CODICI_LINGUE.get(base, ()) for p in pezzi)


def caratteri_font(cartella, log=None, ufficiali=None, sorgente=None):
    """Caratteri che i font di testo del gioco (data.win) sanno disegnare, oppure None se non si sa.

    - contano i font con almeno 20 lettere latine, tranne quelli per un altro alfabeto: oltre all'ASCII hanno
      soprattutto un'altra scrittura (es. un font coreano o giapponese con qualche lettera accentata: almeno 100
      caratteri dell'altra scrittura e piu' di 10 volte le lettere accentate), e tranne i font di ripiego delle
      librerie (scribble_fallback_font: solo ASCII, il gioco non lo usa per i suoi testi);
    - 'ufficiali': lettere accentate che le traduzioni ufficiali del gioco usano (es. é, ü, ç). Se sono
      almeno 3, un font che non ne ha nessuna non e' usato per i testi tradotti (font della chat, dei numeri,
      di un'altra scrittura come il thai) e non conta, tranne se il nome dice che e' il font della lingua
      'sorgente' (es. fnt_english: e' quello che disegnera' la traduzione). Un font con qualche lettera accentata
      invece conta: puo' essere quello dei testi della lingua originale, con le sole lettere che usano (Another
      Door: ftPixeloidSans ha proprio le Á ç é ô del file inglese), e una lettera che non ha sarebbe un quadratino;
    - un font di sole maiuscole (il gioco mette in maiuscolo) vale anche per le minuscole;
    - se qualche font non si riesce a leggere, per sicurezza solo ASCII;
    - il quadratino U+25AF (carattere mancante) non conta come carattere disegnabile."""
    ufficiali = {c for c in (ufficiali or ()) if _latino_accentato(c)}
    percorso = file_dati(cartella)
    if not percorso:
        return None
    try:
        elenco, illeggibili = gamemaker.font(gamemaker.leggi(percorso))
    except (gamemaker.ErroreGameMaker, OSError, ValueError, IndexError, struct.error):
        if log:
            log("  ! Font del gioco non leggibili: per sicurezza uso solo caratteri ASCII")
        return set(ASCII)
    comuni = None
    for f in elenco:
        caratteri = set(f["caratteri"]) - {"\u25af"}
        lettere = [c for c in caratteri if c.isascii() and c.isalpha()]
        if len(lettere) < 20:
            continue
        altro = sum(1 for c in caratteri if _altro_alfabeto(c))
        if altro >= 100 and altro > 10 * sum(1 for c in caratteri if _latino_accentato(c)):
            continue  # font per un'altra lingua (es. coreano, o giapponese con ç ê ñ Œ œ)
        if _FONT_DI_RIPIEGO.search(f.get("nome") or ""):
            if log:
                log("  Font %s non considerato: e' il font di ripiego di una libreria" % f.get("nome"))
            continue
        if len(ufficiali) >= 3 and not (caratteri & ufficiali) and not _font_della_lingua(f.get("nome"), sorgente):
            if log:
                log("  Font %s non considerato: le traduzioni ufficiali del gioco non lo usano (non ha le loro "
                    "lettere accentate)" % f.get("nome"))
            continue
        if not any(c.islower() for c in lettere):
            caratteri |= {c.lower() for c in caratteri}
        elif not any(c.isupper() for c in lettere):
            caratteri |= {c.upper() for c in caratteri}
        comuni = caratteri if comuni is None else comuni & caratteri
    if illeggibili:
        comuni = set(ASCII) if comuni is None else comuni & ASCII
        if log:
            log("  ! %d font del gioco non leggibili: per sicurezza uso solo caratteri ASCII" % illeggibili)
    elif comuni is None and elenco:
        comuni = set(ASCII)
        if log:
            log("  ! Nessun font del gioco (data.win) sembra quello dei testi: per sicurezza uso solo caratteri ASCII")
    return comuni


# --- file di localizzazione JSON (es. Forager: local/english.json, local/french.json...) ----------

_MINIMO_VOCI_JSON = 20


def _leggi_json(percorso):
    from .adapter_webjson import FileJSON
    return FileJSON(percorso).dati


def _valori(dati):
    """I valori di un JSON, anche dentro le sezioni e le liste di testi, nell'ordine del file. Un elenco di oggetti
    ([{"id": "sword", "name": "Sword"}]) e' un file di dati, non di lingua: ValueError (i suoi identificativi, come
    'sword', tradotti non si troverebbero piu')."""
    sezione = isinstance(dati, dict)
    for v in (dati.values() if sezione else dati):
        if isinstance(v, (dict, list)):
            if not sezione:
                raise ValueError("elenco di oggetti")
            yield from _valori(v)
        else:
            yield v


def _stringhe(dati):
    """Valori stringa di un dizionario {chiave: testo}, anche diviso in sezioni ({"menu": {"_play": "Play"...}}) o
    con liste di testi: almeno _MINIMO_VOCI_JSON testi e l'80% dei valori (None se il file non ha questa forma)."""
    if not isinstance(dati, dict):
        return None
    try:
        tutti = list(_valori(dati))
    except (RecursionError, ValueError):
        return None
    valori = [v for v in tutti if isinstance(v, str)]
    return valori if len(valori) >= max(_MINIMO_VOCI_JSON, 0.8 * len(tutti)) else None


def chiave_di(percorso):
    """La chiave di un testo in un file di localizzazione: quella del file ('_play'), o per un testo dentro una sezione
    le chiavi unite con '.' ('menu._play')."""
    return ".".join(str(x) for x in percorso)


def piatto(dati):
    """{chiave: testo} dei testi di un file di localizzazione (vedi chiave_di): per un dizionario piatto e' lui stesso
    (solo i valori stringa)."""
    from .adapter_webjson import foglie
    return {chiave_di(p): v for p, _, v in foglie(dati)} if isinstance(dati, dict) else {}


def al_percorso(dati, percorso):
    """Il valore di 'dati' alla posizione 'percorso' (tupla di chiavi e indici), oppure None se non c'e'."""
    for passo in percorso:
        try:
            dati = dati[passo]
        except (KeyError, IndexError, TypeError):
            return None
    return dati


def creati_da_patchsmith(cartella):
    """Nomi dei file che PatchSmith ha creato in questa cartella (es. la lingua nuova local/italiano.json), letti
    dalle informazioni dei backup: non sono file del gioco. Un file creato e poi sostituito da altri (es. un
    italiano.json ufficiale arrivato con un aggiornamento: impronta diversa da quella scritta) non conta piu'."""
    from . import progetto
    creati = set()
    try:
        nomi = os.listdir(cartella)
    except OSError:
        return creati
    for nome in nomi:
        for suffisso in progetto.SUFFISSI_INFO:
            if nome.endswith(suffisso):
                info = progetto.leggi_info(os.path.join(cartella, nome[:-len(suffisso)])) or {}
                impronte = info.get("hash_creati") or {}
                for creato in info.get("creati", []):
                    p = os.path.join(cartella, creato)
                    if creato not in impronte or not os.path.exists(p) or progetto._hash_file(p) == impronte[creato]:
                        creati.add(creato)
    return creati


def file_json_lingue(cartella, log=None):
    """{relativo: lingua} dei file JSON di localizzazione: dizionari {chiave: testo} (anche divisi in sezioni, e anche
    con commenti // e virgole in fondo, che molti giochi accettano) con la lingua nel percorso, presenti in almeno due
    lingue nella stessa famiglia (local/{L}.json, lang/{L}/ui.json, localization/strings{L}.json; sola lettura).
    I file creati da PatchSmith (la lingua aggiunta) non contano. Con 'log': i file di lingua che non si leggono."""
    per_famiglia, illeggibili = {}, []
    for radice, dirs, files in os.walk(cartella):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        nostri = creati_da_patchsmith(radice) if any(".patchsmith-" in f for f in files) else set()
        for nome in files:
            if not nome.lower().endswith(".json") or ".patchsmith-" in nome or _DA_SALTARE.search(nome) \
                    or nome in nostri:
                continue
            p = os.path.join(radice, nome)
            rel = os.path.relpath(p, cartella).replace(os.sep, "/")
            lingua_file = lingua_del_percorso(rel)
            if not lingua_file:
                continue
            try:
                if os.path.getsize(p) > _MASSIMO:
                    illeggibili.append("%s (%d MB: troppo grande)" % (rel, os.path.getsize(p) // (1024 * 1024)))
                    continue
                if _stringhe(leggi_originale(p, _leggi_json)) is None:
                    continue
            except (OSError, ValueError) as e:
                illeggibili.append("%s (%s)" % (rel, str(e)[:80]))
                continue
            per_famiglia.setdefault(famiglia(rel), {})[rel] = lingua_file
    trovati = {}
    for gruppo in per_famiglia.values():
        if len(set(gruppo.values())) >= 2:
            trovati.update(gruppo)
    if log and illeggibili:
        log("  ! %d file di lingua JSON non letti: %s" % (len(illeggibili), "; ".join(illeggibili[:3])))
    return trovati


def lettere_ufficiali(cartella, trovati, sorgenti):
    """Lettere accentate latine usate dalle traduzioni ufficiali del gioco (gli altri file di lingua)."""
    lettere = set()
    for rel in trovati:
        if rel in sorgenti:
            continue
        p = os.path.join(cartella, *rel.split("/"))
        try:
            if rel.lower().endswith(".json"):
                testi = _stringhe(leggi_originale(p, _leggi_json)) or []
            else:
                testi = [t for _, t in leggi_originale(p, adapter_righe.leggi).unita]
        except (OSError, ValueError, adapter_righe.ErroreRighe):
            continue
        lettere |= {c for t in testi for c in t if _latino_accentato(c)}
    return "".join(sorted(lettere))


# in un file di localizzazione ogni valore e' un testo dell'interfaccia, anche una parola sola ("wood"); non lo
# sono solo gli identificativi rimasti al posto di un testo mancante ("Structure.NuclearFurnace", "menu_title_2")
_IDENTIFICATIVO = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:[.:/\\][A-Za-z0-9_]+)+$|^[a-z0-9]+(?:_[a-z0-9]+)+$")


def testo_localizzazione(valore):
    v = valore.strip() if isinstance(valore, str) else ""
    return bool(re.search(r"[^\W\d_]", v)) and not _IDENTIFICATIVO.match(v)


def schemi_json(dati):
    """Le chiavi con testo di un file di localizzazione: si traducono tutte (sono testi dell'interfaccia; le regole
    sulle chiavi "tecniche" dei JSON generici qui non valgono)."""
    from .adapter_webjson import foglie
    return sorted({schema for _, schema, v in foglie(dati) if testo_localizzazione(v)})


_ASTERISCO_TRA_PAROLE = re.compile(r"[^\W\d_]\*+[^\W\d_]")  # tra due lettere: non e' enfasi ("*sigh*")


def _confermato(altri, posti, prova, forte, debole):
    """True se le traduzioni ufficiali confermano la convenzione: almeno una lingua la segue quasi sempre
    ('prova(valore, posto)' vera in almeno 'forte' dei posti che ha) e almeno meta' delle lingue la segue almeno in
    parte ('debole'): i traduttori la tolgono dove non serve (es. una parola che sta in una riga sola)."""
    quote = []
    for a in altri:
        validi = [p for p in posti if isinstance(a.get(p[0]), str)]
        if validi:
            quote.append(sum(1 for p in validi if prova(a[p[0]], p)) / len(validi))
    return bool(quote) and max(quote) >= forte and sum(1 for q in quote if q >= debole) >= len(altri) / 2


def markup(dati, altri):
    """Convenzioni di scrittura dei testi del file sorgente, confermate dalla maggioranza delle traduzioni ufficiali
    ('altri': gli altri dizionari della stessa cartella): {'a_capo': '*' se l'asterisco fa da a capo (es.
    "iron*ore"), 'punto_elenco': lettera usata come simbolo a inizio paragrafo (es. "U unlocks...**U gain...")}."""
    esito = {"a_capo": None, "punto_elenco": None}
    testi = {k: v for k, v in dati.items() if isinstance(v, str)} if isinstance(dati, dict) else {}
    altri = [a for a in altri if isinstance(a, dict)]
    if not testi or not altri:
        return esito
    con_asterisco = [(k,) for k, v in testi.items() if _ASTERISCO_TRA_PAROLE.search(v)]
    if len(con_asterisco) >= max(5, 0.05 * len(testi)) and \
            _confermato(altri, con_asterisco, lambda valore, _posto: "*" in valore, 0.6, 0.2):
        esito["a_capo"] = "*"
    separatore = "**" if esito["a_capo"] else "\n\n"
    occorrenze = {}  # lettera -> [(chiave, numero del paragrafo)]
    for k, v in testi.items():
        for n, paragrafo in enumerate(v.split(separatore)):
            m = re.match(r"([A-Z]) +[a-z]", paragrafo)
            if m:
                occorrenze.setdefault(m.group(1), []).append((k, n))
    for lettera, posti in sorted(occorrenze.items(), key=lambda x: -len(x[1])):
        if len(posti) < 5:
            break

        def ha_punto(valore, posto, lettera=lettera):
            paragrafi = valore.split(separatore)
            return posto[1] < len(paragrafi) and re.match(re.escape(lettera) + r" +\S", paragrafi[posto[1]])
        if _confermato(altri, posti, ha_punto, 0.8, 0.3):
            esito["punto_elenco"] = lettera
            break
    return esito


def _prefisso_punto(paragrafo, punto):
    if not punto:
        return ""
    m = re.match(re.escape(punto) + r" +(?=\S)", paragrafo)
    return m.group(0) if m else ""


_FINE_FRASE = re.compile(r"[.!?:;\u2026\u3002\uff01\uff1f][\"')\]]*$")


def _corpo(paragrafo, segno, punto):
    corpo = paragrafo[len(_prefisso_punto(paragrafo, punto)):]
    return (corpo.replace(segno, " ") if segno else corpo).strip()


def _gruppi_frase(paragrafi, segno, punto):
    """Paragrafi raggruppati per frase (liste di indici). Il segno doppio separa i paragrafi ma in molti giochi
    spezza anche una frase a meta' (Forager: "U items discarded from your**inventory are instantly**sold
    instead"): tradotti da soli, i pezzi perdono il senso. Un paragrafo continua il precedente se non ha il simbolo
    d'elenco, comincia con una minuscola e il precedente non chiude la frase con la punteggiatura; il precedente
    deve essere una voce d'elenco o avere almeno tre parole (un titolo come "Controls**move with WASD" resta a se')."""
    gruppi = []
    for i, p in enumerate(paragrafi):
        corpo = _corpo(p, segno, punto)
        prima = _corpo(paragrafi[i - 1], segno, punto) if i else ""
        voce = bool(_prefisso_punto(paragrafi[gruppi[-1][0]], punto)) if gruppi else False
        if prima and corpo[:1].islower() and not _prefisso_punto(p, punto) and not _FINE_FRASE.search(prima) \
                and (voce or len(prima.split()) >= 3):
            gruppi[-1].append(i)
        else:
            gruppi.append([i])
    return gruppi


def normalizza_a_capo(testo, segno=None, punto=None):
    """Il testo come lo si traduce: gli a capo 'segno' diventano spazi (la frase si traduce intera), i paragrafi
    (segno doppio) diventano righe separate da una riga vuota, unendo i pezzi della stessa frase; il simbolo a
    inizio paragrafo si toglie."""
    if (not segno and not punto) or "\n" in testo:
        return testo
    separatore = segno * 2 if segno else None
    paragrafi = testo.split(separatore) if separatore else [testo]
    uscita = []
    for gruppo in _gruppi_frase(paragrafi, segno, punto):
        p = " ".join(_corpo(paragrafi[i], segno, punto) for i in gruppo)
        uscita.append(re.sub(r" {2,}", " ", p).strip())
    return "\n\n".join(uscita)


def _a_capo_bilanciato(testo, segmenti, segno):
    """Rimette nel testo tradotto gli a capo dell'originale: stesso numero (o meno, se le parole non bastano),
    in proporzione alle lunghezze delle righe originali, sempre tra due parole. 'segno' e' il segno di a capo,
    oppure l'elenco dei separatori originali tra un segmento e l'altro (a capo e paragrafi della stessa frase)."""
    separatori = [segno] * (len(segmenti) - 1) if isinstance(segno, str) else list(segno)
    n = len(segmenti) - 1
    parole = testo.split(" ")
    if n <= 0 or len(parole) < 2:
        return testo
    n = min(n, len(parole) - 1)
    totale_o = sum(len(s) for s in segmenti) or 1
    posizioni, cumulato = [], 0  # posizione nel testo di ogni spazio tra le parole
    for parola in parole[:-1]:
        cumulato += len(parola)
        posizioni.append(cumulato)
        cumulato += 1
    tagli, inizio, fatto = [], 0, 0
    for i in range(1, n + 1):
        fatto += len(segmenti[i - 1])
        obiettivo = fatto / totale_o * len(testo)
        rimasti = n - i  # tagli ancora da fare dopo questo: lasciano almeno uno spazio ciascuno
        scelte = range(inizio, len(posizioni) - rimasti)
        k = min(scelte, key=lambda j: abs(posizioni[j] - obiettivo))
        tagli.append(k)
        inizio = k + 1
    righe, da = [], 0
    for k in tagli:
        righe.append(" ".join(parole[da:k + 1]))
        da = k + 1
    righe.append(" ".join(parole[da:]))
    if n < len(separatori):  # meno parole che a capo: si tengono prima i separatori di paragrafo (i piu' lunghi)
        tenuti = sorted(sorted(range(len(separatori)), key=lambda j: -len(separatori[j]))[:n])
        separatori = [separatori[j] for j in tenuti]
    return righe[0] + "".join(sep + r for sep, r in zip(separatori, righe[1:]))


def ricomponi_a_capo(originale, tradotto, segno=None, punto=None, larghezza=None, righe=None):
    """Il testo tradotto (normalizzato) riportato alle convenzioni del gioco, con i paragrafi, il simbolo a inizio
    paragrafo e gli a capo dell'originale. None se i paragrafi non corrispondono (resta l'originale).
    Con 'larghezza' e 'righe' (la riga piu' lunga e il numero di righe delle versioni del gioco per questo testo):
    se una riga esce dalla larghezza, gli a capo si ridistribuiscono, aggiungendo righe fino a 'righe'."""
    risultato = _ricomponi(originale, tradotto, segno, punto)
    if risultato is None or not segno or not larghezza or _riga_piu_lunga(risultato, segno) <= larghezza:
        return risultato
    return _entro_larghezza(risultato, segno, punto, larghezza, righe or 1)


def _righe_di(testo, segno):
    """Le righe non vuote del testo come le disegna il gioco (a capo = segno o '\\n')."""
    pezzi = re.split("%s|\n" % re.escape(segno), testo) if segno else testo.split("\n")
    return [x.strip() for x in pezzi if x.strip()]


def _riga_piu_lunga(testo, segno):
    return max((len(x) for x in _righe_di(testo, segno)), default=0)


def _taglio_minimo(parole, k, prima=0):
    """Le parole su k righe in modo che la riga piu' lunga sia la piu' corta possibile (programmazione dinamica).
    'prima': caratteri gia' occupati all'inizio della prima riga (es. il simbolo 'U ').
    Restituisce (indici delle parole che cominciano ogni riga dopo la prima, lunghezza della riga piu' lunga)."""
    n = len(parole)
    k = max(1, min(k, n))
    lunghezze = [0]
    for w in parole:
        lunghezze.append(lunghezze[-1] + len(w))

    def lunga(i, j):  # parole i..j-1 su una riga
        return lunghezze[j] - lunghezze[i] + (j - i - 1) + (prima if i == 0 else 0)
    infinito = float("inf")
    costo = [[infinito] * (n + 1) for _ in range(k + 1)]
    da = [[0] * (n + 1) for _ in range(k + 1)]
    costo[0][0] = 0
    for r in range(1, k + 1):
        for j in range(r, n + 1):
            for i in range(r - 1, j):
                c = max(costo[r - 1][i], lunga(i, j))
                if c < costo[r][j]:
                    costo[r][j], da[r][j] = c, i
    tagli, j = [], n
    for r in range(k, 0, -1):
        i = da[r][j]
        if r > 1:
            tagli.append(i)
        j = i
    return sorted(tagli), costo[k][n]


def _entro_larghezza(testo, segno, punto, larghezza, righe):
    """Ridistribuisce gli a capo dei paragrafi troppo larghi (solo quelli con soli a capo semplici: i paragrafi
    del gioco e le righe vuote restano dove sono), aggiungendo righe finche' il totale non supera 'righe'."""
    separatore = segno * 2
    paragrafi = testo.split(separatore)
    totale = len(_righe_di(testo, segno))
    libere = max(0, righe - totale)
    fatti = set()
    for _ in range(len(paragrafi)):
        larghi = [(i, _riga_piu_lunga(p, segno)) for i, p in enumerate(paragrafi) if i not in fatti]
        if not larghi:
            break
        i, piu_lunga = max(larghi, key=lambda x: x[1])
        if piu_lunga <= larghezza:
            break
        fatti.add(i)
        p = paragrafi[i]
        prefisso = _prefisso_punto(p, punto)
        bordo = r"[\s%s]*" % re.escape(segno)
        testa, interno, coda = re.match(r"(%s)(.*?)(%s)$" % (bordo, bordo), p[len(prefisso):], re.S).groups()
        if re.search("%s\\s*%s" % (re.escape(segno), re.escape(segno)), interno) or "\n" in interno:
            continue  # paragrafi o righe vuote dentro: non si tocca
        parole = interno.replace(segno, " ").split()
        if not parole:
            continue
        attuali = interno.count(segno) + 1
        migliore = None
        for k in range(attuali, attuali + libere + 1):
            tagli, lunga = _taglio_minimo(parole, k, len(prefisso + testa))
            if migliore is None or lunga < migliore[1]:
                migliore = (tagli, lunga, k)
            if lunga <= larghezza:
                break
        if migliore is None or migliore[1] >= piu_lunga:
            continue
        tagli, _, k = migliore
        righe_nuove, da = [], 0
        for t in tagli + [len(parole)]:
            righe_nuove.append(" ".join(parole[da:t]))
            da = t
        paragrafi[i] = prefisso + testa + segno.join(righe_nuove) + coda
        libere -= k - attuali
    return separatore.join(paragrafi)


def _ricomponi(originale, tradotto, segno=None, punto=None):
    if (not segno and not punto) or "\n" in originale:
        return tradotto
    separatore = segno * 2 if segno else None
    paragrafi = originale.split(separatore) if separatore else [originale]
    paragrafi_o = [separatore.join(paragrafi[i] for i in g) if separatore else paragrafi[g[0]]
                   for g in _gruppi_frase(paragrafi, segno, punto)]  # i pezzi della stessa frase, insieme
    paragrafi_t = tradotto.split("\n\n") if len(paragrafi_o) > 1 else [tradotto]
    if len(paragrafi_o) != len(paragrafi_t) or any("\n" in t for t in paragrafi_t):
        return None
    uscita = []
    bordo = r"[\s%s]*" % re.escape(segno) if segno else r"\s*"
    for o, t in zip(paragrafi_o, paragrafi_t):
        prefisso = _prefisso_punto(o, punto)
        corpo = o[len(prefisso):]
        # a capo e spazi in testa e in coda restano fissi ("*attacks burn!" , "your time: ")
        testa, interno, coda = re.match(r"(%s)(.*?)(%s)$" % (bordo, bordo), corpo, re.S).groups()
        t = re.sub(r" {2,}", " ", t.strip())
        if segno and segno in t:
            return None  # il traduttore ha lasciato un segno: non so dove andrebbe
        if not t and interno.strip():
            return None  # paragrafo perso nella traduzione
        if segno and t:
            # a capo singoli e paragrafi della frase; "* *" (riga vuota con uno spazio) e' un separatore solo
            divisi = re.split("(%s(?:\\s*%s)*)" % (re.escape(segno), re.escape(segno)), interno)
            t = _a_capo_bilanciato(t, divisi[0::2], divisi[1::2])
        uscita.append(prefisso + testa + t + coda)
    return (separatore or "").join(uscita)


_CODICE = re.compile(r"/[A-Za-z_]{1,16}/")


def ripara(originale, tradotto):
    """Traduzione che il gioco legge come l'originale, oppure None.

    In GameMaker molti giochi (es. Dokimon) espandono codici tra barre (/name/, /n/): una barra in piu'
    viene presa per l'inizio di un codice e il resto della riga sparisce; un codice proprio alla fine di
    una riga di dialogo non viene chiuso. Quindi: stessi codici dell'originale, nessuna barra in piu'
    (diventa '-'), e uno spazio dopo un codice che finisce la riga."""
    if sorted(_CODICE.findall(originale)) != sorted(_CODICE.findall(tradotto)):
        return None
    barre_originale = _CODICE.sub("", originale).count("/")
    pezzi = _CODICE.split(tradotto)
    codici = _CODICE.findall(tradotto)
    extra = sum(p.count("/") for p in pezzi) - barre_originale
    if extra > 0:
        pezzi = [p.replace("/", "-") for p in pezzi]
        if barre_originale:  # non so quali barre tenere: meglio l'originale
            return None
    testo = pezzi[0] + "".join(c + p for c, p in zip(codici, pezzi[1:]))
    righe_o, righe_t = originale.split("\n"), testo.split("\n")
    if len(righe_o) == len(righe_t):
        righe_t = [t + " " if t.endswith("/") and not o.endswith("/") else t for o, t in zip(righe_o, righe_t)]
        testo = "\n".join(righe_t)
    return testo


# --- maiuscole: giochi scritti tutti in minuscolo, con le maiuscole usate come icone (Forager) -------------

_PAROLA = re.compile(r"[^\W\d_]+")


def _parole_miste(testo):
    """True se il testo ha una parola con maiuscole e minuscole insieme (Hello, CamelCase)."""
    return any(not w.isupper() and not w.islower() for w in _PAROLA.findall(testo))


def _senza_segnaposto(testo):
    from .testo import _SEGNAPOSTO
    return _SEGNAPOSTO.sub(" ", testo)


def stile_maiuscole(testi):
    """Come il gioco usa le maiuscole nei suoi testi: 'minuscolo' (quasi tutto in minuscolo: le maiuscole sono
    codici o icone), 'maiuscolo', 'normale', oppure 'non applicabile' (pochi testi, o scritture senza maiuscole
    come cinese, giapponese, coreano, thai). Identificatori e segnaposto non contano."""
    validi, lettere_casi, lettere = [], 0, 0
    for t in testi:
        if not isinstance(t, str) or _IDENTIFICATIVO.match(t.strip()):
            continue
        t = _senza_segnaposto(t)
        lettere += sum(1 for c in t if c.isalpha())
        con_caso = [c for c in t if c.isalpha() and c.lower() != c.upper()]
        lettere_casi += len(con_caso)
        if con_caso:
            validi.append(t)
    if len(validi) < 30 or lettere_casi < 0.5 * max(1, lettere):
        return "non applicabile"
    senza_minuscole = sum(1 for t in validi if not any(c.islower() for c in t))
    if senza_minuscole >= 0.95 * len(validi):
        return "maiuscolo"
    misti = sum(1 for t in validi if _parole_miste(t))
    if misti <= 0.05 * len(validi) and senza_minuscole <= 0.05 * len(validi):
        return "minuscolo"
    return "normale"


def codici_maiuscoli(testi, mappa=None, punto=None):
    """Nei giochi in minuscolo, le parole tutte maiuscole (al massimo 4 lettere, in almeno 3 testi) sono codici o
    icone del font (Forager: 'EP' = icona XP, 'H' = cuore). Si tengono solo quelle che il font a sprite disegna
    come maiuscole (le sue icone): senza la mappa del font non si puo' sapere, e non si protegge nulla."""
    if not mappa:
        return []
    icone = {c for c in mappa if c.isupper()}
    conta = {}
    for t in testi:
        if not isinstance(t, str) or _parole_miste(_senza_segnaposto(t)):
            continue
        for w in set(_PAROLA.findall(_senza_segnaposto(t))):
            if w.isupper() and len(w) <= 4 and w != punto:
                conta[w] = conta.get(w, 0) + 1
    return sorted(w for w, n in conta.items() if n >= 3 and set(w) <= icone)


def in_minuscolo(tradotto, originale, codici=(), lingua_dest="it"):
    """La traduzione con le maiuscole come le scrive il gioco: per un testo il cui originale e' in minuscolo,
    tutte le lettere in minuscolo tranne i codici dell'originale (icone) e i segnaposto. I testi con maiuscole
    normali (messaggi di sistema) restano come sono."""
    from .testo import _SEGNAPOSTO
    if _parole_miste(_senza_segnaposto(originale)):
        return tradotto
    presenti = [c for c in codici if re.search(r"(?<![^\W\d_])%s(?![^\W\d_])" % re.escape(c), originale)]
    protetti = [_SEGNAPOSTO.pattern]
    if presenti:
        protetti.append(r"(?<![^\W\d_])(?:%s)(?![^\W\d_])" % "|".join(map(re.escape, presenti)))
    turco = lingua_dest.split("-")[0] in ("tr", "az")
    uscita, ultimo = [], 0
    for m in re.finditer("|".join("(?:%s)" % x for x in protetti), tradotto):
        uscita.append(tradotto[ultimo:m.start()])
        uscita.append(None)
        uscita.append(m.group())
        ultimo = m.end()
    uscita.append(tradotto[ultimo:])
    pezzi, proteggi = [], False
    for x in uscita:
        if x is None:
            proteggi = True
            continue
        if proteggi:
            pezzi.append(x)
            proteggi = False
        else:
            pezzi.append(x.replace("I", "\u0131").replace("\u0130", "i").lower() if turco else x.lower())
    return "".join(pezzi)


# --- larghezza delle righe: quanto spazio danno al testo le versioni del gioco ----------------------------------

def font_dedicati(dati_win, codici):
    """Lingue che hanno un font tutto loro (dal nome: fntGerman, fntRussian...): le loro righe hanno un'altra
    larghezza e non servono a misurare lo spazio del testo."""
    try:
        elenco, _ = gamemaker.font(dati_win)
    except (gamemaker.ErroreGameMaker, struct.error, ValueError, IndexError):
        return set()
    return {c for c in codici for f in elenco if _font_della_lingua(f.get("nome"), c)}


def _altra_scrittura_testo(testo):
    lettere = [c for c in testo if c.isalpha()]
    return bool(lettere) and sum(1 for c in lettere if _altro_alfabeto(c)) > 0.3 * len(lettere)


def limiti_righe(dati, altri, segno):
    """{chiave: [larghezza, righe, uguali]} per ogni testo: la riga piu' lunga (in caratteri) e il numero massimo
    di righe tra l'originale e le traduzioni ufficiali che usano lo stesso font ('altri': i loro dizionari), e
    quante traduzioni ufficiali lasciano il testo uguale all'originale (es. 'vsync:')."""
    uscita = {}
    if not isinstance(dati, dict):
        return uscita
    for k, v in dati.items():
        if not isinstance(v, str) or not testo_localizzazione(v):
            continue
        versioni = [v] + [a.get(k) for a in altri if isinstance(a, dict) and isinstance(a.get(k), str)
                          and a.get(k).strip() and not _altra_scrittura_testo(a.get(k))]
        righe = [_righe_di(x, segno) for x in versioni]
        larghezza = max((len(r) for rr in righe for r in rr), default=0)
        uguali = sum(1 for x in versioni[1:] if x.strip().casefold() == v.strip().casefold())
        uscita[k] = [larghezza, max((len(rr) for rr in righe), default=1), uguali]
    return uscita


def nome_lingua_nuova(destinazione, cartella, estensione=".json"):
    """Nome del file della lingua da aggiungere (es. 'italiano.json'): il nome della lingua com'e' nella lingua
    stessa, che il gioco mostra nel suo menu. None se un file con quel nome c'e' gia' e non e' di PatchSmith."""
    nome = (lingua.endonimo(destinazione) or destinazione).split(" (")[0].strip().lower()
    nome = re.sub(r"[^\w]+", "_", nome).strip("_") or destinazione
    file = nome + estensione
    if os.path.exists(os.path.join(cartella, file)) and file not in creati_da_patchsmith(cartella):
        return None
    return file


def caratteri_font_sprite(cartella):
    """Caratteri del font a sprite principale del gioco (la mappa piu' lunga), oppure None se non ce n'e'."""
    dati = file_dati(cartella)
    if not dati:
        return None
    try:
        mappe = gamemaker.mappe_font_sprite(os.path.dirname(dati))
    except (gamemaker.ErroreGameMaker, OSError, ValueError, IndexError, struct.error):
        return None
    if not mappe:
        return None
    return max((m for _, m in mappe), key=len)
