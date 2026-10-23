"""Game sheet: KINGDOM HEARTS HD 1.5+2.5 ReMIX (PC).

How it chooses the language (verified on the executables, without starting them): each of the programs of the
collection (KH1, Re:CoM, KH2, BBS, the launch menu and the Theater) asks the platform (Steam/Epic) for the language
at startup and converts it into a code: english 1, german 3, french 4, spanish 5, italian 6; for any other answer,
or with no answer, it uses a default value: 1 (English). The game has no other language settings (no options,
launch parameters, registry or configuration files). The texts of all these languages are already installed in the
packages (Image/<dt|en>/*.hed + *.pkg).

Lever "lingua": in every executable the default value is changed (1 byte: `mov ebx,1` -> `mov ebx,<codice>`).
The answer "english" (or no answer) thus leads to the chosen language; French, German, Spanish and Italian chosen
by the platform are still respected. The game works as with the language chosen by Steam: same texts, menus, fonts
and images with writing; the voices stay the original ones.
The spot is found by byte pattern (unique, in the code section, followed shortly by the comparison with the string
"english" of the language table), never by fixed address: a different version of the game is rejected. The
launcher with the protection (ReMIX.exe) and the Steam components are not touched.
"""
import glob
import hashlib
import os
import re
import shutil
import struct

from . import configurator, language, project

ID = "kingdom-hearts-hd-1.5-2.5"
NAME = "KINGDOM HEARTS HD 1.5+2.5 ReMIX"
EXECUTABLES = ("KINGDOM HEARTS FINAL MIX.exe", "KINGDOM HEARTS Re_Chain of Memories.exe",
              "KINGDOM HEARTS II FINAL MIX.exe", "KINGDOM HEARTS Birth by Sleep FINAL MIX.exe",
              "KINGDOM HEARTS HD 1.5+2.5 Launcher.exe", "KINGDOM HEARTS Theater.exe")
CODES = {"en": 1, "de": 3, "fr": 4, "es": 5, "it": 6}  # the game's internal codes
_MSG_FOLDER = {"en": "us", "it": "it", "fr": "fr", "de": "gr", "es": "sp"}  # msg/<cartella>/ in KH2
_VALUES = set(CODES.values())
PATTERN = re.compile(rb"\xBB([\x00-\xFF])\x00\x00\x00\x48\x83\x3D[\x00-\xFF]{4}\x00\x75\x31\x8B\xCB\xE8")
TABLE = b"english\x00french\x00\x00italian\x00german\x00\x00spanish\x00"
COMPARE_DISTANCE = 0x300
SAVES = None  # None = the user's Documenti/My Games/<NAME> (can be set for the tests)
MAX_SAVES = 512 * 1024 * 1024


class SheetError(ValueError):
    pass


class ReadError(SheetError):
    pass


def _documents():
    """The user's Documents folder as the game sees it (even if moved or on OneDrive)."""
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0 and buffer.value:  # CSIDL_PERSONAL
            return buffer.value
    except (AttributeError, OSError):
        pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def saves_folder():
    return SAVES or os.path.join(_documents(), "My Games", NAME)


# --- recognition ----------------------------------------------------------------------------------

def _is_root(folder):
    return bool(glob.glob(os.path.join(glob.escape(folder), "Image", "*", "kh2_first.hed"))) and \
        any(os.path.isfile(os.path.join(folder, e)) for e in EXECUTABLES)


def roots(folder):
    """The game's folders (those with Image/ and the executables): the folder itself, or searched up to two
    levels below (more than one if the folder contains several copies of the game)."""
    if not os.path.isdir(folder):
        return []
    if _is_root(folder):
        return [os.path.abspath(folder)]
    matches = []
    for root, dirs, _ in os.walk(folder):
        level = os.path.relpath(root, folder).count(os.sep) + (root != folder)
        if level >= 2:
            dirs[:] = []
        for d in sorted(dirs):
            p = os.path.join(root, d)
            if _is_root(p):
                matches.append(os.path.abspath(p))
        dirs[:] = [d for d in dirs if os.path.abspath(os.path.join(root, d)) not in matches]
    return matches


def identify(folder):
    """The game's folder if there is only one, otherwise None."""
    matches = roots(folder)
    return matches[0] if len(matches) == 1 else None


def present_languages(root):
    """{codes} of the installed text languages (from a sample entry of KH2: msg/<lingua>/sys.bar); None."""
    indices = glob.glob(os.path.join(glob.escape(root), "Image", "*", "kh2_first.hed"))
    if not indices:
        return None
    try:
        with open(indices[0], "rb") as f:
            hed = f.read()
    except OSError:
        return None
    entries = {hed[i:i + 16] for i in range(0, len(hed) - 31, 32)}
    return {code for code, folder in _MSG_FOLDER.items()
            if hashlib.md5(("msg/%s/sys.bar" % folder).encode()).digest() in entries}


# --- the spot of the default language in the executables ------------------------------------------

def _sections(data):
    """[(name, rva, virtual size, offset in the file, size in the file, characteristics)] of a PE."""
    try:
        if data[:2] != b"MZ":
            raise SheetError("non e' un eseguibile Windows")
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\x00\x00":
            raise SheetError("intestazione PE mancante")
        n, = struct.unpack_from("<H", data, pe + 6)
        optional, = struct.unpack_from("<H", data, pe + 20)
        start, output = pe + 24 + optional, []
        for i in range(n):
            s = start + 40 * i
            name = data[s:s + 8].rstrip(b"\x00").decode("latin-1")
            vdim, rva, fsize, foff = struct.unpack_from("<IIII", data, s + 8)
            sec_flags, = struct.unpack_from("<I", data, s + 36)
            output.append((name, rva, vdim, foff, fsize, sec_flags))
        return output
    except struct.error as e:
        raise SheetError("eseguibile illeggibile (%s)" % e) from e


def _section_of(sections, offset):
    for s in sections:
        if s[3] <= offset < s[3] + s[4]:
            return s
    return None


def language_byte(data):
    """(offset in the file of the default language byte, current value). Raises SheetError if the executable
    is not the expected one: the pattern must be unique, in the code, and followed by the comparison with
    "english"."""
    sections = _sections(data)
    found = list(PATTERN.finditer(data))
    if len(found) != 1:
        raise SheetError("punto della lingua %s" % ("non trovato" if not found else "ambiguo"))
    m = found[0]
    code = _section_of(sections, m.start())
    if not code or not code[5] & 0x20000000:
        raise SheetError("il punto della lingua non e' nel codice")
    t = data.find(TABLE)
    if t < 0 or data.find(TABLE, t + 1) >= 0:
        raise SheetError("tabella delle lingue %s" % ("non trovata" if t < 0 else "ambigua"))
    table_section = _section_of(sections, t)
    if not table_section or table_section[5] & 0x20000000:
        raise SheetError("tabella delle lingue fuori posto")
    rva_english = table_section[1] + t - table_section[3]
    for k in range(m.end(), min(m.start() + COMPARE_DISTANCE, len(data) - 7)):
        if data[k:k + 3] == b"\x48\x8D\x15":  # lea rdx,[rip+displacement]
            rva_k = code[1] + k - code[3]
            if rva_k + 7 + struct.unpack_from("<i", data, k + 3)[0] == rva_english:
                return m.start() + 1, data[m.start() + 1]
    raise SheetError("il punto trovato non porta al confronto con la lingua")


def analyze(root):
    """[(name, path, offset, value)] of the executables present. Raises ErroreScheda (with the file name)."""
    output = []
    for name in EXECUTABLES:
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise ReadError("%s: %s" % (name, e)) from e
        try:
            offset, value_ = language_byte(data)
        except SheetError as e:
            raise SheetError("%s: %s" % (name, e)) from e
        output.append((name, path, offset, value_))
    if not output:
        raise SheetError("nessun eseguibile del gioco trovato")
    return output


# --- saves ----------------------------------------------------------------------------------------

def _copy_saves(root, log):
    """Backup copy of the saves (only read) in PatchSmith's folder, the first time. The copy appears only if it is
    complete."""
    if configurator.note(root, "salvataggi_copiati"):
        return
    source = saves_folder()
    if not os.path.isdir(source):
        log("  Salvataggi non trovati in %s: nessuna copia da fare." % source)
        return
    total = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(source) for f in fs)
    if total > MAX_SAVES:
        log("  ! Salvataggi troppo grandi per una copia automatica (%.0f MB): copiali tu se vuoi." % (total / 1048576))
        return
    dest_lang = project.data_folder("save-backups", ID, configurator._now().replace(":", "").replace("-", ""))
    temp_path = dest_lang + ".incompleta"
    try:
        shutil.copytree(source, temp_path)
        os.replace(temp_path, dest_lang)
    except BaseException:
        shutil.rmtree(temp_path, ignore_errors=True)
        raise
    configurator.mark(root, "salvataggi_copiati", dest_lang)
    log("  Copia di sicurezza dei salvataggi: %s" % dest_lang)


# --- language lever -------------------------------------------------------------------------------

def set_language(root, dest_lang, consent=False, log=print):
    """Activates the language 'destinazione'. (True, righe) done or already active; (None, righe) the user's consent
    is needed (proposal); (False, righe) it cannot be done (with the reason)."""
    name = language.label(dest_lang)
    in_platform = ("scegli %s nella piattaforma da cui avvii il gioco (Steam: Proprieta' > Generali > Lingua)"
                         % name)
    code = CODES.get(language.comparison_key(dest_lang))
    if code is None:
        return False, ["%s non ha la lingua %s." % (NAME, name)]
    configurator.repair_interrupted(root, log)
    try:
        periods = analyze(root)
    except ReadError as e:
        return False, ["Non riesco a leggere un eseguibile del gioco (%s): controlla antivirus e permessi e riprova. "
                       "Non ho modificato nulla." % e]
    except SheetError as e:
        lines = ["Non riconosco questa versione degli eseguibili di %s (%s): non modifico nulla. In alternativa %s."
                 % (NAME, e, in_platform)]
        if configurator.lever(root, "lingua"):
            lines.append("Alcuni eseguibili hanno ancora la modifica di PatchSmith: premi 'Ripristina originali'.")
        return False, lines
    unknown = [n for n, _, _, v in periods if v not in _VALUES]
    if unknown:
        return False, ["%s: valore della lingua inatteso, il file e' stato modificato da altri. Non lo tocco."
                       % ", ".join(unknown)]
    to_do = [(n, p, o, v) for n, p, o, v in periods if v != code]
    if not to_do:
        ours = all(project.read_info(p) for _, p, _, _ in periods) or code == CODES["en"]
        return True, ["%s e' gia' attivo nel gioco%s." % (name, "" if ours else " (non impostato da PatchSmith)")]
    problems = configurator.executable_checks()
    if problems:
        return False, problems + ["In alternativa %s." % in_platform]
    if not consent and not configurator.has_consent(root):
        mb = sum(os.path.getsize(p) for _, p, _, _ in to_do
                 if not os.path.exists(p + project.BACKUP_SUFFIX)) / 1048576
        return None, ["Posso attivare %s davvero in %s (%s): il gioco prende la lingua dalla piattaforma e, se la "
                      "risposta e' l'inglese, usa un valore predefinito scritto nei suoi eseguibili. Cambio quel "
                      "valore (1 byte in ciascuno di %d eseguibili), con backup (%.1f MB) e ripristino con "
                      "'Ripristina originali'." % (name, NAME, root, len(to_do), mb),
                      "La firma digitale di quei file non sara' piu' valida: qualche antivirus potrebbe segnalarli. "
                      "Non tocco ne' i componenti di Steam ne' il programma di avvio protetto."]
    if consent:
        configurator.save_consent(root)  # given once: I do not ask again even if something goes wrong now
    try:
        configurator._game_closed(root)
    except configurator.ConfiguratorError as e:
        return False, ["Non ho potuto attivare %s: %s" % (name, e)]
    try:
        _copy_saves(root, log)
    except OSError as e:
        log("  ! Copia dei salvataggi non riuscita (%s): continuo, i salvataggi non vengono toccati." % e)
    plans = [{"percorso": p, "modifiche": [(o, bytes([v]), bytes([code]))]} for _, p, o, v in periods]
    try:
        writes = configurator.apply_bytes(root, "lingua", plans, log, value_=dest_lang,
                                             description="lingua predefinita negli eseguibili (%s)" % ID)
    except (configurator.ConfiguratorError, OSError) as e:
        return False, ["Non ho potuto attivare %s: %s" % (name, e)]
    return True, ["Ho attivato %s in %s (%s): ho cambiato la lingua predefinita in %d eseguibili (1 byte "
                  "ciascuno), con backup." % (name, NAME, root, writes),
                  "Avvia il gioco come sempre: menu, testi e sottotitoli dovrebbero essere in %s (le voci restano "
                  "quelle originali, come nella versione ufficiale)." % name,
                  "Se il gioco e' in francese, tedesco o spagnolo, e' la piattaforma a imporlo: lasciala su inglese "
                  "(o scegli %s). Se un programma non parte o resta in inglese, premi 'Ripristina originali' e "
                  "segnalalo." % name,
                  "Un aggiornamento o la verifica dei file del gioco annullano la modifica: premi di nuovo 'Analizza e "
                  "traduci' e PatchSmith la riapplica da solo."]
