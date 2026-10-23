"""GameMaker adapter: translates the game's external localization files (e.g. Dokimon: Localization/*.txt).

Many GameMaker games read their texts from files included next to the executable, one per language
(Localization/english_ui.txt, Localization/french/ui.txt...). PatchSmith:
1. finds the files of the original language in the "lines with id" format (adapter_righe);
2. translates them (translation memory + Google, with codes like /name/ and /n/ protected);
3. adapts the characters to those of the game's fonts (read from data.win: in GameMaker a character that
   the font lacks becomes a small square), for Italian ì -> í, ò -> ó, ù -> ú if the font has the acute ones;
4. repairs what the game would read wrongly (an extra "/" is taken as the start of a code);
5. writes them back in place with a verified backup of the originals (as for web games).
In the game the language of the translated files stays selected (usually English).

Other recognized formats: JSON files per language (local/english.json, also with sections, // comments and trailing
commas, and with the code attached to the name as in localization/stringsEN.json) and CSV tables with one column per
language (translations.csv: id,english,french...; see adapter_csv).
Limits: texts written in the game's code (data.win) are left untranslated; other localization formats are not
covered.
"""
import os
import re
import struct

from . import adapter_lines as adapter_righe, gamemaker, language as lingua
from .characters import ASCII

# full language names (valid anywhere in the path) and short codes (valid only in safe positions)
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
# words that go with the language in a file name (chinese_traditional, portuguese_br): they are part of the language
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
# files that are not game texts even if they look like them
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


# language code attached at the end of the name, in uppercase after a lowercase letter (Another Door: stringsEN.json,
# stringsIT.json, stringsJP.json, stringsBR.json, stringsSC.json): also the codes used only this way
_ATTACCATO = re.compile(r"^(.*[a-z])([A-Z]{2,4})$")
_CODICI_ATTACCATI = {"br": "pt", "ptbr": "pt", "sc": "zh", "chs": "zh", "tc": "zh-TW", "cht": "zh-TW", "tw": "zh-TW"}


def _attaccato(parola):
    """(radice, codice) if the word (with its original capitalization) ends with an attached language code
    (stringsEN -> ('strings', 'en')), otherwise None."""
    m = _ATTACCATO.match(parola or "")
    if not m:
        return None
    codice = _CODICI_ATTACCATI.get(m.group(2).lower()) or _codice_breve(m.group(2).lower())
    return (m.group(1), codice) if codice else None


def _ultima_parola(rel):
    """The last part of the file name (without extension), with the original capitals: strings_menuEN -> menuEN."""
    nome = rel.split("/")[-1].rsplit(".", 1)[0]
    pezzi = [p for p in re.split(r"[\s_.\-]+", nome) if p]
    return pezzi[-1] if pezzi else ""


def lingua_del_percorso(rel):
    """Code of the language indicated by the path, or None.

    Full names (english, french...) are valid anywhere; short codes (en, it, pt...) only if they are
    a folder name, the whole file name, or the first/last part of the name (ui_it, en_ui), also
    attached in uppercase at the end of the name (stringsEN)."""
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
    """The path with '{L}' in place of the language: 'local/{L}.json', 'lang/{L}/ui.json', '{L}_credits.json'.
    Files of the same family in different languages are translations of each other."""
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
    for n, i in enumerate(parole):  # qualifiers next to the language: chinese_traditional, portuguese_br
        vicini = [pezzi[parole[m]] for m in (n - 1, n + 1) if 0 <= m < len(parole)]
        if pezzi[i] in _QUALIFICATORI and "{L}" in vicini:
            pezzi[i] = "{L}"
    nome = re.sub(r"\{L\}(?:[\s_.\-]+\{L\})+", "{L}", "".join(pezzi))
    return "/".join(cartelle + [nome + punto + estensione])


def leggi_originale(percorso, lettore):
    """The data of the file as it originally was: from PatchSmith's backup if there is one and it is valid (the file
    may already be translated), otherwise from the file itself. 'lettore' reads a path."""
    from . import project as progetto
    backup = percorso + progetto.SUFFISSO_BACKUP
    if os.path.exists(backup):
        info = progetto.leggi_info(percorso)
        if info and progetto._hash_file(backup) == info["hash_originale"]:
            return lettore(backup)
    return lettore(percorso)


def file_localizzazione(cartella):
    """{relativo: language or None} of the files in the 'lines with id' format (read-only)."""
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
            # a file with the language in its name (english_ui.txt) only needs a few texts
            if adapter_righe.sembra_localizzazione(p, minimo=2 if lingua_file else 5):
                trovati[rel] = lingua_file
    return trovati


def scegli_sorgenti(trovati, origine="auto", cartella=None):
    """(files to translate, languages present, language of the chosen files).

    The files of the original language are translated; if they are spread over several folders the one with
    the most is kept (e.g. Localization/), so isolated files elsewhere are not touched."""
    lingue = {l for l in trovati.values() if l}
    base = (origine or "auto").split("-")[0].lower()
    if base == "auto":
        base = "en" if "en" in lingue else None
    sorgenti = sorted(r for r, l in trovati.items() if l == base) if base else []
    if not sorgenti:
        # files without a language in the name next to the translations (e.g. ui.txt + french/ui.txt): they are
        # the original
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


# scripts other than Latin: a font with only these besides ASCII is for other languages (e.g. Korean)
def _altro_alfabeto(c):
    o = ord(c)
    return 0x3040 <= o <= 0x30FF or 0x4E00 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7A3 or 0x0400 <= o <= 0x04FF \
        or 0x3400 <= o <= 0x4DBF or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F


def _latino_accentato(c):
    return 0xC0 <= ord(c) <= 0x24F and c.isalpha()


# fonts that a library adds to the game as a fallback (Scribble: scribble_fallback_font, ASCII only)
_FONT_DI_RIPIEGO = re.compile(r"fallback", re.I)


def file_dati(cartella):
    """Path of the game's data.win (the least deep one), or None."""
    radici = gamemaker.radici(cartella)
    for r in radici:
        for nome in gamemaker.NOMI_DATI:
            if os.path.exists(os.path.join(r, nome)):
                return os.path.join(r, nome)
    return None


def _font_della_lingua(nome_font, codice):
    """True if the font name says it is for the language 'codice' (e.g. fnt_english, font_en)."""
    if not codice or not nome_font:
        return False
    base = codice.split("-")[0].lower()
    pezzi = [p for p in re.split(r"[^a-z]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", nome_font).lower()) if p]
    return any(p in NOMI_LINGUE.get(base, ()) or p in CODICI_LINGUE.get(base, ()) for p in pezzi)


def caratteri_font(cartella, log=None, ufficiali=None, sorgente=None):
    """Characters that the game's text fonts (data.win) can draw, or None if not known.

    - fonts with at least 20 Latin letters count, except those for another alphabet: besides ASCII they mostly
      have another script (e.g. a Korean or Japanese font with a few accented letters: at least 100 characters
      of the other script and more than 10 times the accented letters), and except the fallback fonts of
      libraries (scribble_fallback_font: ASCII only, the game does not use it for its texts);
    - 'ufficiali': accented letters that the game's official translations use (e.g. é, ü, ç). If there are
      at least 3, a font that has none of them is not used for the translated texts (chat font, numbers font,
      font of another script such as Thai) and does not count, unless its name says it is the font of the
      'sorgente' language (e.g. fnt_english: it is the one that will draw the translation). A font with a few
      accented letters does count instead: it can be the one for the original language's texts, with only the
      letters they use (Another Door: ftPixeloidSans has exactly the Á ç é ô of the English file), and a letter
      it lacks would be a small square;
    - an uppercase-only font (the game puts text in uppercase) also works for lowercase letters;
    - if some font cannot be read, to be safe ASCII only;
    - the small square U+25AF (missing character) does not count as a drawable character."""
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
            continue  # font for another language (e.g. Korean, or Japanese with ç ê ñ Œ œ)
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


# --- JSON localization files (e.g. Forager: local/english.json, local/french.json...) -------------

_MINIMO_VOCI_JSON = 20


def _leggi_json(percorso):
    from .adapter_webjson import FileJSON
    return FileJSON(percorso).dati


def _valori(dati):
    """The values of a JSON, also inside sections and lists of texts, in file order. A list of objects
    ([{"id": "sword", "name": "Sword"}]) is a data file, not a language file: ValueError (its identifiers, such as
    'sword', would no longer be found once translated)."""
    sezione = isinstance(dati, dict)
    for v in (dati.values() if sezione else dati):
        if isinstance(v, (dict, list)):
            if not sezione:
                raise ValueError("elenco di oggetti")
            yield from _valori(v)
        else:
            yield v


def _stringhe(dati):
    """String values of a {chiave: testo} dictionary, also split into sections ({"menu": {"_play": "Play"...}}) or
    with lists of texts: at least _MINIMO_VOCI_JSON texts and 80% of the values (None if the file does not have this
    shape)."""
    if not isinstance(dati, dict):
        return None
    try:
        tutti = list(_valori(dati))
    except (RecursionError, ValueError):
        return None
    valori = [v for v in tutti if isinstance(v, str)]
    return valori if len(valori) >= max(_MINIMO_VOCI_JSON, 0.8 * len(tutti)) else None


def chiave_di(percorso):
    """The key of a text in a localization file: the file's own key ('_play'), or for a text inside a section the
    keys joined with '.' ('menu._play')."""
    return ".".join(str(x) for x in percorso)


def piatto(dati):
    """{chiave: testo} of the texts of a localization file (see chiave_di): for a flat dictionary it is the
    dictionary itself (only the string values)."""
    from .adapter_webjson import foglie
    return {chiave_di(p): v for p, _, v in foglie(dati)} if isinstance(dati, dict) else {}


def al_percorso(dati, percorso):
    """The value of 'dati' at position 'percorso' (tuple of keys and indexes), or None if it is not there."""
    for passo in percorso:
        try:
            dati = dati[passo]
        except (KeyError, IndexError, TypeError):
            return None
    return dati


def creati_da_patchsmith(cartella):
    """Names of the files that PatchSmith created in this folder (e.g. the new language local/italiano.json), read
    from the backup information: they are not game files. A file created and then replaced by someone else (e.g. an
    official italiano.json that came with an update: fingerprint different from the recorded one) no longer counts."""
    from . import project as progetto
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
    """{relativo: lingua} of the JSON localization files: {chiave: testo} dictionaries (also split into sections, and
    also with // comments and trailing commas, which many games accept) with the language in the path, present in at
    least two languages in the same family (local/{L}.json, lang/{L}/ui.json, localization/strings{L}.json;
    read-only). Files created by PatchSmith (the added language) do not count. With 'log': the language files that
    cannot be read."""
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
    """Accented Latin letters used by the game's official translations (the other language files)."""
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


# in a localization file every value is an interface text, even a single word ("wood"); only the identifiers left in
# place of a missing text are not ("Structure.NuclearFurnace", "menu_title_2")
_IDENTIFICATIVO = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:[.:/\\][A-Za-z0-9_]+)+$|^[a-z0-9]+(?:_[a-z0-9]+)+$")


def testo_localizzazione(valore):
    v = valore.strip() if isinstance(valore, str) else ""
    return bool(re.search(r"[^\W\d_]", v)) and not _IDENTIFICATIVO.match(v)


def schemi_json(dati):
    """The keys with text of a localization file: they are all translated (they are interface texts; the rules on
    the "technical" keys of generic JSON files do not apply here)."""
    from .adapter_webjson import foglie
    return sorted({schema for _, schema, v in foglie(dati) if testo_localizzazione(v)})


_ASTERISCO_TRA_PAROLE = re.compile(r"[^\W\d_]\*+[^\W\d_]")  # between two letters: not emphasis ("*sigh*")


def _confermato(altri, posti, prova, forte, debole):
    """True if the official translations confirm the convention: at least one language almost always follows it
    ('prova(valore, posto)' true in at least 'forte' of the places it has) and at least half of the languages follow
    it at least in part ('debole'): translators remove it where it is not needed (e.g. a word that fits on one line)."""
    quote = []
    for a in altri:
        validi = [p for p in posti if isinstance(a.get(p[0]), str)]
        if validi:
            quote.append(sum(1 for p in validi if prova(a[p[0]], p)) / len(validi))
    return bool(quote) and max(quote) >= forte and sum(1 for q in quote if q >= debole) >= len(altri) / 2


def markup(dati, altri):
    """Writing conventions of the source file's texts, confirmed by the majority of the official translations
    ('altri': the other dictionaries of the same folder): {'a_capo': '*' if the asterisk acts as a line break (e.g.
    "iron*ore"), 'punto_elenco': letter used as a symbol at the start of a paragraph (e.g.
    "U unlocks...**U gain...")}."""
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
    occorrenze = {}  # letter -> [(key, paragraph number)]
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
    """Paragraphs grouped by sentence (lists of indexes). The double marker separates paragraphs but in many games
    it also splits a sentence in the middle (Forager: "U items discarded from your**inventory are instantly**sold
    instead"): translated on their own, the pieces lose their meaning. A paragraph continues the previous one if it
    has no bullet symbol, starts with a lowercase letter and the previous one does not end the sentence with
    punctuation; the previous one must be a bullet item or have at least three words (a title such as
    "Controls**move with WASD" stays on its own)."""
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
    """The text as it is translated: the 'segno' line breaks become spaces (the sentence is translated whole),
    paragraphs (double marker) become lines separated by a blank line, joining the pieces of the same sentence; the
    symbol at the start of a paragraph is removed."""
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
    """Puts the original's line breaks back into the translated text: the same number (or fewer, if there are not
    enough words), in proportion to the lengths of the original lines, always between two words. 'segno' is the
    line break marker, or the list of the original separators between one segment and the next (line breaks and
    paragraphs of the same sentence)."""
    separatori = [segno] * (len(segmenti) - 1) if isinstance(segno, str) else list(segno)
    n = len(segmenti) - 1
    parole = testo.split(" ")
    if n <= 0 or len(parole) < 2:
        return testo
    n = min(n, len(parole) - 1)
    totale_o = sum(len(s) for s in segmenti) or 1
    posizioni, cumulato = [], 0  # position in the text of each space between words
    for parola in parole[:-1]:
        cumulato += len(parola)
        posizioni.append(cumulato)
        cumulato += 1
    tagli, inizio, fatto = [], 0, 0
    for i in range(1, n + 1):
        fatto += len(segmenti[i - 1])
        obiettivo = fatto / totale_o * len(testo)
        rimasti = n - i  # cuts still to make after this one: each leaves at least one space
        scelte = range(inizio, len(posizioni) - rimasti)
        k = min(scelte, key=lambda j: abs(posizioni[j] - obiettivo))
        tagli.append(k)
        inizio = k + 1
    righe, da = [], 0
    for k in tagli:
        righe.append(" ".join(parole[da:k + 1]))
        da = k + 1
    righe.append(" ".join(parole[da:]))
    if n < len(separatori):  # fewer words than line breaks: paragraph separators (the longest) are kept first
        tenuti = sorted(sorted(range(len(separatori)), key=lambda j: -len(separatori[j]))[:n])
        separatori = [separatori[j] for j in tenuti]
    return righe[0] + "".join(sep + r for sep, r in zip(separatori, righe[1:]))


def ricomponi_a_capo(originale, tradotto, segno=None, punto=None, larghezza=None, righe=None):
    """The translated (normalized) text brought back to the game's conventions, with the original's paragraphs,
    paragraph-start symbol and line breaks. None if the paragraphs do not match (the original stays).
    With 'larghezza' and 'righe' (the longest line and the number of lines of the game's versions of this text):
    if a line exceeds the width, the line breaks are redistributed, adding lines up to 'righe'."""
    risultato = _ricomponi(originale, tradotto, segno, punto)
    if risultato is None or not segno or not larghezza or _riga_piu_lunga(risultato, segno) <= larghezza:
        return risultato
    return _entro_larghezza(risultato, segno, punto, larghezza, righe or 1)


def _righe_di(testo, segno):
    """The non-empty lines of the text as the game draws them (line break = segno or '\\n')."""
    pezzi = re.split("%s|\n" % re.escape(segno), testo) if segno else testo.split("\n")
    return [x.strip() for x in pezzi if x.strip()]


def _riga_piu_lunga(testo, segno):
    return max((len(x) for x in _righe_di(testo, segno)), default=0)


def _taglio_minimo(parole, k, prima=0):
    """The words on k lines so that the longest line is as short as possible (dynamic programming).
    'prima': characters already taken at the start of the first line (e.g. the symbol 'U ').
    Returns (indexes of the words that start each line after the first, length of the longest line)."""
    n = len(parole)
    k = max(1, min(k, n))
    lunghezze = [0]
    for w in parole:
        lunghezze.append(lunghezze[-1] + len(w))

    def lunga(i, j):  # words i..j-1 on one line
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
    """Redistributes the line breaks of paragraphs that are too wide (only those with simple line breaks only: the
    game's paragraphs and blank lines stay where they are), adding lines as long as the total does not exceed
    'righe'."""
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
            continue  # paragraphs or blank lines inside: left alone
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
                   for g in _gruppi_frase(paragrafi, segno, punto)]  # the pieces of the same sentence, together
    paragrafi_t = tradotto.split("\n\n") if len(paragrafi_o) > 1 else [tradotto]
    if len(paragrafi_o) != len(paragrafi_t) or any("\n" in t for t in paragrafi_t):
        return None
    uscita = []
    bordo = r"[\s%s]*" % re.escape(segno) if segno else r"\s*"
    for o, t in zip(paragrafi_o, paragrafi_t):
        prefisso = _prefisso_punto(o, punto)
        corpo = o[len(prefisso):]
        # leading and trailing line breaks and spaces stay fixed ("*attacks burn!" , "your time: ")
        testa, interno, coda = re.match(r"(%s)(.*?)(%s)$" % (bordo, bordo), corpo, re.S).groups()
        t = re.sub(r" {2,}", " ", t.strip())
        if segno and segno in t:
            return None  # the translator left a marker: I do not know where it would go
        if not t and interno.strip():
            return None  # paragraph lost in translation
        if segno and t:
            # single line breaks and paragraphs of the sentence; "* *" (blank line with a space) is one separator
            divisi = re.split("(%s(?:\\s*%s)*)" % (re.escape(segno), re.escape(segno)), interno)
            t = _a_capo_bilanciato(t, divisi[0::2], divisi[1::2])
        uscita.append(prefisso + testa + t + coda)
    return (separatore or "").join(uscita)


_CODICE = re.compile(r"/[A-Za-z_]{1,16}/")


def ripara(originale, tradotto):
    """Translation that the game reads like the original, or None.

    In GameMaker many games (e.g. Dokimon) expand codes between slashes (/name/, /n/): an extra slash
    is taken as the start of a code and the rest of the line disappears; a code right at the end of
    a dialogue line is not closed. So: the same codes as the original, no extra slash
    (it becomes '-'), and a space after a code that ends the line."""
    if sorted(_CODICE.findall(originale)) != sorted(_CODICE.findall(tradotto)):
        return None
    barre_originale = _CODICE.sub("", originale).count("/")
    pezzi = _CODICE.split(tradotto)
    codici = _CODICE.findall(tradotto)
    extra = sum(p.count("/") for p in pezzi) - barre_originale
    if extra > 0:
        pezzi = [p.replace("/", "-") for p in pezzi]
        if barre_originale:  # I do not know which slashes to keep: better the original
            return None
    testo = pezzi[0] + "".join(c + p for c, p in zip(codici, pezzi[1:]))
    righe_o, righe_t = originale.split("\n"), testo.split("\n")
    if len(righe_o) == len(righe_t):
        righe_t = [t + " " if t.endswith("/") and not o.endswith("/") else t for o, t in zip(righe_o, righe_t)]
        testo = "\n".join(righe_t)
    return testo


# --- capitals: games written all in lowercase, with capitals used as icons (Forager) ----------------------

_PAROLA = re.compile(r"[^\W\d_]+")


def _parole_miste(testo):
    """True if the text has a word with both uppercase and lowercase letters (Hello, CamelCase)."""
    return any(not w.isupper() and not w.islower() for w in _PAROLA.findall(testo))


def _senza_segnaposto(testo):
    from .text import _SEGNAPOSTO
    return _SEGNAPOSTO.sub(" ", testo)


def stile_maiuscole(testi):
    """How the game uses capitals in its texts: 'minuscolo' (almost all lowercase: capitals are codes or icons),
    'maiuscolo', 'normale', or 'non applicabile' (few texts, or scripts without capitals such as Chinese,
    Japanese, Korean, Thai). Identifiers and placeholders do not count."""
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
    """In lowercase games, all-uppercase words (at most 4 letters, in at least 3 texts) are codes or font icons
    (Forager: 'EP' = XP icon, 'H' = heart). Only those that the sprite font draws as capitals (its icons) are kept:
    without the font map there is no way to know, and nothing is protected."""
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
    """The translation with capitals as the game writes them: for a text whose original is in lowercase, all
    letters in lowercase except the original's codes (icons) and the placeholders. Texts with normal capitals
    (system messages) stay as they are."""
    from .text import _SEGNAPOSTO
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


# --- line width: how much space the game's versions give to the text -------------------------------------------

def font_dedicati(dati_win, codici):
    """Languages that have a font of their own (from the name: fntGerman, fntRussian...): their lines have a
    different width and cannot be used to measure the text's space."""
    try:
        elenco, _ = gamemaker.font(dati_win)
    except (gamemaker.ErroreGameMaker, struct.error, ValueError, IndexError):
        return set()
    return {c for c in codici for f in elenco if _font_della_lingua(f.get("nome"), c)}


def _altra_scrittura_testo(testo):
    lettere = [c for c in testo if c.isalpha()]
    return bool(lettere) and sum(1 for c in lettere if _altro_alfabeto(c)) > 0.3 * len(lettere)


def limiti_righe(dati, altri, segno):
    """{chiave: [larghezza, righe, uguali]} for each text: the longest line (in characters) and the maximum number
    of lines among the original and the official translations that use the same font ('altri': their
    dictionaries), and how many official translations leave the text the same as the original (e.g. 'vsync:')."""
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
    """File name of the language to add (e.g. 'italiano.json'): the language's name in the language itself,
    which the game shows in its menu. None if a file with that name already exists and is not PatchSmith's."""
    nome = (lingua.endonimo(destinazione) or destinazione).split(" (")[0].strip().lower()
    nome = re.sub(r"[^\w]+", "_", nome).strip("_") or destinazione
    file = nome + estensione
    if os.path.exists(os.path.join(cartella, file)) and file not in creati_da_patchsmith(cartella):
        return None
    return file


def caratteri_font_sprite(cartella):
    """Characters of the game's main sprite font (the longest map), or None if there is none."""
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
