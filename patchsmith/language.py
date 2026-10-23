"""Lingue: nomi leggibili, lingua di Windows e riconoscimento automatico della lingua del gioco.

Il riconoscimento funziona senza rete:
- prima guarda l'alfabeto (kana -> giapponese, hangul -> coreano, ideogrammi -> cinese, cirillico...);
- per le lingue con alfabeto latino conta le parole piu' frequenti (articoli, preposizioni...).
"""
import locale
import re
import sys

AUTO = "auto"

# Codici di Google Translate con il nome in italiano.
NOMI = {
    "en": "Inglese", "it": "Italiano", "ja": "Giapponese", "zh-CN": "Cinese semplificato",
    "zh-TW": "Cinese tradizionale", "ko": "Coreano", "fr": "Francese", "de": "Tedesco", "es": "Spagnolo",
    "pt": "Portoghese", "ru": "Russo", "pl": "Polacco", "tr": "Turco", "nl": "Olandese", "sv": "Svedese",
    "da": "Danese", "no": "Norvegese", "fi": "Finlandese", "cs": "Ceco", "hu": "Ungherese", "ro": "Rumeno",
    "el": "Greco", "uk": "Ucraino", "ar": "Arabo", "he": "Ebraico", "th": "Thailandese", "vi": "Vietnamita",
    "id": "Indonesiano", "hi": "Hindi",
}
ETICHETTA_AUTO = "Rileva automaticamente"

# Nomi delle lingue scritti nella loro lingua: nei menu di scelta della lingua non vanno tradotti.
ENDONIMI = {
    "English", "Español", "Español (Latinoamérica)", "Español - Latinoamérica", "Français", "Deutsch", "Italiano",
    "Português", "Português - Brasil", "Português (Brasil)", "Português Brasileiro", "Русский", "Українська",
    "Polski", "Türkçe", "Nederlands", "Svenska", "Dansk", "Norsk", "Suomi", "Čeština", "Magyar", "Română",
    "Ελληνικά", "日本語", "한국어", "简体中文", "繁體中文", "中文", "中文(简体)", "中文(繁體)", "ภาษาไทย",
    "Tiếng Việt", "Bahasa Indonesia", "العربية", "עברית", "हिन्दी",
}


# Nome di ogni lingua nella lingua stessa (per la voce del menu lingue che ora mostra la traduzione).
ENDONIMO = {
    "en": "English", "it": "Italiano", "fr": "Français", "de": "Deutsch", "es": "Español", "pt": "Português",
    "ru": "Русский", "pl": "Polski", "ja": "日本語", "ko": "한국어", "zh-CN": "简体中文", "zh-TW": "繁體中文",
    "nl": "Nederlands", "tr": "Türkçe", "sv": "Svenska", "da": "Dansk", "no": "Norsk", "fi": "Suomi",
    "cs": "Čeština", "hu": "Magyar", "ro": "Română", "el": "Ελληνικά", "uk": "Українська",
}


def chiave_confronto(codice):
    """Chiave per confrontare due lingue: la lingua base ('pt-BR' -> 'pt'), ma il cinese per scrittura
    ('zh-TW', 'zh-HK', 'zh-Hant' -> 'zh-hant'; 'zh-CN', 'zh-SG', 'zh-Hans', 'zh' -> 'zh-hans'):
    tradizionale e semplificato sono lingue scritte diverse e non si scambiano."""
    c = (codice or "").strip().replace("_", "-").lower()
    base = c.split("-")[0]
    if base in ("zh", "chinese", "schinese", "tchinese"):
        if c == "tchinese" or any(p in ("tw", "hk", "mo", "hant") for p in c.split("-")[1:]):
            return "zh-hant"
        return "zh-hans"
    return base


def stessa_lingua(a, b):
    return chiave_confronto(a) == chiave_confronto(b)


def migliore(candidati, destinazione):
    """Il codice tra 'candidati' che corrisponde meglio a 'destinazione': lo stesso codice, poi la lingua senza
    regione ('it', 'zh-Hans'), poi un'altra variante della stessa lingua (mai l'altra scrittura del cinese).
    None se nessuno corrisponde."""
    dest = (destinazione or "").strip().replace("_", "-").lower()
    chiave = chiave_confronto(dest)
    voti = []
    for c in candidati:
        n = (c or "").strip().replace("_", "-").lower()
        if not n or chiave_confronto(n) != chiave:
            continue
        rango = 0 if n == dest else 1 if n in (chiave, dest.split("-")[0]) else 2
        voti.append((rango, n, c))
    return min(voti)[2] if voti else None


def endonimo(codice):
    return ENDONIMO.get(codice) or ENDONIMO.get((codice or "").split("-")[0])


def etichetta(codice):
    """'Italiano (it)'; 'Rileva automaticamente' per auto; il codice stesso se sconosciuto."""
    if codice == AUTO:
        return ETICHETTA_AUTO
    return "%s (%s)" % (NOMI[codice], codice) if codice in NOMI else codice


def etichette(con_auto=False):
    principali = ["en", "it", "ja", "zh-CN", "ko", "fr", "de", "es", "pt", "ru"]
    altre = sorted((c for c in NOMI if c not in principali), key=lambda c: NOMI[c])
    return ([ETICHETTA_AUTO] if con_auto else []) + [etichetta(c) for c in principali + altre]


def codice(testo):
    """Da etichetta ('Italiano (it)'), nome ('italiano') o codice ('it') al codice."""
    t = (testo or "").strip()
    if not t or t.lower() in (ETICHETTA_AUTO.lower(), AUTO):
        return AUTO
    m = re.search(r"\(([A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?)\)\s*$", t)
    if m:
        return m.group(1)
    for c, nome in NOMI.items():
        if t.lower() in (nome.lower(), c.lower()):
            return c
    return t


def lingua_di_windows(predefinita="it"):
    """Codice della lingua dell'interfaccia di Windows (o del sistema), es. 'it'."""
    nome = None
    if sys.platform.startswith("win"):
        try:
            import ctypes
            nome = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage())
        except (AttributeError, OSError):
            nome = None
    if not nome:
        nome = (locale.getlocale()[0] or "")
    nome = nome.replace("-", "_")
    if nome.lower().startswith("zh"):
        return "zh-TW" if nome.upper().endswith(("TW", "HK", "MO")) else "zh-CN"
    primario = nome.split("_")[0].lower()
    if primario == "nb" or primario == "nn":
        primario = "no"
    return primario if primario in NOMI else predefinita


# --- riconoscimento -----------------------------------------------------------------------------

_SCRITTURE = [  # (lingua, intervalli di caratteri)
    ("ko", [(0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F)]),
    ("kana", [(0x3040, 0x30FF), (0x31F0, 0x31FF)]),
    ("han", [(0x4E00, 0x9FFF), (0x3400, 0x4DBF)]),
    ("cirillico", [(0x0400, 0x04FF)]),
    ("el", [(0x0370, 0x03FF)]),
    ("ar", [(0x0600, 0x06FF)]),
    ("he", [(0x0590, 0x05FF)]),
    ("th", [(0x0E00, 0x0E7F)]),
    ("hi", [(0x0900, 0x097F)]),
]

_PAROLE = {
    "en": "the and of to a in is you that it for on with as are this be your or not can will have from at by "
          "all an if no was what into out up",
    "it": "il di che e la per un una non è in del della le si con sono al alla gli lo da ma come più questo "
          "tuo tua nel nella dei delle anche",
    "fr": "le la les de des et un une est vous pas pour que en du dans sur avec ce qui votre au plus il elle "
          "sont ne",
    "de": "der die das und ist nicht ein eine zu den mit sie es du ich von auf für dem sich auch wird im "
          "werden",
    "es": "el la los las de y que en un una es no para con por se del al lo tu su más está como pero",
    "pt": "o a os as de e que em um uma é não para com do da por se no na você seu sua mais como",
    "nl": "de het een en van is niet dat je op te met voor zijn ook dit naar",
    "pl": "i w na nie z się to jest że do o jak ale co czy jego tak po",
    "tr": "ve bir bu da de için ile ne çok değil gibi olarak daha mi",
    "sv": "och att det som en är på för med inte har till av den om",
}
_PAROLE = {k: set(v.split()) for k, v in _PAROLE.items()}
# parole frequenti di una lingua che negli elenchi di _PAROLE stanno solo tra quelle di altre lingue: in un testo
# italiano "se", "tu", "su", "no" non vogliono dire che e' spagnolo
ANCHE_DI = {"it": {"a", "o", "i", "no", "se", "su", "tu", "ne", "te", "mi", "ce", "sua", "do", "po", "ve", "qui"}}


def lingua_evidente(testo, attesa):
    """La lingua (codice di _PAROLE) di cui il testo ha chiaramente le parole frequenti al posto di quelle della lingua
    'attesa': almeno 2, e piu' di quelle di 'attesa'. None se non si vede un'altra lingua (anche per i testi corti
    senza parole frequenti: il dubbio non basta). Una parola che e' anche della lingua attesa ("la", "in", "se")
    conta solo per lei."""
    parole = re.findall(r"[^\W\d_]+", testo.lower())
    arrivo = attesa.split("-")[0]
    proprie = _PAROLE.get(arrivo, set()) | ANCHE_DI.get(arrivo, set())
    punti = {codice: sum(1 for p in parole if p in insieme and (codice == arrivo or p not in proprie))
             for codice, insieme in _PAROLE.items()}
    migliore = max(punti, key=punti.get)
    if migliore != arrivo and punti[migliore] >= 2 and punti[migliore] > punti.get(arrivo, 0):
        return migliore
    return None


def _scrittura(c):
    o = ord(c)
    for nome, intervalli in _SCRITTURE:
        for a, b in intervalli:
            if a <= o <= b:
                return nome
    return "latino" if c.isalpha() else None


def rileva(testi, massimo=200_000):
    """(codice, descrizione) della lingua prevalente nei testi. ('en', ...) se non si capisce."""
    campione = "\n".join(testi)[:massimo]
    conteggi = {}
    for c in campione:
        s = _scrittura(c)
        if s:
            conteggi[s] = conteggi.get(s, 0) + 1
    lettere = sum(conteggi.values())
    if not lettere:
        return "en", "nessun testo: uso l'inglese"

    kana, han = conteggi.get("kana", 0), conteggi.get("han", 0)
    if (kana + han) / lettere > 0.3:
        if kana > 0.05 * (kana + han):
            return "ja", "caratteri giapponesi (kana)"
        return "zh-CN", "ideogrammi cinesi"
    for lingua in ("ko", "el", "ar", "he", "th", "hi"):
        if conteggi.get(lingua, 0) / lettere > 0.3:
            return lingua, "alfabeto"
    if conteggi.get("cirillico", 0) / lettere > 0.3:
        ucraino = sum(campione.count(c) for c in "ієїґІЄЇҐ")
        return ("uk" if ucraino > 0.005 * conteggi["cirillico"] else "ru"), "alfabeto cirillico"

    parole = re.findall(r"[^\W\d_]+", campione.lower())
    if not parole:
        return "en", "testo non riconosciuto: uso l'inglese"
    punteggi = {lingua: sum(1 for p in parole if p in insieme) for lingua, insieme in _PAROLE.items()}
    migliore = max(punteggi, key=punteggi.get)
    quota = punteggi[migliore] / len(parole)
    if quota < 0.03:
        return "en", "parole non riconosciute: uso l'inglese"
    secondo = sorted(punteggi.values())[-2]
    sicurezza = "alta" if punteggi[migliore] > 1.5 * max(secondo, 1) else "media"
    return migliore, "parole frequenti (affidabilità %s)" % sicurezza
