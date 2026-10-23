"""Ollama simulato per i test: risponde su 127.0.0.1 alla porta di OLLAMA_HOST.

Uso (lo lancia ollama.Server nei test): python -I fake_ollama.py serve
- /api/version, /api/tags, /api/ps, /api/generate, /api/chat (traduce anteponendo "IT:"), /api/pull (NDJSON);
- con PATCHSMITH_FINTO_MUORI=1 esce subito (server che non parte);
- scrive l'ambiente ricevuto in PATCHSMITH_FINTO_AMBIENTE (per controllare le variabili passate);
- con PATCHSMITH_FINTO_LENTO=secondi aspetta prima di rispondere (avvio lento);
- con PATCHSMITH_FINTO_SEMI_SBAGLIATI=42,7,11 risponde con l'originale (non tradotto) alle richieste con quei semi;
- con PATCHSMITH_FINTO_RICHIESTE=<file> aggiunge al file una riga JSON per ogni /api/chat: {testo, seed, temperature,
  model} (il registro delle richieste, per le prove).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Gestore(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _rispondi(self, dati, codice=200):
        corpo = json.dumps(dati).encode()
        self.send_response(codice)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if self.path == "/api/version":
            self._rispondi({"version": "0.40.4"})
        elif self.path == "/api/tags":
            self._rispondi({"models": [{"name": "translategemma:4b-it-q4_K_M", "digest": "c49d986b0764aa"}]})
        elif self.path == "/api/ps":
            self._rispondi({"models": [{"name": "translategemma:4b-it-q4_K_M", "size": 100, "size_vram": 100}]})
        else:
            self._rispondi({"error": "not found"}, 404)

    def do_POST(self):
        dati = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/chat":
            testo = dati["messages"][0]["content"].split("\n\n\n", 1)[1]
            opzioni = dati.get("options") or {}
            if os.environ.get("PATCHSMITH_FINTO_RICHIESTE"):
                with open(os.environ["PATCHSMITH_FINTO_RICHIESTE"], "a", encoding="utf-8") as f:
                    f.write(json.dumps({"testo": testo, "seed": opzioni.get("seed"),
                                        "temperature": opzioni.get("temperature"), "model": dati.get("model")}) + "\n")
            sbagliati = {int(s) for s in os.environ.get("PATCHSMITH_FINTO_SEMI_SBAGLIATI", "").split(",") if s.strip()}
            risposta = testo if opzioni.get("seed") in sbagliati else "IT: " + testo
            self._rispondi({"message": {"content": risposta}, "done_reason": "stop", "eval_count": 10,
                            "eval_duration": 100_000_000, "prompt_eval_count": 50})
        elif self.path == "/api/pull":
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for riga in ({"status": "pulling manifest"}, {"status": "x", "digest": "a", "total": 100, "completed": 50},
                         {"status": "x", "digest": "a", "total": 100, "completed": 100}, {"status": "success"}):
                self.wfile.write((json.dumps(riga) + "\n").encode())
        else:
            self._rispondi({})


if __name__ == "__main__":
    if os.environ.get("PATCHSMITH_FINTO_MUORI"):
        print("errore simulato: impossibile avviare", flush=True)
        sys.exit(3)
    if os.environ.get("PATCHSMITH_FINTO_LENTO"):  # server che ci mette un po' ad avviarsi
        import time
        time.sleep(float(os.environ["PATCHSMITH_FINTO_LENTO"]))
    if os.environ.get("PATCHSMITH_FINTO_AMBIENTE"):
        with open(os.environ["PATCHSMITH_FINTO_AMBIENTE"], "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in os.environ.items() if k.upper().startswith(("OLLAMA", "TEMP", "TMP"))}, f)
    host, porta = os.environ["OLLAMA_HOST"].rsplit(":", 1)
    ThreadingHTTPServer((host, int(porta)), Gestore).serve_forever()
