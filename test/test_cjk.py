"""Test of the recognition of texts in Japanese, Chinese and Korean (and that nothing changes for the rest).

Usage: python -I test/test_cjk.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith.adapter_webui import is_ui_text, js_literals, _decode  # noqa: E402
from patchsmith.text import is_readable_text  # noqa: E402

TEXT = [  # must be recognized as text (data and menus)
    "設定", "はい", "いいえ", "戻る", "はじめる", "つづきから",
    "霧に包まれた屋敷の扉が、ゆっくりと開いた。誰があなたを呼んだのだろうか。この屋敷には、まだ誰も知らない秘密が眠っている。",
    "3つの部屋をクリアすると扉が開く。", "残り5マス", "はい/いいえ", "ＨＰが回復した！",
    "开始游戏", "设置", "继续", "数字在每一行、每一列和每一个宫中只能出现一次，请仔细观察并找出唯一的答案。这是第2关。",
    "設定を保存しました", "시작", "설정", "저택에 오신 것을 환영합니다.", "남은 칸: 3",
]
NOT_TEXT = [  # must stay excluded
    "img/背景", "img/pictures/立ち絵", "立ち絵.png", "背景_01.jpg", "audio/bgm/戦闘.ogg",
    "$gameVariables.setValue(1, \"はい\")", "this.名前 = 1", "a => 'はい'",
]
LATIN = {  # the rules for the Latin alphabet must not change
    "Quit Game": True, "Hello world": True, "OK": False, "Go": False, "room_12": False,
    "UnlockFirstNewRule": False, "Driveway": True, "cellvalue": False, "#ff00aa": False,
}


def main():
    errors = []
    for t in TEXT:
        if not (is_readable_text(t) and is_ui_text(t)):
            errors.append("dovrebbe essere testo: %s (dati=%s, menu=%s)" % (t, is_readable_text(t), is_ui_text(t)))
    for t in NOT_TEXT:
        if is_readable_text(t) or is_ui_text(t):
            errors.append("dovrebbe essere escluso: %s" % t)
    for t, expected in LATIN.items():
        if is_readable_text(t) != expected:
            errors.append("alfabeto latino cambiato: %s -> %s" % (t, not expected))
    # extraction from JavaScript: entries of 1-2 characters get as far as the filter
    code = 'a={t:"設定",y:"はい",n:"いいえ",k:"시작",id:"ok",x:"Go"};b=`残り${n}マス`;'
    extracted = [_decode(s) for s in js_literals(code)]
    found = [s for s in extracted if is_ui_text(s)]
    for expected in ("設定", "はい", "いいえ", "시작", "残り", "マス"):
        if expected not in found:
            errors.append("non estratto dal JavaScript: %s (trovati: %s)" % (expected, found))
    for unwanted in ("ok", "Go"):
        if unwanted in found:
            errors.append("estratto per errore: %s" % unwanted)

    total = len(TEXT) + len(NOT_TEXT) + len(LATIN) + 8
    for e in errors:
        print("ERRORE  " + e)
    print("RISULTATO: %d/%d controlli superati" % (total - len(errors), total))
    return not errors


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main() else 1)
