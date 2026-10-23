"""Test of the protection of placeholders and game codes (text.protect), shared by all engines.

Usage: python -I test/test_placeholders.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import text  # noqa: E402

CASES = [
    # (text, expected placeholders)
    ("Press [Input:12] to jump\\nand {0} times", ["[Input:12]", "\\n", "{0}"]),  # \n written as text
    ("[Button:LeftStick] to navigate", ["[Button:LeftStick]"]),
    ("[x] heroes slain by [y]", ["[x]", "[y]"]),
    ("Line one\\nLine two", ["\\n"]),  # the word after \n is still to be translated
    ("\\C[3]Red\\C[0] text \\N[1] and \\n[2] and \\G", ["\\C[3]", "\\C[0]", "\\N[1]", "\\n[2]", "\\G"]),  # RPG Maker
    ("\\nc<Harold>Hello there!", ["\\nc", "<Harold>"]),  # name in the name box (Yanfly)
    ("\\nr<\\N[2]>Hi", ["\\nr", "<\\N[2]>"]),
    ("Saved to C:\\new\\test\\readme.txt ok", ["C:\\new\\test\\readme.txt"]),  # whole path
    ("%APPDATA%\\roaming\\temp here", ["%APPDATA%\\roaming\\temp"]),
    ("Option A:\\nNext", ["\\n"]),
    ("Can be interrupted;-;Knocks back", [";-;"]),
    ("Hello %s, you have %d coins", ["%s", "%d"]),
    ("[Note: read me] and [12:30]", []),  # bracketed text with spaces or numbers: it is translated
    ("Hey /name/. Got /curr/ dosh", ["/name/", "/curr/"]),  # codes between slashes (GameMaker/Dokimon)
    ("worldly governors/n/worldly governors", ["/n/"]),
    ("either/or and 1/2/3", []),  # ordinary slashes: they are translated
    ("both gain [yellow1]{0}[/c] HP", ["[yellow1]", "{0}", "[/c]"]),  # Scribble tags (GameMaker), closing tag too
    ("[ftAlkhemikalSh]A game by[/f] and [/] end", ["[ftAlkhemikalSh]", "[/f]", "[/]"]),
]


def main():
    ok = 0
    for s, expected in CASES:
        shielded, found = text.protect(s)
        put_back_text = text.restore_placeholders(shielded, found)
        outcome = found == expected and put_back_text == s
        ok += outcome
        print(("OK    " if outcome else "ERROR ") + "  %r -> %r %s" % (s, shielded, found))
    print("\nRESULT: %d/%d checks passed" % (ok, len(CASES)))
    return ok == len(CASES)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main() else 1)
