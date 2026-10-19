#!/usr/bin/env python3
"""Lifetime KTC-Easy toolchange/pickup counters using Moonraker's HTTP API.

No Klipper macros or third-party Python packages are required.
"""
import argparse
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

MOONRAKER = "http://127.0.0.1:7125"
POLL_SECONDS = 0.25
TOOLS = range(4)
COUNTER_NAMES = ["total_toolchanges"] + [
    name
    for tool in TOOLS
    for name in (f"tool_t{tool}_pickups", f"tool_t{tool}_failures")
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOG = logging.getLogger("tool-pickup-counter")


class Moonraker:
    def __init__(self, base_url: str = MOONRAKER, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/{path.lstrip('/')}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        if "error" in result:
            raise RuntimeError(result["error"].get("message", str(result["error"])))
        return result

    def query(self) -> Dict[str, Any]:
        from urllib.parse import urlencode

        objects = {
            "toolchanger": "status,tool_number,detected_tool_number,tool,detected_tool,has_detection",
            "save_variables": "variables",
        }
        url = f"{self.base_url}/printer/objects/query?{urlencode(objects)}"
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        if "error" in result:
            raise RuntimeError(result["error"].get("message", str(result["error"])))
        return result.get("result", {}).get("status", {})

    def save_variable(self, name: str, value: int) -> None:
        self.post(
            "/printer/gcode/script",
            {"script": f"SAVE_VARIABLE VARIABLE={name} VALUE={int(value)}"},
        )


class Counter:
    def __init__(self, api: Moonraker, dry_run: bool = False):
        self.api = api
        # Saving is deliberately isolated from the fast polling loop. A macro can
        # keep Klipper's G-code queue busy long enough for a normal HTTP timeout.
        self.writer_api = Moonraker(api.base_url, timeout=30.0)
        self.dry_run = dry_run
        self.values = {name: 0 for name in COUNTER_NAMES}
        self.previous_status: Optional[str] = None
        self.active_attempt = False
        self.start_tool = -1
        self.target_tool = -1
        self.last_signature = None
        self.pending_saves: Dict[str, int] = {}
        self.pending_lock = threading.Lock()
        self.save_wakeup = threading.Event()

    def load(self, status: Dict[str, Any]) -> None:
        saved = status.get("save_variables", {}).get("variables", {}) or {}
        for name in COUNTER_NAMES:
            try:
                self.values[name] = max(0, int(saved.get(name, 0)))
            except (TypeError, ValueError):
                self.values[name] = 0
        LOG.info("Loaded saved counters: %s", self.summary())

    def persist(self, name: str) -> None:
        value = self.values[name]
        if self.dry_run:
            LOG.info("DRY RUN: would SAVE_VARIABLE %s=%d", name, value)
            return
        # Coalesce updates: only the newest value for a counter needs writing.
        with self.pending_lock:
            self.pending_saves[name] = value
        self.save_wakeup.set()

    def save_worker(self) -> None:
        """Persist queued values outside the monitor's polling loop."""
        while True:
            self.save_wakeup.wait(timeout=0.5)
            self.save_wakeup.clear()

            with self.pending_lock:
                queued = list(self.pending_saves.items())
            if not queued:
                continue

            try:
                status = self.writer_api.query()
                tc = status.get("toolchanger", {})
                state = str(tc.get("status", "unknown")).lower()
                # Do not add G-code to the queue while a toolchange macro is
                # running. Retry after it reaches a stable state.
                if state not in ("ready", "error"):
                    continue
            except (urllib.error.URLError, socket.timeout, TimeoutError, RuntimeError, ValueError, KeyError) as exc:
                LOG.warning("Cannot check toolchanger state before saving; queued values retained: %s", exc)
                continue

            for name, value in queued:
                try:
                    self.writer_api.save_variable(name, value)
                    # SAVE_VARIABLE is asynchronous relative to the HTTP request;
                    # read back the saved value before considering it complete.
                    verify_status = self.writer_api.query()
                    variables = verify_status.get("save_variables", {}).get("variables", {}) or {}
                    try:
                        saved_value = int(variables.get(name, -1))
                    except (TypeError, ValueError):
                        saved_value = -1

                    if saved_value != value:
                        LOG.warning(
                            "SAVE_VARIABLE %s=%d not verified (read back %s); keeping queued for retry",
                            name, value, saved_value,
                        )
                        continue

                    with self.pending_lock:
                        if self.pending_saves.get(name) == value:
                            self.pending_saves.pop(name, None)
                    LOG.info("SAVE VERIFIED %s=%d", name, value)
                except (urllib.error.URLError, socket.timeout, TimeoutError, RuntimeError, ValueError, KeyError) as exc:
                    LOG.warning(
                        "SAVE_VARIABLE %s=%d not verified; keeping queued for retry: %s",
                        name, value, exc,
                    )
                except Exception:
                    LOG.exception("Unexpected save-worker error for %s=%d; queued value retained", name, value)

            with self.pending_lock:
                if self.pending_saves:
                    # Keep retrying, but never block the polling thread.
                    self.save_wakeup.set()

    def increment(self, name: str, reason: str) -> None:
        self.values[name] += 1
        LOG.info("%s -> %d (%s) | %s", name, self.values[name], reason, self.summary())
        self.persist(name)

    def summary(self) -> str:
        parts = [f"total={self.values['total_toolchanges']}"]
        for tool in TOOLS:
            parts.append(
                f"T{tool}:ok={self.values[f'tool_t{tool}_pickups']}"
                f",failed={self.values[f'tool_t{tool}_failures']}"
            )
        return " ".join(parts)

    @staticmethod
    def number(obj: Dict[str, Any], key: str, default: int = -1) -> int:
        try:
            return int(obj.get(key, default))
        except (TypeError, ValueError):
            return default

    def process(self, status: Dict[str, Any]) -> None:
        tc = status.get("toolchanger")
        if not isinstance(tc, dict):
            return
        state = str(tc.get("status", "unknown")).lower()
        selected = self.number(tc, "tool_number")
        detected = self.number(tc, "detected_tool_number")

        # The first observation establishes a baseline; it is not a toolchange.
        if self.previous_status is None:
            self.previous_status = state
            self.last_signature = (state, selected, detected)
            return

        if state == "changing" and self.previous_status != "changing":
            self.active_attempt = True
            self.start_tool = selected
            self.target_tool = -1
            self.increment("total_toolchanges", "toolchange attempt started")
            LOG.info("Toolchange started (current T%d, awaiting target)", self.start_tool)

        if self.active_attempt and state == "changing":
            # KTC-Easy first releases the old tool, then changes tool_number to
            # the requested target. Only a different, valid tool is a target.
            if selected in TOOLS and selected != self.start_tool:
                self.target_tool = selected

        if self.active_attempt and state == "error":
            # Count only when the requested target was observed and no tool is detected.
            if self.target_tool in TOOLS and detected < 0:
                self.increment(f"tool_t{self.target_tool}_failures", "pickup failed")
                LOG.warning("Pickup of T%d failed; recovery will not count as success", self.target_tool)
            else:
                LOG.warning(
                    "Toolchange entered error but pickup failure was not unambiguous "
                    "(target=%s detected=%s); total attempt counted, no per-tool failure added",
                    self.target_tool, detected,
                )
            self.active_attempt = False
            self.target_tool = -1

        elif self.active_attempt and state == "ready":
            success_tool = self.target_tool if self.target_tool in TOOLS else selected
            if success_tool in TOOLS and selected == success_tool and detected == success_tool:
                self.increment(f"tool_t{success_tool}_pickups", "pickup verified")
                self.active_attempt = False
                self.target_tool = -1
            elif selected == detected and selected in TOOLS:
                LOG.info("Ready state observed, but no matching active pickup target; not counted")
                self.active_attempt = False
                self.target_tool = -1

        self.previous_status = state
        self.last_signature = (state, selected, detected)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="log counter changes without writing SAVE_VARIABLE")
    parser.add_argument("--url", default=MOONRAKER, help=f"Moonraker base URL (default: {MOONRAKER})")
    parser.add_argument("--poll", type=float, default=POLL_SECONDS, help="poll interval in seconds (default: 0.25)")
    args = parser.parse_args()
    if args.poll < 0.1:
        parser.error("--poll must be at least 0.1 seconds")

    api = Moonraker(args.url)
    counter = Counter(api, dry_run=args.dry_run)
    LOG.info("Starting monitor%s", " in DRY RUN mode" if args.dry_run else "")

    # Retry connection errors without changing the baseline or counters.
    while True:
        try:
            initial = api.query()
            counter.load(initial)
            break
        except (urllib.error.URLError, socket.timeout, TimeoutError, RuntimeError, ValueError, KeyError) as exc:
            LOG.warning("Waiting for Moonraker: %s", exc)
            time.sleep(2)

    if not args.dry_run:
        threading.Thread(target=counter.save_worker, name="counter-save-worker", daemon=True).start()

    while True:
        try:
            status = api.query()
            counter.process(status)
            time.sleep(args.poll)
        except KeyboardInterrupt:
            LOG.info("Stopped. %s", counter.summary())
            return
        except (urllib.error.URLError, socket.timeout, TimeoutError, RuntimeError, ValueError, KeyError) as exc:
            LOG.warning("Moonraker query failed; retrying without inferring an outcome: %s", exc)
            counter.previous_status = None
            counter.active_attempt = False
            counter.target_tool = -1
            time.sleep(2)


if __name__ == "__main__":
    main()
