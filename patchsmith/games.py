"""Schede di gioco: conoscenze su giochi specifici che non si ricavano dal motore (es. dove il gioco decide la
lingua). Ogni scheda e' un modulo con ID, NOME, radici(cartella) -> [cartelle del gioco], lingue_presenti(radice),
imposta_lingua(radice, destinazione, consenso, log) -> (True/False/None, righe)."""
from . import game_kingdom_hearts as scheda_kingdom_hearts

SCHEDE = (scheda_kingdom_hearts,)


def riconosci(cartella):
    """(scheda, [cartelle del gioco]) oppure (None, []). Piu' cartelle = piu' copie del gioco nella cartella."""
    for scheda in SCHEDE:
        try:
            radici = scheda.radici(cartella)
        except OSError:
            radici = []
        if radici:
            return scheda, radici
    return None, []


def per_id(identificativo):
    return next((s for s in SCHEDE if s.ID == identificativo), None)
