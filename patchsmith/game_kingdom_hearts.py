"""Scheda di gioco: KINGDOM HEARTS HD 1.5+2.5 ReMIX (PC).

Come sceglie la lingua (verificato sugli eseguibili, senza avviarli): ognuno dei programmi della raccolta (KH1,
Re:CoM, KH2, BBS, il menu di avvio e il Theater) all'avvio chiede la lingua alla piattaforma (Steam/Epic) e la
converte in un codice: english 1, german 3, french 4, spanish 5, italian 6; per qualsiasi altra risposta, o senza
risposta, usa un valore predefinito: 1 (inglese). Il gioco non ha altre impostazioni della lingua (niente opzioni,
parametri di avvio, registro o file di configurazione). I testi di tutte queste lingue sono gia' installati nei
pacchetti (Image/<dt|en>/*.hed + *.pkg).

Leva "lingua": in ogni eseguibile si cambia il valore predefinito (1 byte: `mov ebx,1` -> `mov ebx,<codice>`).
La risposta "english" (o nessuna risposta) porta cosi' alla lingua scelta; francese, tedesco, spagnolo e italiano
scelti dalla piattaforma restano rispettati. Il gioco funziona come con la lingua scelta da Steam: stessi testi,
menu, font e immagini con le scritte; le voci restano quelle originali.
Il punto si trova per schema di byte (unico, nella sezione di codice, seguito entro poco dal confronto con la
stringa "english" della tabella delle lingue), mai per indirizzo fisso: una versione diversa del gioco viene
rifiutata. Non si toccano il programma di avvio con la protezione (ReMIX.exe) ne' i componenti di Steam.
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
CODICI = {"en": 1, "de": 3, "fr": 4, "es": 5, "it": 6}  # codici interni del gioco
_CARTELLA_MSG = {"en": "us", "it": "it", "fr": "fr", "de": "gr", "es": "sp"}  # msg/<cartella>/ in KH2
_VALORI = set(CODICI.values())
SCHEMA = re.compile(rb"\xBB([\x00-\xFF])\x00\x00\x00\x48\x83\x3D[\x00-\xFF]{4}\x00\x75\x31\x8B\xCB\xE8")
TABELLA = b"english\x00french\x00\x00italian\x00german\x00\x00spanish\x00"
DISTANZA_CONFRONTO = 0x300
SALVATAGGI = None  # None = Documenti/My Games/<NOME> dell'utente (impostabile per i test)
MASSIMO_SALVATAGGI = 512 * 1024 * 1024


class ErroreScheda(ValueError):
    pass


class ErroreLettura(ErroreScheda):
    pass


def _documenti():
    """La cartella Documenti dell'utente come la vede il gioco (anche se spostata o su OneDrive)."""
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


# --- riconoscimento -------------------------------------------------------------------------------

def _e_radice(cartella):
    return bool(glob.glob(os.path.join(glob.escape(cartella), "Image", "*", "kh2_first.hed"))) and \
        any(os.path.isfile(os.path.join(cartella, e)) for e in ESEGUIBILI)


def radici(cartella):
    """Le cartelle del gioco (quelle con Image/ e gli eseguibili): la cartella stessa, oppure cercate fino a due
    livelli sotto (piu' di una se la cartella contiene piu' copie del gioco)."""
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
    """La cartella del gioco se ce n'e' una sola, altrimenti None."""
    trovate = radici(cartella)
    return trovate[0] if len(trovate) == 1 else None


def lingue_presenti(radice):
    """{codici} delle lingue dei testi installate (da una voce campione di KH2: msg/<lingua>/sys.bar); None."""
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


# --- il punto della lingua predefinita negli eseguibili --------------------------------------------

def _sezioni(dati):
    """[(nome, rva, dimensione virtuale, offset nel file, dimensione nel file, caratteristiche)] di un PE."""
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
    """(offset nel file del byte della lingua predefinita, valore attuale). Solleva ErroreScheda se l'eseguibile
    non e' quello previsto: lo schema deve essere unico, nel codice, e seguito dal confronto con "english"."""
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
        if dati[k:k + 3] == b"\x48\x8D\x15":  # lea rdx,[rip+spostamento]
            rva_k = codice[1] + k - codice[3]
            if rva_k + 7 + struct.unpack_from("<i", dati, k + 3)[0] == rva_english:
                return m.start() + 1, dati[m.start() + 1]
    raise ErroreScheda("il punto trovato non porta al confronto con la lingua")


def analizza(radice):
    """[(nome, percorso, offset, valore)] degli eseguibili presenti. Solleva ErroreScheda (con il nome del file)."""
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


# --- salvataggi -----------------------------------------------------------------------------------

def _copia_salvataggi(radice, log):
    """Copia di sicurezza dei salvataggi (solo letti) nella cartella di PatchSmith, la prima volta. La copia
    compare solo se e' completa."""
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


# --- leva della lingua ----------------------------------------------------------------------------

def imposta_lingua(radice, destinazione, consenso=False, log=print):
    """Attiva la lingua 'destinazione'. (True, righe) fatto o gia' attiva; (None, righe) serve il consenso
    dell'utente (proposta); (False, righe) non si puo' (con il motivo)."""
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
        configuratore.salva_consenso(radice)  # dato una volta: non lo richiedo anche se ora qualcosa va storto
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
