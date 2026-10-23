"""Musica di sottofondo di PatchSmith in stile 8 bit ("Miniera"), scritta qui come uno spartito e suonata dal programma.

Quattro canali come una console NES: melodia, arpeggio (onda quadra, piano), basso (onda triangolare) e batteria (rumore
e un colpo di triangolo che scende). Le note si scrivono a passi di un ottavo: "E5:2" e' un Mi della quinta ottava lungo
due ottavi, "r:2" una pausa di due ottavi. Generata qui, senza file audio scaricati e senza diritti di altri; la
finestra la accende con il pulsante con la nota (spento di default). Suona in loop senza stacchi da un'uscita audio a
parte (waveOut), insieme ai suoni di suoni.py.
"""
import ctypes
import hashlib
import io
import math
import os
import random
import struct
import tempfile
import threading
import wave

FREQUENZA = 22050  # (basta per l'8 bit e si genera in fretta)
VOLUME = 0.2  # da sottofondo: piu' bassa dei suoni (suoni.VOLUME)
NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _hz(nota):
    """'A4' -> 440 Hz; anche diesis e bemolle ('C#5', 'Bb3')."""
    nome, ottava = nota[:-1], int(nota[-1])
    semitono = NOTE[nome[0]] + nome[1:].count("#") - nome[1:].count("b")
    return 440.0 * 2 ** ((semitono + 12 * (ottava + 1) - 69) / 12)


def _leggi(riga):
    """'E5:2 C5 r:2' -> [(nota o None, passi)]."""
    note = []
    for pezzo in riga.split():
        nome, _, passi = pezzo.partition(":")
        note.append((None if nome == "r" else nome, int(passi or 1)))
    return note


def _canale(lunghezza, note, passo, suona):
    """Campioni (float) di un canale: 'suona(campioni, inizio, quanti, frequenza)' scrive ogni nota."""
    campioni = [0.0] * lunghezza
    posizione = 0.0
    for nota, passi in note:
        inizio, quanti = int(posizione * FREQUENZA), int(passi * passo * FREQUENZA)
        if nota:
            suona(campioni, inizio, min(quanti, lunghezza - inizio), _hz(nota))
        posizione += passi * passo
    return campioni


def _quadra(duty, volume, decadimento, sostegno, vibrato=False):
    def suona(campioni, inizio, quanti, frequenza):
        fase = 0.0
        for i in range(quanti):
            t = i / FREQUENZA
            f = frequenza * (1 + 0.01 * math.sin(2 * math.pi * 5.5 * t)) if vibrato and t > 0.18 else frequenza
            fase += f / FREQUENZA
            bordi = min(1.0, t / 0.003, (quanti - i) / FREQUENZA / 0.015)  # niente "click" all'inizio e alla fine
            inviluppo = (sostegno + (1 - sostegno) * math.exp(-t / decadimento)) * bordi
            campioni[inizio + i] += volume * inviluppo * (1.0 if fase % 1.0 < duty else -1.0)
    return suona


def _triangolo(volume):
    def suona(campioni, inizio, quanti, frequenza):
        fase = 0.0
        for i in range(quanti):
            fase += frequenza / FREQUENZA
            f = fase % 1.0
            inviluppo = min(1.0, i / FREQUENZA / 0.003, (quanti - i) / FREQUENZA / 0.015)
            campioni[inizio + i] += volume * inviluppo * (4 * f - 1 if f < 0.5 else 3 - 4 * f)
    return suona


def _batteria(lunghezza, ritmo, passo, battute, volume):
    """Batteria: ritmo e' una battuta di 8 passi ('k' cassa, 's' rullante, 'h' charleston, '.' niente)."""
    campioni = [0.0] * lunghezza
    caso = random.Random(7)
    for battuta in range(battute):
        for n, colpo in enumerate(ritmo):
            inizio = int((battuta * 8 + n) * passo * FREQUENZA)
            if colpo == "k":  # cassa: un triangolo che scende in fretta
                fase = 0.0
                for i in range(min(int(0.11 * FREQUENZA), lunghezza - inizio)):
                    t = i / FREQUENZA
                    fase += (45 + 120 * math.exp(-t / 0.025)) / FREQUENZA
                    f = fase % 1.0
                    campioni[inizio + i] += volume * math.exp(-t / 0.05) * (4 * f - 1 if f < 0.5 else 3 - 4 * f)
            elif colpo in "sh":  # rullante e charleston: rumore che cambia a scatti, come le console
                durata, tau, forza, scatto = (0.13, 0.045, 0.55, 2) if colpo == "s" else (0.035, 0.010, 0.22, 1)
                valore = 0.0
                for i in range(min(int(durata * FREQUENZA), lunghezza - inizio)):
                    if i % scatto == 0:
                        valore = caso.uniform(-1, 1)
                    campioni[inizio + i] += volume * forza * math.exp(-i / FREQUENZA / tau) * valore
    return campioni


def _arpeggio(accordi, passo, ottava, sedicesimi):
    """Le note dell'arpeggio: per ogni battuta un accordo ('Am', 'F', 'C', 'G', 'Em'...) suonato a note staccate."""
    triadi = {"": (0, 4, 7), "m": (0, 3, 7)}
    note = []
    for accordo in accordi:
        radice, tipo = accordo.rstrip("m"), "m" if accordo.endswith("m") else ""
        base = NOTE[radice[0]] + radice[1:].count("#") - radice[1:].count("b")
        nomi = {v: k for k, v in NOTE.items()}
        toni = [base + d for d in triadi[tipo]] + [base + 12]
        quante = 16 if sedicesimi else 8
        for i in range(quante):
            semitono = toni[i % 4]
            nome = nomi.get(semitono % 12) or nomi[(semitono - 1) % 12] + "#"
            note.append(("%s%d" % (nome, ottava + semitono // 12), 1))
    return note, passo / (2 if sedicesimi else 1)


def _wav(campioni, bit=8):
    massimo = max(1e-9, max(abs(c) for c in campioni))
    livelli = 2 ** (bit - 1)
    dati = bytearray()
    for c in campioni:
        dati += struct.pack("<h", int(round(c / massimo * livelli) / livelli * VOLUME * 32767))
    uscita = io.BytesIO()
    with wave.open(uscita, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FREQUENZA)
        w.writeframes(bytes(dati))
    return uscita.getvalue()


def _brano(tempo, accordi, melodia, basso, ritmo, sedicesimi=False, duty_melodia=0.25, forma_melodia="quadra",
           eco=None, ottava_arpeggio=4, volume_arpeggio=0.16, volume_batteria=0.5):
    """Il brano in WAV: eco = (passi di ritardo, volume) sulla melodia, che "fa il giro" del brano (alla fine del loop
    l'eco continua all'inizio: nessuno stacco quando ricomincia)."""
    passo = 60.0 / tempo / 2  # un ottavo
    battute = len(accordi)
    lunghezza = int(battute * 8 * passo * FREQUENZA)
    if forma_melodia == "triangolo":
        voce = _triangolo(0.75)
    else:
        voce = _quadra(duty_melodia, 0.5 if duty_melodia < 0.5 else 0.42, 0.12, 0.7, vibrato=True)
    melodia = _canale(lunghezza, _leggi(melodia), passo, voce)
    if eco:
        ritardo, volume = int(eco[0] * passo * FREQUENZA), eco[1]
        melodia = [m + volume * melodia[(i - ritardo) % lunghezza] for i, m in enumerate(melodia)]
    canali = [melodia, _canale(lunghezza, _leggi(basso), passo, _triangolo(0.55)),
              _batteria(lunghezza, ritmo, passo, battute, volume_batteria)]
    note_arpeggio, passo_arpeggio = _arpeggio(accordi, passo, ottava_arpeggio, sedicesimi)
    canali.append(_canale(lunghezza, note_arpeggio, passo_arpeggio, _quadra(0.5, volume_arpeggio, 0.05, 0.25)))
    return _wav([sum(c) for c in zip(*canali)])


def miniera():
    """D, "Miniera": Mi minore, 76 battiti al minuto, la piu' lenta e spoglia; melodia a triangolo con eco lunga."""
    accordi = ["Em", "C", "Am", "B"] * 2 + ["Am", "Em", "C", "B", "Am", "Em", "B", "B"]
    melodia = """B4:4 G4:2 E4:2   E4:4 G4:2 C5:2   A4:6 E4:2   F#4:4 D#4:4
                 E4:2 G4:2 B4:2 E5:2   D5:3 C5:1 G4:4   A4:2 C5:2 B4:2 A4:2   B4:6 r:2
                 C5:4 B4:2 A4:2   G4:4 B4:4   E5:4 D5:2 C5:2   D#5:4 B4:4
                 A4:3 B4:1 C5:4   B4:2 G4:2 E4:4   F#4:2 A4:2 D#5:4   B4:4 r:4"""
    bassi = {"Em": "E2:6 E3:2", "C": "C2:6 C3:2", "Am": "A1:6 A2:2", "B": "B1:6 B2:2"}
    basso = " ".join(bassi[a] for a in accordi)
    return _brano(76, accordi, melodia, basso, "k.....h.", forma_melodia="triangolo", eco=(4, 0.4),
                  ottava_arpeggio=3, volume_arpeggio=0.12, volume_batteria=0.3)


BRANI = {"miniera": miniera}
_PRONTI = {}  # brano -> campioni PCM gia' generati
_stato = {"voluta": False, "uscita": None, "buffer": None, "intestazione": None, "winmm": None}
_lucchetto = threading.Lock()
_lucchetto_prepara = threading.Lock()


def _file_pronto(brano):
    """Dove resta il brano gia' generato (cartella temporanea di Windows): il nome cambia con questo file, quindi una
    musica cambiata non usa mai quello vecchio."""
    with open(os.path.abspath(__file__), "rb") as f:
        impronta = hashlib.sha1(f.read()).hexdigest()[:12]
    return os.path.join(tempfile.gettempdir(), "PatchSmith", "musica-%s-%s.pcm" % (brano, impronta))


def prepara(brano="miniera"):
    """I campioni del brano pronti per suonare: gia' in memoria, dal file della volta prima o generati (un paio di
    secondi; poi il file si scrive). La finestra la chiama all'apertura in un thread, anche con la musica spenta:
    cosi' il pulsante con la nota la fa partire subito."""
    with _lucchetto_prepara:
        if brano in _PRONTI:
            return _PRONTI[brano]
        percorso = _file_pronto(brano)
        try:
            with open(percorso, "rb") as f:
                pcm = f.read()
        except OSError:
            pcm = b""
        if not pcm:
            with wave.open(io.BytesIO(BRANI[brano]())) as w:
                pcm = w.readframes(w.getnframes())
            try:  # (un nome temporaneo e poi rinominato: mai un file a meta')
                os.makedirs(os.path.dirname(percorso), exist_ok=True)
                with open(percorso + ".tmp", "wb") as f:
                    f.write(pcm)
                os.replace(percorso + ".tmp", percorso)
            except OSError:
                pass
        _PRONTI[brano] = pcm
        return pcm


def avvia(brano="miniera"):
    """Fa partire il brano in loop senza fermare la finestra (in un thread: se non e' ancora pronto, vedi prepara,
    parte appena lo e'). Senza waveOut (non Windows) o senza un'uscita audio non suona nulla."""
    _stato["voluta"] = True
    threading.Thread(target=_prepara_e_suona, args=(brano,), daemon=True).start()


def ferma():
    with _lucchetto:
        _stato["voluta"] = False
        _chiudi()


def _prepara_e_suona(brano):
    pcm = prepara(brano)
    with _lucchetto:
        if _stato["voluta"] and _stato["uscita"] is None:  # (spenta mentre si preparava: niente)
            _apri(pcm)


class _Formato(ctypes.Structure):
    _fields_ = [("wFormatTag", ctypes.c_ushort), ("nChannels", ctypes.c_ushort), ("nSamplesPerSec", ctypes.c_ulong),
                ("nAvgBytesPerSec", ctypes.c_ulong), ("nBlockAlign", ctypes.c_ushort),
                ("wBitsPerSample", ctypes.c_ushort), ("cbSize", ctypes.c_ushort)]


class _Intestazione(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", ctypes.c_ulong), ("dwBytesRecorded", ctypes.c_ulong),
                ("dwUser", ctypes.c_size_t), ("dwFlags", ctypes.c_ulong), ("dwLoops", ctypes.c_ulong),
                ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]


def _apri(pcm):
    """Un'uscita waveOut (mono, 16 bit, FREQUENZA) che ripete 'pcm' all'infinito: il loop lo fa Windows
    (WHDR_BEGINLOOP | WHDR_ENDLOOP), quindi senza stacchi; e' un'uscita a parte, cosi' i suoni di winsound (che ne
    suona uno alla volta) si sentono insieme alla musica. True se suona."""
    try:
        winmm = ctypes.windll.winmm
    except (AttributeError, OSError):
        return False
    winmm.waveOutOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.c_size_t, ctypes.c_ulong]
    for funzione in ("waveOutPrepareHeader", "waveOutWrite", "waveOutUnprepareHeader"):
        getattr(winmm, funzione).argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    for funzione in ("waveOutReset", "waveOutClose"):
        getattr(winmm, funzione).argtypes = [ctypes.c_void_p]
    formato = _Formato(1, 1, FREQUENZA, FREQUENZA * 2, 2, 16, 0)
    uscita = ctypes.c_void_p()
    if winmm.waveOutOpen(ctypes.byref(uscita), 0xFFFFFFFF, ctypes.byref(formato), 0, 0, 0) != 0:  # WAVE_MAPPER
        return False
    buffer = ctypes.create_string_buffer(pcm, len(pcm))
    intestazione = _Intestazione(ctypes.cast(buffer, ctypes.c_void_p), len(pcm), 0, 0, 0, 0, None, 0)
    dimensione = ctypes.sizeof(intestazione)
    if winmm.waveOutPrepareHeader(uscita, ctypes.byref(intestazione), dimensione) != 0:
        winmm.waveOutClose(uscita)
        return False
    intestazione.dwFlags |= 0x04 | 0x08  # WHDR_BEGINLOOP | WHDR_ENDLOOP: questo blocco si ripete
    intestazione.dwLoops = 0xFFFFFFFF
    _stato.update(uscita=uscita, buffer=buffer, intestazione=intestazione, winmm=winmm)
    if winmm.waveOutWrite(uscita, ctypes.byref(intestazione), dimensione) != 0:
        _chiudi()
        return False
    return True


def _chiudi():
    uscita, winmm = _stato["uscita"], _stato["winmm"]
    if uscita is None:
        return
    winmm.waveOutReset(uscita)
    winmm.waveOutUnprepareHeader(uscita, ctypes.byref(_stato["intestazione"]), ctypes.sizeof(_stato["intestazione"]))
    winmm.waveOutClose(uscita)
    _stato.update(uscita=None, buffer=None, intestazione=None)


def _suonati():
    """Byte suonati dall'uscita aperta (contano anche i giri del loop), o None: per le prove."""
    if _stato["uscita"] is None:
        return None

    class Tempo(ctypes.Structure):
        _fields_ = [("wType", ctypes.c_uint), ("valore", ctypes.c_ulong), ("resto", ctypes.c_ulong)]
    tempo = Tempo(4, 0, 0)  # TIME_BYTES
    _stato["winmm"].waveOutGetPosition.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    if _stato["winmm"].waveOutGetPosition(_stato["uscita"], ctypes.byref(tempo), ctypes.sizeof(tempo)) != 0:
        return None
    return tempo.valore
