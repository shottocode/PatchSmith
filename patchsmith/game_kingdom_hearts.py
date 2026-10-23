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

from . import configurator as configuratore, language as lingua, project as progetto

ID = "kingdom-hearts-hd-1.5-2.5"
NOME = "KINGDOM HEARTS HD 1.5+2.5 ReMIX"
ESEGUIBILI = ("KINGDOM HEARTS FINAL MIX.exe", "KINGDOM HEARTS Re_Chain of Memories.exe",
              "KINGDOM HEARTS II FINAL MIX.exe", "KINGDOM HEARTS Birth by Sleep FINAL MIX.exe",
              "KINGDOM HEARTS HD 1.5+2.5 Launcher.exe", "KINGDOM HEARTS Theater.exe")
CODICI = {"en": 1, "de": 3, "fr": 4, "es": 5, "it": 6}  # the game's internal codes
_CARTELLA_MSG = {"en": "us", "it": "it", "fr": "fr", "de": "gr", "es": "sp"}  # msg/<cartella>/ in KH2
_VALORI = set(CODICI.values())
SCHEMA = re.compile(rb"\xBB([\x00-\xFF])\x00\x00\x00\x48\x83\x3D[\x00-\xFF]{4}\x00\x75\x31\x8B\xCB\xE8")
TABELLA = b"english\x00french\x00\x00italian\x00german\x00\x00spanish\x00"
DISTANZA_CONFRONTO = 0x300
SALVATAGGI = None  # None = the user's Documenti/My Games/<NOME> (can be set for the tests)
MASSIMO_SALVATAGGI = 512 * 1024 * 1024


class ErroreScheda(ValueError):
    pass


class ErroreLettura(ErroreScheda):
    pass


def _documenti():
    """The user's Documents folder as the game sees it (even if moved or on OneDrive)."""
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0 and buffer.value:  # CSIDL_PERSONAL
            return buffer.value
    except (AttributeError, OSError):
        pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def cartella_salvataggi():
    return SALVATAGGI or os.path.join(_documenti(), "My Games", NOME)


# --- recognition ----------------------------------------------------------------------------------

def _e_radice(cartella):
    return bool(glob.glob(os.path.join(glob.escape(cartella), "Image", "*", "kh2_first.hed"))) and \
        any(os.path.isfile(os.path.join(cartella, e)) for e in ESEGUIBILI)


def radici(cartella):
    """The game's folders (those with Image/ and the executables): the folder itself, or searched up to two
    levels below (more than one if the folder contains several copies of the game)."""
    if not os.path.isdir(cartella):
        return []
    if _e_radice(cartella):
        return [os.path.abspath(cartella)]
    trovate = []
    for radice, dirs, _ in os.walk(cartella):
        livello = os.path.relpath(radice, cartella).count(os.sep) + (radice != cartella)
        if livello >= 2:
            dirs[:] = []
        for d in sorted(dirs):
            p = os.path.join(radice, d)
            if _e_radice(p):
                trovate.append(os.path.abspath(p))
        dirs[:] = [d for d in dirs if os.path.abspath(os.path.join(radice, d)) not in trovate]
    return trovate


def riconosci(cartella):
    """The game's folder if there is only one, otherwise None."""
    trovate = radici(cartella)
    return trovate[0] if len(trovate) == 1 else None


def lingue_presenti(radice):
    """{codes} of the installed text languages (from a sample entry of KH2: msg/<lingua>/sys.bar); None."""
    indici = glob.glob(os.path.join(glob.escape(radice), "Image", "*", "kh2_first.hed"))
    if not indici:
        return None
    try:
        with open(indici[0], "rb") as f:
            hed = f.read()
    except OSError:
        return None
    voci = {hed[i:i + 16] for i in range(0, len(hed) - 31, 32)}
    return {codice for codice, cartella in _CARTELLA_MSG.items()
            if hashlib.md5(("msg/%s/sys.bar" % cartella).encode()).digest() in voci}


# --- the spot of the default language in the executables ------------------------------------------

def _sezioni(dati):
    """[(name, rva, virtual size, offset in the file, size in the file, characteristics)] of a PE."""
    try:
        if dati[:2] != b"MZ":
            raise ErroreScheda("non e' un eseguibile Windows")
        pe = struct.unpack_from("<I", dati, 0x3C)[0]
        if dati[pe:pe + 4] != b"PE\x00\x00":
            raise ErroreScheda("intestazione PE mancante")
        n, = struct.unpack_from("<H", dati, pe + 6)
        opzionale, = struct.unpack_from("<H", dati, pe + 20)
        inizio, uscita = pe + 24 + opzionale, []
        for i in range(n):
            s = inizio + 40 * i
            nome = dati[s:s + 8].rstrip(b"\x00").decode("latin-1")
            vdim, rva, fdim, foff = struct.unpack_from("<IIII", dati, s + 8)
            car, = struct.unpack_from("<I", dati, s + 36)
            uscita.append((nome, rva, vdim, foff, fdim, car))
        return uscita
    except struct.error as e:
        raise ErroreScheda("eseguibile illeggibile (%s)" % e) from e


def _sezione_di(sezioni, offset):
    for s in sezioni:
        if s[3] <= offset < s[3] + s[4]:
            return s
    return None


def punto_lingua(dati):
    """(offset in the file of the default language byte, current value). Raises ErroreScheda if the executable
    is not the expected one: the pattern must be unique, in the code, and followed by the comparison with
    "english"."""
    sezioni = _sezioni(dati)
    trovati = list(SCHEMA.finditer(dati))
    if len(trovati) != 1:
        raise ErroreScheda("punto della lingua %s" % ("non trovato" if not trovati else "ambiguo"))
    m = trovati[0]
    codice = _sezione_di(sezioni, m.start())
    if not codice or not codice[5] & 0x20000000:
        raise ErroreScheda("il punto della lingua non e' nel codice")
    t = dati.find(TABELLA)
    if t < 0 or dati.find(TABELLA, t + 1) >= 0:
        raise ErroreScheda("tabella delle lingue %s" % ("non trovata" if t < 0 else "ambigua"))
    sez_tabella = _sezione_di(sezioni, t)
    if not sez_tabella or sez_tabella[5] & 0x20000000:
        raise ErroreScheda("tabella delle lingue fuori posto")
    rva_english = sez_tabella[1] + t - sez_tabella[3]
    for k in range(m.end(), min(m.start() + DISTANZA_CONFRONTO, len(dati) - 7)):
        if dati[k:k + 3] == b"\x48\x8D\x15":  # lea rdx,[rip+displacement]
            rva_k = codice[1] + k - codice[3]
            if rva_k + 7 + struct.unpack_from("<i", dati, k + 3)[0] == rva_english:
                return m.start() + 1, dati[m.start() + 1]
    raise ErroreScheda("il punto trovato non porta al confronto con la lingua")


def analizza(radice):
    """[(name, path, offset, value)] of the executables present. Raises ErroreScheda (with the file name)."""
    uscita = []
    for nome in ESEGUIBILI:
        percorso = os.path.join(radice, nome)
        if not os.path.isfile(percorso):
            continue
        try:
            with open(percorso, "rb") as f:
                dati = f.read()
        except OSError as e:
            raise ErroreLettura("%s: %s" % (nome, e)) from e
        try:
            offset, valore = punto_lingua(dati)
        except ErroreScheda as e:
            raise ErroreScheda("%s: %s" % (nome, e)) from e
        uscita.append((nome, percorso, offset, valore))
    if not uscita:
        raise ErroreScheda("nessun eseguibile del gioco trovato")
    return uscita


# --- saves ----------------------------------------------------------------------------------------

def _copia_salvataggi(radice, log):
    """Backup copy of the saves (only read) in PatchSmith's folder, the first time. The copy appears only if it is
    complete."""
    if configuratore.nota(radice, "salvataggi_copiati"):
        return
    sorgente = cartella_salvataggi()
    if not os.path.isdir(sorgente):
        log("  Salvataggi non trovati in %s: nessuna copia da fare." % sorgente)
        return
    totale = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(sorgente) for f in fs)
    if totale > MASSIMO_SALVATAGGI:
        log("  ! Salvataggi troppo grandi per una copia automatica (%.0f MB): copiali tu se vuoi." % (totale / 1048576))
        return
    destinazione = progetto.cartella_dati("save-backups", ID, configuratore._adesso().replace(":", "").replace("-", ""))
    temporanea = destinazione + ".incompleta"
    try:
        shutil.copytree(sorgente, temporanea)
        os.replace(temporanea, destinazione)
    except BaseException:
        shutil.rmtree(temporanea, ignore_errors=True)
        raise
    configuratore.segna(radice, "salvataggi_copiati", destinazione)
    log("  Copia di sicurezza dei salvataggi: %s" % destinazione)


# --- language lever -------------------------------------------------------------------------------

def imposta_lingua(radice, destinazione, consenso=False, log=print):
    """Activates the language 'destinazione'. (True, righe) done or already active; (None, righe) the user's consent
    is needed (proposal); (False, righe) it cannot be done (with the reason)."""
    nome = lingua.etichetta(destinazione)
    nella_piattaforma = ("scegli %s nella piattaforma da cui avvii il gioco (Steam: Proprieta' > Generali > Lingua)"
                         % nome)
    codice = CODICI.get(lingua.chiave_confronto(destinazione))
    if codice is None:
        return False, ["%s non ha la lingua %s." % (NOME, nome)]
    configuratore.ripara_interrotte(radice, log)
    try:
        punti = analizza(radice)
    except ErroreLettura as e:
        return False, ["Non riesco a leggere un eseguibile del gioco (%s): controlla antivirus e permessi e riprova. "
                       "Non ho modificato nulla." % e]
    except ErroreScheda as e:
        righe = ["Non riconosco questa versione degli eseguibili di %s (%s): non modifico nulla. In alternativa %s."
                 % (NOME, e, nella_piattaforma)]
        if configuratore.leva(radice, "lingua"):
            righe.append("Alcuni eseguibili hanno ancora la modifica di PatchSmith: premi 'Ripristina originali'.")
        return False, righe
    strani = [n for n, _, _, v in punti if v not in _VALORI]
    if strani:
        return False, ["%s: valore della lingua inatteso, il file e' stato modificato da altri. Non lo tocco."
                       % ", ".join(strani)]
    da_fare = [(n, p, o, v) for n, p, o, v in punti if v != codice]
    if not da_fare:
        nostri = all(progetto.leggi_info(p) for _, p, _, _ in punti) or codice == CODICI["en"]
        return True, ["%s e' gia' attivo nel gioco%s." % (nome, "" if nostri else " (non impostato da PatchSmith)")]
    problemi = configuratore.controlli_eseguibili()
    if problemi:
        return False, problemi + ["In alternativa %s." % nella_piattaforma]
    if not consenso and not configuratore.ha_consenso(radice):
        mb = sum(os.path.getsize(p) for _, p, _, _ in da_fare
                 if not os.path.exists(p + progetto.SUFFISSO_BACKUP)) / 1048576
        return None, ["Posso attivare %s davvero in %s (%s): il gioco prende la lingua dalla piattaforma e, se la "
                      "risposta e' l'inglese, usa un valore predefinito scritto nei suoi eseguibili. Cambio quel "
                      "valore (1 byte in ciascuno di %d eseguibili), con backup (%.1f MB) e ripristino con "
                      "'Ripristina originali'." % (nome, NOME, radice, len(da_fare), mb),
                      "La firma digitale di quei file non sara' piu' valida: qualche antivirus potrebbe segnalarli. "
                      "Non tocco ne' i componenti di Steam ne' il programma di avvio protetto."]
    if consenso:
        configuratore.salva_consenso(radice)  # given once: I do not ask again even if something goes wrong now
    try:
        configuratore._gioco_chiuso(radice)
    except configuratore.ErroreConfiguratore as e:
        return False, ["Non ho potuto attivare %s: %s" % (nome, e)]
    try:
        _copia_salvataggi(radice, log)
    except OSError as e:
        log("  ! Copia dei salvataggi non riuscita (%s): continuo, i salvataggi non vengono toccati." % e)
    piani = [{"percorso": p, "modifiche": [(o, bytes([v]), bytes([codice]))]} for _, p, o, v in punti]
    try:
        scritti = configuratore.applica_byte(radice, "lingua", piani, log, valore=destinazione,
                                             descrizione="lingua predefinita negli eseguibili (%s)" % ID)
    except (configuratore.ErroreConfiguratore, OSError) as e:
        return False, ["Non ho potuto attivare %s: %s" % (nome, e)]
    return True, ["Ho attivato %s in %s (%s): ho cambiato la lingua predefinita in %d eseguibili (1 byte "
                  "ciascuno), con backup." % (nome, NOME, radice, scritti),
                  "Avvia il gioco come sempre: menu, testi e sottotitoli dovrebbero essere in %s (le voci restano "
                  "quelle originali, come nella versione ufficiale)." % nome,
                  "Se il gioco e' in francese, tedesco o spagnolo, e' la piattaforma a imporlo: lasciala su inglese "
                  "(o scegli %s). Se un programma non parte o resta in inglese, premi 'Ripristina originali' e "
                  "segnalalo." % nome,
                  "Un aggiornamento o la verifica dei file del gioco annullano la modifica: premi di nuovo 'Analizza e "
                  "traduci' e PatchSmith la riapplica da solo."]
