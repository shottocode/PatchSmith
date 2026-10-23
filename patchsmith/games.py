"""Game sheets: knowledge about specific games that cannot be derived from the engine (e.g. where the game decides
the language). Each game sheet is a module with ID, NAME, roots(folder) -> [game folders], present_languages(root),
set_language(root, dest_lang, consent, log) -> (True/False/None, lines)."""
from . import game_kingdom_hearts as kingdom_hearts_sheet

SHEETS = (kingdom_hearts_sheet,)


def identify(folder):
    """(sheet, [game folders]) or (None, []). More than one folder = more than one copy of the game in the folder."""
    for sheet in SHEETS:
        try:
            roots = sheet.roots(folder)
        except OSError:
            roots = []
        if roots:
            return sheet, roots
    return None, []


def by_id(identifier):
    return next((s for s in SHEETS if s.ID == identifier), None)
