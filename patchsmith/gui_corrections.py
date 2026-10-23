"""The "Fix translations" window: the game's translations (corrections.load) with search and the lists of problems
(left untranslated, too long, not written to the game, numbers and placeholders, another language, suspicious, fixed
by hand), and below the translation of the chosen line to fix, with the length and the checks while you type.

"Save and apply to the game" saves the fixes in the translation memory (corrections.save: for Unity also the lines to
change in XUnity's cache) and closes the window; then PatchSmith writes them to the game by itself with "Reapply"
(with the preliminary checks: with the game closed). "Re-translate selected…", "Re-translate everything with Quality
(12B)…" and "Start over…" become operations of the main window (with "Stop" and the end window).

Keyboard: Ctrl+F searches; Enter (or F2) from the list goes to the translation; Ctrl+Enter confirms and moves to the
next line; Esc cancels the line's edit (from the list it closes the window); Ctrl+S saves; Alt+right/left arrow (or
Ctrl+PgDn/PgUp) change page. The list shows PAGE lines at a time: even games with tens of thousands of texts stay
fast.
"""
import tkinter as tk
from tkinter import ttk

from . import corrections, ui_language as ui, theme

PAGE = 300  # list lines shown together
CELL_WIDTH = 160  # characters shown in a cell (the whole text is below, in the chosen line)
COLUMNS = (("originale", "Original", 280), ("traduzione", "Translation", 280), ("limite", "Limit", 70),
           ("problema", "Problem", 230))


def _in_cell(text):
    t = " ⏎ ".join((text or "").split("\n"))
    return t if len(t) <= CELL_WIDTH else t[:CELL_WIDTH - 1] + "…"


def _number(n):
    return "{:,}".format(n).replace(",", ".")


def _count_label(n, one, many):
    """'1 line', '2 lines'."""
    return "%d %s" % (n, one if n == 1 else many)


class Window:
    """The (modal) window above the main one. app: gui.App; elenco: correzioni.Elenco already loaded."""

    def __init__(self, app, items, filter_=None):
        self.app = app
        self.c = app.colors
        self.items = items
        self.changes = {}  # {text: translation written here}, until saved
        self.idx = {x.text: i for i, x in enumerate(items.entries)}
        self.shown = []
        self.page = 0
        self.current = None  # the line in the translation below
        self.is_closed = False
        self._pending_search = None
        self.filter_keys = [k for k, _ in corrections.FILTERS]

        top = self.top = tk.Toplevel(app.root)
        top.withdraw()
        top.title(self._title())
        top.configure(background=self.c["sfondo"])
        top.transient(app.root)
        top.minsize(780, 560)
        frame = ttk.Frame(top, padding=10)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        bar = ttk.Frame(frame)
        bar.grid(row=1, column=0, sticky="ew", pady=(8, 6))
        ttk.Label(bar, text="Search:").pack(side="left")
        self.search = tk.StringVar()
        self.search_entry = ttk.Entry(bar, textvariable=self.search, width=34)
        self.search_entry.pack(side="left", padx=(4, 14))
        self.search.trace_add("write", lambda *_a: self._search_later())
        ttk.Label(bar, text="Show:").pack(side="left")
        self.filter_choice = ttk.Combobox(bar, state="readonly", width=34, values=self._filter_labels())
        self.filter_choice.pack(side="left", padx=(4, 0))
        self.filter_choice.current(self.filter_keys.index(filter_) if filter_ in self.filter_keys else 0)
        self.filter_choice.bind("<<ComboboxSelected>>", lambda _e: self.update_list())

        items_frame = ttk.Frame(frame)
        items_frame.grid(row=2, column=0, sticky="nsew")
        items_frame.columnconfigure(0, weight=1)
        items_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(items_frame, columns=[k for k, _, _ in COLUMNS], show="headings",
                                   selectmode="extended", height=14)
        for key, title_text, width in COLUMNS:
            self.tree.heading(key, text=title_text, anchor="w")
            self.tree.column(key, width=width, minwidth=50, stretch=key != "limite",
                               anchor="center" if key == "limite" else "w")
        # "Limit" (longest line/space in the game) only if the game makes the space of its texts known (GameMaker
        # with measured line breaks): in the other games it would be empty in every line
        if not any(x.space for x in self.items.entries):
            self.tree.configure(displaycolumns=[k for k, _, _ in COLUMNS if k != "limite"])
        self.tree.tag_configure("modificata", foreground=self.c["accento"])
        scrollbar = ttk.Scrollbar(items_frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._select())
        for hotkey in ("<Return>", "<F2>"):
            self.tree.bind(hotkey, self._go_to_translation)
        self.tree.bind("<Escape>", lambda _e: self.close())
        self.tree.bind("<Control-a>", self._all_lines)

        # pages of the list (PAGE lines each): the row is shown only with more than one page, or to say that there
        # are no lines
        pages = self.pages_row = ttk.Frame(frame)
        pages.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self.btn_prev = ttk.Button(pages, text="◀ Previous page", command=lambda: self.change_page(-1))
        self.position = ui.TextVar()
        self.position_label = ttk.Label(pages, textvariable=self.position, style="Stato.TLabel")
        self.btn_next = ttk.Button(pages, text="Next page ▶", command=lambda: self.change_page(1))

        editor = ttk.Frame(frame)
        editor.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        editor.columnconfigure(1, weight=1)
        ttk.Label(editor, text="Original:").grid(row=0, column=0, sticky="nw", padx=(0, 8))
        self.original_text = tk.Text(editor, height=3, wrap="word", font=("Segoe UI", 10), state="disabled",
                                       takefocus=False)
        theme.color_text(self.original_text, self.c)
        self.original_text.grid(row=0, column=1, sticky="ew")
        ttk.Label(editor, text="Translation:").grid(row=1, column=0, sticky="nw", padx=(0, 8), pady=(6, 0))
        self.translation_text = tk.Text(editor, height=3, wrap="word", font=("Segoe UI", 10), undo=True)
        theme.color_text(self.translation_text, self.c)
        self.translation_text.grid(row=1, column=1, sticky="ew", pady=(6, 0))
        self.translation_text.bind("<<Modified>>", lambda _e: self._edited())
        self.translation_text.bind("<Control-Return>", self._confirm_and_next)
        self.translation_text.bind("<Escape>", self._undo_line)
        self.translation_text.bind("<Tab>", lambda _e: self._next_field(_e.widget, 1))
        self.translation_text.bind("<Shift-Tab>", lambda _e: self._next_field(_e.widget, -1))
        info = ttk.Frame(editor)
        info.grid(row=2, column=1, sticky="ew", pady=(4, 0))
        self.length = ui.TextVar()
        self.length_label = ttk.Label(info, textvariable=self.length, style="Stato.TLabel")
        self.length_label.pack(side="left")
        self.warning = ui.TextVar()
        self.warning_label = ttk.Label(info, textvariable=self.warning, foreground=theme.ICONS["avviso"][1])
        self.warning_label.pack(side="left", padx=(14, 0))
        # the line's whole problem (in the list column only the beginning is visible)
        self.problem = ui.TextVar()
        ttk.Label(editor, textvariable=self.problem, style="Stato.TLabel", wraplength=860, justify="left").grid(
            row=3, column=1, sticky="w")

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        self.btn_save = ttk.Button(buttons, text="Save and apply to the game", underline=0, command=self.save)
        self.btn_save.pack(side="left")
        self.btn_retranslate = ttk.Button(buttons, text="Re-translate selected…", underline=0,
                                        command=self.retranslate_selected)
        self.btn_retranslate.pack(side="left", padx=6)
        ttk.Button(buttons, text="Close", command=self.close).pack(side="right")
        ttk.Button(buttons, text="Open the file (advanced)", command=self.open_file).pack(side="right", padx=6)

        for hotkey in ("<Control-f>", "<Control-F>"):
            top.bind(hotkey, self._go_to_search)
        for hotkey in ("<Control-s>", "<Control-S>", "<Alt-s>", "<Alt-S>"):
            top.bind(hotkey, lambda _e: self.save() or "break")
        for hotkey in ("<Alt-r>", "<Alt-R>"):
            top.bind(hotkey, lambda _e: self.retranslate_selected() or "break")
        for hotkey, direction in (("<Alt-Right>", 1), ("<Alt-Left>", -1),
                                  ("<Control-Next>", 1), ("<Control-Prior>", -1)):
            top.bind(hotkey, lambda _e, v=direction: self.change_page(v) or "break")
        self.search_entry.bind("<Return>", self._go_to_list)
        self.search_entry.bind("<Down>", self._go_to_list)
        self.search_entry.bind("<Escape>", self._esc_search)
        top.protocol("WM_DELETE_WINDOW", self.close)

        self.update_list()
        self._show_translation(None)
        top.update_idletasks()
        root = app.root
        x = root.winfo_rootx() + max(0, (root.winfo_width() - top.winfo_reqwidth()) // 2)
        y = root.winfo_rooty() + max(0, (root.winfo_height() - top.winfo_reqheight()) // 4)
        top.geometry("+%d+%d" % (max(0, x), max(0, y)))
        top.deiconify()
        theme._title_bar(top, self.c.get("nome") == "scuro")
        self._resume()
        if filter_ and self.shown:
            self._choose_line(0)
            self.tree.focus_set()
        else:
            self.search_entry.focus_set()

    # --- list --------------------------------------------------------------------------------------------

    def _title(self):
        return "Fix translations - %s%s" % (self.items.name, " *" if self.changes else "")

    def _filter_labels(self):
        occurrences = self.items.counts()
        return ["%s (%s)" % (label, _number(occurrences.get(k, 0))) for k, label in corrections.FILTERS]

    @property
    def filter_(self):
        i = self.filter_choice.current()
        return self.filter_keys[i] if 0 <= i < len(self.filter_keys) else "tutte"

    def choose_filter(self, filter_):
        self.filter_choice.current(self.filter_keys.index(filter_))
        self.update_list()

    def _search_later(self):
        """The search starts as soon as you stop typing (not at every letter)."""
        if self._pending_search is not None:
            self.top.after_cancel(self._pending_search)
        self._pending_search = self.top.after(250, self.update_list)

    def update_list(self):
        self._pending_search = None
        self.shown = self.items.apply_filter(self.filter_, self.search.get())
        self.page = 0
        self._show_page()

    def _pages(self):
        return max(1, (len(self.shown) + PAGE - 1) // PAGE)

    def change_page(self, direction):
        new_value = min(max(0, self.page + direction), self._pages() - 1)
        if new_value != self.page:
            self.page = new_value
            self._show_page()
            if self.shown:
                self._choose_line(0)

    def _show_page(self):
        """The page's lines. The line you are fixing, if still there (e.g. after a search), stays selected."""
        current = self.current
        self.tree.delete(*self.tree.get_children())
        start = self.page * PAGE
        for x in self.shown[start:start + PAGE]:
            self.tree.insert("", "end", iid=str(self.idx[x.text]), values=self._values(x),
                               tags=("modificata",) if x.text in self.changes else ())
        multi_page = self._pages() > 1
        for w in (self.btn_prev, self.position_label, self.btn_next):
            w.pack_forget()
        if multi_page:
            self.position.set("Page %d of %d (lines %s–%s of %s)" % (
                self.page + 1, self._pages(), _number(start + 1),
                _number(min(start + PAGE, len(self.shown))), _number(len(self.shown))))
            self.btn_prev.pack(side="left")
            self.position_label.pack(side="left", padx=10)
            self.btn_next.pack(side="left")
        elif not self.shown:
            self.position.set("No rows" + (" with this search" if self.search.get().strip() else ""))
            self.position_label.pack(side="left")
        if multi_page or not self.shown:
            self.pages_row.grid()
        else:
            self.pages_row.grid_remove()
        self.btn_prev.state(["!disabled"] if self.page > 0 else ["disabled"])
        self.btn_next.state(["!disabled"] if self.page < self._pages() - 1 else ["disabled"])
        iid = str(self.idx[current.text]) if current is not None else None
        if iid is not None and self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)
        else:
            self._show_translation(None)

    def translation_of(self, x):
        return self.changes.get(x.text, x.translation)

    def _values(self, x):
        translated = self.translation_of(x)
        measured = corrections.measure(x, translated) if translated else None
        limit = "%d/%d" % measured if measured and measured[0] is not None else ""
        problem = x.reasons()
        if x.text in self.changes:
            problem = "; ".join(["modified, to be saved"] + self._warnings(x))
        return _in_cell(x.text), _in_cell(translated), limit, problem

    def _warnings(self, x):
        return corrections.warnings(x.text, self.translation_of(x), self.items.protected)

    def selected_lines(self):
        return [self.items.entries[int(i)] for i in self.tree.selection()]

    def _choose_line(self, position):
        children = self.tree.get_children()
        if not children:
            return
        iid = children[max(0, min(position, len(children) - 1))]
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        self._select()

    def _select(self):
        choices = self.selected_lines()
        if len(choices) == 1 and choices[0] is self.current:
            return  # the same line (e.g. the selection event arriving later): what you are typing stays
        self._show_translation(choices[0] if len(choices) == 1 else None, len(choices))

    def _all_lines(self, _event=None):
        self.tree.selection_set(self.tree.get_children())
        return "break"

    # --- translation of the chosen line -------------------------------------------------------------------

    def _set_text(self, widget, text, active=True):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.edit_reset()
        widget.edit_modified(False)
        widget.configure(state="normal" if active else "disabled")

    def _show_translation(self, x, choices=0):
        self.current = None
        if x is None:
            self._set_text(self.original_text, "", active=False)
            self._set_text(self.translation_text, "", active=False)
            self.length.set("You've selected %d lines: you can re-translate them using \"Re-translate selected…\"."
                            % choices if choices > 1 else "Choose a row from the list.")
            self.warning.set("")
            self.problem.set("")
            return
        self._set_text(self.original_text, x.text, active=False)
        self._set_text(self.translation_text, self.translation_of(x))
        self.current = x
        self.problem.set("Problem: %s" % x.reasons() if x.reasons() else "")
        self._update_info()

    def _edited(self):
        """The translation changed (also by pasting): the change is kept in memory until you save."""
        if not self.translation_text.edit_modified():
            return
        self.translation_text.edit_modified(False)
        x = self.current
        if x is None:
            return
        new = self.translation_text.get("1.0", "end-1c")
        if new == x.translation:
            self.changes.pop(x.text, None)
        else:
            self.changes[x.text] = new
        iid = str(self.idx[x.text])
        if self.tree.exists(iid):
            self.tree.item(iid, values=self._values(x), tags=("modificata",) if x.text in self.changes else ())
        self.top.title(self._title())
        self._update_info()

    def _update_info(self):
        x = self.current
        if x is None:
            return
        translated = self.translation_of(x)
        measured = corrections.measure(x, translated) if translated.strip() else None
        if measured and measured[0] is not None:
            line_len, width = measured
            self.length.set("Longest line: %d characters (about %d fit in the game)%s" % (
                line_len, width, ": too long" if corrections.too_long(line_len, width) else ""))
        elif measured:
            self.length.set("It doesn't have the same format as the original (different paragraphs or line breaks)")
        else:
            self.length.set("Length: %d characters (original: %d)" % (len(translated), len(x.text)))
        warnings = self._warnings(x) if translated.strip() or x.text in self.changes else []
        self.warning.set(("Warning: " + "; ".join(warnings)) if warnings else "")

    def _go_to_translation(self, _event=None):
        if self.current is not None:
            self.translation_text.focus_set()
            self.translation_text.mark_set("insert", "end-1c")
        return "break"

    def _confirm_and_next(self, _event=None):
        """Ctrl+Enter: the change stays (it is saved with the others) and moves to the next line."""
        children = self.tree.get_children()
        choice = self.tree.selection()
        position = children.index(choice[0]) + 1 if choice and choice[0] in children else 0
        if position >= len(children) and self.page < self._pages() - 1:
            self.change_page(1)
        else:
            self._choose_line(position)
        self._go_to_translation()
        return "break"

    def _undo_line(self, _event=None):
        """Esc in the translation: the previous (saved) one comes back and focus returns to the list."""
        x = self.current
        if x is not None and x.text in self.changes:
            del self.changes[x.text]
            iid = str(self.idx[x.text])
            if self.tree.exists(iid):
                self.tree.item(iid, values=self._values(x), tags=())
            self._show_translation(x)
            self.top.title(self._title())
        self.tree.focus_set()
        return "break"

    @staticmethod
    def _next_field(widget, direction):
        (widget.tk_focusNext() if direction > 0 else widget.tk_focusPrev()).focus_set()
        return "break"

    def _go_to_search(self, _event=None):
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")
        return "break"

    def _go_to_list(self, _event=None):
        if self._pending_search is not None:
            self.top.after_cancel(self._pending_search)
            self.update_list()
        self.tree.focus_set()
        self._choose_line(0)
        return "break"

    def _esc_search(self, _event=None):
        if self.search.get():
            self.search.set("")
        else:
            self.close()
        return "break"

    # --- question dialogs (modal, above this one) ---------------------------------------------------------

    def _resume(self):
        """After a question dialog (which releases its grab when it closes) this one is again the only active one."""
        if self.is_closed:
            return
        try:
            self.top.grab_set()
            self.top.lift()
        except tk.TclError:
            pass

    def _choose(self, title_text, text, buttons, kind="domanda"):
        try:
            return theme.choose(self.top, self.c, title_text, text, buttons, kind)
        finally:
            self._resume()

    def _warn(self, title_text, text):
        try:
            theme.warn(self.top, self.c, title_text, text)
        finally:
            self._resume()

    # --- actions ------------------------------------------------------------------------------------------

    def _close_window(self):
        self.is_closed = True
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        self.top.destroy()

    def close(self):
        if self.changes:
            choice = self._choose("Save the changes?",
                                  "You have %s. Save the corrections and write them into the game?"
                                  % _count_label(len(self.changes), "unsaved correction", "unsaved changes"),
                                  [("Save", "salva"), ("Don't save", "esci"), ("Cancel", None)])
            if choice == "salva":
                return self.save()
            if choice != "esci":
                return False
        self._close_window()
        return True

    def _to_save(self):
        """The fixes ready to save (None if one is empty or if you chose to go back to fixing)."""
        blanks = [t for t, v in self.changes.items() if not v.strip()]
        if blanks:
            self._warn("Empty translation", 'The translation for "%s" is empty: write it, or press Esc to keep the '
                                           'previous translation. To re-translate using PatchSmith, use "Re-translate '
                                           'selected…".'
                         % _in_cell(blanks[0])[:80])
            self._go_to(blanks[0])
            return None
        doubts = [(t, corrections.warnings(t, v, self.items.protected)) for t, v in self.changes.items()]
        doubts = [(t, a) for t, a in doubts if a]
        if doubts:
            lines = "\n".join("- \"%s\": %s" % (_in_cell(t)[:50], "; ".join(a)) for t, a in doubts[:5])
            choice = self._choose("Check the corrections", "Something looks wrong in some fixes:\n%s%s\n\nThe game "
                                                             "might display them incorrectly (for example, a "
                                                             "placeholder like {0} must be copied exactly as it is). "
                                                             "Save anyway?" % (lines, "\n…" if len(doubts) > 5 else ""),
                                  [("Go back to editing", None), ("Save anyway", "salva")], "avviso")
            if choice != "salva":
                self._go_to(doubts[0][0])
                return None
        return dict(self.changes)

    def _go_to(self, text):
        """Shows a text's line (with all the lines, if the filter or the search hide it)."""
        x = self.items.entries[self.idx[text]]
        if x not in self.shown:
            self.search.set("")
            if self._pending_search is not None:  # (the empty search is redone right below, immediately)
                self.top.after_cancel(self._pending_search)
            self.filter_choice.current(0)
            self.update_list()
        position = self.shown.index(x)
        self.page = position // PAGE
        self._show_page()
        self._choose_line(position % PAGE)
        self._go_to_translation()

    def _save_changes(self, changes):
        """Saves (correzioni.salva); False if it fails (the window stays open, with the changes)."""
        seen = {t: self.items.entries[self.idx[t]].cache for t in changes} if self.items.unity else None
        try:
            corrections.save(self.items.profile_path, changes, seen, test=self.items.test,
                             log=self.app._log)
        except (OSError, RuntimeError, ValueError) as e:
            self._warn("Unsaved changes", "I wasn't able to save the changes: %s\n\nThe fixes are still there: "
                                                 "try again in a moment (for example, after closing the game)." % e)
            return False
        self.changes = {}
        return True

    def save(self):
        """"Save and apply to the game": the fixes in the translation memory, then (window closed) "Reapply" by
        itself."""
        if not self.changes:
            self._warn("Nothing to save", "You haven't yet corrected any translations: choose a line, write the "
                                            'translation below, and then press "Save and apply to the game".')
            return False
        changes = self._to_save()
        if changes is None or not self._save_changes(changes):
            return False
        self._close_window()
        self.app._after_fixes(len(changes))
        return True

    def _save_first(self):
        """Before an operation of the main window: the unsaved fixes are saved (or it is cancelled)."""
        if not self.changes:
            return True
        choice = self._choose("Unsaved changes", "You have %s: save the corrections before continuing?"
                              % _count_label(len(self.changes), "unsaved correction", "unsaved changes"),
                              [("Save and continue", "salva"), ("Cancel", None)])
        if choice != "salva":
            return False
        changes = self._to_save()
        return changes is not None and self._save_changes(changes)

    def _current_translator(self):
        from .gui import TRANSLATORS
        name = self.app._backend()
        return name, TRANSLATORS.get(name, "the test translator" if name == "finto" else name)

    def retranslate_selected(self):
        from . import ollama
        lines = self.selected_lines()
        if not lines:
            self._warn("No row selected", "Choose the lines to re-translate in the list (with Shift or Ctrl you "
                                              "can choose more than one; Ctrl+A selects all those on the page).")
            return False
        name, label = self._current_translator()
        buttons = []
        if name != "finto":
            try:
                is_ready = ollama.state() == "pronto"
            except OSError:
                is_ready = False
            if is_ready:
                from .gui import LOCAL_MODELS
                buttons += [("Local translator: %s" % LOCAL_MODELS[k], k) for k in ("12b", "4b")
                             if ollama.model_present(k)]
            if name == "locale" and not buttons:
                self._warn("Local translator to install", "To re-translate, you'll need a translator: the local "
                                                              "translator hasn't been installed yet. Install it using "
                                                              '"Install…" in the main window, or choose a different '
                                                              "translator there.")
                return False
        if name != "locale":
            buttons.append(("With %s" % label, "attuale"))
        text = ("I'm re-translating %s with new requests, so the translation can come out different from the current "
                "one (even for the lines you fixed by hand: you chose them). If the new translation doesn't pass the "
                "checks, the previous one stays. Then I write it into the game."
                 % _count_label(len(lines), "line", "lines"))
        if name in ("google", "deepl", "mymemory") and not any(k in ("12b", "4b") for _, k in buttons):
            text += ("\n\nWith %s the translation usually comes back the same: for a different one you need the local "
                     "translator."
                      % label)
        choice = self._choose("Re-translate the selected lines", text, buttons + [("Cancel", None)])
        if choice is None or not self._save_first():
            return False
        self._close_window()
        self.app._retranslate_fixes([x.text for x in lines], None if choice == "attuale" else choice)
        return True

    def retranslate_all(self):
        from . import backend, ollama
        from .limiter import describe_duration
        name, _ = self._current_translator()
        try:
            present_value = name == "finto" or (ollama.state() == "pronto" and ollama.model_present("12b"))
        except OSError:
            present_value = False
        if not present_value:
            choice = self._choose("Quality model to install", 'To re-translate using the "Quality" model (12B), '
                                                                   "you need to install it first: PatchSmith will tell "
                                                                   "you how much data needs to be downloaded and will "
                                                                   "only start after you confirm. Once the "
                                                                   "installation is complete, it will automatically "
                                                                   "re-translate. From that point on, the selected "
                                                                   "translator will be the local translator using the "
                                                                   '"Quality" model (you can change this in the main '
                                                                   "window).",
                                  [("Install the Quality model", "installa"), ("Cancel", None)])
            if choice != "installa" or not self._save_first():
                return False
            self._close_window()
            self.app._install_12b_and_retranslate()
            return True
        texts, by_hand = corrections.texts_to_retranslate(self.items)
        if not texts:
            self._warn("Nothing to re-translate", "All translations of this game were fixed by hand: they stay as they "
                                               "are.")
            return False
        estimate = backend.Local("12b", log=None).estimate([len(t) for t in texts])
        text = ("Retranslating with the Quality model (12B) %d texts of this game: the machine translations and the "
                "texts left untranslated. " % len(texts))
        if self.items.known_machine:
            text += ("Your manual corrections (%d) stay as they are. " % by_hand) if by_hand else ""
            if self.items.unannotated:
                text += ("%s from an old version of PatchSmith, which didn't remember which ones you fixed by hand: "
                         "I'll re-translate %s too. " % (
                              _count_label(self.items.unannotated, "translation is", "translations are"),
                              "quella" if self.items.unannotated == 1 else "quelle"))
        else:
            text += ("This game has been translated using an older version of PatchSmith, which doesn't remember which "
                     "translations you corrected manually. I will also re-translate those. ")
        text += ("If a new translation fails, the previous one stays. Estimated time: %s (the game must stay closed). "
                 "Then I write them into the game." % describe_duration(estimate))
        choice = self._choose("Re-translate everything with Quality (12B)", text,
                              [("Cancel", None), ("Re-translate", "si")])
        if choice != "si" or not self._save_first():
            return False
        self._close_window()
        self.app._retranslate_fixes(None, "12b")
        return True

    def restart(self):
        by_hand = sum(1 for x in self.items.entries if x.by_hand)
        translated_count = sum(1 for x in self.items.entries if x.memory is not None or x.cache is not None)
        choice = self._choose(
            "Start over from scratch?",
            'The game goes back to how it was (as with "Restore originals"), all its translations (%d%s) are set aside '
            "in a backup copy, in PatchSmith's memory\\copies folder, and the game is translated again from scratch "
            "with the chosen translator. Only needed if the translations came out badly across the board: for a few "
            "phrases, just fix them here.\n\nThe game must be closed." % (
                translated_count, ", including %d corrected by hand" % by_hand if by_hand else ""),
            [("Cancel", None), ("Start over", "si")], "avviso")
        if choice != "si":
            return False
        self._close_window()
        self.app._translate(from_scratch=True)
        return True

    def open_file(self):
        if not self._save_first():
            return False
        self._close_window()
        self.app._open_translations_file()
        return True
