"""Simulated Ollama for the tests: answers on 127.0.0.1 at the port of OLLAMA_HOST.

Usage (ollama.Server starts it in the tests): python -I fake_ollama.py serve
- /api/version, /api/tags, /api/ps, /api/generate, /api/chat (translates by prepending "IT:"), /api/pull (NDJSON);
- with PATCHSMITH_FAKE_DIE=1 it exits at once (a server that does not start);
- writes the environment it received to PATCHSMITH_FAKE_ENVIRONMENT (to check the variables passed);
- with PATCHSMITH_FAKE_SLOW=seconds it waits before answering (slow start);
- with PATCHSMITH_FAKE_WRONG_SEEDS=42,7,11 it answers with the original (untranslated) to requests with those seeds;
- with PATCHSMITH_FAKE_REQUESTS=<file> it appends to the file one JSON line per /api/chat: {text, seed,
  temperature, model} (the log of requests, for the tests).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _reply(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/version":
            self._reply({"version": "0.40.4"})
        elif self.path == "/api/tags":
            self._reply({"models": [{"name": "translategemma:4b-it-q4_K_M", "digest": "c49d986b0764aa"}]})
        elif self.path == "/api/ps":
            self._reply({"models": [{"name": "translategemma:4b-it-q4_K_M", "size": 100, "size_vram": 100}]})
        else:
            self._reply({"error": "not found"}, 404)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/chat":
            text = data["messages"][0]["content"].split("\n\n\n", 1)[1]
            options = data.get("options") or {}
            if os.environ.get("PATCHSMITH_FAKE_REQUESTS"):
                with open(os.environ["PATCHSMITH_FAKE_REQUESTS"], "a", encoding="utf-8") as f:
                    f.write(json.dumps({"text": text, "seed": options.get("seed"),
                                        "temperature": options.get("temperature"), "model": data.get("model")}) + "\n")
            wrong_seeds = {int(s) for s in os.environ.get("PATCHSMITH_FAKE_WRONG_SEEDS", "").split(",")
                           if s.strip()}
            response = text if options.get("seed") in wrong_seeds else "IT: " + text
            self._reply({"message": {"content": response}, "done_reason": "stop", "eval_count": 10,
                            "eval_duration": 100_000_000, "prompt_eval_count": 50})
        elif self.path == "/api/pull":
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for line in ({"status": "pulling manifest"}, {"status": "x", "digest": "a", "total": 100, "completed": 50},
                         {"status": "x", "digest": "a", "total": 100, "completed": 100}, {"status": "success"}):
                self.wfile.write((json.dumps(line) + "\n").encode())
        else:
            self._reply({})


if __name__ == "__main__":
    if os.environ.get("PATCHSMITH_FAKE_DIE"):
        print("simulated error: cannot start", flush=True)
        sys.exit(3)
    if os.environ.get("PATCHSMITH_FAKE_SLOW"):  # a server that takes a while to start
        import time
        time.sleep(float(os.environ["PATCHSMITH_FAKE_SLOW"]))
    if os.environ.get("PATCHSMITH_FAKE_ENVIRONMENT"):
        with open(os.environ["PATCHSMITH_FAKE_ENVIRONMENT"], "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in os.environ.items() if k.upper().startswith(("OLLAMA", "TEMP", "TMP"))}, f)
    host, port = os.environ["OLLAMA_HOST"].rsplit(":", 1)
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()
