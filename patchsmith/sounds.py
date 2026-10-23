"""PatchSmith's 8-bit style sounds (square and triangle waves, like old consoles).

Generated here, with no downloaded audio files and no third-party rights: each sound is a sequence of notes written as
an in-memory WAV (16 bit, mono). They all have the same "voice" (a full square wave and, an octave below, a triangle
wave, with notes that fade out) and the same duration (DURATION). Three sounds: "done" (operation finished), "error"
and "question" (PatchSmith is waiting for a choice); the window plays them if they are on (the speaker button, off by
default). Plus "click", very short, for every button pressed.
"""
import io
import math
import struct
import threading
import wave

SAMPLE_RATE = 44100
DURATION = 0.75  # seconds, the same for all sounds
VOLUME = 0.32  # (not too loud: winsound has no volume control)

# notes (Hz)
C3, EB3, G3 = 130.81, 155.56, 196.0
C4, E4, G4, A4 = 261.63, 329.63, 392.0, 440.0
C5, D5 = 523.25, 587.33


def _waveform(shape, phase):
    f = phase % 1.0
    if shape == "square":
        return 1.0 if f < 0.5 else -1.0
    return 4 * f - 1 if f < 0.5 else 3 - 4 * f  # triangle


def _note(samples, start, duration, frequency, shape, volume, decay, slide=None, vibrato=False):
    """Adds a note to the samples (float): start and duration in seconds; decay: time (s) in which the volume
    drops to about a third; slide: frequency reached at the end; vibrato: light, after 80 ms."""
    first, count = int(start * SAMPLE_RATE), int(duration * SAMPLE_RATE)
    if len(samples) < first + count:
        samples.extend([0.0] * (first + count - len(samples)))
    phase = 0.0
    for i in range(count):
        t = i / SAMPLE_RATE
        f = frequency if slide is None else frequency + (slide - frequency) * i / count
        if vibrato:
            f *= 1 + 0.012 * math.sin(2 * math.pi * 6 * t) * min(1.0, t / 0.08)
        phase += f / SAMPLE_RATE
        envelope = min(1.0, t / 0.004, (duration - t) / 0.01) * math.exp(-t / decay)  # no "click"
        samples[first + i] += volume * envelope * _waveform(shape, phase)


def _voice(samples, start, duration, frequency, decay, slide=None, vibrato=False):
    """The voice of all sounds: a square wave and, an octave below, a triangle wave."""
    _note(samples, start, duration, frequency, "square", 0.6, decay, slide, vibrato)
    _note(samples, start, duration, frequency / 2, "triangle", 0.6, decay,
          slide / 2 if slide else None, vibrato)


def _wav(samples, bit=6, duration=DURATION, volume=VOLUME):
    """In-memory WAV lasting 'duration' seconds; the samples are reduced to 'bit' levels (the "grainy" 8-bit sound)."""
    count = int(duration * SAMPLE_RATE)
    samples = (samples + [0.0] * count)[:count]
    maximum = max(1e-9, max(abs(c) for c in samples))
    levels = 2 ** (bit - 1)
    data = bytearray()
    for c in samples:
        c = round(c / maximum * levels) / levels * volume
        data += struct.pack("<h", int(max(-1.0, min(1.0, c)) * 32767))
    output = io.BytesIO()
    with wave.open(output, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(data))
    return output.getvalue()


def error():
    """Error: two low notes going down ("bonk-bonk")."""
    c = []
    _voice(c, 0.0, 0.16, G3, 0.14)
    _voice(c, 0.17, 0.58, EB3, 0.22, slide=C3)
    return _wav(c)


def done():
    """Translation finished: three rising notes, the last one held with a little vibrato."""
    c = []
    _voice(c, 0.0, 0.11, C4, 0.12)
    _voice(c, 0.12, 0.11, E4, 0.12)
    _voice(c, 0.24, 0.51, G4, 0.26, vibrato=True)
    return _wav(c)


def question():
    """Question waiting: a high and a low note bouncing ("ping-pong-ping-pong"), neither rising nor falling: it
    cannot be mistaken for "done" (rises) or for "error" (falls)."""
    c = []
    for i, start in enumerate((0.0, 0.13, 0.26, 0.39)):
        _voice(c, start, 0.12 if i < 3 else 0.3, D5 if i % 2 == 0 else A4, 0.1 if i < 3 else 0.14)
    return _wav(c)


def click():
    """A button pressed (and sounds just turned on): two micro-"beeps", like pressing and releasing. Shorter and
    quieter than the others: it is a confirmation, not a warning."""
    c = []
    _note(c, 0.0, 0.02, E4 * 2, "square", 0.6, 0.007)
    _note(c, 0.03, 0.02, A4 * 2, "square", 0.6, 0.007)
    return _wav(c, duration=0.06, volume=0.16)


SOUNDS = {"done": done, "error": error, "question": question, "click": click}
_READY = {}  # the WAVs already generated


def _play(data):
    import winsound
    winsound.PlaySound(data, winsound.SND_MEMORY)


def play(name):
    """Plays 'name' (done, error, question, click) without blocking the window: generated and played in a thread. On a
    system without winsound (not Windows) it plays nothing. True if it started."""
    try:
        import winsound  # noqa: F401
    except ImportError:
        return False

    def _work():
        if name not in _READY:
            _READY[name] = SOUNDS[name]()
        try:
            _play(_READY[name])
        except RuntimeError:  # no audio output: no sound, no error
            pass
    threading.Thread(target=_work, daemon=True).start()
    return True
