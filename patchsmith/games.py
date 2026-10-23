"""Game sheets: knowledge about specific games that cannot be derived from the engine (e.g. where the game decides
the language). Each game sheet is a module with ID, NOME, radici(cartella) -> [game folders], lingue_presenti(radice),
imposta_lingua(radice, destinazione, consenso, log) -> (True/False/None, righe)."""
from . import game_kingdom_hearts as scheda_kingdom_hearts

SCHEDE = (scheda_kingdom_hearts,)


def riconosci(cartella):
    """(scheda, [game folders]) or (None, []). More than one folder = more than one copy of the game in the folder."""
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
