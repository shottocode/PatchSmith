"""Rate limiter for the requests to the online translators, with the blocks remembered on disk.

Google does not publish the limits of the free translator (and today it rejects some automated access entirely):
the values below are cautious margins chosen in advance, not measured limits. The rule that matters most:
at the first block we stop at once and remember until when not to retry, because insisting during a block can
make it longer.

The state is in services.json among the user's data (project.data_folder: normally next to the program;
atomic write, with a lock so that the interface, the command line and the translator started by Unity games
(xunity_service) count the same requests and respect the same pauses):
{"versione": 1, "google": {"richieste": [[time, characters], ...], "ultimo_ok": time, "ok_totali": n,
                           "blocco": {"dal", "fino", "livello", "tipo", "http", "disattivato"}}}
"""
import datetime
import json
import os
import random
import time

from . import interruption

MINUTE, HOUR, DAY = 60, 3600, 86400

# per service: minimum pause between the start of two requests (+ random variation) and windows
# (duration in seconds, maximum requests, maximum characters)
RULES = {
    "google": {"pausa": 4.0, "variazione": 2.0,
               "finestre": ((MINUTE, 8, 32_000), (10 * MINUTE, 30, 120_000), (HOUR, 60, 240_000),
                            (DAY, 300, 1_000_000))},
    "deepl": {"pausa": 0.5, "variazione": 0.0, "finestre": ()},
    "mymemory": {"pausa": 1.0, "variazione": 0.5, "finestre": ((MINUTE, 50, None),)},
}
# waits after a block: "a freddo" (cold) = the very first request was rejected (not a limit exceeded, access is
# refused: no point retrying soon); "dopo traffico" (after traffic) = after successful requests (a rate limit)
WAITS = {"a_freddo": (DAY, 3 * DAY, 7 * DAY), "dopo_traffico": (HOUR, 6 * HOUR, DAY, 3 * DAY)}
MAX_FUTURE = 8 * DAY  # a farther "fino" (wrong clock, damaged file) is reduced
FORGET_AFTER = 7 * DAY  # a week without blocks: start again from the first level
OK_TO_RESET = 10
NAMES = {"google": "Google", "deepl": "DeepL", "mymemory": "MyMemory"}
LOCAL_ADVICE = ("To continue right away, use the local translator (recommended: no limits and no blocks) or another "
                "translator.")


class Paused(Exception):
    """The service is paused because of a previous block: no requests until it is over."""


def state_path():
    from . import project
    return project.data_folder("services.json")


def when(instant):
    """'alle 18:30 di domani', 'alle 18:30 del 22/10' (for the messages)."""
    moment = datetime.datetime.fromtimestamp(instant)
    today = datetime.date.today()
    if moment.date() == today:
        return "at %s" % moment.strftime("%H:%M")
    if moment.date() == today + datetime.timedelta(days=1):
        return "at %s tomorrow" % moment.strftime("%H:%M")
    return "at %s on %s" % (moment.strftime("%H:%M"), moment.strftime("%d/%m"))


class _Lock:
    """File lock (os.O_EXCL): one abandoned for more than 60 s is considered free."""

    def __init__(self, path):
        self.path = path + ".lock"
        self.acquired = False

    def __enter__(self):
        limit = time.monotonic() + 5
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                self.acquired = True
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > 60:
                        os.remove(self.path)
                        continue
                except OSError:
                    pass
                if time.monotonic() > limit:
                    return self  # better to go on without a lock than to get stuck
                time.sleep(0.05)
            except OSError:
                return self  # folder not writable: work without a lock

    def __exit__(self, *_):
        if self.acquired:
            try:
                os.remove(self.path)
            except OSError:
                pass


class Limiter:
    def __init__(self, service, log=print, path=None, current_time=time.time, sleep=None, rng=random.random):
        self._corrected = False
        self.service = service
        self.rules = RULES.get(service, {"pausa": 1.0, "variazione": 0.0, "finestre": ()})
        self.log = log
        self.path = path or state_path()
        self.current_time = current_time
        self.sleep = sleep or interruption.wait_for
        self.rng = rng
        self.session_ok = 0
        self._in_memory = None  # state kept in memory if the file cannot be written
        self._warned = False

    # --- state file -------------------------------------------------------------------------------

    def _read(self):
        if self._in_memory is not None:
            return self._in_memory
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {"versione": 1}
        except FileNotFoundError:
            return {"versione": 1}
        except (OSError, ValueError):
            self._warning("  ! Translator status unreadable (%s): starting from scratch." % self.path)
            return {"versione": 1}

    def _write(self, data):
        if self._in_memory is not None:
            self._in_memory = data
            return
        text = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
        temp_file = self.path + ".tmp"
        for attempt in range(5):
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(temp_file, "wb") as f:
                    f.write(text)
                os.replace(temp_file, self.path)
                return
            except OSError:
                time.sleep(0.1)  # antivirus or indexer holding the file: retry
        self._warning("  ! I can't save the translators' state (%s): keeping it in memory only." % self.path)
        self._in_memory = data

    def _warning(self, text):
        if not self._warned and self.log:
            self.log(text)
        self._warned = True

    def _own_state(self, data, now):
        s = data.setdefault(self.service, {})
        if not isinstance(s, dict):
            s = data[self.service] = {}
        s["richieste"] = [r for r in s.get("richieste", []) if isinstance(r, list) and len(r) == 2
                          and 0 <= now - r[0] < DAY]
        block = s.get("blocco")
        if isinstance(block, dict):
            if block.get("dal", now) > now or (block.get("fino") and block["fino"] - now > MAX_FUTURE):
                # recorded with the clock ahead (or damaged file): restart from now, only once
                duration = max(0, block.get("fino", now) - block.get("dal", now))
                block["dal"] = now
                block["fino"] = now + min(duration, MAX_FUTURE)
                self._corrected = True
            if not block.get("disattivato") and now - block.get("fino", now) > FORGET_AFTER:
                s.pop("blocco")  # a week without blocks after the end of the last one: start over
        else:
            s.pop("blocco", None)
        return s

    # --- queries ----------------------------------------------------------------------------------

    def _corrected_state(self):
        """The service's state; if there were dates to correct, saves them at once (so the correction applies once)."""
        self._corrected = False
        data = self._read()
        now = self.current_time()
        s = self._own_state(data, now)
        if self._corrected:
            with _Lock(self.path):
                self._write(data)
        return s, now

    def current_block(self):
        s, now = self._corrected_state()
        block = s.get("blocco")
        return block if block and (block.get("disattivato") or now < block.get("fino", 0)) else None

    def pause(self):
        """None if the service can be used, otherwise the message saying until when it is paused."""
        s, now = self._corrected_state()
        block = s.get("blocco")
        if not block:
            return None
        if block.get("disattivato"):
            return ("%s is disabled after repeated rejections (last %s): use the local translator (no limits and no "
                    "blocks) or another translator, or reactivate it with the 'Reactivate' button next to the "
                    "translator."
                    % (NAMES.get(self.service, self.service), when(block.get("dal", now))))
        if now < block.get("fino", 0) and block.get("tipo") == "quota":
            return "%s: %s, resumes %s." % (NAMES.get(self.service, self.service),
                                                block.get("dettaglio") or "quota reached", when(block["fino"]))
        if now < block.get("fino", 0):
            return ("%s is paused until %s (block received %s): PatchSmith won't send requests before then, so as not "
                    "to extend it." % (NAMES.get(self.service, self.service), when(block["fino"]),
                                     when(block.get("dal", now))))
        return None

    def short_status(self):
        """'in pausa fino alle 05:58 di domani', 'disattivato' or '' (for the interface)."""
        try:
            s, now = self._corrected_state()
            block = s.get("blocco")
        except (OSError, ValueError, TypeError, AttributeError):
            return ""
        if not block:
            return ""
        if block.get("disattivato"):
            return "disabled after repeated rejections"
        if now < block.get("fino", 0):
            return "paused, resumes %s" % when(block["fino"])
        return ""

    def _wait(self, s, chars, now):
        requests = s["richieste"]
        wait = 0.0
        if requests:
            wait = requests[-1][0] + self.rules["pausa"] + s.get("variazione", 0.0) - now
        for duration, max_n, max_c in self.rules["finestre"]:
            inside = [r for r in requests if now - r[0] < duration]
            while inside and (len(inside) + 1 > max_n or
                              (max_c is not None and sum(c for _, c in inside) + chars > max_c)):
                wait = max(wait, inside.pop(0)[0] + duration - now)
        return max(0.0, wait)

    # --- actions ----------------------------------------------------------------------------------

    def before_request(self, chars):
        """Waits the time needed and records the request (before sending it). Raises Paused if the
        service is paused because of a block."""
        while True:
            message = self.pause()
            if message:
                raise Paused(message)
            with _Lock(self.path):
                data = self._read()
                now = self.current_time()
                s = self._own_state(data, now)
                wait = self._wait(s, chars, now)
                if wait <= 0:
                    s["richieste"].append([now, chars])
                    s["variazione"] = round(self.rng() * self.rules["variazione"], 3)
                    self._write(data)
                    return
            if wait > 15 and self.log:
                self.log("    Pausing for %s to stay within the safe margins of %s: resuming %s. You can stop: the "
                         "work done stays saved." % (_duration(wait), NAMES.get(self.service, self.service),
                                                          when(self.current_time() + wait)))
            self.sleep(wait)

    def ok(self):
        with _Lock(self.path):
            data = self._read()
            now = self.current_time()
            s = self._own_state(data, now)
            s["ultimo_ok"] = now
            s["ok_totali"] = s.get("ok_totali", 0) + 1
            if s["ok_totali"] >= OK_TO_RESET and s.get("blocco") and now >= s["blocco"].get("fino", 0):
                s.pop("blocco")  # reliable again: the next blocks start again from the first level
            self.session_ok += 1
            self._write(data)

    def blocked(self, http=None, detail=None, now=None, until=None, extra=None):
        """Records a block and returns the message for the user. 'fino': pause until that exact time (e.g.
        daily quota used up), without levels."""
        with _Lock(self.path):
            data = self._read()
            now = self.current_time() if now is None else now
            s = self._own_state(data, now)
            if until is not None:
                s["blocco"] = dict({"dal": now, "fino": min(until, now + MAX_FUTURE), "livello": 0,
                                    "tipo": "quota", "http": http, "dettaglio": detail, "disattivato": False},
                                   **(extra or {}))
                self._write(data)
                return "%s paused until %s (%s)." % (NAMES.get(self.service, self.service), when(until), detail)
            most_recent = s.get("ultimo_ok") and now - s["ultimo_ok"] < DAY
            successes_24h = s.get("ok_totali", 0)
            kind = "dopo_traffico" if (self.session_ok or most_recent) else "a_freddo"
            previous = s.get("blocco") or {}
            level = previous.get("livello", 0) + 1 if previous.get("tipo") == kind else 1
            expectations = WAITS[kind]
            disabled = kind == "a_freddo" and level > len(expectations)
            until = now + expectations[min(level, len(expectations)) - 1]
            s["blocco"] = {"dal": now, "fino": until, "livello": level, "tipo": kind, "http": http,
                           "dettaglio": detail, "disattivato": disabled}
            s["ok_totali"] = 0
            self._write(data)
        name = NAMES.get(self.service, self.service)
        if disabled:
            return ("%s rejected the requests again: disabling it until you reactivate it ('Reactivate' button next to "
                    "the translator). %s" % (name, LOCAL_ADVICE))
        if kind == "a_freddo":
            return ("%s rejected the first request: it's not an exceeded limit, %s doesn't accept automatic requests "
                    "from this program (this happens to many). Retrying right away won't help and could extend the "
                    "block: PatchSmith won't retry before %s. The translations done stay saved. To continue right "
                    "away, use the local translator (recommended: no limits and no blocks), or DeepL (free with your "
                    "own key, 500,000 characters per month) or MyMemory (no account needed)."
                    % (name, name, when(until)))
        after = ("after %d successful requests" % self.session_ok if self.session_ok else
                "after %d successful requests in the last few hours" % successes_24h if successes_24h else
                "after the successful requests of the last few hours")
        return ("%s has paused requests %s: PatchSmith will resume %s, not before. The work done is saved. %s"
                % (name, after, when(until), LOCAL_ADVICE))

    def reset(self):
        with _Lock(self.path):
            data = self._read()
            s = self._own_state(data, self.current_time())
            s.pop("blocco", None)
            s["ok_totali"] = 0
            self._write(data)

    def estimate(self, chars_per_request, response_duration=1.5):
        """Estimated seconds to send these requests while respecting the rules (starting from the current state)."""
        data = self._read()
        clock = [self.current_time()]
        s = self._own_state(json.loads(json.dumps(data)), clock[0])
        s["variazione"] = self.rules["variazione"] / 2
        start = clock[0]
        for chars in chars_per_request:
            clock[0] += self._wait(s, chars, clock[0])
            s["richieste"].append([clock[0], chars])
            clock[0] += response_duration
        return clock[0] - start


def _duration(seconds):
    if seconds < 90:
        return "%d seconds" % round(seconds)
    if seconds < 90 * 60:
        return "%d minutes" % round(seconds / 60)
    return "%.1f hours" % (seconds / 3600)


def describe_duration(seconds):
    return _duration(seconds)
