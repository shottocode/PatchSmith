"""Lingua ufficiale: se il gioco ha gia' la traduzione nella lingua di arrivo, si usa quella invece di tradurre.

Fonti:
1. Steam: per un gioco installato da Steam, la cache di Steam (appinfo.vdf) elenca le lingue ufficiali;
2. i file del gioco, motore per motore (Unreal: .locres per lingua; Unity: lingue di I2 / Unity Localization;
   XNA/.NET: satelliti; GameMaker: file per lingua);
3. per una cartella che non e' un'installazione Steam, il gioco si puo' riconoscere dagli eseguibili: vale solo
   se i file non dicono nulla e il nome della cartella somiglia a quello del gioco, altrimenti e' solo una nota.
Per il cinese conta la scrittura (zh-TW e zh-CN sono lingue diverse).
Quando la lingua c'e', PatchSmith la IMPOSTA dove esiste un'impostazione standard, sicura e reversibile
(Unreal: GameUserSettings.ini; Unity con I2: la preferenza "I2 Language"), a gioco chiuso, con registro delle
modifiche e ripristino; altrimenti spiega esattamente come sceglierla. Non modifica Steam ne' componenti non
ufficiali.
"""
import datetime
import hashlib
import json
import os

from . import lingua, steam


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


# --- rilevamento ----------------------------------------------------------------------------------

def rileva(cartella, destinazione, motore=None, log=None):
    """Cosa si sa della lingua 'destinazione' nel gioco.

    {'presente': True/False/None, 'fonti': [testo], 'note': [testo], 'steam': info installazione o None,
     'steam_conosciuto': gioco riconosciuto dagli eseguibili o None, 'lingua_steam': lingua attuale in Steam,
     'selezionabile_steam': True/False/None, 'motore': {...dati per l'impostazione...}}"""
    r = {"presente": None, "fonti": [], "note": [], "steam": None, "steam_conosciuto": None, "lingua_steam": None,
         "selezionabile_steam": None, "motore": {}}
    nome = _nome_steam(destinazione)
    etichetta = lingua.etichetta(destinazione)

    # 1) Steam (un problema con la cache di Steam non deve mai bloccare la traduzione)
    info, conosciuto, steam_dice, selezionabile = _dati_steam(cartella, nome, log)
    # 2) file del gioco, secondo il motore
    try:
        presenti, dati_motore = lingue_nei_file(cartella, motore, destinazione)
    except Exception as e:  # un rilevatore che fallisce non deve bloccare la traduzione
        presenti, dati_motore = None, {}
        if log:
            log("  ! Controllo delle lingue nei file non riuscito (%s: %s)" % (type(e).__name__, e))
    r["motore"] = dati_motore
    radice = dati_motore.get("radice") if dati_motore.get("tipo") == "scheda" else None
    if not info and radice and os.path.normcase(radice) != os.path.normcase(os.path.abspath(cartella)):
        # scelta la cartella sopra il gioco: l'installazione Steam si riconosce dalla cartella del gioco
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

    # 3) decisione
    if info:  # installazione Steam: vale Steam (la lingua puo' stare in un pacchetto non ancora scaricato)
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
    """(installazione Steam, gioco riconosciuto dagli eseguibili, lingua ufficiale per Steam, selezionabile in
    Steam); None dove non si sa. Gli errori di Steam non fermano nulla."""
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
    """({lingue} oppure None se non si sa, dati per impostarla; 'trovata' dice se c'e' la lingua cercata).
    Le schede di gioco (giochi conosciuti uno per uno) vengono prima del motore."""
    from . import schede
    scheda, radici = schede.riconosci(cartella)
    if scheda:
        lingue = scheda.lingue_presenti(radici[0])
        return lingue, {"tipo": "scheda", "scheda": scheda.ID, "radice": radici[0] if len(radici) == 1 else None,
                        "radici": radici, "trovata": bool(lingue) and lingua.migliore(lingue, destinazione) is not None}
    if motore == "Unreal":
        from . import lingua_unreal
        lingue, progetto = lingua_unreal.lingue_del_gioco(cartella)
        tag = lingua_unreal.tag_italiano(lingue, destinazione) if lingue else None
        return lingue, {"tipo": "unreal", "progetto": progetto, "tag": tag, "trovata": tag is not None}
    if motore == "Unity":
        from . import lingua_unity
        dati = lingua_unity.cartella_dati(cartella)
        if not dati:
            return None, {}
        lingue, voce, i2, ul = set(), None, None, None
        try:
            i2 = lingua_unity.lingue_i2(dati) if lingua_unity.usa_i2(dati) else None
        except Exception:  # I2 illeggibile: resta Unity Localization
            i2 = None
        if i2:
            lingue |= {c or n for n, c in i2}
            voce = lingua_unity.voce_i2(i2, destinazione)
        try:
            ul = lingua_unity.lingue_unity_localization(dati)
        except Exception:
            ul = None
        # anche le lingue delle tabelle dei testi, riconosciute dal contenuto dei bundle: il catalogo non le elenca
        # sempre sotto "Locales/" (es. "Locals/Italian (it).asset", o nessun nome con il codice)
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
        insiemi = adapter_xna.trova_risorse(cartella)  # i satelliti creati da PatchSmith non contano
        if not insiemi:
            return None, {"tipo": "xna"}
        codici = {c for i in insiemi for culture in i["basi"].values() for c in culture}
        codici |= {i["lingua_neutra"] for i in insiemi if i["lingua_neutra"]}
        return codici, {"tipo": "xna", "trovata": lingua.migliore(codici, destinazione) is not None}
    if motore == "GameMaker":
        from . import adapter_csv, adapter_gamemaker
        trovati = adapter_gamemaker.file_localizzazione(cartella)
        trovati.update(adapter_gamemaker.file_json_lingue(cartella))  # anche i file JSON per lingua
        lingue = {l for l in trovati.values() if l}
        # e le tabelle CSV con una colonna per lingua: contano le colonne piene (una vuota e' una lingua prevista
        # ma non tradotta)
        tabelle = adapter_csv.tabelle(cartella)
        for f in tabelle.values():
            lingue |= f.lingue_piene()
        return (lingue if trovati or tabelle else None), {"tipo": "gamemaker",
                                                          "trovata": lingua.migliore(lingue, destinazione) is not None}
    return None, {}


# --- impostazione e ripristino --------------------------------------------------------------------

def _registro(cartella):
    from .progetto import cartella_dati
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
    """True la prima volta che si chiede di avviare il gioco per creare le sue impostazioni (poi non si ripete,
    per non chiedere all'infinito di avviarlo se le impostazioni stanno altrove)."""
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
    """None se il gioco e' chiuso, altrimenti il motivo per non toccare nulla."""
    from . import sistema
    try:
        if sistema.gioco_aperto(cartella):
            return "il gioco e' aperto: chiudilo e riprova"
    except sistema.ErroreSistema as e:
        return str(e)
    return None


def imposta(cartella, rilevamento, destinazione, log=print, consenso=False):
    """Imposta la lingua dove si puo' farlo in sicurezza. Restituisce (fatto, [righe da mostrare]); fatto e' None
    quando serve il consenso dell'utente (es. per modificare gli eseguibili): allora le righe sono la proposta."""
    m = rilevamento.get("motore") or {}
    nome = lingua.etichetta(destinazione)
    nel_menu = "scegli %s nel menu delle opzioni del gioco" % nome
    if m.get("tipo") == "scheda":
        from . import schede
        if rilevamento.get("steam") and rilevamento.get("selezionabile_steam"):
            return False, []  # installato da Steam: la lingua si sceglie li' (il messaggio dice dove)
        scheda = schede.per_id(m.get("scheda"))
        if scheda and not m.get("radice") and len(m.get("radici") or []) > 1:
            return False, ["Nella cartella ci sono %d copie di %s (%s): scegli la cartella di una sola."
                           % (len(m["radici"]), scheda.NOME, "; ".join(m["radici"]))]
        if scheda and m.get("radice"):
            return scheda.imposta_lingua(m["radice"], destinazione, consenso, log)
    if m.get("tipo") == "unreal" and m.get("tag"):
        from . import lingua_unreal
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
            modifiche = precedente["modifiche"] + modifiche  # i valori piu' vecchi (quelli originali) vincono
        righe = ["Ho impostato %s nelle impostazioni del gioco (%s, [Internationalization])." % (nome, gus),
                 "Avvia il gioco: se e' ancora in un'altra lingua, il gioco usa un suo menu: %s." % nel_menu]
        try:
            _salva_registro(cartella, {"tipo": "unreal", "file": gus, "tag": m["tag"], "modifiche": modifiche,
                                       "data": _adesso()})
        except OSError as e:
            righe.append("Attenzione: non ho potuto salvare il registro per il ripristino (%s)." % e)
        return True, righe
    if m.get("tipo") == "unity" and m.get("i2_lingua"):
        from . import lingua_unity
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
            prima = vecchio.get("precedente")  # il valore di prima della prima volta
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
    """Dopo aver aggiunto una lingua al gioco (es. local/italiano.json di un gioco GameMaker), la imposta nelle
    opzioni salvate dal gioco, con copia di sicurezza e registro per il ripristino. (fatto, [righe]).
    Se dopo la volta precedente il giocatore ha scelto un'altra lingua, la sua scelta resta. forza=True: la lingua
    l'ha cambiata il gioco, non il giocatore (es. un aggiornamento aveva tolto il file della lingua aggiunta e il
    gioco e' tornato all'originale: lo dice il controllo dopo aver giocato)."""
    from . import lingua_gamemaker
    from .progetto import cartella_dati
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
        registro["precedente"] = vecchio["precedente"]  # vale la lingua di prima della prima volta
    elif not registro.get("precedente") and sorgente:
        registro["precedente"] = sorgente  # gia' impostata (es. dal giocatore): al ripristino torna l'originale
    registro["data"] = _adesso()
    try:
        _salva_registro(cartella, registro)
    except OSError as e:
        return True, ["Attenzione: non ho potuto salvare il registro per il ripristino (%s)." % e]
    return True, []


def rimetti_lingua_aggiunta(cartella, aggiunte, sorgente, lingue, log=print):
    """Prima di togliere le lingue aggiunte da PatchSmith (es. 'italiano'): se le opzioni salvate dal gioco ce
    l'hanno ancora, si rimette la lingua di prima, cosi' il gioco non riparte con una lingua che non c'e' piu'.
    Solo se e' stato PatchSmith a impostarla per QUESTA cartella (registro): le opzioni stanno in %LOCALAPPDATA%
    con il nome del gioco, e una copia del gioco (es. di prova) le condivide con quello vero, che puo' avere la
    sua lingua aggiunta. Senza registro si lascia tutto (Forager, senza il file della lingua, torna all'inglese).
    None se non c'era niente da fare, True se rimessa, False se serviva ma non si e' potuto."""
    from . import lingua_gamemaker
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
    """Le cartelle in cui puo' esserci la leva della lingua del configuratore (la scelta e quella del gioco)."""
    from . import configuratore, schede
    radici = [os.path.abspath(cartella)]
    for radice in schede.riconosci(cartella)[1]:
        if os.path.normcase(radice) not in {os.path.normcase(r) for r in radici}:
            radici.append(radice)
    return [r for r in radici if configuratore.leva(r, "lingua")]


def impostata(cartella):
    return os.path.exists(_registro(cartella)) or bool(_radici_leva(cartella))


def ripristina(cartella, log=print):
    """Rimette la lingua com'era prima di PatchSmith. None se non c'era niente da rimettere, True se fatto (o se
    nel frattempo era gia' cambiata), False se non si e' potuto: il registro resta per riprovare."""
    from . import configuratore
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
            from . import lingua_unreal
            ok = lingua_unreal.ripristina_cultura(dati["file"], dati["modifiche"], dati["tag"]) > 0
        elif dati.get("tipo") == "unity_i2":
            from . import lingua_unity
            precedente = tuple(dati["precedente"]) if dati.get("precedente") else None
            ok = lingua_unity.ripristina_pref(dati["azienda"], dati["prodotto"], dati["chiave"], precedente,
                                              bytes.fromhex(dati["scritto"]))
        elif dati.get("tipo") == "gamemaker_opzioni":
            from . import lingua_gamemaker
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


# --- messaggi -------------------------------------------------------------------------------------

def messaggio(rilevamento, destinazione, impostazione=None, fatto=False):
    """Righe da mostrare all'utente quando la lingua ufficiale c'e'."""
    nome = lingua.etichetta(destinazione)
    righe = ["Il gioco ha gia' la traduzione ufficiale in %s: e' meglio di una traduzione automatica." % nome]
    righe += ["  - " + f for f in rilevamento["fonti"]]
    if impostazione:
        righe += impostazione
    if fatto is None:  # proposta in attesa del consenso dell'utente: niente altre istruzioni
        return righe
    if (rilevamento.get("motore") or {}).get("tipo") == "scheda" and impostazione:
        return righe  # la scheda ha gia' detto cosa ha fatto, o perche' no e cosa fare
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
