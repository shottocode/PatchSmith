"""Test of the configurator and of the Kingdom Hearts HD 1.5+2.5 game sheet (default language in the executables).

Usage: python -I test/test_configurator.py <empty_temp_folder> [<real game folder>]
Builds a fake installation (fake PE executables, fake .hed index) and tests: recognition, consent,
all-or-nothing writing, refusals, game updates, identical restore.
With the real game's folder, it copies ONLY its executables and an index into the temporary folder and repeats the
test on the copies: the game's folder is never touched and no program is started.
"""
import hashlib
import os
import shutil
import stat
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patchsmith import (adapter_webjson, auto as automatic, cli, configurator,
                        official_language, project, game_kingdom_hearts as kh,
                        system, steam)

OUTCOMES = []


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERRORE") + "  " + name)


def silent(_):
    pass


def fingerprint(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def fingerprints(folder):
    return {n: fingerprint(os.path.join(folder, n)) for n in kh.EXECUTABLES if os.path.exists(os.path.join(folder, n))}


def fake_pe(value_=1, double=False, no_compare=False, signature=b""):
    """Minimal PE64: .text with the pattern of the default language and the comparison with "english", .rdata with
    the table of languages."""
    text = bytearray(0x400)
    pattern = (b"\xBB" + bytes([value_]) + b"\x00\x00\x00\x48\x83\x3D\x11\x22\x33\x44\x00\x75\x31\x8B\xCB\xE8"
              + b"\x00\x00\x00\x00")
    text[0x10:0x10 + len(pattern)] = pattern
    if double:
        text[0x200:0x200 + len(pattern)] = pattern
    rdata = bytearray(0x200)
    rdata[0x40:0x40 + len(kh.TABLE)] = kh.TABLE
    k = 0x10 + 0x63
    if not no_compare:
        text[k:k + 7] = b"\x48\x8D\x15" + struct.pack("<i", (0x2000 + 0x40) - (0x1000 + k + 7))
    head = bytearray(0x400)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x80)
    head[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<HHIIIHH", head, 0x84, 0x8664, 2, 0, 0, 0, 0xF0, 0x22)
    struct.pack_into("<H", head, 0x98, 0x20B)
    struct.pack_into("<Q", head, 0x98 + 24, 0x140000000)
    sections = 0x98 + 0xF0
    for i, (name, rva, foff, dim, sec_flags) in enumerate(((b".text", 0x1000, 0x400, 0x400, 0x60000020),
                                                     (b".rdata", 0x2000, 0x800, 0x200, 0x40000040))):
        s = sections + 40 * i
        head[s:s + 8] = name.ljust(8, b"\x00")
        struct.pack_into("<IIII", head, s + 8, dim, rva, dim, foff)
        struct.pack_into("<I", head, s + 36, sec_flags)
    return bytes(head) + bytes(text) + bytes(rdata) + signature


def create_game(folder):
    os.makedirs(os.path.join(folder, "Image", "dt"))
    entries = b"".join(hashlib.md5(n.encode()).digest() + b"\x00" * 16 for n in
                    ("msg/us/sys.bar", "msg/it/sys.bar", "msg/fr/sys.bar", "msg/us/al.bar"))
    open(os.path.join(folder, "Image", "dt", "kh2_first.hed"), "wb").write(entries)
    for n in kh.EXECUTABLES:
        open(os.path.join(folder, n), "wb").write(fake_pe(signature=n.encode()))
    open(os.path.join(folder, "KINGDOM HEARTS HD 1.5+2.5 ReMIX.exe"), "wb").write(b"MZ protezione")


def byte_diff(a, b):
    with open(a, "rb") as f:
        x = f.read()
    with open(b, "rb") as f:
        y = f.read()
    return [i for i in range(min(len(x), len(y))) if x[i] != y[i]] if len(x) == len(y) else None


def main(base, real_game=None):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base)
    project.PROJECT_FOLDER = os.path.join(base, "patchsmith")
    project.DATA_ROOT = project.PROJECT_FOLDER  # the user's data here too
    automatic.PROFILES_FOLDER = os.path.join(base, "patchsmith", "profiles")
    os.makedirs(automatic.PROFILES_FOLDER)
    steam.steam_folder = lambda: None
    system.game_open = lambda c: False
    configurator.smart_app_control = lambda: 0
    saves = os.path.join(base, "Documenti", "My Games", kh.NAME, "Steam", "123")
    os.makedirs(saves)
    open(os.path.join(saves, "KHFM_WW.png"), "wb").write(b"salvataggio")
    kh.SAVES = os.path.dirname(os.path.dirname(saves))

    outer = os.path.join(base, "giochi", "kingdom hearts hd")
    game = os.path.join(outer, "KH")
    create_game(game)
    originals = fingerprints(game)
    protection = fingerprint(os.path.join(game, "KINGDOM HEARTS HD 1.5+2.5 ReMIX.exe"))

    # --- recognition
    check("scheda: riconosciuta dalla cartella del gioco e da quella sopra",
              kh.identify(game) == game and kh.identify(outer) == game and kh.identify(base) is None)
    check("scheda: lingue dei testi installate (dall'indice .hed)", kh.present_languages(game) == {"en", "it", "fr"})
    check("scheda: punto della lingua trovato e verificato",
              kh.language_byte(fake_pe())[1] == 1 and kh.language_byte(fake_pe(6))[1] == 6)
    for name, data in (("doppio", fake_pe(double=True)), ("senza confronto con 'english'",
                                                           fake_pe(no_compare=True)),
                       ("non PE", b"ciao" * 100)):
        try:
            kh.language_byte(data)
            check("scheda: eseguibile %s -> rifiutato" % name, False)
        except kh.SheetError:
            check("scheda: eseguibile %s -> rifiutato" % name, True)
    r = official_language.detect(game, "it")
    check("rilevamento: italiano presente dai file del gioco (senza Steam)",
              r["presente"] is True and r["motore"]["tipo"] == "scheda")

    # --- without consent: only the proposal, nothing written
    path, outcome = automatic.prepare(outer, "auto", "it", "finto", silent)
    check("senza consenso: proposta, nessun file toccato",
              path is None and outcome.get("serve_consenso") and fingerprints(game) == originals
              and any("1 byte" in x and game in x for x in outcome["lingua_ufficiale_righe"])
              and not any("I can't set it myself" in x for x in outcome["lingua_ufficiale_righe"]))
    fake = dict(r, steam_conosciuto={"appid": "1", "nome": "x", "eseguibile": "x.exe"})
    check("messaggio: se la scheda spiega il rifiuto, niente 'Non posso impostarla io' contraddittorio",
              not any("I can't" in x for x in official_language.message(fake, "it", ["motivo e rimedio"], False)))

    # --- refusals: nothing written
    configurator.smart_app_control = lambda: 1
    done, lines = official_language.set_(game, r, "it", silent, consent=True)
    check("Smart App Control attivo -> rifiuto prima di chiedere il consenso, nessun file toccato",
              done is False and fingerprints(game) == originals and any("Smart App Control" in x for x in lines)
              and not configurator.has_consent(game))
    configurator.smart_app_control = lambda: 0
    system.game_open = lambda c: True
    done, lines = official_language.set_(game, r, "it", silent, consent=True)
    check("gioco aperto -> rifiuto, nessun file toccato, salvataggi non copiati, consenso ricordato",
              done is False and fingerprints(game) == originals and not configurator.note(game, "salvataggi_copiati")
              and configurator.has_consent(game))
    system.game_open = lambda c: False
    fourth = os.path.join(game, kh.EXECUTABLES[3])
    os.chmod(fourth, stat.S_IREAD)
    done, lines = official_language.set_(game, r, "it", silent, consent=True)
    os.chmod(fourth, stat.S_IREAD | stat.S_IWRITE)
    check("un eseguibile di sola lettura -> nessuno modificato",
              done is False and fingerprints(game) == originals and not configurator.lever(game, "lingua"))
    real_atomic_write = adapter_webjson.write_atomic
    for rng, final in (("scrittura del terzo eseguibile", ".exe"),
                       ("backup del terzo eseguibile", project.INFO_SUFFIX)):
        calls = []

        def failing_write(path, data, final=final, calls=calls):
            if path.endswith(final) and "patchsmith" + os.sep + "configurazioni" not in path:
                calls.append(path)
                if len(calls) == 3:
                    raise OSError("disco pieno (simulato)")
            real_atomic_write(path, data)
        adapter_webjson.write_atomic = failing_write
        done, lines = official_language.set_(game, r, "it", silent, consent=True)
        adapter_webjson.write_atomic = real_atomic_write
        check("guasto durante %s -> tutto com'era (file, backup, registro)" % rng,
                  done is False and len(calls) >= 3 and fingerprints(game) == originals
                  and not configurator.lever(game, "lingua")
                  and not [n for n in os.listdir(game) if "patchsmith" in n])
    check("il consenso dato resta anche se la modifica non e' riuscita", configurator.has_consent(game))

    # --- with consent
    path, outcome = automatic.prepare(outer, "auto", "it", "finto", silent, consent=True)
    changes = {n: byte_diff(os.path.join(game, n), os.path.join(game, n + project.BACKUP_SUFFIX))
             for n in kh.EXECUTABLES}
    check("con consenso: 1 byte cambiato in ognuno dei 6 eseguibili (valore 6 = italiano), con backup",
              outcome.get("lingua_impostata") is True and all(c == [0x411] for c in changes.values())
              and all(kh.language_byte(open(os.path.join(game, n), "rb").read())[1] == 6 for n in kh.EXECUTABLES))
    check("protezione e altri file non toccati",
              fingerprint(os.path.join(game, "KINGDOM HEARTS HD 1.5+2.5 ReMIX.exe")) == protection)
    check("consenso salvato per il gioco, registro della leva, salvataggi copiati",
              configurator.has_consent(game) and configurator.lever(game, "lingua")["stato"] == "applicata"
              and os.path.isdir(str(configurator.note(game, "salvataggi_copiati"))))
    check("messaggio: attivata, senza 'Non posso impostarla io' e senza 'traduzione installata'",
              any("I activated" in x for x in outcome["lingua_ufficiale_righe"])
              and not any("I can't" in x for x in outcome["lingua_ufficiale_righe"])
              and not outcome.get("traduzione_installata"))
    after = fingerprints(game)
    path, outcome = automatic.prepare(game, "auto", "it", "finto", silent)
    check("seconda analisi: gia' attivo, niente riscritto",
              outcome.get("lingua_impostata") is True and fingerprints(game) == after
              and any("already active" in x for x in outcome["lingua_ufficiale_righe"]))

    # --- game update: an executable goes back to original but different; with the saved consent it is reapplied
    new = fake_pe(signature=b"versione 2")
    open(os.path.join(game, kh.EXECUTABLES[1]), "wb").write(new)
    path, outcome = automatic.prepare(game, "auto", "it", "finto", silent)
    p1 = os.path.join(game, kh.EXECUTABLES[1])
    check("aggiornamento: riapplicato da solo (consenso gia' dato), il nuovo originale ha il suo backup",
              outcome.get("lingua_impostata") is True and kh.language_byte(open(p1, "rb").read())[1] == 6
              and open(p1 + project.BACKUP_SUFFIX, "rb").read() == new
              and os.path.exists(p1 + project.BACKUP_SUFFIX + ".1"))

    # --- restore
    system.game_open = lambda c: True
    check("ripristino con il gioco aperto -> non tocca nulla", automatic.restore(game, silent) is False
              and kh.language_byte(open(p1, "rb").read())[1] == 6)
    system.game_open = lambda c: False
    automatic.restore(outer, silent)
    expectations = dict(originals, **{kh.EXECUTABLES[1]: hashlib.sha256(new).hexdigest()})
    check("Ripristina originali: eseguibili identici (l'aggiornato al suo nuovo originale), niente backup "
              "rimasti, leva tolta, consenso conservato",
              fingerprints(game) == expectations and not [n for n in os.listdir(game) if ".patchsmith" in n]
              and not configurator.lever(game, "lingua") and configurator.has_consent(game)
              and not official_language.is_set(game))
    check("riga di comando: auto con il consenso gia' dato riattiva ed esce con 0",
              cli.main(["auto", game, "--a", "it", "--backend", "finto"]) == 0
              and all(kh.language_byte(open(os.path.join(game, n), "rb").read())[1] == 6 for n in kh.EXECUTABLES))
    check("riga di comando: ripristina con la cartella", cli.main(["ripristina", game]) == 0
              and fingerprints(game) == expectations)

    # --- cases found by the review
    automatic.prepare(game, "auto", "it", "finto", silent)  # consent already given: activates
    new2 = fake_pe(signature=b"versione 3")
    p2 = os.path.join(game, kh.EXECUTABLES[2])
    open(p2, "wb").write(new2)
    check("eseguibile aggiornato dal gioco, poi Ripristina: chiuso al primo colpo, resta il file nuovo",
              automatic.restore(game, silent) is True and not configurator.lever(game, "lingua")
              and open(p2, "rb").read() == new2 and not [n for n in os.listdir(game) if ".patchsmith" in n])
    expectations[kh.EXECUTABLES[2]] = hashlib.sha256(new2).hexdigest()

    automatic.prepare(game, "auto", "it", "finto", silent)
    italian = fingerprints(game)
    calls = []

    def broken_at_third(path, data):
        if path.endswith(".exe"):
            calls.append(path)
            if len(calls) == 3:
                raise OSError("file in uso (simulato)")
        real_atomic_write(path, data)
    adapter_webjson.write_atomic = broken_at_third
    done, lines = official_language.set_(game, official_language.detect(game, "fr"), "fr", silent, consent=True)
    adapter_webjson.write_atomic = real_atomic_write
    check("guasto passando al francese -> si torna all'italiano di prima (non all'originale), leva conservata",
              done is False and fingerprints(game) == italian and configurator.lever(game, "lingua")["valore"] == "it"
              and any("as it was" in x for x in lines))

    automatic.restore(game, silent)
    now = fingerprints(game)

    def interrupted(path, data):
        if path.endswith(kh.EXECUTABLES[3]):
            raise KeyboardInterrupt
        real_atomic_write(path, data)
    adapter_webjson.write_atomic = interrupted
    try:
        official_language.set_(game, official_language.detect(game, "it"), "it", silent, consent=True)
        passed = False
    except KeyboardInterrupt:
        passed = True
    adapter_webjson.write_atomic = real_atomic_write
    check("interruzione (Ctrl+C) durante la scrittura -> file com'erano, nessuna leva a meta'",
              passed and fingerprints(game) == now and not configurator.lever(game, "lingua")
              and not [n for n in os.listdir(game) if ".patchsmith" in n])

    automatic.prepare(game, "auto", "it", "finto", silent)
    log_file = configurator.read_file(game)
    log_file["leve"]["lingua"]["stato"] = "in_corso"  # as if the process had died halfway
    configurator._save(game, log_file)
    messages = []
    done, lines = official_language.set_(game, official_language.detect(game, "it"), "it", messages.append)
    check("operazione rimasta a meta': la volta dopo rimette gli originali e riapplica",
              any("interrupted" in m for m in messages) and done is True
              and configurator.lever(game, "lingua")["stato"] == "applicata")

    def blocked_write(path, data):
        if path.endswith(kh.EXECUTABLES[4]):
            raise PermissionError("in uso da un altro programma (simulato)")
        real_atomic_write(path, data)
    adapter_webjson.write_atomic = blocked_write
    messages = []
    outcome_r = automatic.restore(game, messages.append)
    adapter_webjson.write_atomic = real_atomic_write
    values = {n: kh.language_byte(open(os.path.join(game, n), "rb").read())[1] for n in kh.EXECUTABLES}
    check("Ripristina con un eseguibile bloccato: gli altri tornano originali, avviso e risultato False",
              outcome_r is False and values[kh.EXECUTABLES[4]] == 6
              and all(v == 1 for n, v in values.items() if n != kh.EXECUTABLES[4])
              and any("Still to put back" in m for m in messages))
    check("... ripetendo si completa", automatic.restore(game, silent) is True and fingerprints(game) == expectations)

    one_folder = os.path.join(base, "una cartella")
    os.makedirs(one_folder)
    try:
        adapter_webjson.write_atomic(one_folder, b"x")
    except OSError:
        pass
    check("scrittura atomica fallita: nessun temporaneo lasciato",
              not os.path.exists(one_folder + adapter_webjson.TMP_SUFFIX))
    read_only = os.path.join(base, "sola lettura.json")
    open(read_only, "wb").write(b"{}")
    os.chmod(read_only, stat.S_IREAD)
    project.create_backup(read_only)
    check("backup di un file di sola lettura: riuscito e il backup resta scrivibile",
              os.access(read_only + project.BACKUP_SUFFIX, os.W_OK))
    os.chmod(read_only, stat.S_IREAD | stat.S_IWRITE)

    automatic.prepare(game, "auto", "it", "finto", silent)
    path, outcome = automatic.prepare(game, "auto", "it", "finto", silent, translate_anyway=True)
    check("'traduci comunque' senza nulla da tradurre: la lingua attivata resta",
              path is None and configurator.lever(game, "lingua")
              and kh.language_byte(open(os.path.join(game, kh.EXECUTABLES[0]), "rb").read())[1] == 6)
    automatic.restore(game, silent)
    configurator.smart_app_control = lambda: 1
    check("riga di comando: attivazione rifiutata con --modifica-eseguibili -> uscita 2",
              cli.main(["auto", game, "--a", "it", "--backend", "finto", "--modifica-eseguibili"]) == 2)
    configurator.smart_app_control = lambda: 0

    two_copies = os.path.join(base, "due copie")
    create_game(os.path.join(two_copies, "copia A"))
    create_game(os.path.join(two_copies, "copia B"))
    before = {d: fingerprints(os.path.join(two_copies, d)) for d in ("copia A", "copia B")}
    path, outcome = automatic.prepare(two_copies, "auto", "it", "finto", silent, consent=True)
    check("due copie del gioco nella cartella: nessuna modifica, chiede di sceglierne una",
              outcome.get("lingua_impostata") is False
              and any("2 copies" in x for x in outcome["lingua_ufficiale_righe"])
              and all(fingerprints(os.path.join(two_copies, d)) == before[d] for d in before))

    import test_official_language as plu
    steam_root, library = os.path.join(base, "Steam"), os.path.join(base, "Libreria")
    os.makedirs(os.path.join(steam_root, "appcache"))
    os.makedirs(os.path.join(steam_root, "steamapps"))
    open(os.path.join(steam_root, "steamapps", "libraryfolders.vdf"), "w").write(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % library.replace("\\", "\\\\"))
    open(os.path.join(steam_root, "appcache", "appinfo.vdf"), "wb").write(plu.appinfo({2552430: {
        "common": {"name": kh.NAME, "type": "Game", "supported_languages": {"english": {"supported": "true"},
                                                                           "italian": {"supported": "true"}}},
        "depots": {"baselanguages": "english,italian"}}}))
    shared = os.path.join(library, "steamapps", "common")
    create_game(os.path.join(shared, "KH Steam"))
    open(os.path.join(library, "steamapps", "appmanifest_2552430.acf"), "w").write(
        '"AppState"\n{\n\t"appid"\t\t"2552430"\n\t"name"\t\t"%s"\n\t"installdir"\t\t"KH Steam"\n}\n' % kh.NAME)
    steam.steam_folder = lambda: steam_root
    steam._CACHE.clear()
    before = fingerprints(os.path.join(shared, "KH Steam"))
    path, outcome = automatic.prepare(shared, "auto", "it", "finto", silent, consent=True)
    steam.steam_folder = lambda: None
    check("installato da Steam (scelta la cartella sopra): menu Lingua di Steam, eseguibili non toccati",
              any("Properties > General > Language" in x for x in outcome.get("lingua_ufficiale_righe", []))
              and fingerprints(os.path.join(shared, "KH Steam")) == before)

    # --- an executable of an unknown version: none is modified
    other = os.path.join(base, "KH2")
    create_game(other)
    open(os.path.join(other, kh.EXECUTABLES[2]), "wb").write(fake_pe(no_compare=True))
    before = fingerprints(other)
    done, lines = official_language.set_(other, official_language.detect(other, "it"), "it", silent, consent=True)
    check("versione sconosciuta di un eseguibile -> nessun file modificato, spiegazione",
              done is False and fingerprints(other) == before and any("I don't recognize" in x for x in lines))

    # --- test on copies of the real executables (the game's folder is not touched)
    if real_game:
        copy_dir = os.path.join(base, "copia del gioco")
        os.makedirs(os.path.join(copy_dir, "Image", "dt"))
        shutil.copy2(os.path.join(real_game, "Image", "dt", "kh2_first.hed"), os.path.join(copy_dir, "Image", "dt"))
        for n in kh.EXECUTABLES:
            if os.path.exists(os.path.join(real_game, n)):
                shutil.copy2(os.path.join(real_game, n), copy_dir)
        actual = fingerprints(copy_dir)
        check("gioco vero (copie): lingue dei testi installate", kh.present_languages(copy_dir) >= {"en", "it"})
        r = official_language.detect(copy_dir, "it")
        done, lines = official_language.set_(copy_dir, r, "it", silent, consent=True)
        changes = {n: byte_diff(os.path.join(copy_dir, n), os.path.join(copy_dir, n + project.BACKUP_SUFFIX))
                 for n in actual}
        periods = {n: kh.language_byte(open(os.path.join(copy_dir, n), "rb").read()) for n in actual}
        check("gioco vero (copie): esattamente 1 byte per eseguibile, ora vale 6 (italiano)",
                  done is True and len(actual) == 6 and all(c and len(c) == 1 and c[0] == periods[n][0]
                                                           for n, c in changes.items())
                  and all(v == 6 for _, v in periods.values()))
        for n, (o, _) in sorted(periods.items()):
            print("        %-45s byte 0x%X" % (n, o))
        automatic.restore(copy_dir, silent)
        check("gioco vero (copie): ripristino identico", fingerprints(copy_dir) == actual)

    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None) else 1)
