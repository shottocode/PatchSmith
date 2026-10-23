"""PatchSmith's icon: a hammer over a folder (the game's folder, where PatchSmith does its work).

Drawn here, with no downloaded files: run on its own (patchsmith/icon.py) it remakes the images in patchsmith/icon/
(a PNG for each size and PatchSmith.ico), which the window loads at startup (apply), and PatchSmith.exe
(executable). The shapes are described on a square of side 1 and each pixel takes the average color of its sub-points
(soft edges, transparency around).
"""
import math
import os
import struct
import subprocess
import sys
import zlib

FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon")
PROGRAM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the folder with PatchSmith.pyw
SIDES = (16, 24, 32, 48, 64, 128, 256)

FOLDER_FRONT, FOLDER_BACK, FOLDER_EDGE = (0xff, 0xc8, 0x45), (0xe0, 0x9a, 0x12), (0x9a, 0x62, 0x08)
HEAD, HANDLE, OUTLINE = (0xdf, 0xe5, 0xec), (0x9b, 0x5b, 0x2a), (0x1f, 0x29, 0x37)

# the hammer: handle from A towards the top right (45 degrees), head across it at the top
_A = (0.46, 0.90)
_D = (math.sqrt(0.5), -math.sqrt(0.5))  # along the handle
_N = (-_D[1], _D[0])  # across
HANDLE_END, HALF_WIDTH = 0.38, 0.048
HEAD_FROM, HEAD_TO, HALF_HEAD = 0.38, 0.52, 0.15
OUTLINE_THICKNESS = 0.035


def _rounded(x, y, x0, y0, x1, y1, r):
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx, cy = min(max(x, x0 + r), x1 - r), min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _folder(x, y, margin=0.0):
    """'front' (the lower, lighter part), 'back' (with the tab at the top left) or None; with a negative margin
    the shapes shrink (what is left outside is the border)."""
    m = margin
    if _rounded(x, y, 0.06 - m, 0.34 - m, 0.90 + m, 0.84 + m, 0.06 + m):
        return "front"
    if _rounded(x, y, 0.06 - m, 0.14 - m, 0.42 + m, 0.32 + m, 0.05 + m) or \
            _rounded(x, y, 0.06 - m, 0.22 - m, 0.90 + m, 0.84 + m, 0.06 + m):
        return "back"
    return None


def _hammer(x, y, margin=0.0):
    """'head', 'handle' or None; with a margin the shapes grow (for the outline)."""
    t = (x - _A[0]) * _D[0] + (y - _A[1]) * _D[1]
    s = (x - _A[0]) * _N[0] + (y - _A[1]) * _N[1]
    if abs(s) <= HALF_HEAD + margin:
        # the head: on the side opposite the striking face it gets thinner (the peen)
        half_thickness = (HEAD_TO - HEAD_FROM) / 2 * (1 - 0.6 * max(0.0, -s / HALF_HEAD))
        if abs(t - (HEAD_FROM + HEAD_TO) / 2) <= half_thickness + margin:
            return "head"
    if 0 <= t <= HANDLE_END and abs(s) <= HALF_WIDTH + margin:
        return "handle"
    if t < 0 and math.hypot(t, s) <= HALF_WIDTH + margin:  # rounded end of the handle
        return "handle"
    return None


def _color(x, y):
    """(r, g, b, a) of the point (x, y) of the square of side 1."""
    part = _hammer(x, y)
    if part:
        return (HEAD if part == "head" else HANDLE) + (255,)
    if _hammer(x, y, OUTLINE_THICKNESS):
        return OUTLINE + (255,)
    part = _folder(x, y)
    if part:
        inside = _folder(x, y, -OUTLINE_THICKNESS)
        if part == "front" and inside == "front":
            return FOLDER_FRONT + (255,)
        if part == "back" and inside:
            return FOLDER_BACK + (255,)
        return FOLDER_EDGE + (255,)
    return (0, 0, 0, 0)


def draw(side):
    """RGBA pixels (bytes, row by row) of the icon of 'side' pixels."""
    steps = max(4, 64 // side)  # sub-points per side of a pixel: more for small icons
    lines = []
    for py in range(side):
        line = bytearray()
        for px in range(side):
            sums = [0, 0, 0, 0]
            for sy in range(steps):
                for sx in range(steps):
                    r, g, b, a = _color((px + (sx + 0.5) / steps) / side, (py + (sy + 0.5) / steps) / side)
                    sums[0] += r * a
                    sums[1] += g * a
                    sums[2] += b * a
                    sums[3] += a
            if sums[3]:
                line += bytes((round(sums[0] / sums[3]), round(sums[1] / sums[3]), round(sums[2] / sums[3]),
                               round(sums[3] / steps ** 2)))
            else:
                line += b"\0\0\0\0"
        lines.append(bytes(line))
    return lines


def png(side):
    """The icon of 'side' pixels as a PNG file (RGBA)."""
    def block(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    raw = b"".join(b"\0" + line for line in draw(side))
    return (b"\x89PNG\r\n\x1a\n" + block(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 6, 0, 0, 0))
            + block(b"IDAT", zlib.compress(raw, 9)) + block(b"IEND", b""))


def ico(images):
    """Windows .ico file with the given PNG images ({side: png})."""
    head = struct.pack("<HHH", 0, 1, len(images))
    position, entries, data = 6 + 16 * len(images), b"", b""
    for side, content in sorted(images.items()):
        entries += struct.pack("<BBBBHHII", side % 256, side % 256, 0, 0, 1, 32, len(content), position)
        position += len(content)
        data += content
    return head + entries + data


def write(folder=FOLDER):
    """Remakes the icon images: <side>.png for each size and PatchSmith.ico."""
    os.makedirs(folder, exist_ok=True)
    images = {side: png(side) for side in SIDES}
    for side, content in images.items():
        with open(os.path.join(folder, "%d.png" % side), "wb") as f:
            f.write(content)
    with open(os.path.join(folder, "PatchSmith.ico"), "wb") as f:
        f.write(ico({k: v for k, v in images.items() if k <= 256}))
    return images


def apply(root):
    """Puts the icon on the window and on all those opened afterwards (dialogs included). Without the images
    (icon/ folder missing) Tk's icon stays: it is not an error."""
    import tkinter as tk
    images = []
    for side in SIDES:
        path = os.path.join(FOLDER, "%d.png" % side)
        if side <= 128 and os.path.isfile(path):  # (above 128 pixels Windows does not use them for windows)
            images.append(tk.PhotoImage(master=root, file=path))
    if images:
        root.iconphoto(True, *images)
        root._patchsmith_icon = images  # (Tk images must be kept, otherwise they disappear)
    return bool(images)


def _csc():
    """C# compiler of .NET Framework 4 (present in every Windows 10 and 11; adapter_unity uses it too)."""
    windows = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    for framework in ("Framework64", "Framework"):
        p = os.path.join(windows, "Microsoft.NET", framework, "v4.0.30319", "csc.exe")
        if os.path.exists(p):
            return p
    return None


def executable(folder=PROGRAM):
    """PatchSmith.exe next to PatchSmith.pyw, with this icon inside: the program to double-click (the .pyw necessarily
    has Python's icon, which Windows gives to the whole file type). It is the small launcher of launcher.cs, which opens
    PatchSmith.pyw with the installed Python. It is compiled (Windows C# compiler, nothing to download) only if it is
    missing or older than launcher.cs or the icon. True if it exists (already or now)."""
    source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.cs")
    icon = os.path.join(FOLDER, "PatchSmith.ico")
    dest_lang = os.path.join(folder, "PatchSmith.exe")
    if not sys.platform.startswith("win") or not (os.path.isfile(source) and os.path.isfile(icon)):
        return False
    try:
        if os.path.getmtime(dest_lang) >= max(os.path.getmtime(source), os.path.getmtime(icon)):
            return True
    except OSError:
        pass
    csc = _csc()
    if not csc:
        return os.path.isfile(dest_lang)
    temp_file = os.path.join(folder, "PatchSmith.nuovo.exe")  # renamed afterwards: never a half-written .exe
    command = [csc, "-nologo", "-target:winexe", "-optimize", "-codepage:65001", "-win32icon:" + icon,
               "-r:System.Windows.Forms.dll", "-out:" + temp_file, source]
    try:
        outcome = subprocess.run(command, capture_output=True, timeout=120, creationflags=0x08000000)
        if outcome.returncode == 0 and os.path.isfile(temp_file):
            os.replace(temp_file, dest_lang)
    except (OSError, subprocess.SubprocessError):
        pass
    if os.path.isfile(temp_file):
        try:
            os.remove(temp_file)
        except OSError:
            pass
    return os.path.isfile(dest_lang)


if __name__ == "__main__":
    write()
    print("Icon images in", FOLDER)
    if executable():
        print("PatchSmith.exe in", PROGRAM)
