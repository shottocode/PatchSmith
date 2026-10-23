"""Tabelle di traduzione CSV con una colonna per lingua (es. Megaloot: translations.csv con
id,english,french,german,spanish,japanese,koreana,...,schinese,tchinese).

Si traduce la colonna della lingua originale (di solito english) al suo posto: nel gioco resta selezionata quella
lingua. Serve una colonna che non e' una lingua (la chiave, es. 'id'): senza, il gioco potrebbe cercare i testi
proprio con il loro valore inglese, e la tabella non si tocca.

Il file si riscrive come l'aveva scritto il gioco (separatore , ; o tab, BOM, a capo \\n o \\r\\n, celle tra
virgolette o no): si sostituiscono solo le celle tradotte, il resto resta identico byte per byte; dopo la scrittura
il file si rilegge e ogni cella deve essere quella attesa. Molti giochi leggono il CSV a mano, dividendo le righe
sulle virgole (Megaloot: nessun testo inglese ha una virgola, '"?"' e' un testo con le virgolette): se il file non
usa le virgolette alla maniera del CSV (una cella tra virgolette con dentro il separatore o un a capo), le
virgolette sono testo come le altre lettere, e nelle traduzioni il separatore e le virgolette si sostituiscono come
fanno i testi del gioco (',' -> ';', '"' -> "'"); una traduzione con un a capo in piu' resta in originale.
"""
import csv
import io
import os
import re

from . import adapter_gamemaker

SEPARATORI = (",", ";", "\t")
MASSIMO = 20 * 1024 * 1024
MINIMO_TESTI = 3  # righe con un testo nella colonna originale, per essere una tabella di traduzione
PIENA = 0.5  # una lingua c'e' (traduzione ufficiale) se la sua colonna ha almeno meta' dei testi dell'originale
ESTENSIONI = (".csv", ".tsv")


class ErroreCSV(ValueError):
    pass


def lingua_della_colonna(nome):
    """Il codice della lingua del nome di una colonna ('english', 'en', 'English (US)', 'koreana', 'schinese',
    'textEN'...), oppure None ('id', 'key', 'notes'...)."""
    n = (nome or "").strip().lstrip("\ufeff").strip()
    if not n or len(n) > 40 or "/" in n or "\\" in n:
        return None
    return adapter_gamemaker.lingua_del_percorso(re.sub(r"[()\[\]:,]", " ", n).strip())


def _celle_csv(testo, sep):
    """[[(inizio, fine, valore, tra_virgolette)]] delle righe, con le regole del CSV (virgolette, "" dentro una cella
    tra virgolette, a capo dentro le virgolette). Solleva ErroreCSV se le virgolette non sono usate cosi'."""
    righe, riga, i, n = [], [], 0, len(testo)
    fine_cella = re.compile("[%s\r\n]" % re.escape(sep))
    while n:
        if testo.startswith('"', i):
            j, pezzi = i + 1, []
            while True:
                k = testo.find('"', j)
                if k < 0:
                    raise ErroreCSV("virgolette non chiuse")
                pezzi.append(testo[j:k])
                if testo.startswith('"', k + 1):
                    pezzi.append('"')
                    j = k + 2
                    continue
                fine = k + 1
                break
            if fine < n and testo[fine] not in sep + "\r\n":
                raise ErroreCSV("testo dopo le virgolette di chiusura")
            riga.append((i, fine, "".join(pezzi), True))
        else:
            m = fine_cella.search(testo, i)
            fine = m.start() if m else n
            riga.append((i, fine, testo[i:fine], False))
        if fine >= n:
            righe.append(riga)
            break
        if testo[fine] == sep:
            i = fine + 1
            continue
        righe.append(riga)
        riga = []
        i = fine + (2 if testo.startswith("\r\n", fine) else 1)
        if i >= n:
            break
    return righe


def _celle_semplici(testo, sep):
    """Le righe divise come fa un gioco che legge il CSV a mano: a ogni a capo e a ogni separatore (le virgolette
    sono testo)."""
    righe, i, n = [], 0, len(testo)
    for m in re.finditer(r"\r\n|\n|\r|\Z", testo):
        if m.start() == n and i >= n:
            break
        riga, inizio = [], i
        for pezzo in testo[i:m.start()].split(sep):
            riga.append((inizio, inizio + len(pezzo), pezzo, False))
            inizio += len(pezzo) + 1
        righe.append(riga)
        i = m.end()
        if m.start() == n:
            break
    return righe


def _separatore(testo):
    """Il separatore della tabella: quello che compare di piu' nella prima riga (fuori dalle virgolette)."""
    prima = re.split(r"\r\n|\n|\r", testo, 1)[0]
    prima = re.sub(r'"[^"]*"', "", prima)
    conti = {s: prima.count(s) for s in SEPARATORI}
    migliore = max(SEPARATORI, key=lambda s: conti[s])
    if not conti[migliore]:
        raise ErroreCSV("nessun separatore nella prima riga")
    return migliore


class FileCSV:
    """Una tabella CSV letta: celle con la loro posizione nel testo, intestazione, colonne delle lingue."""

    def __init__(self, dati_byte, a_mano=False):
        """a_mano=True: le righe si dividono come fa un gioco che legge il CSV a mano anche se il file e' un CSV
        valido (per rileggere un file riscritto come l'originale, vedi serializza)."""
        try:
            testo = dati_byte.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ErroreCSV("il file non e' UTF-8: %s" % e) from e
        self.bom = testo.startswith("\ufeff")
        self.testo = testo[1:] if self.bom else testo
        self.separatore = _separatore(self.testo)
        try:
            if a_mano:
                raise ErroreCSV("diviso a mano")
            self.righe = _celle_csv(self.testo, self.separatore)
            self.virgolette = True
        except ErroreCSV:
            self.righe = _celle_semplici(self.testo, self.separatore)
            self.virgolette = False
        citate = [c[2] for r in self.righe for c in r if c[3]]
        # il gioco legge davvero il CSV con le virgolette solo se il file ne ha bisogno (separatore o a capo dentro
        # una cella); altrimenti il gioco potrebbe dividere a mano: niente separatori ne' a capo nelle traduzioni
        self.speciali = any(self.separatore in v or "\n" in v or "\r" in v for v in citate)
        self.doppie = any('"' in v for v in citate)
        if self.virgolette:
            self._controlla_con_csv()
        self.intestazione = [c[2] for c in self.righe[0]] if self.righe else []
        self.lingue = {i: lingua_della_colonna(n) for i, n in enumerate(self.intestazione)}
        self.lingue = {i: c for i, c in self.lingue.items() if c}
        # la chiave: piena in quasi tutte le righe con qualcosa (le righe vuote, che molte tabelle usano per
        # dividere i gruppi di testi, non contano)
        usate = sum(1 for r in range(1, len(self.righe)) if any(c[2].strip() for c in self.righe[r]))
        self.chiave = next((i for i in range(len(self.intestazione)) if i not in self.lingue
                            and self._piene(i) >= 0.8 * max(1, usate)), None)

    def _controlla_con_csv(self):
        """Le celle lette qui devono essere quelle del modulo csv di Python (stesso dialetto)."""
        try:
            letti = list(csv.reader(io.StringIO(self.testo, newline=""), delimiter=self.separatore, quotechar='"',
                                    doublequote=True, strict=True))
        except csv.Error as e:
            raise ErroreCSV("CSV non valido: %s" % e) from e
        nostri = [[] if len(r) == 1 and not r[0][3] and not r[0][2] else [c[2] for c in r] for r in self.righe]
        if [r for r in letti if r] != [r for r in nostri if r]:
            raise ErroreCSV("celle diverse da quelle del modulo csv")

    def _cella(self, r, c):
        riga = self.righe[r]
        return riga[c][2] if c < len(riga) else ""

    def _piene(self, c):
        return sum(1 for r in range(1, len(self.righe)) if self._cella(r, c).strip())

    def _con_testo(self, c):
        return sum(1 for r in range(1, len(self.righe)) if adapter_gamemaker.testo_localizzazione(self._cella(r, c)))

    def colonna(self, nome):
        """L'indice della colonna con questo nome (senza maiuscole e spazi), oppure None."""
        cerca = (nome or "").strip().lower()
        return next((i for i, n in enumerate(self.intestazione)
                     if n.strip().lstrip("\ufeff").lower() == cerca), None)

    def sorgente(self, origine="auto"):
        """(indice, codice) della colonna da tradurre: quella della lingua 'origine' (con 'auto' l'inglese, se c'e',
        altrimenti la prima lingua), la piu' piena se ce n'e' piu' d'una; (None, None) se non c'e'."""
        base = (origine or "auto").split("-")[0].lower()
        scelte = [i for i, c in self.lingue.items() if c.split("-")[0] == ("en" if base == "auto" else base)]
        if not scelte and base == "auto" and self.lingue:
            scelte = [min(self.lingue)]
        if not scelte:
            return None, None
        i = max(scelte, key=lambda k: (self._con_testo(k), -k))
        return i, self.lingue[i]

    def tabella(self):
        """True se e' una tabella di traduzione utilizzabile: almeno due lingue, una colonna delle chiavi e qualche
        testo nella colonna originale."""
        i, _ = self.sorgente()
        return len(set(self.lingue.values())) >= 2 and self.chiave is not None and i is not None \
            and self._con_testo(i) >= MINIMO_TESTI

    def lingue_piene(self, sorgente=None):
        """Le lingue con la loro colonna piena (almeno PIENA dei testi della colonna 'sorgente'): sono traduzioni
        ufficiali del gioco. Le colonne vuote o quasi (lingue previste ma non tradotte) non contano."""
        sorgente = self.sorgente()[0] if sorgente is None else sorgente
        totale = self._con_testo(sorgente) if sorgente is not None else 0
        if not totale:
            return set()
        return {c for i, c in self.lingue.items() if self._con_testo(i) >= PIENA * totale}

    def testi(self, colonna):
        """[((riga, colonna), testo)] dei testi della colonna (gli a capo \\r\\n dentro una cella diventano \\n)."""
        uscita = []
        for r in range(1, len(self.righe)):
            if colonna < len(self.righe[r]):
                v = self.righe[r][colonna][2]
                if adapter_gamemaker.testo_localizzazione(v):
                    uscita.append(((r, colonna), v.replace("\r\n", "\n")))
        return uscita

    def chiave_di(self, pos):
        """Il valore della colonna delle chiavi nella riga del testo (contesto per il traduttore), oppure ''."""
        return self._cella(pos[0], self.chiave) if self.chiave is not None else ""

    def riferimenti(self, pos, codici):
        """{codice: testo} delle traduzioni ufficiali dello stesso testo (le colonne di quelle lingue)."""
        uscita = {}
        for i, c in self.lingue.items():
            if c in codici and c not in uscita and i != pos[1]:
                v = self._cella(pos[0], i).strip()
                if v:
                    uscita[c] = v.replace("\r\n", "\n")
        return uscita

    def non_scrivibile(self, tradotto):
        """I caratteri della traduzione che in questo file non si possono scrivere senza cambiare come il gioco lo
        legge (separatore, virgolette, a capo), oppure ''."""
        vietati = ""
        if not self.speciali:
            vietati += self.separatore + "\n\r"
        if not self.doppie or not self.virgolette:
            vietati += '"'
        return "".join(c for c in dict.fromkeys(tradotto) if c in vietati)

    # al posto dei caratteri che il file non permette, quelli che usano i giochi stessi (Megaloot: ';' per ',')
    SOSTITUTI = {",": ";", ";": ",", "\t": " ", '"': "'"}

    def adatta(self, tradotto):
        """La traduzione con i caratteri che il file non permette (vedi non_scrivibile) sostituiti come fanno i testi
        originali: ',' -> ';' in un file diviso sulle virgole, '"' -> "'". Gli a capo restano (si controllano a
        parte)."""
        for c in self.non_scrivibile(tradotto):
            sostituto = self.SOSTITUTI.get(c)
            if sostituto and sostituto not in self.non_scrivibile(sostituto):
                tradotto = tradotto.replace(c, sostituto)
        return tradotto

    def _scrivi_cella(self, valore, tra_virgolette):
        if self.virgolette and (tra_virgolette or any(c in valore for c in (self.separatore, '"', "\n", "\r"))):
            return '"' + valore.replace('"', '""') + '"'
        return valore

    def serializza(self, sostituzioni):
        """Byte del file con le celle sostituite {(riga, colonna): nuovo testo}: il resto identico. Solleva ErroreCSV
        se una traduzione cambierebbe come si legge il file (vedi non_scrivibile) o se il file riletto non torna."""
        pezzi, ultimo, attese = [], 0, {}
        for (r, c), nuovo in sorted(sostituzioni.items(), key=lambda x: self.righe[x[0][0]][x[0][1]][0]):
            inizio, fine, valore, tra_virgolette = self.righe[r][c]
            if self.non_scrivibile(nuovo):
                raise ErroreCSV("riga %d: la traduzione ha caratteri che il file non permette" % (r + 1))
            if "\r\n" in valore:
                nuovo = nuovo.replace("\r\n", "\n").replace("\n", "\r\n")
            attese[(r, c)] = nuovo
            if nuovo == valore:
                continue
            pezzi += [self.testo[ultimo:inizio], self._scrivi_cella(nuovo, tra_virgolette)]
            ultimo = fine
        testo = "".join(pezzi) + self.testo[ultimo:]
        dati = (("\ufeff" if self.bom else "") + testo).encode("utf-8")
        # riletto come lo legge il gioco: in una tabella divisa a mano una traduzione puo' togliere l'unica cella che
        # non era CSV valido ('"Hero" rises' -> "L'eroe sorge"), e il file sembrerebbe un CSV con le virgolette
        controllo = FileCSV(dati, a_mano=not self.virgolette)
        if (controllo.separatore, controllo.virgolette) != (self.separatore, self.virgolette) or \
                [len(r) for r in controllo.righe] != [len(r) for r in self.righe] or \
                any(controllo.righe[r][c][2] != attese.get((r, c), self.righe[r][c][2])
                    for r in range(len(self.righe)) for c in range(len(self.righe[r]))):
            raise ErroreCSV("il file riletto non torna: non scrivo")
        return dati


def leggi(percorso):
    with open(percorso, "rb") as f:
        return FileCSV(f.read())


def tabella(percorso):
    """La tabella di traduzione (FileCSV) del file, oppure None se il file non lo e' (o non si legge)."""
    try:
        if os.path.getsize(percorso) > MASSIMO:
            return None
        f = leggi(percorso)
    except (OSError, ErroreCSV):
        return None
    return f if f.tabella() else None


def tabelle(cartella):
    """{relativo: FileCSV} delle tabelle di traduzione CSV della cartella (sola lettura). Si saltano i file creati
    da PatchSmith e i sorgenti di Godot (un .csv con accanto il suo .csv.import: il gioco usa il file compilato)."""
    from . import adapter_webjson
    trovate = {}
    for radice, dirs, files in os.walk(cartella):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.lower() not in ("node_modules", "__pycache__"))
        for nome in sorted(files):
            if not nome.lower().endswith(ESTENSIONI) or adapter_webjson.MARCATORE in nome or nome + ".import" in files:
                continue
            p = os.path.join(radice, nome)
            f = tabella(adapter_gamemaker.leggi_originale(p, lambda x: x))
            if f:
                trovate[os.path.relpath(p, cartella).replace(os.sep, "/")] = f
    return trovate
