#!/usr/bin/env python3
"""FS25 live telemetry: read-only private GIANTS XML -> public minimal JSON."""
from __future__ import annotations
import json
import math
import os
import sys
import time
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / "private" / "source.json"
MAX_XML_BYTES = 2_000_000
DAY_MS = 86_400_000
LOCK = threading.Lock()
STATE = {
    "lastSample": None, "lastFetchAt": None, "lastChangedAt": None,
    "prevTimeMs": None, "prevChangedEpoch": None,
    "lastSpeedObserved": None, "totalFetches": 0,
    "failedFetches": 0, "lastError": None, "latencyMs": None
}

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def parse_xml(blob):
    root = ET.fromstring(blob)
    if root.tag != "Server":
        raise ValueError("unexpected XML root")
    raw_time = root.get("dayTime")
    if raw_time is None:
        raise ValueError("dayTime missing")
    ms = int(raw_time)
    if not (0 <= ms < DAY_MS):
        raise ValueError("invalid dayTime")
    slots = root.find("Slots")
    used = int(slots.get("numUsed", "0")) if slots is not None else 0
    cap = int(slots.get("capacity", "0")) if slots is not None else 0
    if not (0 <= used <= cap <= 128):
        raise ValueError("invalid slot count")
    return {
        "serverName": str(root.get("name", "FS25"))[:90],
        "mapName": str(root.get("mapName", "Unknown"))[:90],
        "gameVersion": str(root.get("version", ""))[:24],
        "dayTimeMs": ms,
        "players": used,
        "capacity": cap
    }

def read_source(config):
    url = config["sourceUrl"] + "?" + urlencode({"code": config["apiCode"]})
    request = Request(url, headers={"Accept": "application/xml", "User-Agent": "FS25LiveDashboard/1.0", "Cache-Control": "no-cache"})
    start = time.monotonic()
    with urlopen(request, timeout=7) as response:
        if response.status != 200:
            raise ValueError("unexpected HTTP response")
        blob = response.read(MAX_XML_BYTES + 1)
    if len(blob) > MAX_XML_BYTES:
        raise ValueError("XML too large")
    return parse_xml(blob), round((time.monotonic() - start) * 1000)

def collect_once(config):
    fetched_at = utc_now()
    tick = time.monotonic()
    try:
        parsed, latency = read_source(config)
    except Exception as exc:
        with LOCK:
            STATE["failedFetches"] += 1
            STATE["lastError"] = type(exc).__name__
        return False
    with LOCK:
        previous = STATE["lastSample"]
        if previous is None or previous["dayTimeMs"] != parsed["dayTimeMs"]:
            speed = None
            if previous is not None and STATE["prevChangedEpoch"] is not None:
                dt = tick - STATE["prevChangedEpoch"]
                dg = (parsed["dayTimeMs"] - previous["dayTimeMs"]) % DAY_MS
                if dt > 0.5 and dg > 0:
                    speed = round(dg / (dt * 1000), 2)
            STATE["lastChangedAt"] = fetched_at
            STATE["prevChangedEpoch"] = tick
            STATE["lastSpeedObserved"] = speed
        STATE["lastSample"] = parsed
        STATE["lastFetchAt"] = fetched_at
        STATE["lastError"] = None
        STATE["latencyMs"] = latency
        STATE["totalFetches"] += 1
    return True

def get_payload(config):
    with LOCK:
        s = STATE.copy()
        item = dict(s["lastSample"]) if s["lastSample"] else {}
    now = datetime.now(timezone.utc)
    def age(v):
        if not v:
            return None
        return max(0, round((now - datetime.fromisoformat(v.replace("Z", "+00:00"))).total_seconds(), 1))
    sample_age = age(s["lastFetchAt"])
    change_age = age(s["lastChangedAt"])
    online = sample_age is not None and sample_age <= max(25, int(config["pollSeconds"]) * 2.5)
    # An unchanged dayTime may be a paused game or a cached GIANTS feed: do NOT assert pause.
    if not online:
        quality = "offline"
    elif change_age is not None and change_age <= 45:
        quality = "recent"
    else:
        quality = "uncertain"
    return {
        "schema": "fs25-live-v1",
        "online": online,
        "quality": quality,
        "server": item.get("serverName"),
        "map": item.get("mapName"),
        "version": item.get("gameVersion"),
        "players": item.get("players"),
        "capacity": item.get("capacity"),
        "gameTimeMs": item.get("dayTimeMs"),
        "observedAt": s["lastChangedAt"],
        "fetchedAt": s["lastFetchAt"],
        "servedAt": utc_now(),
        "ageSinceChangeSeconds": change_age,
        "ageSinceFetchSeconds": sample_age,
        "speedObserved": s["lastSpeedObserved"],
        "pollSeconds": config["pollSeconds"],
        "sourceError": s["lastError"],
        "latencyMs": s["latencyMs"],
        "schedule": {"dayStart": config["dayStarts"], "nightStart": config["nightStarts"],
                     "daySpeed": config["daySpeed"], "nightSpeed": config["nightSpeed"]},
        "estimated": True
    }

def handler_factory(config):
    origin = config["allowOrigin"]
    class Handler(BaseHTTPRequestHandler):
        server_version = "FS25Telemetry/1.0"
        sys_version = ""
        def log_message(self, *args):
            return
        def respond(self, status, body):
            data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        def do_OPTIONS(self):
            if self.path in ("/v1/status", "/api/v1/status", "/fs25-live/v1/status", "/fs25-live/api/v1/status"):
                self.respond(200, {})
            else:
                self.respond(404, {"error": "not_found"})
        def do_GET(self):
            if self.path not in ("/v1/status", "/api/v1/status", "/fs25-live/v1/status", "/fs25-live/api/v1/status"):
                self.respond(404, {"error": "not_found"})
                return
            self.respond(200, get_payload(config))
    return Handler

def poll(config):
    while True:
        collect_once(config)
        time.sleep(max(5, int(config["pollSeconds"])))

def selftest():
    example = b'<Server dayTime="29422464" mapName="Boa Vista"><Slots capacity="8" numUsed="2"/></Server>'
    p = parse_xml(example)
    assert p["dayTimeMs"] == 29422464 and p["players"] == 2
    assert (86399900 - 1000) % DAY_MS == 86398900
    print("SELFTEST PASS (xml, time bounds, slots)")

def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    config = json.loads(PRIVATE.read_text(encoding="utf-8-sig"))
    if not (config.get("sourceUrl", "").startswith("http://") and
            1 <= int(config["bindPort"]) <= 65535 and
            config["bindHost"] == "127.0.0.1"):
        raise ValueError("Invalid configuration")
    worker = threading.Thread(target=poll, args=(config,), name="fs25-feed-poll", daemon=True)
    worker.start()
    with ThreadingHTTPServer((config["bindHost"], int(config["bindPort"])), handler_factory(config)) as server:
        server.serve_forever(poll_interval=0.5)

if __name__ == "__main__":
    main()
