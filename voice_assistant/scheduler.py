"""Persistent reminder store + a background thread that fires due reminders.

Reminders survive a restart: they're written to a JSON file with absolute fire
timestamps and reloaded on startup, so a pending "in 30 minutes" still fires
even if the daemon is bounced in between.
"""

import json
import os
import threading
import time

from . import actions, config


class Scheduler:
    def __init__(self, path=None, fire=actions.fire_reminder, tick=2.0):
        self.path = path or config.STORE_PATH
        self._fire = fire
        self._tick = tick
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._items = self._load()

    # --- persistence ---------------------------------------------------------
    def _load(self):
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path) as f:
                return [r for r in json.load(f) if not r.get("fired")]
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._items, f, indent=2)
        os.replace(tmp, self.path)  # atomic

    # --- public API ----------------------------------------------------------
    def add(self, text, delay_minutes, now=None):
        """Schedule `text` to fire `delay_minutes` from now. Returns fire_at epoch."""
        now = time.time() if now is None else now
        fire_at = now + delay_minutes * 60
        with self._lock:
            self._items.append({"text": text, "fire_at": fire_at, "fired": False})
            self._items.sort(key=lambda r: r["fire_at"])
            self._save()
        return fire_at

    def pending(self):
        with self._lock:
            return list(self._items)

    def _due(self, now):
        ready = [r for r in self._items if not r["fired"] and r["fire_at"] <= now]
        for r in ready:
            r["fired"] = True
        if ready:
            self._items = [r for r in self._items if not r["fired"]]
            self._save()
        return ready

    # --- background loop -----------------------------------------------------
    def _run(self):
        while not self._stop.is_set():
            now = time.time()
            with self._lock:
                ready = self._due(now)
            for r in ready:
                try:
                    self._fire(r["text"])
                except Exception as e:  # a bad fire must not kill the loop
                    print(f"  (reminder fire error: {e})")
            self._stop.wait(self._tick)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
