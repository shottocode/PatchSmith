"""Official language: if the game already has the translation in the target language, that is used instead of
translating.

Sources:
1. Steam: for a game installed from Steam, Steam's cache (appinfo.vdf) lists the official languages;
2. the game's files, engine by engine (Unreal: .locres per language; Unity: I2 / Unity Localization languages;
   XNA/.NET: satellites; GameMaker: files per language);
3. for a folder that is not a Steam installation, the game can be recognized from its executables: this counts only
   if the files say nothing and the folder name resembles the game's, otherwise it is just a note.
For Chinese the script matters (zh-TW and zh-CN are different languages).
When the language is there, PatchSmith SETS it where a standard, safe and reversible setting exists
(Unreal: GameUserSettings.ini; Unity with I2: the "I2 Language" preference), with the game closed, with a log of the
changes and restore; otherwise it explains exactly how to choose it. It does not modify Steam or unofficial
components.
"""
import datetime
import hashlib
import json
import os

from . import language as lingua, steam


def _base(codice):
    return (codice or "").split("-")[0].lower()


def _nome_steam(codice):
    if codice in steam.NOMI_STEAM:
        return steam.NOMI_STEAM[codice]
    chiave = lingua.chiave_confronto(codice)
    if chiave == "zh-hant":
        return "tchinese"
    if chiave == "zh-hans":
        return "schinese"
    return steam.NOMI_STEAM.get(_base(codice))


def _adesso():
    return datetime.datetime.now().isoformat(timespec="seconds")


# --- detection ------------------------------------------------------------------------------------

def rileva(cartella, destinazione, motore=None, log=None):
    """What is known about the 'destinazione' language in the game.

    {'presente': True/False/None, 'fonti': [text], 'note': [text], 'steam': installation info or None,
     'steam_conosciuto': game recognized from the executables or None, 'lingua_steam': current language in Steam,
     'selezionabile_steam': True/False/None, 'motore': {...data for the setting...}}"""
    r = {"presente": None, "fonti": [], "note": [], "steam": None, "steam_conosciuto": None, "lingua_steam": None,
         "selezionabile_steam": None, "motore": {}}
    nome = _nome_steam(destinazione)
    etichetta = lingua.etichetta(destinazione)

    # 1) Steam (a problem with Steam's cache must never block the translation)
    info, conosciuto, steam_dice, selezionabile = _dati_steam(cartella, nome, log)
    # 2) the game's files, according to the engine
    try:
        presenti, dati_motore = lingue_nei_file(cartella, motore, destinazione)
    except Exception as e:  # a detector that fails must not block the translation
        presenti, dati_motore = None, {}
        if log:
            log("  ! Controllo delle lingue nei file non riuscito (%s: %s)" % (type(e).__name__, e))
    r["motore"] = dati_motore
    radice = dati_motore.get("radice") if dati_motore.get("tipo") == "scheda" else None
    if not info and radice and os.path.normcase(radice) != os.path.normcase(os.path.abspath(cartella)):
        # the folder above the game was chosen: the Steam installation is recognized from the game's folder
        info_radice, _, dice_radice, sel_radice = _dati_steam(radice, nome, log, solo_installato=True)
        if info_radice:
            info, conosciuto, steam_dice, selezionabile = info_radice, None, dice_radice, sel_radice
    r["selezionabile_steam"] = selezionabile
    if info:
        r["steam"], r["lingua_steam"] = info, info.get("lingua")
    elif conosciuto:
        r["steam_conosciuto"] = conosciuto
    if steam_dice is not None:
        stato = "e' tra le lingue ufficiali" if steam_dice else "non e' tra le lingue ufficiali"
        if info:
            r["fonti"].append("Steam: %s %s" % (etichetta, stato))
    nei_file = None if presenti is None else bool(dati_motore.get("trovata"))
    if nei_file:
        r["fonti"].append("File del gioco: c'e' la lingua %s" % etichetta)

    # 3) decision
    if info:  # Steam installation: Steam wins (the language may be in a package not yet downloaded)
        r["presente"] = True if nei_file else steam_dice if steam_dice is not None else nei_file
    else:
        r["presente"] = nei_file
        if conosciuto and steam_dice is not None:
            gioco = "\"%s\"" % (conosciuto.get("nome") or conosciuto["appid"])
            if nei_file is None and conosciuto.get("cartella_simile"):
                r["presente"] = steam_dice
                r["fonti"].append("Steam conosce questo gioco (%s, avviato da %s): %s %s" % (
                    gioco, conosciuto["eseguibile"], etichetta, stato))
            elif steam_dice and not nei_file:
                r["note"].append("Forse e' il gioco Steam %s, che ha %s ufficiale: se e' lui, cerca la lingua nelle "
                                 "opzioni del gioco o nel launcher." % (gioco, etichetta))
    return r


def _dati_steam(cartella, nome, log, solo_installato=False):
    """(Steam installation, game recognized from the executables, official language according to Steam, selectable
    in Steam); None where it is not known. Steam errors stop nothing."""
    info = conosciuto = steam_dice = selezionabile = None
    try:
        info = steam.gioco_installato(cartella)
        if not info and not solo_installato:
            conosciuto = steam.riconosci_da_eseguibili(cartella)
        appid = info["appid"] if info else conosciuto["appid"] if conosciuto else None
        if appid and nome:
            dati_app = steam.info_app(appid)
            if dati_app:
                ufficiali = steam.ufficiali_da_info(dati_app)
                if ufficiali is not None:
                    steam_dice = bool(ufficiali.get(nome, {}).get("supported"))
                selezionabile = nome in steam.selezionabili_da_info(dati_app)
    except Exception as e:
        if log:
            log("  ! Lettura dei dati di Steam non riuscita (%s: %s): continuo senza" % (type(e).__name__, e))
    return info, conosciuto, steam_dice, selezionabile


def lingue_nei_file(cartella, motore, destinazione):
    """({languages} or None if not known, data to set it; 'trovata' says whether the requested language is there).
    Game sheets (games known one by one) come before the engine."""
    from . import games as schede
    scheda, radici = schede.riconosci(cartella)
    if scheda:
        lingue = scheda.lingue_presenti(radici[0])
        return lingue, {"tipo": "scheda", "scheda": scheda.ID, "radice": radici[0] if len(radici) == 1 else None,
                        "radici": radici, "trovata": bool(lingue) and lingua.migliore(lingue, destinazione) is not None}
    if motore == "Unreal":
        from . import language_unreal as lingua_unreal
        lingue, progetto = lingua_unreal.lingue_del_gioco(cartella)
        tag = lingua_unreal.tag_italiano(lingue, destinazione) if lingue else None
        return lingue, {"tipo": "unreal", "progetto": progetto, "tag": tag, "trovata": tag is not None}
    if motore == "Unity":
        from . import language_unity as lingua_unity
        dati = lingua_unity.cartella_dati(cartella)
        if not dati:
            return None, {}
        lingue, voce, i2, ul = set(), None, None, None
        try:
            i2 = lingua_unity.lingue_i2(dati) if lingua_unity.usa_i2(dati) else None
        except Exception:  # I2 unreadable: Unity Localization remains
            i2 = None
        if i2:
            lingue |= {c or n for n, c in i2}
            voce = lingua_unity.voce_i2(i2, destinazione)
        try:
            ul = lingua_unity.lingue_unity_localization(dati)
        except Exception:
            ul = None
        # also the languages of the text tables, recognized from the bundles' content: the catalog does not always
        # list them under "Locales/" (e.g. "Locals/Italian (it).asset", or no name with the code)
        try:
            from . import adapter_unity_loc
            streaming = os.path.join(dati, "StreamingAssets")
            tabelle = adapter_unity_loc.tabelle_per_contenuto(streaming) if os.path.isdir(streaming) else {}
        except Exception:
            tabelle = {}
        if tabelle:
            presenti = {c.lower() for c in ul or ()}
            ul = set(ul or ()) | {c for c in tabelle if c.lower() not in presenti}
        if ul:
            lingue |= set(ul)
        azienda, prodotto = lingua_unity.azienda_prodotto(dati)
        trovata = voce is not None or bool(ul and lingua.migliore(ul, destinazione))
        dati_motore = {"tipo": "unity", "dati": dati, "azienda": azienda, "prodotto": prodotto,
                       "i2_lingua": voce, "i2_personalizzato": bool(voce) and lingua_unity.i2_personalizzato(dati),
                       "unity_localization": sorted(ul) if ul else None, "trovata": trovata}
        return (lingue if (i2 or ul) else None), dati_motore
    if motore in ("XNA", "FNA", "MonoGame"):
        from . import adapter_xna
        insiemi = adapter_xna.trova_risorse(cartella)  # satellites created by PatchSmith do not count
        if not insiemi:
            return None, {"tipo": "xna"}
        codici = {c for i in insiemi for culture in i["basi"].values() for c in culture}
        codici |= {i["lingua_neutra"] for i in insiemi if i["lingua_neutra"]}
        return codici, {"tipo": "xna", "trovata": lingua.migliore(codici, destinazione) is not None}
    if motore == "GameMaker":
        from . import adapter_csv, adapter_gamemaker
        trovati = adapter_gamemaker.file_localizzazione(cartella)
        trovati.update(adapter_gamemaker.file_json_lingue(cartella))  # also the JSON files per language
        lingue = {l for l in trovati.values() if l}
        # and the CSV tables with one column per language: the full columns count (an empty one is a language
        # planned but not translated)
        tabelle = adapter_csv.tabelle(cartella)
        for f in tabelle.values():
            lingue |= f.lingue_piene()
        return (lingue if trovati or tabelle else None), {"tipo": "gamemaker",
                                                          "trovata": lingua.migliore(lingue, destinazione) is not None}
    return None, {}


# --- setting and restore --------------------------------------------------------------------------

def _registro(cartella):
    from .project import cartella_dati
    chiave = hashlib.sha1(os.path.normcase(os.path.abspath(cartella)).encode("utf-8")).hexdigest()[:16]
    return cartella_dati("languages", "%s.json" % chiave)


def _leggi_registro(cartella):
    try:
        with open(_registro(cartella), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _salva_registro(cartella, dati):
    from .adapter_webjson import scrivi_atomico
    percorso = _registro(cartella)
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    scrivi_atomico(percorso, json.dumps(dati, ensure_ascii=False, indent=2).encode("utf-8"))


def _prima_volta(cartella):
    """True the first time the user is asked to start the game to create its settings (then it is not repeated,
    so as not to keep asking to start it forever if the settings are somewhere else)."""
    segno = _registro(cartella)[:-len(".json")] + ".avvio"
    if os.path.exists(segno):
        return False
    try:
        os.makedirs(os.path.dirname(segno), exist_ok=True)
        with open(segno, "w", encoding="utf-8") as f:
            f.write(_adesso())
    except OSError:
        pass
    return True


def _aperto(cartella):
    """None if the game is closed, otherwise the reason not to touch anything."""
    from . import system as sistema
    try:
        if sistema.gioco_aperto(cartella):
            return "il gioco e' aperto: chiudilo e riprova"
    except sistema.ErroreSistema as e:
        return str(e)
    return None


def imposta(cartella, rilevamento, destinazione, log=print, consenso=False):
    """Sets the language where it can be done safely. Returns (fatto, [lines to show]); fatto is None when the
    user's consent is needed (e.g. to modify the executables): then the lines are the proposal."""
    m = rilevamento.get("motore") or {}
    nome = lingua.etichetta(destinazione)
    nel_menu = "scegli %s nel menu delle opzioni del gioco" % nome
    if m.get("tipo") == "scheda":
        from . import games as schede
        if rilevamento.get("steam") and rilevamento.get("selezionabile_steam"):
            return False, []  # installed from Steam: the language is chosen there (the message says where)
        scheda = schede.per_id(m.get("scheda"))
        if scheda and not m.get("radice") and len(m.get("radici") or []) > 1:
            return False, ["Nella cartella ci sono %d copie di %s (%s): scegli la cartella di una sola."
                           % (len(m["radici"]), scheda.NOME, "; ".join(m["radici"]))]
        if scheda and m.get("radice"):
            return scheda.imposta_lingua(m["radice"], destinazione, consenso, log)
    if m.get("tipo") == "unreal" and m.get("tag"):
        from . import language_unreal as lingua_unreal
        gus = lingua_unreal.file_impostazioni(m.get("progetto"), cartella)
        if not gus:
            if _prima_volta(cartella):
                return False, ["Non trovo ancora le impostazioni del gioco: avvialo una volta e chiudilo, poi ripeti "
                               "'Analizza e traduci' e provo a impostare %s io (oppure %s)." % (nome, nel_menu)]
            return False, ["Non trovo le impostazioni del gioco (GameUserSettings.ini): %s." % nel_menu]
        try:
            _, altre = lingua_unreal.leggi_internazionalizzazione(gus)
        except (OSError, ValueError) as e:
            return False, ["Non ho potuto leggere le impostazioni del gioco (%s): %s." % (e, nel_menu)]
        if altre:
            return False, ["Il gioco gestisce la lingua da solo (%s): %s." % (", ".join(altre[:2]), nel_menu)]
        motivo = _aperto(cartella)
        if motivo:
            return False, ["Non imposto la lingua: %s ('Analizza e traduci' la imposta)." % motivo]
        try:
            modifiche = lingua_unreal.imposta_cultura(gus, m["tag"])
        except (OSError, ValueError) as e:
            return False, ["Non ho potuto scrivere le impostazioni del gioco (%s): %s." % (e, nel_menu)]
        if not modifiche:
            return True, ["Le impostazioni del gioco sono gia' su %s (%s)." % (nome, gus),
                          "Se il gioco e' ancora in un'altra lingua, %s." % nel_menu]
        precedente = _leggi_registro(cartella)
        if precedente and precedente.get("tipo") == "unreal" and precedente.get("file") == gus:
            modifiche = precedente["modifiche"] + modifiche  # the oldest values (the original ones) win
        righe = ["Ho impostato %s nelle impostazioni del gioco (%s, [Internationalization])." % (nome, gus),
                 "Avvia il gioco: se e' ancora in un'altra lingua, il gioco usa un suo menu: %s." % nel_menu]
        try:
            _salva_registro(cartella, {"tipo": "unreal", "file": gus, "tag": m["tag"], "modifiche": modifiche,
                                       "data": _adesso()})
        except OSError as e:
            righe.append("Attenzione: non ho potuto salvare il registro per il ripristino (%s)." % e)
        return True, righe
    if m.get("tipo") == "unity" and m.get("i2_lingua"):
        from . import language_unity as lingua_unity
        azienda, prodotto = m.get("azienda"), m.get("prodotto")
        if not azienda or not prodotto:
            return False, [nel_menu[0].upper() + nel_menu[1:] + "."]
        if m.get("i2_personalizzato"):
            return False, ["Il gioco salva la lingua a modo suo: %s." % nel_menu]
        try:
            precedente = lingua_unity.leggi_pref(azienda, prodotto, lingua_unity.PREF_I2)
        except OSError as e:
            return False, ["Non ho potuto leggere le preferenze del gioco (%s): %s." % (e, nel_menu)]
        if precedente is False:
            if _prima_volta(cartella):
                return False, ["Il gioco non ha ancora le sue preferenze: avvialo una volta e chiudilo, poi ripeti "
                               "'Analizza e traduci' e provo a impostare %s io (oppure %s)." % (nome, nel_menu)]
            return False, ["Non trovo le preferenze del gioco: %s." % nel_menu]
        valore = m["i2_lingua"]
        scritto = valore.encode("utf-8") + b"\x00"
        if precedente and precedente[1] == scritto:
            return True, ["La lingua del gioco e' gia' impostata su %s." % nome,
                          "Se il gioco e' ancora in un'altra lingua, %s." % nel_menu]
        motivo = _aperto(cartella)
        if motivo:
            return False, ["Non imposto la lingua: %s ('Analizza e traduci' la imposta)." % motivo]
        try:
            lingua_unity.scrivi_pref_stringa(azienda, prodotto, lingua_unity.PREF_I2, valore)
        except (OSError, ValueError) as e:
            return False, ["Non ho potuto impostare la lingua (%s): %s." % (e, nel_menu)]
        vecchio = _leggi_registro(cartella)
        if vecchio and vecchio.get("tipo") == "unity_i2" and vecchio.get("azienda") == azienda and \
                vecchio.get("prodotto") == prodotto:
            prima = vecchio.get("precedente")  # the value from before the first time
        else:
            prima = [precedente[0], precedente[1].hex()] if precedente else None
        righe = ["Ho impostato la lingua del gioco su %s (preferenza di I2 Localization: \"%s\")." % (nome, valore),
                 "Avvia il gioco: dovrebbe partire in %s. Se e' ancora in un'altra lingua, il gioco usa una sua "
                 "impostazione: %s." % (nome, nel_menu)]
        try:
            _salva_registro(cartella, {"tipo": "unity_i2", "azienda": azienda, "prodotto": prodotto,
                                       "chiave": lingua_unity.PREF_I2, "scritto": scritto.hex(), "precedente": prima,
                                       "data": _adesso()})
        except OSError as e:
            righe.append("Attenzione: non ho potuto salvare il registro per il ripristino (%s)." % e)
        return True, righe
    return False, []


def imposta_lingua_aggiunta(cartella, nome, lingue, log=print, sorgente=None, forza=False):
    """After adding a language to the game (e.g. local/italiano.json of a GameMaker game), sets it in the options
    saved by the game, with a backup copy and a log for the restore. (fatto, [righe]).
    If the player chose another language after the previous time, their choice stays. forza=True: the language was
    changed by the game, not by the player (e.g. an update had removed the added language's file and the game went
    back to the original: the after-play check says so)."""
    from . import language_gamemaker as lingua_gamemaker
    from .project import cartella_dati
    motivo = _aperto(cartella)
    if motivo:
        return False, ["Non imposto la lingua '%s' nelle opzioni del gioco: %s." % (nome, motivo)]
    salvataggi = cartella_dati("save-backups", "lingua-" + os.path.basename(_registro(cartella))[:-5])
    vecchio = _leggi_registro(cartella)
    if not (vecchio and vecchio.get("tipo") == "gamemaker_opzioni"):
        vecchio = None
    try:
        registro, motivo = lingua_gamemaker.imposta(cartella, nome, lingue, salvataggi, log,
                                                    rispetta=vecchio.get("scritto") if vecchio and not forza else None)
    except (OSError, ValueError, KeyError, StopIteration) as e:
        return False, ["Non ho potuto impostare la lingua nelle opzioni del gioco (%s)." % e]
    if registro is None:
        return False, ["Non imposto la lingua da fuori: %s." % motivo]
    if vecchio and vecchio.get("file") == registro["file"] and vecchio.get("precedente"):
        registro["precedente"] = vecchio["precedente"]  # the language from before the first time wins
    elif not registro.get("precedente") and sorgente:
        registro["precedente"] = sorgente  # already set (e.g. by the player): on restore the original comes back
    registro["data"] = _adesso()
    try:
        _salva_registro(cartella, registro)
    except OSError as e:
        return True, ["Attenzione: non ho potuto salvare il registro per il ripristino (%s)." % e]
    return True, []


def rimetti_lingua_aggiunta(cartella, aggiunte, sorgente, lingue, log=print):
    """Before removing the languages added by PatchSmith (e.g. 'italiano'): if the options saved by the game still
    have it, the previous language is put back, so the game does not restart with a language that no longer exists.
    Only if PatchSmith set it for THIS folder (log): the options are in %LOCALAPPDATA% under the game's name, and a
    copy of the game (e.g. a test copy) shares them with the real one, which may have its own added language.
    Without a log everything is left alone (Forager, without the language file, goes back to English).
    None if there was nothing to do, True if put back, False if it was needed but could not be done."""
    from . import language_gamemaker as lingua_gamemaker
    registro = _leggi_registro(cartella) or {}
    if registro.get("tipo") != "gamemaker_opzioni":
        return None
    try:
        percorso, posto = lingua_gamemaker.cerca(cartella, set(lingue) | set(aggiunte))
    except OSError:
        return None
    if percorso is None or posto[3] not in aggiunte or os.path.normcase(percorso) != os.path.normcase(registro.get("file") or ""):
        return None
    precedente = registro.get("precedente")
    if not precedente or precedente in aggiunte:
        precedente = sorgente
    motivo = _aperto(cartella)
    if motivo:
        log("  ! Lingua del gioco non rimessa: %s." % motivo)
        return False
    try:
        with open(percorso, encoding="utf-8", newline="") as f:
            testo = f.read()
        from .adapter_webjson import scrivi_atomico
        scrivi_atomico(percorso, lingua_gamemaker.cambia(testo, posto, precedente).encode("utf-8"))
    except (OSError, ValueError) as e:
        log("  ! Non ho potuto rimettere la lingua '%s' nelle opzioni del gioco (%s)." % (precedente, e))
        return False
    log("  Lingua del gioco: rimessa '%s' (prima era '%s', la lingua aggiunta)" % (precedente, posto[3]))
    if registro.get("tipo") == "gamemaker_opzioni":
        try:
            os.remove(_registro(cartella))
        except OSError:
            pass
    return True


def _radici_leva(cartella):
    """The folders where the configurator's language lever can be (the chosen one and the game's)."""
    from . import configurator as configuratore, games as schede
    radici = [os.path.abspath(cartella)]
    for radice in schede.riconosci(cartella)[1]:
        if os.path.normcase(radice) not in {os.path.normcase(r) for r in radici}:
            radici.append(radice)
    return [r for r in radici if configuratore.leva(r, "lingua")]


def impostata(cartella):
    return os.path.exists(_registro(cartella)) or bool(_radici_leva(cartella))


def ripristina(cartella, log=print):
    """Puts the language back as it was before PatchSmith. None if there was nothing to put back, True if done (or
    if it had already changed in the meantime), False if it could not be done: the log stays so it can be retried."""
    from . import configurator as configuratore
    esiti = [_ripristina_impostazione(cartella, log)]
    for radice in _radici_leva(cartella):
        esito = configuratore.ripristina(radice, "lingua", log)
        if esito:
            log("  Lingua del gioco: rimessa com'era prima")
        esiti.append(esito)
    if False in esiti:
        return False
    return True if True in esiti else None


def _ripristina_impostazione(cartella, log):
    percorso = _registro(cartella)
    if not os.path.exists(percorso):
        return None
    dati = _leggi_registro(cartella)
    if not isinstance(dati, dict):
        log("  ! Lingua del gioco: registro illeggibile (%s): scegli la lingua nel menu del gioco." % percorso)
        return False
    motivo = _aperto(cartella)
    if motivo:
        log("  ! Lingua del gioco non rimessa: %s (resta da fare)." % motivo)
        return False
    try:
        if dati.get("tipo") == "unreal":
            from . import language_unreal as lingua_unreal
            ok = lingua_unreal.ripristina_cultura(dati["file"], dati["modifiche"], dati["tag"]) > 0
        elif dati.get("tipo") == "unity_i2":
            from . import language_unity as lingua_unity
            precedente = tuple(dati["precedente"]) if dati.get("precedente") else None
            ok = lingua_unity.ripristina_pref(dati["azienda"], dati["prodotto"], dati["chiave"], precedente,
                                              bytes.fromhex(dati["scritto"]))
        elif dati.get("tipo") == "gamemaker_opzioni":
            from . import language_gamemaker as lingua_gamemaker
            ok = lingua_gamemaker.ripristina(dati)
        else:
            ok = False
    except (OSError, ValueError, TypeError, KeyError) as e:
        dove = dati.get("file") or "registro di Windows, HKCU\\Software\\%s\\%s" % (dati.get("azienda"),
                                                                                   dati.get("prodotto"))
        log("  ! Non ho potuto rimettere la lingua del gioco com'era (%s; %s): togli la sola lettura e riprova, "
            "oppure scegli la lingua nel menu del gioco." % (e, dove))
        return False
    log("  Lingua del gioco: %s" % ("rimessa com'era prima" if ok else
                                    "cambiata nel frattempo (dal gioco o da te): la lascio com'e'"))
    os.remove(percorso)
    return True


# --- messages -------------------------------------------------------------------------------------

def messaggio(rilevamento, destinazione, impostazione=None, fatto=False):
    """Lines to show the user when the official language is there."""
    nome = lingua.etichetta(destinazione)
    righe = ["Il gioco ha gia' la traduzione ufficiale in %s: e' meglio di una traduzione automatica." % nome]
    righe += ["  - " + f for f in rilevamento["fonti"]]
    if impostazione:
        righe += impostazione
    if fatto is None:  # proposal waiting for the user's consent: no other instructions
        return righe
    if (rilevamento.get("motore") or {}).get("tipo") == "scheda" and impostazione:
        return righe  # the game sheet already said what it did, or why not and what to do
    info = rilevamento.get("steam")
    if info:
        if rilevamento.get("lingua_steam") == _nome_steam(destinazione):
            righe.append("Steam usa gia' %s per questo gioco: se il gioco e' in un'altra lingua, sceglila nelle sue "
                         "opzioni." % nome)
        elif rilevamento.get("selezionabile_steam"):
            righe.append("In Steam: Libreria > tasto destro su \"%s\" > Proprieta' > Generali > Lingua > %s." % (
                info.get("nome") or "il gioco", nome))
        elif not impostazione:
            righe.append("Scegli %s nelle opzioni del gioco (in Steam questo gioco non ha il menu Lingua)." % nome)
    elif rilevamento.get("steam_conosciuto"):
        if fatto is False:
            righe.append("Non posso impostarla io: questa cartella non e' un'installazione di Steam, e molti giochi "
                         "usano la lingua che gli passa il negozio (Steam o Epic) all'avvio.")
            if rilevamento.get("selezionabile_steam"):
                righe.append("Da Steam: Libreria > tasto destro sul gioco > Proprieta' > Generali > Lingua > %s." % nome)
            righe.append("Altrimenti cerca %s nelle opzioni del gioco o nel launcher da cui lo avvii." % nome)
    elif not impostazione:
        righe.append("Per attivarla: scegli %s nelle opzioni del gioco (o nel launcher da cui lo avvii)." % nome)
    return righe
