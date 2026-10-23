"""Configuratore: modifiche fatte "da fuori" alle impostazioni e ai file di un gioco, sempre reversibili.

Ogni modifica e' una "leva" (lingua, in futuro schermo...) e ha:
- un registro in <patchsmith>/configurations/<impronta della cartella>.json: cosa, quando, quali file, consensi;
- per ogni file toccato il backup verificato accanto (<file>.patchsmith-originale e .patchsmith-info.json, lo stesso
  meccanismo delle traduzioni): "Ripristina originali" funziona anche senza registro;
- scrittura tutto-o-niente: se un file non si puo' scrivere, quelli gia' scritti tornano com'erano.
Le modifiche agli eseguibili si fanno solo con il consenso dell'utente, chiesto una volta per gioco e salvato nel
registro (cosi' dopo un aggiornamento del gioco si possono riapplicare da sole).
"""
import datetime
import hashlib
import json
import os
import shutil
import sys

from . import adapter_webjson, progetto, sistema

MARGINE_SPAZIO = 100 * 1024 * 1024


class ErroreConfiguratore(ValueError):
    pass


def _adesso():
    return datetime.datetime.now().isoformat(timespec="seconds")


# --- registro -------------------------------------------------------------------------------------

def _registro(cartella):
    chiave = hashlib.sha1(os.path.normcase(os.path.abspath(cartella)).encode("utf-8")).hexdigest()[:16]
    return progetto.cartella_dati("configurations", "%s.json" % chiave)


def leggi(cartella):
    """Il registro della cartella ({} se non c'e')."""
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
    """I dati della leva se e' registrata ({'stato', 'valore', 'file', ...}), altrimenti None."""
    return leggi(cartella).get("leve", {}).get(nome)


def segna(cartella, chiave, valore=True):
    """Annota un fatto nel registro (es. salvataggi gia' copiati)."""
    dati = leggi(cartella)
    dati.setdefault("note", {})[chiave] = valore
    _salva(cartella, dati)


def nota(cartella, chiave):
    return leggi(cartella).get("note", {}).get(chiave)


# --- controlli ------------------------------------------------------------------------------------

def smart_app_control():
    """Stato di Smart App Control di Windows 11: 0 spento, 1 attivo, 2 in valutazione, None se non si sa."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy") as k:
            return int(winreg.QueryValueEx(k, "VerifiedAndReputablePolicyState")[0])
    except (OSError, ValueError, TypeError, ImportError):
        return None


def controlli_eseguibili():
    """Motivi per NON modificare eseguibili su questo PC (lista vuota = si puo')."""
    if smart_app_control() in (1, 2):
        return ["Smart App Control di Windows e' attivo (o in valutazione): bloccherebbe gli eseguibili modificati, "
                "perche' la loro firma digitale non sarebbe piu' valida. Non modifico nulla."]
    return []


# --- applicazione tutto-o-niente ------------------------------------------------------------------

def _backup_pronto(percorso, dati_attuali, nome_leva, log):
    """Backup dell'originale pronto all'uso: lo crea, lo tiene, o (se il gioco ha aggiornato il file) lo rinnova
    conservando il precedente. Restituisce (info, True se il backup e' stato creato adesso)."""
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
    # il file e' cambiato per mano d'altri (aggiornamento): e' il nuovo originale, il vecchio resta conservato
    n = len(info.get("precedenti", [])) + 1
    vecchio = "%s.%d" % (os.path.basename(backup), n)
    percorso_vecchio = os.path.join(os.path.dirname(backup), vecchio)
    os.replace(backup, percorso_vecchio)
    try:
        nuove = progetto.crea_backup(percorso, {"configuratore": nome_leva, "creati": info.get("creati", []),
                                                "precedenti": info.get("precedenti", []) + [vecchio]})
    except BaseException:
        os.replace(percorso_vecchio, backup)  # il vecchio backup torna al suo posto, con le sue info
        raise
    log("  %s e' cambiato (aggiornamento del gioco?): e' il nuovo originale, il precedente e' in %s"
        % (os.path.basename(percorso), vecchio))
    return nuove, False


def _serve_backup(percorso, dati):
    """True se applicando bisognera' creare (o rinnovare) il backup di questo file: serve spazio."""
    if not os.path.exists(percorso + progetto.SUFFISSO_BACKUP):
        return True
    info = progetto.leggi_info(percorso) or {}
    attuale = progetto._hash_byte(dati)
    return attuale != info.get("hash_originale") and attuale not in info.get("hash_scritti", [])


def applica_byte(cartella, nome_leva, piani, log=print, valore=None, descrizione=None):
    """Applica modifiche di byte a piu' file, tutto o niente.

    piani: [{'percorso': assoluto, 'modifiche': [(offset, byte_prima, byte_dopo)]}]. Una modifica gia' fatta
    (ci sono gia' i byte 'dopo') si salta; byte diversi da 'prima' e da 'dopo' fermano tutto.
    Restituisce quanti file sono stati scritti. Se qualcosa va storto (anche un'interruzione) rimette ogni file
    com'era PRIMA di questa operazione e solleva ErroreConfiguratore."""
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
            raise  # interruzione (Ctrl+C...): i file sono gia' stati rimessi com'erano
        if rimasti:
            raise ErroreConfiguratore("Modifica non riuscita (%s). Non sono riuscito a rimettere com'erano: %s. "
                                      "Chiudi i programmi che li usano e premi 'Ripristina originali'."
                                      % (e, ", ".join(rimasti))) from e
        raise ErroreConfiguratore("Modifica non riuscita (%s): ho rimesso tutto com'era prima." % e) from e
    _registra_leva(cartella, nome_leva, "applicata", valore, rel, descrizione)
    return len(preparati)


def _annulla(preparati, backup_nuovi, scritti, log):
    """Rimette ogni file com'era prima dell'operazione. Restituisce i nomi dei file che non ci e' riuscito."""
    rimasti = []
    for percorso, dati, _ in preparati:
        nome = os.path.basename(percorso)
        try:
            if percorso in backup_nuovi:
                # backup creato adesso: il file torna all'originale (= com'era) e backup e info spariscono
                if progetto.leggi_info(percorso) and not progetto.ripristina_file(percorso, lambda _m: None):
                    rimasti.append(nome)
            elif percorso in scritti:
                # il file aveva gia' un backup (modificato prima, o aggiornato dal gioco): torna com'era prima
                if not os.path.exists(percorso) or progetto._hash_file(percorso) != progetto._hash_byte(dati):
                    adapter_webjson.scrivi_atomico(percorso, dati)
                    if progetto._hash_file(percorso) != progetto._hash_byte(dati):
                        rimasti.append(nome)
        except (OSError, RuntimeError) as e:
            rimasti.append(nome)
            log("  ! %s non rimesso com'era (%s)" % (nome, e))
    return rimasti


def ripara_interrotte(cartella, log=print):
    """Se un'operazione e' stata interrotta a meta' (registro 'in_corso'), rimette gli originali di quella leva."""
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


# --- ripristino -----------------------------------------------------------------------------------

def ripristina(cartella, nome_leva, log=print):
    """Rimette com'erano i file della leva. None se la leva non c'e', True se fatto, False se qualcosa resta da
    fare (il registro resta per riprovare). Il consenso dato per il gioco resta."""
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
            continue  # gia' rimesso (es. da "Ripristina originali") o mai toccato
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
    """True se il file e' stato modificato dal configuratore (non da una traduzione)."""
    info = progetto.leggi_info(percorso)
    return bool(info and info.get("configuratore"))
