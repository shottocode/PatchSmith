"""Reading the data written by earlier versions, when the keys and codes of PatchSmith's files were Italian.

Settings, game profiles, the side files of the translation memory, the after-play check state, the backup info next to
the game's files, manifests, the journal of the live translator... are upgraded when they are read: old keys renamed
(KEYS), stored codes mapped (CODES, only in the fields that hold them). Files are written with the new names only.

Safety rules:
- a dictionary's keys are renamed only if all of them look like names (no paths, no texts with spaces): the file
  lists of manifests and similar maps are never touched;
- the translation memory (game text -> translation) is never passed here: a game text could be any word;
- a key already present with its new name wins over the old one (a file half written by both versions).
"""
import json
import re

# old key -> new key (the names the code used before it was translated into English)
KEYS = {
    'Argomenti': 'Arguments', 'CARTELLA_SBAGLIATA': 'WRONG_FOLDER', 'Cartella': 'Folder', 'Data': 'Date',
    'Dettaglio': 'Detail', 'ERRORE': 'ERROR', 'Esito': 'Outcome', 'GIA_ORIGINALE': 'ALREADY_ORIGINAL',
    'GIOCO_APERTO': 'GAME_OPEN', 'LINGUA': 'LANGUAGE', 'LOCALE_ASSENTE': 'LOCAL_MISSING',
    'NON_SUPPORTATO': 'NOT_SUPPORTED', 'NULLA_DA_FARE': 'NOTHING_TO_DO', 'PARZIALE': 'PARTIAL',
    'PERMESSI': 'PERMISSIONS', 'Processo': 'Process', 'Programma': 'Program', 'SOLA_LETTURA': 'READ_ONLY',
    'SPAZIO': 'DISK_SPACE', 'STESSO_GIOCO': 'SAME_GAME', 'TRADUTTORE_ASSENTE': 'TRANSLATOR_MISSING',
    'TRADUTTORE_BLOCCATO': 'TRANSLATOR_BLOCKED', 'TRADUTTORE_IN_PAUSA': 'TRANSLATOR_PAUSED', 'UFFICIALE': 'OFFICIAL',
    '_nota': '_note', 'a_capo': 'line_break', 'a_freddo': 'cold', 'accento': 'accent', 'accorciate': 'shortened',
    'adattate_font': 'font_adapted', 'aggiornato': 'updated', 'aggiunte': 'additions', 'albero': 'tree',
    'aperto': 'is_open', 'architettura': 'architecture', 'argomenti': 'arguments', 'automatica': 'is_machine',
    'avvio': 'startup', 'avviso': 'warning', 'azienda': 'company', 'azione': 'action', 'bandiera': 'flag',
    'basi': 'bases', 'blocco': 'block', 'bordo': 'border', 'cache_corrette': 'cache_fixed', 'campo': 'field',
    'caratteri': 'chars', 'caratteri_mancanti': 'missing_chars', 'cartella': 'folder',
    'cartella_gioco': 'game_folder', 'cartella_precedente': 'previous_folder', 'cartella_simile': 'similar_folder',
    'cartelle_create': 'created_folders', 'catalogo': 'catalog', 'cfg-mancante': 'cfg-missing', 'chiaro': 'light',
    'chiave': 'key', 'chiave_deepl': 'deepl_key', 'chiave_pubblica': 'public_key', 'chiavi': 'keys',
    'chiesta': 'requested', 'chiudi_gioco': 'close_game', 'cirillico': 'cyrillic', 'classe': 'class', 'clic': 'click',
    'codice': 'code', 'codici': 'codes', 'colonna': 'column', 'comando': 'command', 'completa': 'complete',
    'componenti': 'components', 'con_email': 'with_email', 'configuratore': 'configurator', 'conflitti': 'conflicts',
    'consensi': 'consents', 'consigli': 'advice', 'continua': 'continue', 'controlla': 'check', 'controllo': 'checks',
    'copia': 'copy_dir', 'correggi': 'fix', 'corrette_a_mano_scartate': 'hand_fixes_discarded',
    'corretti': 'fixed_count', 'corretto': 'fixed', 'correzioni_scritte': 'fixes_written',
    'crea_rapporto': 'create_report', 'creati': 'created', 'cultura': 'culture', 'da_correggere': 'to_fix',
    'da_fare': 'to_do', 'da_profilo': 'from_profile', 'da_regole': 'from_rules', 'dal': 'since', 'dal_vivo': 'live',
    'dal_vivo_nota': 'live_note', 'data': 'date', 'dati': 'data_folder', 'deepl_da_variabile': 'deepl_from_env',
    'descrizione': 'description', 'dettaglio': 'detail', 'diario_fino': 'journal_until', 'dimensione': 'size',
    'disattivato': 'disabled', 'divisi': 'split', 'dizionario': 'dictionary', 'domanda': 'question',
    'dopo_traffico': 'after_traffic', 'dove': 'where', 'entrypoint_forzato': 'forced_entrypoint', 'errore': 'error',
    'escludi': 'exclude', 'eseguibile': 'executable', 'esempi': 'examples', 'esito': 'outcome', 'etichetta': 'label',
    'eventi': 'events', 'eventi_dal': 'events_since', 'evidenze': 'evidence', 'fatto': 'done',
    'file_esaminati': 'files_examined', 'finestre': 'windows', 'fino': 'until', 'finto': 'fake', 'firmati': 'signed',
    'firmato': 'signed', 'fonte': 'source', 'fonti': 'sources', 'formato': 'format', 'fotogrammi': 'frames',
    'frasi': 'sentences', 'gia_tentato': 'already_tried', 'gioco': 'game', 'gravita': 'severity',
    'hash_creati': 'created_hashes', 'hash_originale': 'original_hash', 'hash_scritti': 'written_hashes',
    'i2_lingua': 'i2_language', 'i2_personalizzato': 'i2_custom', 'identita': 'identity', 'impostata': 'is_set',
    'imprevisto': 'unexpected', 'impronta': 'fingerprint', 'in_corso': 'in_progress', 'includi': 'include',
    'insieme': 'item_set', 'insiemi': 'resource_sets', 'installa_locale': 'install_local', 'installato': 'installed',
    'interfaccia': 'interface', 'interlinea': 'line_spacing', 'interrotta': 'interrupted', 'intervalli': 'ranges',
    'intestazione': 'header', 'istante': 'instant', 'larghezza': 'width', 'leggibili': 'readable',
    'lettere_ufficiali': 'official_letters', 'lettore': 'reader', 'leve': 'levers', 'liberi': 'free_bytes',
    'licenza': 'license', 'limiti': 'limits', 'lingua': 'language', 'lingua_aggiunta': 'added_language',
    'lingua_destinazione': 'target_language', 'lingua_file': 'file_language', 'lingua_impostata': 'language_set',
    'lingua_neutra': 'neutral_language', 'lingua_nuova': 'new_language', 'lingua_origine': 'source_language',
    'lingua_steam': 'steam_language', 'lingua_ufficiale': 'official_language', 'lingue': 'languages',
    'livello': 'level', 'locale': 'local', 'localizzazione': 'localization', 'lunghi': 'long',
    'maiuscole': 'capitals', 'mancanti': 'missing', 'manifesto': 'manifest', 'mano': 'by_hand', 'mappa': 'mapping',
    'messaggio': 'message', 'miniera': 'mine', 'modelli': 'models', 'modello': 'model',
    'modello_locale': 'local_model', 'modifiche': 'changes', 'modo': 'mode', 'mostra_rimasti': 'show_remaining',
    'motivo': 'reason', 'motore': 'engine', 'motore_gioco': 'game_engine', 'motore_principale': 'main_engine',
    'motore_rilevato': 'detected_engine', 'motore_supportato': 'engine_supported', 'musica': 'music',
    'nessuno': 'none', 'nome': 'name', 'nomi_trovati': 'found_names', 'non_riuscito': 'failed',
    'non_tradurre': 'do_not_translate', 'non_usare': 'do_not_use', 'note': 'notes', 'ok_totali': 'ok_total',
    'ordine_hash': 'hash_order', 'orizzontali': 'horizontal', 'override_originale': 'original_override',
    'parallele': 'parallel', 'patchsmith-mancante': 'patchsmith-missing', 'patchsmith_versione': 'patchsmith_version',
    'pausa': 'pause', 'percorso': 'path', 'plugin_nessuno': 'plugin_none', 'porta': 'port', 'posto': 'spot',
    'precedente': 'previous', 'precedenti': 'prior', 'predefinito': 'default', 'premuto': 'pressed',
    'presente': 'present', 'prima': 'before', 'problemi': 'problems', 'prodotto': 'product', 'profilo': 'profile',
    'progetto': 'project', 'programma': 'program', 'pronto': 'ready', 'protetti': 'protected', 'pulsante': 'button',
    'pulsante_sopra': 'button_hover', 'pulsanti': 'buttons', 'punteggio': 'score', 'punto_elenco': 'bullet',
    'python-mancante': 'python-missing', 'quando': 'when', 'radice': 'root', 'radice_prima': 'root_before',
    'radici': 'roots', 'radici_web': 'web_roots', 'riapplica': 'reapply', 'richieste': 'requests',
    'rifatte': 'redone', 'riferimenti': 'references', 'righe': 'lines', 'rimandato': 'postponed',
    'rimaste': 'remaining_count', 'rimasti': 'remaining', 'rimedi': 'remedies', 'ripiegati': 'fallen_back',
    'ripristina': 'restore', 'riprova': 'retry', 'risposta': 'response', 'risultati': 'results',
    'ritentati': 'retried', 'ritradotte': 'retranslated', 'ritraduci': 'retranslate', 'ritraduzione': 'retranslation',
    'riusa': 'reuse', 'riuscito': 'success', 'scartate': 'discarded', 'scheda': 'sheet',
    'schemi_localizzazione': 'localization_patterns', 'scritti': 'writes', 'scritto': 'written', 'scuro': 'dark',
    'secondi': 'seconds', 'secondi_avvio': 'startup_seconds', 'secondi_per_testo': 'seconds_per_text',
    'segnale': 'signal', 'segnaposto': 'placeholders', 'selezionabile_steam': 'steam_selectable',
    'selezione': 'selection', 'senza_traduttore': 'no_translator', 'serve_consenso': 'needs_consent',
    'sezione': 'section', 'sfondo': 'background', 'sicuro': 'safe', 'solo_creati': 'created_only',
    'sorgenti': 'sources', 'sospetti': 'suspicious', 'sostituite': 'replaced', 'spaziatura': 'spacing',
    'spazio': 'space', 'sperimentale': 'experimental', 'sperimentale_possibile': 'experimental_possible',
    'spostato': 'moved', 'statistiche': 'stats', 'stato': 'state', 'steam_conosciuto': 'steam_known',
    'stringhe': 'strings', 'stringhe_leggibili': 'readable_strings', 'suoni': 'sounds', 'supportato': 'is_supported',
    'tabella': 'table', 'tema': 'theme', 'tentativi': 'attempts', 'tenue': 'muted', 'testa': 'head', 'testi': 'texts',
    'testi_localizzazione': 'localization_texts', 'testo': 'text', 'tipi': 'kinds', 'tipo': 'kind', 'titolo': 'title',
    'traduci': 'translate', 'traduci_comunque': 'translate_anyway', 'traduttore': 'translator',
    'traduzione': 'translation', 'traduzioni': 'translations', 'traduzioni_da': 'translations_from',
    'troncato': 'truncated', 'troppo_lunghe': 'too_long', 'trovata': 'found', 'tutte': 'all', 'ultimo': 'last',
    'ultimo_controllo': 'last_check', 'ultimo_ok': 'last_ok', 'usa_locale': 'use_local', 'uscita': 'output',
    'valore': 'value', 'variazione': 'variation', 'versione': 'version', 'versione_unity': 'unity_version',
    'versioni': 'versions', 'verticali': 'vertical', 'visti': 'seen', 'voci': 'entries', 'voluta': 'wanted',
    'web_vivo': 'web_live',
}
# new key -> {old code: new code}, for the fields that store codes
CODES = {
    'Outcome': {'avviato': 'started', 'cfg-mancante': 'cfg-missing', 'errore': 'error',
        'patchsmith-mancante': 'patchsmith-missing', 'python-mancante': 'python-missing'},
    'action': {'chiudi_gioco': 'close_game', 'correggi': 'fix', 'crea_rapporto': 'create_report',
        'installa_locale': 'install_local', 'mostra_rimasti': 'show_remaining', 'riapplica': 'reapply',
        'ripristina': 'restore', 'ritraduci': 'retranslate', 'scegli_lingua': 'choose_language',
        'sperimentale': 'experimental', 'traduci': 'translate', 'traduci_comunque': 'translate_anyway',
        'usa_locale': 'use_local'},
    'backend': {'comune': 'shared', 'finto': 'fake', "gia' tradotta": 'already_translated', 'locale': 'local',
        'memoria': 'memory', 'prova': 'test'},
    'capitals': {'maiuscolo': 'uppercase', 'minuscolo': 'lowercase', 'non applicabile': 'not applicable',
        'normale': 'normal'},
    'changes': {'aggiunta': 'added', 'riga': 'line', 'sezione': 'section'},
    'code': {'AGGIORNATO': 'UPDATED', 'AVVIO_INCOMPLETO': 'INCOMPLETE_STARTUP', 'CARTELLA_SBAGLIATA': 'WRONG_FOLDER',
        'CONFIGURAZIONE': 'CONFIGURATION', 'CORREZIONI_IN_ATTESA': 'PENDING_FIXES', 'CRASH_AVVIO': 'STARTUP_CRASH',
        'CRASH_CON_MOD': 'MOD_CRASH', 'DAL_VIVO': 'LIVE', 'ERRORE': 'ERROR', 'FILE_MANCANTI': 'MISSING_FILES',
        'FONT_MANCANTI': 'MISSING_FONT_CHARS', 'FRASI_DA_RIFARE': 'SENTENCES_TO_REDO',
        'FRASI_DA_RITRADURRE': 'SENTENCES_TO_RETRANSLATE', 'FRASI_PERSE': 'LOST_SENTENCES',
        'GIA_ORIGINALE': 'ALREADY_ORIGINAL', 'GIOCO_APERTO': 'GAME_OPEN',
        'INSTALLAZIONE_INTERROTTA': 'INSTALL_INTERRUPTED', 'LINGUA': 'LANGUAGE', 'LINGUA_AGGIUNTA': 'LANGUAGE_ADDED',
        'LINGUA_UNREAL': 'UNREAL_LANGUAGE', 'LOCALE_ASSENTE': 'LOCAL_MISSING', 'MANIFESTO': 'MANIFEST',
        'NON_SUPPORTATO': 'NOT_SUPPORTED', 'NULLA_DA_FARE': 'NOTHING_TO_DO', 'PAK_NON_CARICATO': 'PAK_NOT_LOADED',
        'PARZIALE': 'PARTIAL',
        'PERMESSI': 'PERMISSIONS',
        'RIPRISTINATO_DAL_GIOCO': 'RESTORED_BY_GAME', 'SERVIZIO': 'SERVICE', 'SERVIZIO_REGISTRO': 'SERVICE_LOG',
        'SOLA_LETTURA': 'READ_ONLY', 'SPAZIO': 'DISK_SPACE', 'STESSO_GIOCO': 'SAME_GAME', 'TEMPORANEI': 'TEMP_FILES',
        'TRADUTTORE_ASSENTE': 'TRANSLATOR_MISSING', 'TRADUTTORE_BLOCCATO': 'TRANSLATOR_BLOCKED',
        'TRADUTTORE_FERMO': 'TRANSLATOR_STOPPED', 'TRADUTTORE_IN_PAUSA': 'TRANSLATOR_PAUSED',
        'TRADUZIONE_TOLTA': 'TRANSLATION_REMOVED', 'UFFICIALE': 'OFFICIAL', 'VERSIONE': 'VERSION'},
    'configurator': {'lingua': 'language'},
    'consents': {'eseguibili': 'executables'},
    'engine': {'web-vivo': 'web-live'},
    'format': {'righe': 'lines', 'unity-tabelle': 'unity-tables'},
    'game_engine': {'web-vivo': 'web-live'},
    'kind': {'a_freddo': 'cold', 'dopo_traffico': 'after_traffic', 'gamemaker_opzioni': 'gamemaker_options',
        'scheda': 'sheet'},
    'levers': {'lingua': 'language'},
    'live': {'locale': 'local', 'nessuno': 'none', 'servizio': 'service'},
    'notes': {'salvataggi_copiati': 'saves_copied'},
    'outcome': {'errore': 'error', 'interrotta': 'interrupted', 'rifiutata': 'rejected', 'rilassata': 'relaxed',
        'rinviata': 'deferred'},
    'remedies': {'AGGIORNATO': 'UPDATED', 'AVVIO_INCOMPLETO': 'INCOMPLETE_STARTUP',
        'CARTELLA_SBAGLIATA': 'WRONG_FOLDER', 'CONFIGURAZIONE': 'CONFIGURATION',
        'CORREZIONI_IN_ATTESA': 'PENDING_FIXES', 'CRASH_AVVIO': 'STARTUP_CRASH', 'CRASH_CON_MOD': 'MOD_CRASH',
        'DAL_VIVO': 'LIVE', 'ERRORE': 'ERROR', 'FILE_MANCANTI': 'MISSING_FILES',
        'FONT_MANCANTI': 'MISSING_FONT_CHARS', 'FRASI_DA_RIFARE': 'SENTENCES_TO_REDO',
        'FRASI_DA_RITRADURRE': 'SENTENCES_TO_RETRANSLATE', 'FRASI_PERSE': 'LOST_SENTENCES',
        'GIA_ORIGINALE': 'ALREADY_ORIGINAL', 'GIOCO_APERTO': 'GAME_OPEN',
        'INSTALLAZIONE_INTERROTTA': 'INSTALL_INTERRUPTED', 'LINGUA': 'LANGUAGE', 'LINGUA_AGGIUNTA': 'LANGUAGE_ADDED',
        'LINGUA_UNREAL': 'UNREAL_LANGUAGE', 'LOCALE_ASSENTE': 'LOCAL_MISSING', 'MANIFESTO': 'MANIFEST',
        'NON_SUPPORTATO': 'NOT_SUPPORTED', 'NULLA_DA_FARE': 'NOTHING_TO_DO', 'PAK_NON_CARICATO': 'PAK_NOT_LOADED',
        'PARZIALE': 'PARTIAL',
        'PERMESSI': 'PERMISSIONS',
        'RIPRISTINATO_DAL_GIOCO': 'RESTORED_BY_GAME', 'SERVIZIO': 'SERVICE', 'SERVIZIO_REGISTRO': 'SERVICE_LOG',
        'SOLA_LETTURA': 'READ_ONLY', 'SPAZIO': 'DISK_SPACE', 'STESSO_GIOCO': 'SAME_GAME', 'TEMPORANEI': 'TEMP_FILES',
        'TRADUTTORE_ASSENTE': 'TRANSLATOR_MISSING', 'TRADUTTORE_BLOCCATO': 'TRANSLATOR_BLOCKED',
        'TRADUTTORE_FERMO': 'TRANSLATOR_STOPPED', 'TRADUTTORE_IN_PAUSA': 'TRANSLATOR_PAUSED',
        'TRADUZIONE_TOLTA': 'TRANSLATION_REMOVED', 'UFFICIALE': 'OFFICIAL', 'VERSIONE': 'VERSION',
        'correzioni': 'corrections', 'riapplica': 'reapply', 'riconfigura': 'reconfigure'},
    'seen': {'da_scrivere': 'to_write', 'diario': 'journal', 'manifesto': 'manifest', 'versione': 'version'},
    'severity': {'avviso': 'warning', 'errore': 'error'},
    'state': {'applicata': 'applied', 'corretto': 'fixed', 'da_correggere': 'to_fix', 'da_fare': 'to_do',
        'gia_tentato': 'already_tried', 'in_corso': 'in_progress', 'non_riuscito': 'failed', 'rimandato': 'postponed',
        'trovato': 'found'},
    'theme': {'chiaro': 'light', 'scuro': 'dark'},
    'where': {'gioco': 'game', 'lunghezza': 'length', 'memoria': 'memory', 'traduzione': 'translation'},
}

# maps whose keys are chosen outside PatchSmith (files of the game, texts, language codes): never renamed
OPAQUE = {"file", "texts", "sources", "prior", "dictionary", "translations", "references"}
# "da"/"a" (source and target language) are renamed only together: alone, "da" is the Danish language code
PAIR = {"da": "from", "a": "to"}
# the signals of the after-play check (in the fixes already tried and among the signals seen): old -> new, whole
# signals and prefixes
SIGNALS = {"in_corso": "in_progress", "manifesto": "manifest"}
SIGNAL_PREFIXES = {"mancanti:": "missing:", "servizio:": "service:", "dal_vivo:": "live:", "versione:": "version:",
                   "diario:": "journal:", "diario-perse:": "journal-lost:", "correzioni:": "corrections:",
                   "registro:": "service-log:", "ripristinato:": "restored:", "catalogo:": "catalog:",
                   "cambiati:": "changed:", "lingua:": "language:", "tolti:": "removed:", "sorgenti:": "sources:",
                   "eventi:": "events:", "creato:": "created:", "opzioni:": "options:"}

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*$")


def upgrade(data):
    """The data with the old keys and codes renamed (a new object; the input is not changed)."""
    if isinstance(data, list):
        return [upgrade(x) for x in data]
    if not isinstance(data, dict):
        return data
    names = all(isinstance(k, str) and _NAME.match(k) for k in data)
    pair = names and all(k in data for k in PAIR)
    output = {}
    for k, v in data.items():
        new = (PAIR.get(k) if pair and k in PAIR else KEYS.get(k, k)) if names else k
        if new != k and new in data:
            continue  # both names: the new one wins
        if new in OPAQUE and isinstance(v, dict):
            output[new] = v
        else:
            output[new] = _code(new, upgrade(v))
    return output


def upgrade_entries(data):
    """For maps {text: entry} (problems, pending fixes, origins of the shared memory): the entries are upgraded, the
    texts used as keys are not."""
    if not isinstance(data, dict):
        return data
    return {k: upgrade(v) for k, v in data.items()}


def _signal(s):
    if s in SIGNALS:
        return SIGNALS[s]
    if ":" not in s:
        return s
    prefix = s.split(":", 1)[0] + ":"
    return SIGNAL_PREFIXES[prefix] + s[len(prefix):] if prefix in SIGNAL_PREFIXES else s


def _code(key, value):
    if key == "signal" and isinstance(value, str):
        return _signal(value)
    if key == "seen" and isinstance(value, dict):  # signal -> value (old names of the whole signals too)
        value = {_signal(k) if isinstance(k, str) else k: v for k, v in value.items()}
    codes = CODES.get(key)
    if not codes:
        return value
    if isinstance(value, str):
        return codes.get(value, value)
    if isinstance(value, list):  # codes, or lists that start with a code (the changes of the Unreal settings)
        return [codes.get(x, x) if isinstance(x, str) else
                [codes.get(x[0], x[0])] + x[1:] if isinstance(x, list) and x and isinstance(x[0], str) else x
                for x in value]
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):  # codes used as keys (e.g. remedies)
        return {codes.get(k, k): v for k, v in value.items()}
    return value


def code(key, value):
    """A single stored code (e.g. the backend of an old game profile) in its new name."""
    return _code(key, value)


def load(f):
    """json.load + upgrade (for PatchSmith's structured files, never for translation memories)."""
    return upgrade(json.load(f))


def loads(text):
    return upgrade(json.loads(text))
