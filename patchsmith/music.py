"""PatchSmith's 8-bit style background music ("Miniera"), written here like a score and played by the program.

Four channels like a NES console: melody, arpeggio (square wave, soft), bass (triangle wave) and drums (noise and a
falling triangle hit). Notes are written in eighth-note steps: "E5:2" is an E of the fifth octave two eighths long,
"r:2" a rest of two eighths. Generated here, with no downloaded audio files and no third-party rights; the window turns
it on with the button with the note (off by default). It plays in a seamless loop from a separate audio output
(waveOut), together with the sounds of sounds.py.
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

SAMPLE_RATE = 22050  # (enough for 8 bit and quick to generate)
VOLUME = 0.2  # background level: lower than the sounds (sounds.VOLUME)
NOTES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _hz(note):
    """'A4' -> 440 Hz; sharps and flats too ('C#5', 'Bb3')."""
    name, octave = note[:-1], int(note[-1])
    semitone = NOTES[name[0]] + name[1:].count("#") - name[1:].count("b")
    return 440.0 * 2 ** ((semitone + 12 * (octave + 1) - 69) / 12)


def _read(line):
    """'E5:2 C5 r:2' -> [(note or None, steps)]."""
    notes = []
    for piece in line.split():
        name, _, steps = piece.partition(":")
        notes.append((None if name == "r" else name, int(steps or 1)))
    return notes


def _channel(length, notes, step, play):
    """Samples (float) of a channel: 'suona(campioni, inizio, quanti, frequenza)' writes each note."""
    samples = [0.0] * length
    position = 0.0
    for note, steps in notes:
        start, count = int(position * SAMPLE_RATE), int(steps * step * SAMPLE_RATE)
        if note:
            play(samples, start, min(count, length - start), _hz(note))
        position += steps * step
    return samples


def _square(duty, volume, decay, sustain, vibrato=False):
    def play(samples, start, count, frequency):
        phase = 0.0
        for i in range(count):
            t = i / SAMPLE_RATE
            f = frequency * (1 + 0.01 * math.sin(2 * math.pi * 5.5 * t)) if vibrato and t > 0.18 else frequency
            phase += f / SAMPLE_RATE
            edges = min(1.0, t / 0.003, (count - i) / SAMPLE_RATE / 0.015)  # no "click" at the start and at the end
            envelope = (sustain + (1 - sustain) * math.exp(-t / decay)) * edges
            samples[start + i] += volume * envelope * (1.0 if phase % 1.0 < duty else -1.0)
    return play


def _triangle(volume):
    def play(samples, start, count, frequency):
        phase = 0.0
        for i in range(count):
            phase += frequency / SAMPLE_RATE
            f = phase % 1.0
            envelope = min(1.0, i / SAMPLE_RATE / 0.003, (count - i) / SAMPLE_RATE / 0.015)
            samples[start + i] += volume * envelope * (4 * f - 1 if f < 0.5 else 3 - 4 * f)
    return play


def _drums(length, rhythm, step, measures, volume):
    """Drums: ritmo is a bar of 8 steps ('k' kick, 's' snare, 'h' hi-hat, '.' nothing)."""
    samples = [0.0] * length
    rng = random.Random(7)
    for measure in range(measures):
        for n, hit in enumerate(rhythm):
            start = int((measure * 8 + n) * step * SAMPLE_RATE)
            if hit == "k":  # kick: a triangle that falls quickly
                phase = 0.0
                for i in range(min(int(0.11 * SAMPLE_RATE), length - start)):
                    t = i / SAMPLE_RATE
                    phase += (45 + 120 * math.exp(-t / 0.025)) / SAMPLE_RATE
                    f = phase % 1.0
                    samples[start + i] += volume * math.exp(-t / 0.05) * (4 * f - 1 if f < 0.5 else 3 - 4 * f)
            elif hit in "sh":  # snare and hi-hat: noise that changes in steps, like the consoles
                duration, tau, force, snap = (0.13, 0.045, 0.55, 2) if hit == "s" else (0.035, 0.010, 0.22, 1)
                value_ = 0.0
                for i in range(min(int(duration * SAMPLE_RATE), length - start)):
                    if i % snap == 0:
                        value_ = rng.uniform(-1, 1)
                    samples[start + i] += volume * force * math.exp(-i / SAMPLE_RATE / tau) * value_
    return samples


def _arpeggio(chords, step, octave, sixteenths):
    """The arpeggio notes: for each bar a chord ('Am', 'F', 'C', 'G', 'Em'...) played as separate notes."""
    triads = {"": (0, 4, 7), "m": (0, 3, 7)}
    notes = []
    for chord in chords:
        root, kind = chord.rstrip("m"), "m" if chord.endswith("m") else ""
        base = NOTES[root[0]] + root[1:].count("#") - root[1:].count("b")
        names = {v: k for k, v in NOTES.items()}
        tones = [base + d for d in triads[kind]] + [base + 12]
        how_many = 16 if sixteenths else 8
        for i in range(how_many):
            semitone = tones[i % 4]
            name = names.get(semitone % 12) or names[(semitone - 1) % 12] + "#"
            notes.append(("%s%d" % (name, octave + semitone // 12), 1))
    return notes, step / (2 if sixteenths else 1)


def _wav(samples, bit=8):
    maximum = max(1e-9, max(abs(c) for c in samples))
    levels = 2 ** (bit - 1)
    data = bytearray()
    for c in samples:
        data += struct.pack("<h", int(round(c / maximum * levels) / levels * VOLUME * 32767))
    output = io.BytesIO()
    with wave.open(output, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(data))
    return output.getvalue()


def _track(tempo, chords, melody, low, rhythm, sixteenths=False, melody_duty=0.25, melody_shape="quadra",
           echo=None, arpeggio_octave=4, arpeggio_volume=0.16, drums_volume=0.5):
    """The track as WAV: eco = (delay steps, volume) on the melody, which "wraps around" the track (at the end of the
    loop the echo continues at the start: no gap when it starts again)."""
    step = 60.0 / tempo / 2  # an eighth
    measures = len(chords)
    length = int(measures * 8 * step * SAMPLE_RATE)
    if melody_shape == "triangolo":
        entry = _triangle(0.75)
    else:
        entry = _square(melody_duty, 0.5 if melody_duty < 0.5 else 0.42, 0.12, 0.7, vibrato=True)
    melody = _channel(length, _read(melody), step, entry)
    if echo:
        delay, volume = int(echo[0] * step * SAMPLE_RATE), echo[1]
        melody = [m + volume * melody[(i - delay) % length] for i, m in enumerate(melody)]
    channels = [melody, _channel(length, _read(low), step, _triangle(0.55)),
              _drums(length, rhythm, step, measures, drums_volume)]
    arpeggio_notes, arpeggio_step = _arpeggio(chords, step, arpeggio_octave, sixteenths)
    channels.append(_channel(length, arpeggio_notes, arpeggio_step, _square(0.5, arpeggio_volume, 0.05, 0.25)))
    return _wav([sum(c) for c in zip(*channels)])


def mine():
    """D, "Miniera": E minor, 76 beats per minute, the slowest and barest; triangle melody with a long echo."""
    chords = ["Em", "C", "Am", "B"] * 2 + ["Am", "Em", "C", "B", "Am", "Em", "B", "B"]
    melody = """B4:4 G4:2 E4:2   E4:4 G4:2 C5:2   A4:6 E4:2   F#4:4 D#4:4
                 E4:2 G4:2 B4:2 E5:2   D5:3 C5:1 G4:4   A4:2 C5:2 B4:2 A4:2   B4:6 r:2
                 C5:4 B4:2 A4:2   G4:4 B4:4   E5:4 D5:2 C5:2   D#5:4 B4:4
                 A4:3 B4:1 C5:4   B4:2 G4:2 E4:4   F#4:2 A4:2 D#5:4   B4:4 r:4"""
    bass_lines = {"Em": "E2:6 E3:2", "C": "C2:6 C3:2", "Am": "A1:6 A2:2", "B": "B1:6 B2:2"}
    low = " ".join(bass_lines[a] for a in chords)
    return _track(76, chords, melody, low, "k.....h.", melody_shape="triangolo", echo=(4, 0.4),
                  arpeggio_octave=3, arpeggio_volume=0.12, drums_volume=0.3)


TRACKS = {"miniera": mine}
_READY = {}  # track -> PCM samples already generated
_state = {"voluta": False, "uscita": None, "buffer": None, "intestazione": None, "winmm": None}
_lock = threading.Lock()
_prepare_lock = threading.Lock()


def _ready_file(track):
    """Where the already generated track is kept (Windows temporary folder): the name changes with this file, so
    changed music never uses the old one."""
    with open(os.path.abspath(__file__), "rb") as f:
        fingerprint = hashlib.sha1(f.read()).hexdigest()[:12]
    return os.path.join(tempfile.gettempdir(), "PatchSmith", "musica-%s-%s.pcm" % (track, fingerprint))


def prepare(track="miniera"):
    """The track's samples ready to play: already in memory, from the file of the previous time or generated (a couple
    of seconds; then the file is written). The window calls it when it opens, in a thread, even with the music off:
    that way the button with the note starts it right away."""
    with _prepare_lock:
        if track in _READY:
            return _READY[track]
        path = _ready_file(track)
        try:
            with open(path, "rb") as f:
                pcm = f.read()
        except OSError:
            pcm = b""
        if not pcm:
            with wave.open(io.BytesIO(TRACKS[track]())) as w:
                pcm = w.readframes(w.getnframes())
            try:  # (a temporary name, renamed afterwards: never a half-written file)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path + ".tmp", "wb") as f:
                    f.write(pcm)
                os.replace(path + ".tmp", path)
            except OSError:
                pass
        _READY[track] = pcm
        return pcm


def launch(track="miniera"):
    """Starts the track in a loop without blocking the window (in a thread: if it is not ready yet, see prepara, it
    starts as soon as it is). Without waveOut (not Windows) or without an audio output nothing plays."""
    _state["voluta"] = True
    threading.Thread(target=_prepare_and_play, args=(track,), daemon=True).start()


def stop():
    with _lock:
        _state["voluta"] = False
        _close()


def _prepare_and_play(track):
    pcm = prepare(track)
    with _lock:
        if _state["voluta"] and _state["uscita"] is None:  # (turned off while it was being prepared: nothing)
            _open(pcm)


class _Format(ctypes.Structure):
    _fields_ = [("wFormatTag", ctypes.c_ushort), ("nChannels", ctypes.c_ushort), ("nSamplesPerSec", ctypes.c_ulong),
                ("nAvgBytesPerSec", ctypes.c_ulong), ("nBlockAlign", ctypes.c_ushort),
                ("wBitsPerSample", ctypes.c_ushort), ("cbSize", ctypes.c_ushort)]


class _Header(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", ctypes.c_ulong), ("dwBytesRecorded", ctypes.c_ulong),
                ("dwUser", ctypes.c_size_t), ("dwFlags", ctypes.c_ulong), ("dwLoops", ctypes.c_ulong),
                ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]


def _open(pcm):
    """A waveOut output (mono, 16 bit, SAMPLE_RATE) that repeats 'pcm' forever: Windows does the loop
    (WHDR_BEGINLOOP | WHDR_ENDLOOP), so with no gaps; it is a separate output, so the winsound sounds (which plays one
    at a time) are heard together with the music. True if it plays."""
    try:
        winmm = ctypes.windll.winmm
    except (AttributeError, OSError):
        return False
    winmm.waveOutOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.c_size_t, ctypes.c_ulong]
    for func in ("waveOutPrepareHeader", "waveOutWrite", "waveOutUnprepareHeader"):
        getattr(winmm, func).argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    for func in ("waveOutReset", "waveOutClose"):
        getattr(winmm, func).argtypes = [ctypes.c_void_p]
    fmt = _Format(1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16, 0)
    output = ctypes.c_void_p()
    if winmm.waveOutOpen(ctypes.byref(output), 0xFFFFFFFF, ctypes.byref(fmt), 0, 0, 0) != 0:  # WAVE_MAPPER
        return False
    buffer = ctypes.create_string_buffer(pcm, len(pcm))
    header = _Header(ctypes.cast(buffer, ctypes.c_void_p), len(pcm), 0, 0, 0, 0, None, 0)
    size = ctypes.sizeof(header)
    if winmm.waveOutPrepareHeader(output, ctypes.byref(header), size) != 0:
        winmm.waveOutClose(output)
        return False
    header.dwFlags |= 0x04 | 0x08  # WHDR_BEGINLOOP | WHDR_ENDLOOP: this block repeats
    header.dwLoops = 0xFFFFFFFF
    _state.update(uscita=output, buffer=buffer, intestazione=header, winmm=winmm)
    if winmm.waveOutWrite(output, ctypes.byref(header), size) != 0:
        _close()
        return False
    return True


def _close():
    output, winmm = _state["uscita"], _state["winmm"]
    if output is None:
        return
    winmm.waveOutReset(output)
    winmm.waveOutUnprepareHeader(output, ctypes.byref(_state["intestazione"]), ctypes.sizeof(_state["intestazione"]))
    winmm.waveOutClose(output)
    _state.update(uscita=None, buffer=None, intestazione=None)


def _played():
    """Bytes played by the open output (loop repetitions count too), or None: for the tests."""
    if _state["uscita"] is None:
        return None

    class MmTime(ctypes.Structure):
        _fields_ = [("wType", ctypes.c_uint), ("value_", ctypes.c_ulong), ("rest", ctypes.c_ulong)]
    tempo = MmTime(4, 0, 0)  # TIME_BYTES
    _state["winmm"].waveOutGetPosition.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    if _state["winmm"].waveOutGetPosition(_state["uscita"], ctypes.byref(tempo), ctypes.sizeof(tempo)) != 0:
        return None
    return tempo.value_
