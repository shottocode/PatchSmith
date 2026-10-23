"""Configurator: changes made "from outside" to a game's settings and files, always reversible.

Each change is a "lever" (language, in the future screen...) and has:
- a record in <patchsmith>/configurations/<fingerprint of the folder>.json: what, when, which files, consents;
- for each file touched, the verified backup next to it (<file>.patchsmith-originale and .patchsmith-info.json, the
  same mechanism as translations): "Restore originals" works even without the record;
- all-or-nothing writing: if a file cannot be written, the ones already written go back to how they were.
Changes to executables are made only with the user's consent, asked once per game and saved in the record (so after a
game update they can be reapplied on their own).
"""
import datetime
import hashlib
import json
import os
import shutil
import sys

from . import adapter_webjson, project as progetto, system as sistema

MARGINE_SPAZIO = 100 * 1024 * 1024


class ErroreConfiguratore(ValueError):
    pass


def _adesso():
    return datetime.datetime.now().isoformat(timespec="seconds")


# --- record ---------------------------------------------------------------------------------------

def _registro(cartella):
    chiave = hashlib.sha1(os.path.normcase(os.path.abspath(cartella)).encode("utf-8")).hexdigest()[:16]
    return progetto.cartella_dati("configurations", "%s.json" % chiave)


def leggi(cartella):
    """The folder's record ({} if there is none)."""
    try:
        with open(_registro(cartella), encoding="utf-8") as f:
            dati = json.load(f)
        return dati if isinstance(dati, dict) else {}
    except (OSError, ValueError):
        return {}


def _salva(cartella, dati):
    percorso = _registro(cartella)
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    dati.setdefault("versione", 1)
    dati["cartella_gioco"] = os.path.abspath(cartella).replace(os.sep, "/")
    adapter_webjson.scrivi_atomico(percorso, json.dumps(dati, ensure_ascii=False, indent=2).encode("utf-8"))


def ha_consenso(cartella, tipo="eseguibili"):
    return bool(leggi(cartella).get("consensi", {}).get(tipo))


def salva_consenso(cartella, tipo="eseguibili"):
    dati = leggi(cartella)
    dati.setdefault("consensi", {})[tipo] = _adesso()
    _salva(cartella, dati)


def leva(cartella, nome):
    """The lever's data if it is recorded ({'stato', 'valore', 'file', ...}), otherwise None."""
    return leggi(cartella).get("leve", {}).get(nome)


def segna(cartella, chiave, valore=True):
    """Notes a fact in the record (e.g. saves already copied)."""
    dati = leggi(cartella)
    dati.setdefault("note", {})[chiave] = valore
    _salva(cartella, dati)


def nota(cartella, chiave):
    return leggi(cartella).get("note", {}).get(chiave)


# --- checks ---------------------------------------------------------------------------------------

def smart_app_control():
    """State of Windows 11 Smart App Control: 0 off, 1 on, 2 in evaluation, None if unknown."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy") as k:
            return int(winreg.QueryValueEx(k, "VerifiedAndReputablePolicyState")[0])
    except (OSError, ValueError, TypeError, ImportError):
        return None


def controlli_eseguibili():
    """Reasons NOT to modify executables on this PC (empty list = it is allowed)."""
    if smart_app_control() in (1, 2):
        return ["Smart App Control di Windows e' attivo (o in valutazione): bloccherebbe gli eseguibili modificati, "
                "perche' la loro firma digitale non sarebbe piu' valida. Non modifico nulla."]
    return []


# --- all-or-nothing apply -------------------------------------------------------------------------

def _backup_pronto(percorso, dati_attuali, nome_leva, log):
    """Backup of the original ready to use: creates it, keeps it, or (if the game updated the file) renews it keeping
    the previous one. Returns (info, True if the backup was created now)."""
    backup = percorso + progetto.SUFFISSO_BACKUP
    if not os.path.exists(backup):
        return progetto.crea_backup(percorso, {"configuratore": nome_leva}), True
    info = progetto.leggi_info(percorso)
    if not info:
        raise ErroreConfiguratore("%s ha un backup senza informazioni: controllalo a mano prima di procedere."
                                  % os.path.basename(percorso))
    if progetto._hash_file(backup) != info["hash_originale"]:
        raise ErroreConfiguratore("Il backup di %s non corrisponde all'originale registrato: mi fermo."
                                  % os.path.basename(percorso))
    attuale = progetto._hash_byte(dati_attuali)
    if attuale == info["hash_originale"] or attuale in info.get("hash_scritti", []):
        return info, False
    # the file was changed by someone else (update): it is the new original, the old one stays kept
    n = len(info.get("precedenti", [])) + 1
    vecchio = "%s.%d" % (os.path.basename(backup), n)
    percorso_vecchio = os.path.join(os.path.dirname(backup), vecchio)
    os.replace(backup, percorso_vecchio)
    try:
        nuove = progetto.crea_backup(percorso, {"configuratore": nome_leva, "creati": info.get("creati", []),
                                                "precedenti": info.get("precedenti", []) + [vecchio]})
    except BaseException:
        os.replace(percorso_vecchio, backup)  # the old backup goes back in its place, with its info
        raise
    log("  %s e' cambiato (aggiornamento del gioco?): e' il nuovo originale, il precedente e' in %s"
        % (os.path.basename(percorso), vecchio))
    return nuove, False


def _serve_backup(percorso, dati):
    """True if applying will need to create (or renew) the backup of this file: it needs space."""
    if not os.path.exists(percorso + progetto.SUFFISSO_BACKUP):
        return True
    info = progetto.leggi_info(percorso) or {}
    attuale = progetto._hash_byte(dati)
    return attuale != info.get("hash_originale") and attuale not in info.get("hash_scritti", [])


def applica_byte(cartella, nome_leva, piani, log=print, valore=None, descrizione=None):
    """Applies byte changes to several files, all or nothing.

    piani: [{'percorso': assoluto, 'modifiche': [(offset, byte_prima, byte_dopo)]}]. A change already made (the
    'dopo' bytes are already there) is skipped; bytes different from both 'prima' and 'dopo' stop everything.
    Returns how many files were written. If something goes wrong (an interruption too) it puts every file back as it
    was BEFORE this operation and raises ErroreConfiguratore."""
    preparati = []
    for piano in piani:
        percorso = piano["percorso"]
        with open(percorso, "rb") as f:
            dati = f.read()
        nuovi = bytearray(dati)
        for offset, prima, dopo in piano["modifiche"]:
            attuali = bytes(dati[offset:offset + len(prima)])
            if attuali == bytes(dopo):
                continue
            if attuali != bytes(prima):
                raise ErroreConfiguratore("%s: a 0x%X ci sono byte inattesi (%s invece di %s): non e' la versione "
                                          "prevista del file, non modifico nulla." % (
                                              os.path.basename(percorso), offset, attuali.hex(), bytes(prima).hex()))
            nuovi[offset:offset + len(dopo)] = dopo
        if bytes(nuovi) != dati:
            preparati.append((percorso, dati, bytes(nuovi)))
    rel = [os.path.relpath(p["percorso"], cartella).replace(os.sep, "/") for p in piani]
    if not preparati:
        _registra_leva(cartella, nome_leva, "applicata", valore, rel, descrizione)
        return 0
    for percorso, _, _ in preparati:
        if not os.access(percorso, os.W_OK):
            raise ErroreConfiguratore("%s e' di sola lettura: togli la sola lettura e riprova. Non ho modificato "
                                      "nulla." % os.path.basename(percorso))
    servono = sum(len(d) for p, d, _ in preparati if _serve_backup(p, d))
    if shutil.disk_usage(cartella).free < servono + MARGINE_SPAZIO:
        raise ErroreConfiguratore("Spazio libero insufficiente per i backup (servono %.0f MB): non ho modificato "
                                  "nulla." % ((servono + MARGINE_SPAZIO) / 1048576))
    _gioco_chiuso(cartella)
    precedente = leva(cartella, nome_leva)
    _registra_leva(cartella, nome_leva, "in_corso", valore, rel, descrizione)
    backup_nuovi, scritti = [], []
    try:
        for percorso, dati, nuovi in preparati:
            info, nuovo = _backup_pronto(percorso, dati, nome_leva, log)
            if nuovo:
                backup_nuovi.append(percorso)
            impronta = progetto._hash_byte(nuovi)
            info["hash_scritti"] = ([h for h in info.get("hash_scritti", []) if h != impronta]
                                    + [impronta])[-progetto.MAX_IMPRONTE:]
            info["configuratore"] = nome_leva
            progetto.salva_info(percorso, info)
        _gioco_chiuso(cartella)
        for percorso, _, nuovi in preparati:
            scritti.append(percorso)
            adapter_webjson.scrivi_atomico(percorso, nuovi)
            if progetto._hash_file(percorso) != progetto._hash_byte(nuovi):
                raise ErroreConfiguratore("%s: la scrittura non si rilegge uguale" % os.path.basename(percorso))
    except BaseException as e:
        rimasti = _annulla(preparati, backup_nuovi, scritti, log)
        try:
            if precedente:
                _rimetti_leva(cartella, nome_leva, precedente)
            else:
                _togli_leva(cartella, nome_leva)
        except OSError:
            pass
        if not isinstance(e, (OSError, ErroreConfiguratore, RuntimeError)):
            raise  # interruption (Ctrl+C...): the files have already been put back as they were
        if rimasti:
            raise ErroreConfiguratore("Modifica non riuscita (%s). Non sono riuscito a rimettere com'erano: %s. "
                                      "Chiudi i programmi che li usano e premi 'Ripristina originali'."
                                      % (e, ", ".join(rimasti))) from e
        raise ErroreConfiguratore("Modifica non riuscita (%s): ho rimesso tutto com'era prima." % e) from e
    _registra_leva(cartella, nome_leva, "applicata", valore, rel, descrizione)
    return len(preparati)


def _annulla(preparati, backup_nuovi, scritti, log):
    """Puts every file back as it was before the operation. Returns the names of the files it could not restore."""
    rimasti = []
    for percorso, dati, _ in preparati:
        nome = os.path.basename(percorso)
        try:
            if percorso in backup_nuovi:
                # backup created now: the file goes back to the original (= as it was), backup and info disappear
                if progetto.leggi_info(percorso) and not progetto.ripristina_file(percorso, lambda _m: None):
                    rimasti.append(nome)
            elif percorso in scritti:
                # the file already had a backup (changed before, or updated by the game): it goes back to how it was
                if not os.path.exists(percorso) or progetto._hash_file(percorso) != progetto._hash_byte(dati):
                    adapter_webjson.scrivi_atomico(percorso, dati)
                    if progetto._hash_file(percorso) != progetto._hash_byte(dati):
                        rimasti.append(nome)
        except (OSError, RuntimeError) as e:
            rimasti.append(nome)
            log("  ! %s non rimesso com'era (%s)" % (nome, e))
    return rimasti


def ripara_interrotte(cartella, log=print):
    """If an operation was interrupted halfway (record 'in_corso'), puts back the originals of that lever."""
    for nome, dati in list(leggi(cartella).get("leve", {}).items()):
        if isinstance(dati, dict) and dati.get("stato") == "in_corso":
            log("  L'operazione precedente (%s) era stata interrotta a meta': rimetto gli originali." % nome)
            ripristina(cartella, nome, log)


def _gioco_chiuso(cartella):
    try:
        aperto = sistema.gioco_aperto(cartella)
    except sistema.ErroreSistema as e:
        raise ErroreConfiguratore(str(e)) from e
    if aperto:
        raise ErroreConfiguratore("Il gioco e' aperto: chiudilo e riprova. Non ho modificato nulla.")


def _registra_leva(cartella, nome, stato, valore, file, descrizione):
    dati = leggi(cartella)
    dati.setdefault("leve", {})[nome] = {"stato": stato, "valore": valore, "file": file,
                                         "descrizione": descrizione, "data": _adesso()}
    _salva(cartella, dati)


def _rimetti_leva(cartella, nome, voce):
    dati = leggi(cartella)
    dati.setdefault("leve", {})[nome] = voce
    _salva(cartella, dati)


def _togli_leva(cartella, nome):
    dati = leggi(cartella)
    if nome in dati.get("leve", {}):
        del dati["leve"][nome]
        _salva(cartella, dati)


# --- restore --------------------------------------------------------------------------------------

def ripristina(cartella, nome_leva, log=print):
    """Puts the lever's files back as they were. None if the lever is not there, True if done, False if something is
    left to do (the record stays to try again). The consent given for the game stays."""
    dati = leva(cartella, nome_leva)
    if not dati:
        return None
    try:
        _gioco_chiuso(cartella)
    except ErroreConfiguratore as e:
        log("  ! %s" % e)
        return False
    ok = True
    for rel in dati.get("file", []):
        percorso = os.path.join(cartella, *rel.split("/"))
        info = progetto.leggi_info(percorso)
        if not info:
            continue  # already put back (e.g. by "Restore originals") or never touched
        try:
            if not progetto.ripristina_file(percorso, log):
                ok = False
        except OSError as e:
            ok = False
            log("  ! %s non rimesso com'era (%s)" % (os.path.basename(percorso), e))
    if ok:
        _togli_leva(cartella, nome_leva)
    return ok


def file_del_configuratore(percorso):
    """True if the file was modified by the configurator (not by a translation)."""
    info = progetto.leggi_info(percorso)
    return bool(info and info.get("configuratore"))
