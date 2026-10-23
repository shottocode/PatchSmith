"""Suoni di PatchSmith in stile 8 bit (onde quadre e triangolari, come le console di una volta).

Generati qui, senza file audio scaricati e senza diritti di altri: ogni suono e' una sequenza di note scritta come WAV
(16 bit, mono) in memoria. Tutti hanno la stessa "voce" (onda quadra piena e, un'ottava sotto, un'onda triangolare, con
le note che si spengono) e la stessa durata (DURATA). Tre suoni: "fatto" (operazione finita), "errore" e "domanda"
(PatchSmith aspetta una scelta); li suona la finestra se sono accesi (pulsante con l'altoparlante, spento di default).
In piu' "clic", cortissimo, per ogni pulsante premuto.
"""
import io
import math
import struct
import threading
import wave

FREQUENZA = 44100
DURATA = 0.75  # secondi, uguale per tutti i suoni
VOLUME = 0.32  # (non troppo forte: winsound non ha un controllo del volume)

# note (Hz)
DO3, MIB3, SOL3 = 130.81, 155.56, 196.0
DO4, MI4, SOL4, LA4 = 261.63, 329.63, 392.0, 440.0
DO5, RE5 = 523.25, 587.33


def _onda(forma, fase):
    f = fase % 1.0
    if forma == "quadra":
        return 1.0 if f < 0.5 else -1.0
    return 4 * f - 1 if f < 0.5 else 3 - 4 * f  # triangolo


def _nota(campioni, inizio, durata, frequenza, forma, volume, decadimento, scivola=None, vibrato=False):
    """Aggiunge una nota ai campioni (float): inizio e durata in secondi; decadimento: tempo (s) in cui il volume
    scende a circa un terzo; scivola: frequenza a cui arriva alla fine; vibrato: dopo 80 ms, leggero."""
    primo, quanti = int(inizio * FREQUENZA), int(durata * FREQUENZA)
    if len(campioni) < primo + quanti:
        campioni.extend([0.0] * (primo + quanti - len(campioni)))
    fase = 0.0
    for i in range(quanti):
        t = i / FREQUENZA
        f = frequenza if scivola is None else frequenza + (scivola - frequenza) * i / quanti
        if vibrato:
            f *= 1 + 0.012 * math.sin(2 * math.pi * 6 * t) * min(1.0, t / 0.08)
        fase += f / FREQUENZA
        inviluppo = min(1.0, t / 0.004, (durata - t) / 0.01) * math.exp(-t / decadimento)  # niente "click"
        campioni[primo + i] += volume * inviluppo * _onda(forma, fase)


def _voce(campioni, inizio, durata, frequenza, decadimento, scivola=None, vibrato=False):
    """La voce di tutti i suoni: onda quadra e, un'ottava sotto, onda triangolare."""
    _nota(campioni, inizio, durata, frequenza, "quadra", 0.6, decadimento, scivola, vibrato)
    _nota(campioni, inizio, durata, frequenza / 2, "triangolo", 0.6, decadimento,
          scivola / 2 if scivola else None, vibrato)


def _wav(campioni, bit=6, durata=DURATA, volume=VOLUME):
    """WAV in memoria di 'durata' secondi; i campioni si riducono a 'bit' livelli (il suono "sgranato" degli 8 bit)."""
    quanti = int(durata * FREQUENZA)
    campioni = (campioni + [0.0] * quanti)[:quanti]
    massimo = max(1e-9, max(abs(c) for c in campioni))
    livelli = 2 ** (bit - 1)
    dati = bytearray()
    for c in campioni:
        c = round(c / massimo * livelli) / livelli * volume
        dati += struct.pack("<h", int(max(-1.0, min(1.0, c)) * 32767))
    uscita = io.BytesIO()
    with wave.open(uscita, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FREQUENZA)
        w.writeframes(bytes(dati))
    return uscita.getvalue()


def errore():
    """Errore: due note basse che scendono ("bonk-bonk")."""
    c = []
    _voce(c, 0.0, 0.16, SOL3, 0.14)
    _voce(c, 0.17, 0.58, MIB3, 0.22, scivola=DO3)
    return _wav(c)


def fatto():
    """Traduzione finita: tre note che salgono, l'ultima tenuta con un po' di vibrato."""
    c = []
    _voce(c, 0.0, 0.11, DO4, 0.12)
    _voce(c, 0.12, 0.11, MI4, 0.12)
    _voce(c, 0.24, 0.51, SOL4, 0.26, vibrato=True)
    return _wav(c)


def domanda():
    """Domanda in attesa: una nota alta e una bassa che rimbalzano ("ping-pong-ping-pong"), senza salire ne'
    scendere: non si confonde con "fatto" (sale) ne' con "errore" (scende)."""
    c = []
    for i, inizio in enumerate((0.0, 0.13, 0.26, 0.39)):
        _voce(c, inizio, 0.12 if i < 3 else 0.3, RE5 if i % 2 == 0 else LA4, 0.1 if i < 3 else 0.14)
    return _wav(c)


def clic():
    """Un pulsante premuto (e i suoni appena accesi): due micro-"bip", come premere e rilasciare. Corto e piu' basso
    degli altri: e' una conferma, non un avviso."""
    c = []
    _nota(c, 0.0, 0.02, MI4 * 2, "quadra", 0.6, 0.007)
    _nota(c, 0.03, 0.02, LA4 * 2, "quadra", 0.6, 0.007)
    return _wav(c, durata=0.06, volume=0.16)


SUONI = {"fatto": fatto, "errore": errore, "domanda": domanda, "clic": clic}
_PRONTI = {}  # i WAV gia' generati


def _riproduci(dati):
    import winsound
    winsound.PlaySound(dati, winsound.SND_MEMORY)


def suona(nome):
    """Suona 'nome' (fatto, errore, domanda) senza fermare la finestra: generato e riprodotto in un thread. Su un
    sistema senza winsound (non Windows) non suona nulla. True se e' partito."""
    try:
        import winsound  # noqa: F401
    except ImportError:
        return False

    def _lavoro():
        if nome not in _PRONTI:
            _PRONTI[nome] = SUONI[nome]()
        try:
            _riproduci(_PRONTI[nome])
        except RuntimeError:  # nessuna uscita audio: niente suono, niente errore
            pass
    threading.Thread(target=_lavoro, daemon=True).start()
    return True
