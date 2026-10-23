"""PatchSmith's 8-bit style sounds (square and triangle waves, like old consoles).

Generated here, with no downloaded audio files and no third-party rights: each sound is a sequence of notes written as
an in-memory WAV (16 bit, mono). They all have the same "voice" (a full square wave and, an octave below, a triangle
wave, with notes that fade out) and the same duration (DURATA). Three sounds: "fatto" (operation finished), "errore" and
"domanda" (PatchSmith is waiting for a choice); the window plays them if they are on (the speaker button, off by
default). Plus "clic", very short, for every button pressed.
"""
import io
import math
import struct
import threading
import wave

FREQUENZA = 44100
DURATA = 0.75  # seconds, the same for all sounds
VOLUME = 0.32  # (not too loud: winsound has no volume control)

# notes (Hz)
DO3, MIB3, SOL3 = 130.81, 155.56, 196.0
DO4, MI4, SOL4, LA4 = 261.63, 329.63, 392.0, 440.0
DO5, RE5 = 523.25, 587.33


def _onda(forma, fase):
    f = fase % 1.0
    if forma == "quadra":
        return 1.0 if f < 0.5 else -1.0
    return 4 * f - 1 if f < 0.5 else 3 - 4 * f  # triangle


def _nota(campioni, inizio, durata, frequenza, forma, volume, decadimento, scivola=None, vibrato=False):
    """Adds a note to the samples (float): inizio and durata in seconds; decadimento: time (s) in which the volume
    drops to about a third; scivola: frequency reached at the end; vibrato: light, after 80 ms."""
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
        inviluppo = min(1.0, t / 0.004, (durata - t) / 0.01) * math.exp(-t / decadimento)  # no "click"
        campioni[primo + i] += volume * inviluppo * _onda(forma, fase)


def _voce(campioni, inizio, durata, frequenza, decadimento, scivola=None, vibrato=False):
    """The voice of all sounds: a square wave and, an octave below, a triangle wave."""
    _nota(campioni, inizio, durata, frequenza, "quadra", 0.6, decadimento, scivola, vibrato)
    _nota(campioni, inizio, durata, frequenza / 2, "triangolo", 0.6, decadimento,
          scivola / 2 if scivola else None, vibrato)


def _wav(campioni, bit=6, durata=DURATA, volume=VOLUME):
    """In-memory WAV lasting 'durata' seconds; the samples are reduced to 'bit' levels (the "grainy" 8-bit sound)."""
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
    """Error: two low notes going down ("bonk-bonk")."""
    c = []
    _voce(c, 0.0, 0.16, SOL3, 0.14)
    _voce(c, 0.17, 0.58, MIB3, 0.22, scivola=DO3)
    return _wav(c)


def fatto():
    """Translation finished: three rising notes, the last one held with a little vibrato."""
    c = []
    _voce(c, 0.0, 0.11, DO4, 0.12)
    _voce(c, 0.12, 0.11, MI4, 0.12)
    _voce(c, 0.24, 0.51, SOL4, 0.26, vibrato=True)
    return _wav(c)


def domanda():
    """Question waiting: a high and a low note bouncing ("ping-pong-ping-pong"), neither rising nor falling: it
    cannot be mistaken for "fatto" (rises) or for "errore" (falls)."""
    c = []
    for i, inizio in enumerate((0.0, 0.13, 0.26, 0.39)):
        _voce(c, inizio, 0.12 if i < 3 else 0.3, RE5 if i % 2 == 0 else LA4, 0.1 if i < 3 else 0.14)
    return _wav(c)


def clic():
    """A button pressed (and sounds just turned on): two micro-"beeps", like pressing and releasing. Shorter and
    quieter than the others: it is a confirmation, not a warning."""
    c = []
    _nota(c, 0.0, 0.02, MI4 * 2, "quadra", 0.6, 0.007)
    _nota(c, 0.03, 0.02, LA4 * 2, "quadra", 0.6, 0.007)
    return _wav(c, durata=0.06, volume=0.16)


SUONI = {"fatto": fatto, "errore": errore, "domanda": domanda, "clic": clic}
_PRONTI = {}  # the WAVs already generated


def _riproduci(dati):
    import winsound
    winsound.PlaySound(dati, winsound.SND_MEMORY)


def suona(nome):
    """Plays 'nome' (fatto, errore, domanda) without blocking the window: generated and played in a thread. On a
    system without winsound (not Windows) it plays nothing. True if it started."""
    try:
        import winsound  # noqa: F401
    except ImportError:
        return False

    def _lavoro():
        if nome not in _PRONTI:
            _PRONTI[nome] = SUONI[nome]()
        try:
            _riproduci(_PRONTI[nome])
        except RuntimeError:  # no audio output: no sound, no error
            pass
    threading.Thread(target=_lavoro, daemon=True).start()
    return True
