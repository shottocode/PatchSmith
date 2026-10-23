"""GameMaker adapter: translates the game's external localization files (e.g. Dokimon: Localization/*.txt).

Many GameMaker games read their texts from files included next to the executable, one per language
(Localization/english_ui.txt, Localization/french/ui.txt...). PatchSmith:
1. finds the files of the original language in the "lines with id" format (adapter_lines);
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

from . import adapter_lines, gamemaker, language
from .characters import ASCII

# full language names (valid anywhere in the path) and short codes (valid only in safe positions)
LANGUAGE_NAMES = {
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
_TRADITIONAL_CHINESE = re.compile(r"(?<![a-z])(?:traditional|tradition|tchinese|hant|cht|tw)(?![a-z])")
# words that go with the language in a file name (chinese_traditional, portuguese_br): they are part of the language
_QUALIFIERS = {"traditional", "tradition", "simplified", "hant", "hans", "tw", "cn", "hk", "br", "pt", "latam",
                  "mx", "us", "uk", "gb", "eu", "es"}
LANGUAGE_CODES = {
    "en": {"en", "eng", "en-us", "en_us"}, "fr": {"fr", "fra", "fre"}, "de": {"de", "ger", "deu"},
    "es": {"es", "spa"}, "it": {"it", "ita"}, "pt": {"pt", "ptbr", "pt-br", "pt_br", "por"}, "ru": {"ru", "rus"},
    "ja": {"ja", "jp", "jpn"}, "ko": {"ko", "kr", "kor"}, "zh": {"zh", "cn", "chs", "cht", "zh-cn", "zh_cn"},
    "pl": {"pl", "pol"}, "tr": {"tr", "tur"},
}
_EXTENSIONS = (".txt", ".lang", ".loc", ".csv")
_MAXIMUM = 20 * 1024 * 1024
# files that are not game texts even if they look like them
_TO_SKIP = re.compile(r"copia|copy|backup|\bbak\b|_bak|\.bak|_old|\bold\b|readme|license|licence|changelog|"
                         r"patch.?notes|credits?_?list|save|score|config|settings|\blog\b|debug", re.I)


def _code_of(word):
    for code, names in LANGUAGE_NAMES.items():
        if word in names:
            return code
    return None


def _short_code(word):
    for code, codes in LANGUAGE_CODES.items():
        if word in codes:
            return code
    return None


# language code attached at the end of the name, in uppercase after a lowercase letter (Another Door: stringsEN.json,
# stringsIT.json, stringsJP.json, stringsBR.json, stringsSC.json): also the codes used only this way
_ATTACHED = re.compile(r"^(.*[a-z])([A-Z]{2,4})$")
_ATTACHED_CODES = {"br": "pt", "ptbr": "pt", "sc": "zh", "chs": "zh", "tc": "zh-TW", "cht": "zh-TW", "tw": "zh-TW"}


def _attached(word):
    """(root, code) if the word (with its original capitalization) ends with an attached language code
    (stringsEN -> ('strings', 'en')), otherwise None."""
    m = _ATTACHED.match(word or "")
    if not m:
        return None
    code = _ATTACHED_CODES.get(m.group(2).lower()) or _short_code(m.group(2).lower())
    return (m.group(1), code) if code else None


def _last_word(rel):
    """The last part of the file name (without extension), with the original capitals: strings_menuEN -> menuEN."""
    name = rel.split("/")[-1].rsplit(".", 1)[0]
    pieces = [p for p in re.split(r"[\s_.\-]+", name) if p]
    return pieces[-1] if pieces else ""


def path_language(rel):
    """Code of the language indicated by the path, or None.

    Full names (english, french...) are valid anywhere; short codes (en, it, pt...) only if they are
    a folder name, the whole file name, or the first/last part of the name (ui_it, en_ui), also
    attached in uppercase at the end of the name (stringsEN)."""
    parts = rel.lower().split("/")
    folders, name = parts[:-1], parts[-1].rsplit(".", 1)[0]
    matches = set()
    for c in folders:
        code = _code_of(c) or _short_code(c)
        if code:
            matches.add(code)
    pieces = [p for p in re.split(r"[\s_.\-]+", name) if p]
    for p in pieces:
        if _code_of(p):
            matches.add(_code_of(p))
    if len(pieces) == 1 and _short_code(name):
        matches.add(_short_code(name))
    elif len(pieces) > 1:
        for p in (pieces[0], pieces[-1]):
            if _short_code(p):
                matches.add(_short_code(p))
    attached = _attached(_last_word(rel))
    if attached:
        matches.add(attached[1])
    if matches == {"zh"} and _TRADITIONAL_CHINESE.search(rel.lower()):
        return "zh-TW"
    return matches.pop() if len(matches) == 1 else None


def family(rel):
    """The path with '{L}' in place of the language: 'local/{L}.json', 'lang/{L}/ui.json', '{L}_credits.json'.
    Files of the same family in different languages are translations of each other."""
    parts = rel.lower().split("/")
    folders = ["{L}" if (_code_of(c) or _short_code(c)) else c for c in parts[:-1]]
    name = parts[-1]
    root, bullet, extension = name.rpartition(".") if "." in name else (name, "", "")
    pieces = re.split(r"([\s_.\-]+)", root)
    words = list(range(0, len(pieces), 2))
    attached = _attached(_last_word(rel))
    if attached:  # stringsEN, stringsIT -> strings{L}
        pieces[words[-1]] = attached[0].lower() + "{L}"
    for n, i in enumerate(words):
        p = pieces[i]
        if _code_of(p) or (_short_code(p) and (len(words) == 1 or n in (0, len(words) - 1))):
            pieces[i] = "{L}"
    for n, i in enumerate(words):  # qualifiers next to the language: chinese_traditional, portuguese_br
        neighbors = [pieces[words[m]] for m in (n - 1, n + 1) if 0 <= m < len(words)]
        if pieces[i] in _QUALIFIERS and "{L}" in neighbors:
            pieces[i] = "{L}"
    name = re.sub(r"\{L\}(?:[\s_.\-]+\{L\})+", "{L}", "".join(pieces))
    return "/".join(folders + [name + bullet + extension])


def read_original(path, reader):
    """The data of the file as it originally was: from PatchSmith's backup if there is one and it is valid (the file
    may already be translated), otherwise from the file itself. 'reader' reads a path."""
    from . import project
    backup = path + project.BACKUP_SUFFIX
    if os.path.exists(backup):
        info = project.read_info(path)
        if info and project._hash_file(backup) == info["original_hash"]:
            return reader(backup)
    return reader(path)


def localization_file(folder):
    """{relative path: language or None} of the files in the 'lines with id' format (read-only)."""
    found = {}
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in files:
            if not name.lower().endswith(_EXTENSIONS) or ".patchsmith-" in name or _TO_SKIP.search(name):
                continue
            p = os.path.join(root, name)
            try:
                if os.path.getsize(p) > _MAXIMUM:
                    continue
            except OSError:
                continue
            rel = os.path.relpath(p, folder).replace(os.sep, "/")
            file_language = path_language(rel)
            # a file with the language in its name (english_ui.txt) only needs a few texts
            if adapter_lines.looks_like_localization(p, minimum=2 if file_language else 5):
                found[rel] = file_language
    return found


def choose_sources(found, origin="auto", folder=None):
    """(files to translate, languages present, language of the chosen files).

    The files of the original language are translated; if they are spread over several folders the one with
    the most is kept (e.g. Localization/), so isolated files elsewhere are not touched."""
    languages = {l for l in found.values() if l}
    base = (origin or "auto").split("-")[0].lower()
    if base == "auto":
        base = "en" if "en" in languages else None
    sources = sorted(r for r, l in found.items() if l == base) if base else []
    if not sources:
        # files without a language in the name next to the translations (e.g. ui.txt + french/ui.txt): they are
        # the original
        unlabeled = sorted(r for r, l in found.items() if l is None)
        if unlabeled and folder:
            texts = []
            for r in unlabeled:
                try:
                    texts += [t for _, t in adapter_lines.read_file(os.path.join(folder, *r.split("/"))).units]
                except (adapter_lines.LinesError, OSError):
                    continue
            detected = language.detect(texts)[0].split("-")[0] if texts else None
            if base is None or detected == base:
                sources, base = unlabeled, detected
        elif unlabeled and not languages:
            sources = unlabeled
    if sources:
        tally = {}
        for r in sources:
            tally[os.path.dirname(r)] = tally.get(os.path.dirname(r), 0) + 1
        main_src = max(tally, key=lambda d: (tally[d], -len(d)))
        sources = [r for r in sources if os.path.dirname(r) == main_src]
    return sources, languages, base


# scripts other than Latin: a font with only these besides ASCII is for other languages (e.g. Korean)
def _other_alphabet(c):
    o = ord(c)
    return 0x3040 <= o <= 0x30FF or 0x4E00 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7A3 or 0x0400 <= o <= 0x04FF \
        or 0x3400 <= o <= 0x4DBF or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F


def _accented_latin(c):
    return 0xC0 <= ord(c) <= 0x24F and c.isalpha()


# fonts that a library adds to the game as a fallback (Scribble: scribble_fallback_font, ASCII only)
_FALLBACK_FONT = re.compile(r"fallback", re.I)


def data_file(folder):
    """Path of the game's data.win (the least deep one), or None."""
    roots = gamemaker.roots(folder)
    for r in roots:
        for name in gamemaker.DATA_NAMES:
            if os.path.exists(os.path.join(r, name)):
                return os.path.join(r, name)
    return None


def _language_font(font_name, code):
    """True if the font name says it is for the language 'code' (e.g. fnt_english, font_en)."""
    if not code or not font_name:
        return False
    base = code.split("-")[0].lower()
    pieces = [p for p in re.split(r"[^a-z]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", font_name).lower()) if p]
    return any(p in LANGUAGE_NAMES.get(base, ()) or p in LANGUAGE_CODES.get(base, ()) for p in pieces)


def font_chars(folder, log=None, officials=None, source=None):
    """Characters that the game's text fonts (data.win) can draw, or None if not known.

    - fonts with at least 20 Latin letters count, except those for another alphabet: besides ASCII they mostly
      have another script (e.g. a Korean or Japanese font with a few accented letters: at least 100 characters
      of the other script and more than 10 times the accented letters), and except the fallback fonts of
      libraries (scribble_fallback_font: ASCII only, the game does not use it for its texts);
    - 'officials': accented letters that the game's official translations use (e.g. é, ü, ç). If there are
      at least 3, a font that has none of them is not used for the translated texts (chat font, numbers font,
      font of another script such as Thai) and does not count, unless its name says it is the font of the
      'source' language (e.g. fnt_english: it is the one that will draw the translation). A font with a few
      accented letters does count instead: it can be the one for the original language's texts, with only the
      letters they use (Another Door: ftPixeloidSans has exactly the Á ç é ô of the English file), and a letter
      it lacks would be a small square;
    - an uppercase-only font (the game puts text in uppercase) also works for lowercase letters;
    - if some font cannot be read, to be safe ASCII only;
    - the small square U+25AF (missing character) does not count as a drawable character."""
    officials = {c for c in (officials or ()) if _accented_latin(c)}
    path = data_file(folder)
    if not path:
        return None
    try:
        items, unreadable = gamemaker.font(gamemaker.read_file(path))
    except (gamemaker.GameMakerError, OSError, ValueError, IndexError, struct.error):
        if log:
            log("  ! Game fonts not readable: to be safe, I only use ASCII characters")
        return set(ASCII)
    common = None
    for f in items:
        chars = set(f["chars"]) - {"\u25af"}
        letters = [c for c in chars if c.isascii() and c.isalpha()]
        if len(letters) < 20:
            continue
        other = sum(1 for c in chars if _other_alphabet(c))
        if other >= 100 and other > 10 * sum(1 for c in chars if _accented_latin(c)):
            continue  # font for another language (e.g. Korean, or Japanese with ç ê ñ Œ œ)
        if _FALLBACK_FONT.search(f.get("name") or ""):
            if log:
                log("  Font %s ignored: it's a library's fallback font" % f.get("name"))
            continue
        if len(officials) >= 3 and not (chars & officials) and not _language_font(f.get("name"), source):
            if log:
                log("  Font %s ignored: the game's official translations don't use it (it lacks their accented letters)"
                    % f.get("name"))
            continue
        if not any(c.islower() for c in letters):
            chars |= {c.lower() for c in chars}
        elif not any(c.isupper() for c in letters):
            chars |= {c.upper() for c in chars}
        common = chars if common is None else common & chars
    if unreadable:
        common = set(ASCII) if common is None else common & ASCII
        if log:
            log("  ! %d game fonts not readable: to be safe, I only use ASCII characters" % unreadable)
    elif common is None and items:
        common = set(ASCII)
        if log:
            log("  ! None of the game's fonts (data.win) seem to match the text: for safety, I only use ASCII "
                "characters")
    return common


# --- JSON localization files (e.g. Forager: local/english.json, local/french.json...) -------------

_MIN_JSON_ENTRIES = 20


def _read_json(path):
    from .adapter_webjson import JSONFile
    return JSONFile(path).data


def _values(data):
    """The values of a JSON, also inside sections and lists of texts, in file order. A list of objects
    ([{"id": "sword", "name": "Sword"}]) is a data file, not a language file: ValueError (its identifiers, such as
    'sword', would no longer be found once translated)."""
    section = isinstance(data, dict)
    for v in (data.values() if section else data):
        if isinstance(v, (dict, list)):
            if not section:
                raise ValueError("item list")
            yield from _values(v)
        else:
            yield v


def _strings(data):
    """String values of a {key: text} dictionary, also split into sections ({"menu": {"_play": "Play"...}}) or
    with lists of texts: at least _MIN_JSON_ENTRIES texts and 80% of the values (None if the file does not have this
    shape)."""
    if not isinstance(data, dict):
        return None
    try:
        all_ = list(_values(data))
    except (RecursionError, ValueError):
        return None
    values = [v for v in all_ if isinstance(v, str)]
    return values if len(values) >= max(_MIN_JSON_ENTRIES, 0.8 * len(all_)) else None


def key_of(path):
    """The key of a text in a localization file: the file's own key ('_play'), or for a text inside a section the
    keys joined with '.' ('menu._play')."""
    return ".".join(str(x) for x in path)


def flatten(data):
    """{key: text} of the texts of a localization file (see key_of): for a flat dictionary it is the
    dictionary itself (only the string values)."""
    from .adapter_webjson import leaves
    return {key_of(p): v for p, _, v in leaves(data)} if isinstance(data, dict) else {}


def at_path(data, path):
    """The value of 'data' at position 'path' (tuple of keys and indexes), or None if it is not there."""
    for step in path:
        try:
            data = data[step]
        except (KeyError, IndexError, TypeError):
            return None
    return data


def created_by_patchsmith(folder):
    """Names of the files that PatchSmith created in this folder (e.g. the new language local/italian.json), read
    from the backup information: they are not game files. A file created and then replaced by someone else (e.g. an
    official italian.json that came with an update: fingerprint different from the recorded one) no longer counts."""
    from . import project
    created = set()
    try:
        names = os.listdir(folder)
    except OSError:
        return created
    for name in names:
        for suffix in project.INFO_SUFFIXES:
            if name.endswith(suffix):
                info = project.read_info(os.path.join(folder, name[:-len(suffix)])) or {}
                fingerprints = info.get("created_hashes") or {}
                for created_one in info.get("created", []):
                    p = os.path.join(folder, created_one)
                    if (created_one not in fingerprints or not os.path.exists(p)
                            or project._hash_file(p) == fingerprints[created_one]):
                        created.add(created_one)
    return created


def language_json_files(folder, log=None):
    """{relative path: language} of the JSON localization files: {key: text} dictionaries (also split into sections,
    and also with // comments and trailing commas, which many games accept) with the language in the path, present
    in at least two languages in the same family (local/{L}.json, lang/{L}/ui.json, localization/strings{L}.json;
    read-only). Files created by PatchSmith (the added language) do not count. With 'log': the language files that
    cannot be read."""
    by_family, unreadable = {}, []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        ours = created_by_patchsmith(root) if any(".patchsmith-" in f for f in files) else set()
        for name in files:
            if not name.lower().endswith(".json") or ".patchsmith-" in name or _TO_SKIP.search(name) \
                    or name in ours:
                continue
            p = os.path.join(root, name)
            rel = os.path.relpath(p, folder).replace(os.sep, "/")
            file_language = path_language(rel)
            if not file_language:
                continue
            try:
                if os.path.getsize(p) > _MAXIMUM:
                    unreadable.append("%s (%d MB: too large)" % (rel, os.path.getsize(p) // (1024 * 1024)))
                    continue
                if _strings(read_original(p, _read_json)) is None:
                    continue
            except (OSError, ValueError) as e:
                unreadable.append("%s (%s)" % (rel, str(e)[:80]))
                continue
            by_family.setdefault(family(rel), {})[rel] = file_language
    found = {}
    for group in by_family.values():
        if len(set(group.values())) >= 2:
            found.update(group)
    if log and unreadable:
        log("  ! %d JSON language files not read: %s" % (len(unreadable), "; ".join(unreadable[:3])))
    return found


def official_letters(folder, found, sources):
    """Accented Latin letters used by the game's official translations (the other language files)."""
    letters = set()
    for rel in found:
        if rel in sources:
            continue
        p = os.path.join(folder, *rel.split("/"))
        try:
            if rel.lower().endswith(".json"):
                texts = _strings(read_original(p, _read_json)) or []
            else:
                texts = [t for _, t in read_original(p, adapter_lines.read_file).units]
        except (OSError, ValueError, adapter_lines.LinesError):
            continue
        letters |= {c for t in texts for c in t if _accented_latin(c)}
    return "".join(sorted(letters))


# in a localization file every value is an interface text, even a single word ("wood"); only the identifiers left in
# place of a missing text are not ("Structure.NuclearFurnace", "menu_title_2")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:[.:/\\][A-Za-z0-9_]+)+$|^[a-z0-9]+(?:_[a-z0-9]+)+$")


def localization_text(value_):
    v = value_.strip() if isinstance(value_, str) else ""
    return bool(re.search(r"[^\W\d_]", v)) and not _IDENTIFIER.match(v)


def json_patterns(data):
    """The keys with text of a localization file: they are all translated (they are interface texts; the rules on
    the "technical" keys of generic JSON files do not apply here)."""
    from .adapter_webjson import leaves
    return sorted({pattern for _, pattern, v in leaves(data) if localization_text(v)})


_ASTERISK_BETWEEN_WORDS = re.compile(r"[^\W\d_]\*+[^\W\d_]")  # between two letters: not emphasis ("*sigh*")


def _confirmed(others, spots, test, strong, weak):
    """True if the official translations confirm the convention: at least one language almost always follows it
    ('test(value, spot)' true in at least 'strong' of the places it has) and at least half of the languages follow
    it at least in part ('weak'): translators remove it where it is not needed (e.g. a word that fits on one line)."""
    shares = []
    for a in others:
        valid_spots = [p for p in spots if isinstance(a.get(p[0]), str)]
        if valid_spots:
            shares.append(sum(1 for p in valid_spots if test(a[p[0]], p)) / len(valid_spots))
    return bool(shares) and max(shares) >= strong and sum(1 for q in shares if q >= weak) >= len(others) / 2


def markup(data, others):
    """Writing conventions of the source file's texts, confirmed by the majority of the official translations
    ('others': the other dictionaries of the same folder): {'line_break': '*' if the asterisk acts as a line break (e.g.
    "iron*ore"), 'bullet': letter used as a symbol at the start of a paragraph (e.g.
    "U unlocks...**U gain...")}."""
    outcome = {"line_break": None, "bullet": None}
    texts = {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}
    others = [a for a in others if isinstance(a, dict)]
    if not texts or not others:
        return outcome
    with_asterisk = [(k,) for k, v in texts.items() if _ASTERISK_BETWEEN_WORDS.search(v)]
    if len(with_asterisk) >= max(5, 0.05 * len(texts)) and \
            _confirmed(others, with_asterisk, lambda value_, _spot: "*" in value_, 0.6, 0.2):
        outcome["line_break"] = "*"
    separator = "**" if outcome["line_break"] else "\n\n"
    occurrences = {}  # letter -> [(key, paragraph number)]
    for k, v in texts.items():
        for n, paragraph in enumerate(v.split(separator)):
            m = re.match(r"([A-Z]) +[a-z]", paragraph)
            if m:
                occurrences.setdefault(m.group(1), []).append((k, n))
    for letter, spots in sorted(occurrences.items(), key=lambda x: -len(x[1])):
        if len(spots) < 5:
            break

        def has_bullet(value_, spot, letter=letter):
            paragraphs = value_.split(separator)
            return spot[1] < len(paragraphs) and re.match(re.escape(letter) + r" +\S", paragraphs[spot[1]])
        if _confirmed(others, spots, has_bullet, 0.8, 0.3):
            outcome["bullet"] = letter
            break
    return outcome


def _point_prefix(paragraph, bullet):
    if not bullet:
        return ""
    m = re.match(re.escape(bullet) + r" +(?=\S)", paragraph)
    return m.group(0) if m else ""


_SENTENCE_END = re.compile(r"[.!?:;\u2026\u3002\uff01\uff1f][\"')\]]*$")


def _body(paragraph, marker, bullet):
    body = paragraph[len(_point_prefix(paragraph, bullet)):]
    return (body.replace(marker, " ") if marker else body).strip()


def _sentence_groups(paragraphs, marker, bullet):
    """Paragraphs grouped by sentence (lists of indexes). The double marker separates paragraphs but in many games
    it also splits a sentence in the middle (Forager: "U items discarded from your**inventory are instantly**sold
    instead"): translated on their own, the pieces lose their meaning. A paragraph continues the previous one if it
    has no bullet symbol, starts with a lowercase letter and the previous one does not end the sentence with
    punctuation; the previous one must be a bullet item or have at least three words (a title such as
    "Controls**move with WASD" stays on its own)."""
    groups = []
    for i, p in enumerate(paragraphs):
        body = _body(p, marker, bullet)
        before = _body(paragraphs[i - 1], marker, bullet) if i else ""
        entry = bool(_point_prefix(paragraphs[groups[-1][0]], bullet)) if groups else False
        if before and body[:1].islower() and not _point_prefix(p, bullet) and not _SENTENCE_END.search(before) \
                and (entry or len(before.split()) >= 3):
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def normalize_line_breaks(text, marker=None, bullet=None):
    """The text as it is translated: the 'marker' line breaks become spaces (the sentence is translated whole),
    paragraphs (double marker) become lines separated by a blank line, joining the pieces of the same sentence; the
    symbol at the start of a paragraph is removed."""
    if (not marker and not bullet) or "\n" in text:
        return text
    separator = marker * 2 if marker else None
    paragraphs = text.split(separator) if separator else [text]
    output = []
    for group in _sentence_groups(paragraphs, marker, bullet):
        p = " ".join(_body(paragraphs[i], marker, bullet) for i in group)
        output.append(re.sub(r" {2,}", " ", p).strip())
    return "\n\n".join(output)


def _balanced_line_breaks(text, segments, marker):
    """Puts the original's line breaks back into the translated text: the same number (or fewer, if there are not
    enough words), in proportion to the lengths of the original lines, always between two words. 'marker' is the
    line break marker, or the list of the original separators between one segment and the next (line breaks and
    paragraphs of the same sentence)."""
    separators = [marker] * (len(segments) - 1) if isinstance(marker, str) else list(marker)
    n = len(segments) - 1
    words = text.split(" ")
    if n <= 0 or len(words) < 2:
        return text
    n = min(n, len(words) - 1)
    total_o = sum(len(s) for s in segments) or 1
    positions, cumulative = [], 0  # position in the text of each space between words
    for word in words[:-1]:
        cumulative += len(word)
        positions.append(cumulative)
        cumulative += 1
    cuts, start, done = [], 0, 0
    for i in range(1, n + 1):
        done += len(segments[i - 1])
        goal = done / total_o * len(text)
        remaining = n - i  # cuts still to make after this one: each leaves at least one space
        choices = range(start, len(positions) - remaining)
        k = min(choices, key=lambda j: abs(positions[j] - goal))
        cuts.append(k)
        start = k + 1
    lines, src_lang = [], 0
    for k in cuts:
        lines.append(" ".join(words[src_lang:k + 1]))
        src_lang = k + 1
    lines.append(" ".join(words[src_lang:]))
    if n < len(separators):  # fewer words than line breaks: paragraph separators (the longest) are kept first
        kept_items = sorted(sorted(range(len(separators)), key=lambda j: -len(separators[j]))[:n])
        separators = [separators[j] for j in kept_items]
    return lines[0] + "".join(sep + r for sep, r in zip(separators, lines[1:]))


def reassemble_line_breaks(original, translated, marker=None, bullet=None, width=None, lines=None):
    """The translated (normalized) text brought back to the game's conventions, with the original's paragraphs,
    paragraph-start symbol and line breaks. None if the paragraphs do not match (the original stays).
    With 'width' and 'lines' (the longest line and the number of lines of the game's versions of this text):
    if a line exceeds the width, the line breaks are redistributed, adding lines up to 'lines'."""
    result = _reassemble(original, translated, marker, bullet)
    if result is None or not marker or not width or _longest_line(result, marker) <= width:
        return result
    return _within_width(result, marker, bullet, width, lines or 1)


def _lines_of(text, marker):
    """The non-empty lines of the text as the game draws them (line break = marker or '\\n')."""
    pieces = re.split("%s|\n" % re.escape(marker), text) if marker else text.split("\n")
    return [x.strip() for x in pieces if x.strip()]


def _longest_line(text, marker):
    return max((len(x) for x in _lines_of(text, marker)), default=0)


def _min_cut(words, k, before=0):
    """The words on k lines so that the longest line is as short as possible (dynamic programming).
    'before': characters already taken at the start of the first line (e.g. the symbol 'U ').
    Returns (indexes of the words that start each line after the first, length of the longest line)."""
    n = len(words)
    k = max(1, min(k, n))
    lengths = [0]
    for w in words:
        lengths.append(lengths[-1] + len(w))

    def line_len(i, j):  # words i..j-1 on one line
        return lengths[j] - lengths[i] + (j - i - 1) + (before if i == 0 else 0)
    infinity = float("inf")
    cost = [[infinity] * (n + 1) for _ in range(k + 1)]
    src_lang = [[0] * (n + 1) for _ in range(k + 1)]
    cost[0][0] = 0
    for r in range(1, k + 1):
        for j in range(r, n + 1):
            for i in range(r - 1, j):
                c = max(cost[r - 1][i], line_len(i, j))
                if c < cost[r][j]:
                    cost[r][j], src_lang[r][j] = c, i
    cuts, j = [], n
    for r in range(k, 0, -1):
        i = src_lang[r][j]
        if r > 1:
            cuts.append(i)
        j = i
    return sorted(cuts), cost[k][n]


def _within_width(text, marker, bullet, width, lines):
    """Redistributes the line breaks of paragraphs that are too wide (only those with simple line breaks only: the
    game's paragraphs and blank lines stay where they are), adding lines as long as the total does not exceed
    'lines'."""
    separator = marker * 2
    paragraphs = text.split(separator)
    total = len(_lines_of(text, marker))
    free_lines = max(0, lines - total)
    handled = set()
    for _ in range(len(paragraphs)):
        wide = [(i, _longest_line(p, marker)) for i, p in enumerate(paragraphs) if i not in handled]
        if not wide:
            break
        i, longest = max(wide, key=lambda x: x[1])
        if longest <= width:
            break
        handled.add(i)
        p = paragraphs[i]
        prefix = _point_prefix(p, bullet)
        edge = r"[\s%s]*" % re.escape(marker)
        head, inner, tail = re.match(r"(%s)(.*?)(%s)$" % (edge, edge), p[len(prefix):], re.S).groups()
        if re.search("%s\\s*%s" % (re.escape(marker), re.escape(marker)), inner) or "\n" in inner:
            continue  # paragraphs or blank lines inside: left alone
        words = inner.replace(marker, " ").split()
        if not words:
            continue
        current_count = inner.count(marker) + 1
        best = None
        for k in range(current_count, current_count + free_lines + 1):
            cuts, line_len = _min_cut(words, k, len(prefix + head))
            if best is None or line_len < best[1]:
                best = (cuts, line_len, k)
            if line_len <= width:
                break
        if best is None or best[1] >= longest:
            continue
        cuts, _, k = best
        new_lines, src_lang = [], 0
        for t in cuts + [len(words)]:
            new_lines.append(" ".join(words[src_lang:t]))
            src_lang = t
        paragraphs[i] = prefix + head + marker.join(new_lines) + tail
        free_lines -= k - current_count
    return separator.join(paragraphs)


def _reassemble(original, translated, marker=None, bullet=None):
    if (not marker and not bullet) or "\n" in original:
        return translated
    separator = marker * 2 if marker else None
    paragraphs = original.split(separator) if separator else [original]
    paragraphs_o = [separator.join(paragraphs[i] for i in g) if separator else paragraphs[g[0]]
                   for g in _sentence_groups(paragraphs, marker, bullet)]  # the pieces of the same sentence, together
    paragraphs_t = translated.split("\n\n") if len(paragraphs_o) > 1 else [translated]
    if len(paragraphs_o) != len(paragraphs_t) or any("\n" in t for t in paragraphs_t):
        return None
    output = []
    edge = r"[\s%s]*" % re.escape(marker) if marker else r"\s*"
    for o, t in zip(paragraphs_o, paragraphs_t):
        prefix = _point_prefix(o, bullet)
        body = o[len(prefix):]
        # leading and trailing line breaks and spaces stay fixed ("*attacks burn!" , "your time: ")
        head, inner, tail = re.match(r"(%s)(.*?)(%s)$" % (edge, edge), body, re.S).groups()
        t = re.sub(r" {2,}", " ", t.strip())
        if marker and marker in t:
            return None  # the translator left a marker: I do not know where it would go
        if not t and inner.strip():
            return None  # paragraph lost in translation
        if marker and t:
            # single line breaks and paragraphs of the sentence; "* *" (blank line with a space) is one separator
            split_parts = re.split("(%s(?:\\s*%s)*)" % (re.escape(marker), re.escape(marker)), inner)
            t = _balanced_line_breaks(t, split_parts[0::2], split_parts[1::2])
        output.append(prefix + head + t + tail)
    return (separator or "").join(output)


_CODE = re.compile(r"/[A-Za-z_]{1,16}/")


def repair(original, translated):
    """Translation that the game reads like the original, or None.

    In GameMaker many games (e.g. Dokimon) expand codes between slashes (/name/, /n/): an extra slash
    is taken as the start of a code and the rest of the line disappears; a code right at the end of
    a dialogue line is not closed. So: the same codes as the original, no extra slash
    (it becomes '-'), and a space after a code that ends the line."""
    if sorted(_CODE.findall(original)) != sorted(_CODE.findall(translated)):
        return None
    original_slashes = _CODE.sub("", original).count("/")
    pieces = _CODE.split(translated)
    codes = _CODE.findall(translated)
    extra = sum(p.count("/") for p in pieces) - original_slashes
    if extra > 0:
        pieces = [p.replace("/", "-") for p in pieces]
        if original_slashes:  # I do not know which slashes to keep: better the original
            return None
    text = pieces[0] + "".join(c + p for c, p in zip(codes, pieces[1:]))
    lines_o, lines_t = original.split("\n"), text.split("\n")
    if len(lines_o) == len(lines_t):
        lines_t = [t + " " if t.endswith("/") and not o.endswith("/") else t for o, t in zip(lines_o, lines_t)]
        text = "\n".join(lines_t)
    return text


# --- capitals: games written all in lowercase, with capitals used as icons (Forager) ----------------------

_WORD = re.compile(r"[^\W\d_]+")


def _mixed_words(text):
    """True if the text has a word with both uppercase and lowercase letters (Hello, CamelCase)."""
    return any(not w.isupper() and not w.islower() for w in _WORD.findall(text))


def _without_placeholders(text):
    from .text import _PLACEHOLDER
    return _PLACEHOLDER.sub(" ", text)


def caps_style(texts):
    """How the game uses capitals in its texts: 'lowercase' (almost all lowercase: capitals are codes or icons),
    'uppercase', 'normal', or 'not applicable' (few texts, or scripts without capitals such as Chinese,
    Japanese, Korean, Thai). Identifiers and placeholders do not count."""
    valid_spots, cased_letters, letters = [], 0, 0
    for t in texts:
        if not isinstance(t, str) or _IDENTIFIER.match(t.strip()):
            continue
        t = _without_placeholders(t)
        letters += sum(1 for c in t if c.isalpha())
        cased = [c for c in t if c.isalpha() and c.lower() != c.upper()]
        cased_letters += len(cased)
        if cased:
            valid_spots.append(t)
    if len(valid_spots) < 30 or cased_letters < 0.5 * max(1, letters):
        return "not applicable"
    no_lowercase = sum(1 for t in valid_spots if not any(c.islower() for c in t))
    if no_lowercase >= 0.95 * len(valid_spots):
        return "uppercase"
    mixed_count = sum(1 for t in valid_spots if _mixed_words(t))
    if mixed_count <= 0.05 * len(valid_spots) and no_lowercase <= 0.05 * len(valid_spots):
        return "lowercase"
    return "normal"


def uppercase_codes(texts, mapping=None, bullet=None):
    """In lowercase games, all-uppercase words (at most 4 letters, in at least 3 texts) are codes or font icons
    (Forager: 'EP' = XP icon, 'H' = heart). Only those that the sprite font draws as capitals (its icons) are kept:
    without the font map there is no way to know, and nothing is protected."""
    if not mapping:
        return []
    icons = {c for c in mapping if c.isupper()}
    tally = {}
    for t in texts:
        if not isinstance(t, str) or _mixed_words(_without_placeholders(t)):
            continue
        for w in set(_WORD.findall(_without_placeholders(t))):
            if w.isupper() and len(w) <= 4 and w != bullet:
                tally[w] = tally.get(w, 0) + 1
    return sorted(w for w, n in tally.items() if n >= 3 and set(w) <= icons)


def to_lowercase(translated, original, codes=(), target_lang="it"):
    """The translation with capitals as the game writes them: for a text whose original is in lowercase, all
    letters in lowercase except the original's codes (icons) and the placeholders. Texts with normal capitals
    (system messages) stay as they are."""
    from .text import _PLACEHOLDER
    if _mixed_words(_without_placeholders(original)):
        return translated
    present = [c for c in codes if re.search(r"(?<![^\W\d_])%s(?![^\W\d_])" % re.escape(c), original)]
    protected = [_PLACEHOLDER.pattern]
    if present:
        protected.append(r"(?<![^\W\d_])(?:%s)(?![^\W\d_])" % "|".join(map(re.escape, present)))
    turkish = target_lang.split("-")[0] in ("tr", "az")
    output, last = [], 0
    for m in re.finditer("|".join("(?:%s)" % x for x in protected), translated):
        output.append(translated[last:m.start()])
        output.append(None)
        output.append(m.group())
        last = m.end()
    output.append(translated[last:])
    pieces, protect = [], False
    for x in output:
        if x is None:
            protect = True
            continue
        if protect:
            pieces.append(x)
            protect = False
        else:
            pieces.append(x.replace("I", "\u0131").replace("\u0130", "i").lower() if turkish else x.lower())
    return "".join(pieces)


# --- line width: how much space the game's versions give to the text -------------------------------------------

def dedicated_fonts(data_win, codes):
    """Languages that have a font of their own (from the name: fntGerman, fntRussian...): their lines have a
    different width and cannot be used to measure the text's space."""
    try:
        items, _ = gamemaker.font(data_win)
    except (gamemaker.GameMakerError, struct.error, ValueError, IndexError):
        return set()
    return {c for c in codes for f in items if _language_font(f.get("name"), c)}


def _other_script_text(text):
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if _other_alphabet(c)) > 0.3 * len(letters)


def line_limits(data, others, marker):
    """{key: [width, lines, equal_count]} for each text: the longest line (in characters) and the maximum number
    of lines among the original and the official translations that use the same font ('others': their
    dictionaries), and how many official translations leave the text the same as the original (e.g. 'vsync:')."""
    output = {}
    if not isinstance(data, dict):
        return output
    for k, v in data.items():
        if not isinstance(v, str) or not localization_text(v):
            continue
        versions = [v] + [a.get(k) for a in others if isinstance(a, dict) and isinstance(a.get(k), str)
                          and a.get(k).strip() and not _other_script_text(a.get(k))]
        lines = [_lines_of(x, marker) for x in versions]
        width = max((len(r) for rr in lines for r in rr), default=0)
        equal_count = sum(1 for x in versions[1:] if x.strip().casefold() == v.strip().casefold())
        output[k] = [width, max((len(rr) for rr in lines), default=1), equal_count]
    return output


def new_language_name(dest_lang, folder, extension=".json"):
    """File name of the language to add (e.g. 'italiano.json'): the language's name in the language itself,
    which the game shows in its menu. None if a file with that name already exists and is not PatchSmith's."""
    name = (language.endonym(dest_lang) or dest_lang).split(" (")[0].strip().lower()
    name = re.sub(r"[^\w]+", "_", name).strip("_") or dest_lang
    file = name + extension
    if os.path.exists(os.path.join(folder, file)) and file not in created_by_patchsmith(folder):
        return None
    return file


def sprite_font_chars(folder):
    """Characters of the game's main sprite font (the longest map), or None if there is none."""
    data = data_file(folder)
    if not data:
        return None
    try:
        maps = gamemaker.font_sprite_maps(os.path.dirname(data))
    except (gamemaker.GameMakerError, OSError, ValueError, IndexError, struct.error):
        return None
    if not maps:
        return None
    return max((m for _, m in maps), key=len)
