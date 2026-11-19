# -*- coding: utf-8 -*-
"""Jilo — локальный сервер интерфейса (127.0.0.1)."""
import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

import cv2
import numpy as np

from . import core

VERSION = "1.0.1"


def resource_dir():
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "jilo")
    return os.path.dirname(os.path.abspath(__file__))


UI_DIR = os.path.join(resource_dir(), "ui")
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".woff2": "font/woff2", ".otf": "font/otf", ".ttf": "font/ttf"}


def _jpeg(img, width=None, q=88):
    if width and img.shape[1] > width:
        h = int(round(img.shape[0] * width / img.shape[1]))
        img = cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return buf.tobytes()


def _data_url(img, width=None, q=88):
    return "data:image/jpeg;base64," + base64.b64encode(_jpeg(img, width, q)).decode("ascii")


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


def reveal(path):
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
        return True
    except Exception:
        return False


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.path = None
        self.info = None
        self.cache = {}            # (s0, count) -> frames
        self.analysis = {}         # ключ анализа -> подготовленные кадры
        self.last_orig = None      # последний исходный кадр превью (для пипетки)
        self.motion = {"state": "idle"}
        self.motion_frames = ([], [])
        self.export = {"state": "idle"}
        self.cancel_export = False
        self.cancel_motion = False

    # --------------------------------------------------- файл
    def load(self, path):
        path = os.path.abspath(os.path.expanduser(path.strip().strip('"').strip("'")))
        if not os.path.isfile(path):
            raise ValueError("Файл не найден: " + path)
        info = core.probe(path)
        with self.lock:
            self.path = path
            self.info = info
            self.cache = {}
            self.analysis = {}
            self.last_orig = None
            self.motion = {"state": "idle"}
            self.motion_frames = ([], [])
        return {"path": path, "name": os.path.basename(path), "dir": os.path.dirname(path),
                "size_mb": round(os.path.getsize(path) / 1048576, 1), **info}

    def _frames(self, s0, count):
        key = (s0, count)
        fr = self.cache.get(key)
        if fr is None:
            W, H, fps = self.info["w"], self.info["h"], self.info["fps"]
            fr = core.read_frames(self.path, s0 / fps, count, W, H)
            if len(self.cache) > 2:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = fr
        return fr

    # --------------------------------------------------- превью кадра
    def preview(self, t, settings, width=1600):
        if not self.path:
            raise ValueError("Сначала откройте видео")
        fps = self.info["fps"]
        total = max(self.info["frames"], 1)
        fi = int(round(float(t) * fps))
        fi = min(max(fi, 0), total - 1)
        s0 = max(0, fi - 2 * core.WIN)
        count = fi - s0 + core.WIN + 1
        st_ = core.settings_from(settings)
        akey = (s0, count, json.dumps(st_["color"]), json.dumps(st_["region"]), st_["direction"], st_["bands"])
        with self.lock:
            frames = self._frames(s0, count)
            if not frames:
                raise ValueError("Не удалось прочитать кадр")
            want = min(fi - s0, len(frames) - 1)
            proc = core.Processor(st_)
            prepared = self.analysis.get(akey)
            if prepared is None:
                prepared = proc.analyze_list(frames)
                self.analysis = {akey: prepared}      # храним только последний анализ
            _, a, b, st = proc.render_index(prepared, want)
        with self.lock:
            self.last_orig = a
        return {"t": (s0 + want) / fps, "frame": s0 + want, "before": _data_url(a, width), "after": _data_url(b, width),
                "stats": st}

    def sample(self, x, y):
        if self.last_orig is None:
            raise ValueError("Нет кадра")
        return core.sample_color(self.last_orig, float(x), float(y))

    # --------------------------------------------------- просмотр в движении
    def motion_start(self, t, seconds, settings):
        if not self.path:
            raise ValueError("Сначала откройте видео")
        if self.motion.get("state") == "running":
            self.cancel_motion = True
            time.sleep(0.2)
        self.cancel_motion = False
        fps = self.info["fps"]
        n = int(round(float(seconds) * fps))
        fi = int(round(float(t) * fps))
        s0 = max(0, fi - core.WIN)
        self.motion = {"state": "running", "done": 0, "total": n, "fps": fps, "t": fi / fps}
        self.motion_frames = ([], [])
        threading.Thread(target=self._motion_job, args=(s0, fi, n, settings), daemon=True).start()
        return self.motion

    def _motion_job(self, s0, fi, n, settings):
        try:
            W, H = self.info["w"], self.info["h"]
            frames = core.read_frames(self.path, s0 / self.info["fps"], (fi - s0) + n + core.WIN, W, H)
            p = core.Processor(settings)
            A, B = [], []

            def take(items):
                for i, a, b, _ in items:
                    k = s0 + i
                    if fi <= k < fi + n:
                        A.append(_jpeg(a, 1280, 84)); B.append(_jpeg(b, 1280, 84))
                        self.motion["done"] = len(A)
            for f in frames:
                if self.cancel_motion:
                    self.motion = {"state": "idle"}
                    return
                take(p.push(f))
            take(p.flush())
            self.motion_frames = (A, B)
            self.motion = {"state": "done", "done": len(A), "total": len(A), "fps": self.info["fps"], "n": len(A)}
        except Exception as e:
            traceback.print_exc()
            self.motion = {"state": "error", "error": str(e)}

    # --------------------------------------------------- экспорт
    def export_start(self, settings, fmt="mp4", out=None):
        if not self.path:
            raise ValueError("Сначала откройте видео")
        if self.export.get("state") == "running":
            raise ValueError("Обработка уже идёт")
        out = out or core.output_name(self.path, settings, fmt)
        self.cancel_export = False
        self.export = {"state": "running", "done": 0, "total": self.info["frames"], "speed": 0, "eta": None,
                       "out": out, "started": time.time()}
        threading.Thread(target=self._export_job, args=(dict(settings), fmt, out), daemon=True).start()
        return self.export

    def _export_job(self, settings, fmt, out):
        def prog(done, total, speed):
            eta = (total - done) / speed if speed > 0 and total else None
            self.export.update({"done": done, "total": total, "speed": round(speed, 1), "eta": eta})
        try:
            r = core.process_file(self.path, out, settings, fmt=fmt, progress=prog, cancel=lambda: self.cancel_export)
            if r.get("ok"):
                self.export.update({"state": "done", "out": r["out"], "seconds": r["seconds"]})
            elif r.get("cancelled"):
                self.export = {"state": "cancelled"}
            else:
                self.export = {"state": "error", "error": r.get("error", "Ошибка")}
        except Exception as e:
            traceback.print_exc()
            self.export = {"state": "error", "error": str(e)}


STATE = State()


class Handler(BaseHTTPRequestHandler):
    server_version = "Jilo/" + VERSION

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(_clean(body), ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self._static("index.html")
            if u.path.startswith("/ui/"):
                return self._static(unquote(u.path[4:]))
            if u.path == "/api/defaults":
                return self._send(200, {"defaults": core.DEFAULTS, "presets": core.PRESETS, "version": VERSION,
                                        "platform": sys.platform})
            if u.path == "/api/motion_status":
                return self._send(200, STATE.motion)
            if u.path == "/api/motion_frame":
                side = q.get("side", ["b"])[0]
                i = int(q.get("i", ["0"])[0])
                arr = STATE.motion_frames[0 if side == "a" else 1]
                if 0 <= i < len(arr):
                    return self._send(200, arr[i], "image/jpeg")
                return self._send(404, {"error": "нет кадра"})
            if u.path == "/api/export_status":
                return self._send(200, STATE.export)
            return self._send(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            d = self._json()
            if u.path == "/api/load":
                return self._send(200, STATE.load(d.get("path", "")))
            if u.path == "/api/preview":
                return self._send(200, STATE.preview(d.get("t", 0), d.get("settings"), int(d.get("width", 1600))))
            if u.path == "/api/sample":
                return self._send(200, STATE.sample(d.get("x", 0.5), d.get("y", 0.5)))
            if u.path == "/api/motion":
                return self._send(200, STATE.motion_start(d.get("t", 0), d.get("seconds", 2), d.get("settings")))
            if u.path == "/api/motion_cancel":
                STATE.cancel_motion = True
                return self._send(200, {"ok": True})
            if u.path == "/api/export":
                return self._send(200, STATE.export_start(d.get("settings"), d.get("fmt", "mp4"), d.get("out")))
            if u.path == "/api/cancel":
                STATE.cancel_export = True
                return self._send(200, {"ok": True})
            if u.path == "/api/out_name":
                if not STATE.path:
                    return self._send(200, {"out": ""})
                return self._send(200, {"out": core.output_name(STATE.path, d.get("settings"), d.get("fmt", "mp4"))})
            if u.path == "/api/reveal":
                return self._send(200, {"ok": reveal(d.get("path") or STATE.path or "")})
            return self._send(404, {"error": "not found"})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"error": str(e)})

    def _static(self, rel):
        p = os.path.normpath(os.path.join(UI_DIR, rel))
        if not p.startswith(os.path.normpath(UI_DIR)) or not os.path.isfile(p):
            return self._send(404, {"error": "not found"})
        with open(p, "rb") as f:
            data = f.read()
        return self._send(200, data, MIME.get(os.path.splitext(p)[1].lower(), "application/octet-stream"))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start(port=None):
    port = port or free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, "http://127.0.0.1:%d/" % port
