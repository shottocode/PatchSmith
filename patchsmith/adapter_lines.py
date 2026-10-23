"""File di localizzazione "a righe con id" (es. Dokimon/GameMaker: Localization/english_ui.txt).

Due tipi di file, riconosciuti dal contenuto:

  a RIGHE (ui, scene...)                 a BLOCCHI (npc, char, trainer...)
    // commento: non si tocca              12                <- riga con il solo id
    0 Fight                                Hello there!      <- una riga per riquadro
    5  do next?   (separatore: spazio,     Bye!
                   tab o spazio ideografico) .               <- chiusura della sezione ('.', '. ', '\uff0e')
                                           Second part...    <- altra sezione della stessa voce
                                           .                    (es. allenatore: prima/dopo la lotta)

Si traduce solo il testo: id, separatori, commenti, righe vuote, chiusure e a capo (CRLF)
restano identici byte per byte. Le righe che il formato non prevede (errori dei file originali)
non si toccano. Ogni traduzione viene controllata da sola (valida): una che cambierebbe il modo in
cui il gioco legge il file resta in originale, senza bloccare le altre. Dopo la scrittura il file
viene riletto e la struttura confrontata.
"""
import re

_SEPARATORI = " \t\u3000"
_ID_TESTO = re.compile(r"^(\d+(?:\.\d+)*)([ \t\u3000])(.*)$", re.S)
_ID_DA_SOLO = re.compile(r"^\d+(?:\.\d+)*[ \t\u3000]*$")
_CHIUSURE = {".", "\uff0e"}  # '.' e il punto a larghezza piena (anche con spazi attorno)
# prefisso numerico che i giochi confrontano con l'id cercato (es. Dokimon: ogni riga e' un id candidato)
_PREFISSO_NUMERO = re.compile(r"^[\s\u3000\ufeff]*[-+]?(?:\d{0,30}\.)?\d{1,30}")
BOM = "\ufeff"
# caratteri di controllo e separatori di riga Unicode: un gioco potrebbe leggerli come a capo
_CONTROLLO = frozenset([chr(c) for c in range(32) if c not in (9, 10)] + ["\x7f", "\x85", "\u2028", "\u2029"])


class ErroreRighe(ValueError):
    pass


def _dividi(testo):
    """Righe con il loro a capo; si divide solo su \\n (non su altri separatori Unicode)."""
    parti = testo.split("\n")
    righe = [p + "\n" for p in parti[:-1]]
    if parti[-1]:
        righe.append(parti[-1])
    return righe


def _separa_fine(riga):
    corpo = riga.rstrip("\r\n")
    return corpo, riga[len(corpo):]


def _e_testo(s):
    return any(c.isalpha() for c in s)


def _commento_o_vuota(corpo):
    return corpo.startswith("//") or not corpo.strip()


def _chiusura(corpo):
    return corpo.strip(" \t\u3000") in _CHIUSURE


def _prefisso_numero(s):
    m = _PREFISSO_NUMERO.match(s)
    return m.group(0).strip(" \t\u3000\ufeff") if m else ""


def modo(righe):
    """'blocchi' se il file e' fatto di voci su piu' righe chiuse da '.', altrimenti 'righe'."""
    corpi = [_separa_fine(r)[0] for r in righe]
    punti = sum(1 for c in corpi if _chiusura(c))
    voci_riga = sum(1 for c in corpi if _ID_TESTO.match(c) and _ID_TESTO.match(c).group(3).strip())
    return "blocchi" if punti >= 3 and punti >= voci_riga else "righe"


def valida(pos, originale, tradotto):
    """True se la traduzione puo' sostituire l'originale senza cambiare come il gioco legge il file."""
    if "\r" in tradotto or any(c in _CONTROLLO for c in tradotto):
        return False
    if pos[0] == "riga":
        return ("\n" not in tradotto and _e_testo(tradotto) and tradotto.strip(_SEPARATORI) != ""
                and not tradotto.lstrip(_SEPARATORI).startswith("//"))
    righe_o, righe_t = originale.split("\n"), tradotto.split("\n")
    if len(righe_o) != len(righe_t) or not _e_testo(tradotto):
        return False
    for o, t in zip(righe_o, righe_t):
        if o.strip() and not t.strip():
            return False  # una riga di testo non puo' diventare vuota (la prima aprirebbe un'altra voce)
        if _chiusura(t) or _ID_DA_SOLO.match(t) or t.startswith("//"):
            return False
        if _prefisso_numero(t) != _prefisso_numero(o):
            return False  # una riga che comincia con un numero diverso potrebbe "rubare" un'altra voce
    return True


class FileRighe:
    """File letto: righe (con i loro a capo), unita' di testo traducibili e struttura fissa."""

    def __init__(self, dati_byte):
        try:
            testo = dati_byte.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ErroreRighe("il file non e' UTF-8: %s" % e) from e
        self.bom = testo.startswith(BOM)
        if self.bom:
            testo = testo[1:]
        self.righe = _dividi(testo)
        self.modo = modo(self.righe)
        self.unita, self.forma, self.anomale = (_analizza_blocchi if self.modo == "blocchi"
                                                else _analizza_righe)(self.righe)

    def testi(self):
        """[(posizione, testo)]: posizione = ('riga', i) oppure ('blocco', i, j) per le righe i..j-1."""
        return list(self.unita)

    def _scrivi(self, sostituzioni):
        righe = list(self.righe)
        for pos, nuovo in sostituzioni.items():
            if pos[0] == "riga":
                i = pos[1]
                corpo, fine = _separa_fine(righe[i])
                m = _ID_TESTO.match(corpo)
                if not m or "\n" in nuovo or "\r" in nuovo:
                    raise ErroreRighe("traduzione non valida per la riga %d" % (i + 1))
                righe[i] = m.group(1) + m.group(2) + nuovo + fine
            else:
                _, i, j = pos
                nuove = nuovo.split("\n")
                if len(nuove) != j - i or any("\r" in r for r in nuove):
                    raise ErroreRighe("la traduzione delle righe %d-%d ha un numero di righe diverso" % (i + 1, j))
                for k, r in enumerate(nuove):
                    righe[i + k] = r + _separa_fine(righe[i + k])[1]
        dati = ((BOM if self.bom else "") + "".join(righe)).encode("utf-8")
        controllo = FileRighe(dati)
        if (controllo.modo, controllo.forma) != (self.modo, self.forma) or \
                [p for p, _ in controllo.unita] != [p for p, _ in self.unita]:
            raise ErroreRighe("la struttura del file cambierebbe: non scrivo")
        return dati

    def serializza(self, sostituzioni):
        """Byte del file con i testi sostituiti {posizione: nuovo testo}; struttura verificata."""
        return self._scrivi(sostituzioni)

    def serializza_robusto(self, sostituzioni):
        """(byte, posizioni scartate): le traduzioni che cambierebbero la struttura restano in originale."""
        originali = dict(self.unita)
        buone = {p: t for p, t in sostituzioni.items() if valida(p, originali.get(p, ""), t)}
        scartate = [p for p in sostituzioni if p not in buone]
        try:
            return self._scrivi(buone), scartate
        except ErroreRighe:
            pass
        # caso raro: controllo una traduzione alla volta
        tenute = {}
        for p, t in buone.items():
            try:
                self._scrivi({p: t})
                tenute[p] = t
            except ErroreRighe:
                scartate.append(p)
        return self._scrivi(tenute), scartate


def _analizza_righe(righe):
    unita, forma, anomale = [], [], []
    for i, riga in enumerate(righe):
        corpo, fine = _separa_fine(riga)
        m = _ID_TESTO.match(corpo)
        if _commento_o_vuota(corpo) or _ID_DA_SOLO.match(corpo):
            forma.append(("fisso", riga))
        elif m:
            forma.append(("voce", m.group(1), m.group(2), fine))
            if _e_testo(m.group(3)) and not m.group(3).lstrip(_SEPARATORI).startswith("//"):
                unita.append((("riga", i), m.group(3)))
        else:
            forma.append(("fisso", riga))
            anomale.append(i + 1)
    return unita, forma, anomale


def _analizza_blocchi(righe):
    unita, forma, anomale = [], [], []
    i, n = 0, len(righe)
    while i < n:
        corpo, _ = _separa_fine(righe[i])
        if _commento_o_vuota(corpo) or _ID_DA_SOLO.match(corpo) or _chiusura(corpo):
            forma.append(("fisso", righe[i]))
            i += 1
            continue
        # sezione di testo: fino alla riga di chiusura
        j = i
        while j < n and not _chiusura(_separa_fine(righe[j])[0]):
            j += 1
        if j >= n:  # sezione senza chiusura: non la tocco
            for k in range(i, n):
                forma.append(("fisso", righe[k]))
                anomale.append(k + 1)
            break
        contenuto = [_separa_fine(r)[0] for r in righe[i:j]]
        forma.append(("sezione", j - i, tuple(_separa_fine(r)[1] for r in righe[i:j]), righe[j],
                      tuple(_prefisso_numero(c) for c in contenuto)))
        if _e_testo("\n".join(contenuto)) and not any(c.startswith("//") for c in contenuto):
            unita.append((("blocco", i, j), "\n".join(contenuto)))
        else:
            forma.append(("contenuto", tuple(righe[i:j])))
        i = j + 1
    return unita, forma, anomale


def leggi(percorso):
    with open(percorso, "rb") as f:
        return FileRighe(f.read())


def sembra_localizzazione(percorso, minimo=5):
    """True se il file e' nel formato 'a righe con id' e ha almeno 'minimo' testi."""
    try:
        f = leggi(percorso)
    except (ErroreRighe, OSError):
        return False
    return len(f.unita) >= minimo and len(f.anomale) <= max(3, len(f.unita) // 20)
