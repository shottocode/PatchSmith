"""Test del riconoscimento dei testi in giapponese, cinese e coreano (e che nulla cambi per il resto).

Uso: python -I test/prova_cjk.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith.adapter_webui import e_testo_ui, letterali_js, _decodifica  # noqa: E402
from patchsmith.testo import e_testo_leggibile  # noqa: E402

TESTO = [  # devono essere riconosciuti come testo (dati e menu)
    "設定", "はい", "いいえ", "戻る", "はじめる", "つづきから",
    "霧に包まれた屋敷の扉が、ゆっくりと開いた。誰があなたを呼んだのだろうか。この屋敷には、まだ誰も知らない秘密が眠っている。",
    "3つの部屋をクリアすると扉が開く。", "残り5マス", "はい/いいえ", "ＨＰが回復した！",
    "开始游戏", "设置", "继续", "数字在每一行、每一列和每一个宫中只能出现一次，请仔细观察并找出唯一的答案。这是第2关。",
    "設定を保存しました", "시작", "설정", "저택에 오신 것을 환영합니다.", "남은 칸: 3",
]
NON_TESTO = [  # devono restare esclusi
    "img/背景", "img/pictures/立ち絵", "立ち絵.png", "背景_01.jpg", "audio/bgm/戦闘.ogg",
    "$gameVariables.setValue(1, \"はい\")", "this.名前 = 1", "a => 'はい'",
]
LATINO = {  # le regole per l'alfabeto latino non devono cambiare
    "Quit Game": True, "Hello world": True, "OK": False, "Go": False, "room_12": False,
    "UnlockFirstNewRule": False, "Driveway": True, "cellvalue": False, "#ff00aa": False,
}


def main():
    errori = []
    for t in TESTO:
        if not (e_testo_leggibile(t) and e_testo_ui(t)):
            errori.append("dovrebbe essere testo: %s (dati=%s, menu=%s)" % (t, e_testo_leggibile(t), e_testo_ui(t)))
    for t in NON_TESTO:
        if e_testo_leggibile(t) or e_testo_ui(t):
            errori.append("dovrebbe essere escluso: %s" % t)
    for t, atteso in LATINO.items():
        if e_testo_leggibile(t) != atteso:
            errori.append("alfabeto latino cambiato: %s -> %s" % (t, not atteso))
    # estrazione dal JavaScript: le voci di 1-2 caratteri arrivano fino al filtro
    codice = 'a={t:"設定",y:"はい",n:"いいえ",k:"시작",id:"ok",x:"Go"};b=`残り${n}マス`;'
    estratti = [_decodifica(s) for s in letterali_js(codice)]
    trovati = [s for s in estratti if e_testo_ui(s)]
    for atteso in ("設定", "はい", "いいえ", "시작", "残り", "マス"):
        if atteso not in trovati:
            errori.append("non estratto dal JavaScript: %s (trovati: %s)" % (atteso, trovati))
    for indesiderato in ("ok", "Go"):
        if indesiderato in trovati:
            errori.append("estratto per errore: %s" % indesiderato)

    totale = len(TESTO) + len(NON_TESTO) + len(LATINO) + 8
    for e in errori:
        print("ERRORE  " + e)
    print("RISULTATO: %d/%d controlli superati" % (totale - len(errori), totale))
    return not errori


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main() else 1)
