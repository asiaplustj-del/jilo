# -*- coding: utf-8 -*-
"""
Jilo — движок.
Студия видеопродакшена «Азия-Плюс».

Что умеет:
  • полосы LED-экрана (горизонтальные или вертикальные) — построчная коррекция
    только на пикселях экрана, спикер и всё остальное не трогается;
  • мерцание всего кадра (свет «пульсирует») — выравнивание общей яркости;
  • сглаживание экрана — убирает шум и пиксельную сетку там, где фильтр
    осветлял полосы;
  • цвет экрана: синий по умолчанию или любой, взятый пипеткой;
  • область: можно ограничить работу прямоугольником.
"""
import os
import sys
import time
import subprocess
from collections import deque

import numpy as np
import cv2

# ---------------------------------------------------------------- константы
VAL_MIN, VAL_MAX = 30, 250
REL_MIN = 0.22
WIN = 8
PCT = 85
DEADZONE = 0.01
ROW_MIN_FRAC = 0.06
GATE_LO, GATE_HI = 0.10, 0.20
MOTION_LO, MOTION_HI = 4, 10
STRIPS = 4
HOLE_MAX = 0.06

DEFAULTS = {
    "bands": True,          # убирать полосы экрана
    "direction": "h",       # "h" — горизонтальные, "v" — вертикальные
    "strength": 0.85,       # 0.1..1.0
    "ceiling": 1.9,         # 1.3..2.4
    "smooth": 0.2,          # 0..1 — сглаживание шума и сетки экрана
    "flicker": False,       # мерцание всего кадра
    "flicker_strength": 0.8,
    "color": None,          # None — синий экран; [h, s, v] — цвет с пипетки (OpenCV HSV)
    "region": None,         # None — весь кадр; [x0, y0, x1, y1] в долях 0..1
}

PRESETS = {
    "soft":   {"strength": 0.7,  "ceiling": 1.6,  "smooth": 0.25},
    "medium": {"strength": 0.85, "ceiling": 1.9,  "smooth": 0.2},
    "strong": {"strength": 1.0,  "ceiling": 2.4,  "smooth": 0.35},
}


def settings_from(d=None):
    s = dict(DEFAULTS)
    for k, v in (d or {}).items():
        if k in s:
            s[k] = v
    s["strength"] = float(min(max(float(s["strength"]), 0.0), 1.0))
    s["ceiling"] = float(min(max(float(s["ceiling"]), 1.05), 3.0))
    s["smooth"] = float(min(max(float(s["smooth"]), 0.0), 1.0))
    s["flicker_strength"] = float(min(max(float(s["flicker_strength"]), 0.0), 1.0))
    s["bands"] = bool(s["bands"])
    s["flicker"] = bool(s["flicker"])
    s["direction"] = "v" if s["direction"] == "v" else "h"
    r = s["region"]
    if r:
        x0, y0, x1, y1 = [float(min(max(t, 0.0), 1.0)) for t in r]
        x0, x1 = sorted((x0, x1)); y0, y1 = sorted((y0, y1))
        s["region"] = [x0, y0, x1, y1] if (x1 - x0) > 0.02 and (y1 - y0) > 0.02 else None
    c = s["color"]
    s["color"] = [int(c[0]), int(c[1]), int(c[2])] if c else None
    return s


def get_ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0   # не открывать консоль на Windows


def _popen(cmd, **kw):
    if NO_WINDOW:
        kw["creationflags"] = NO_WINDOW
    return subprocess.Popen(cmd, **kw)


def _run(cmd, **kw):
    if NO_WINDOW:
        kw["creationflags"] = NO_WINDOW
    return subprocess.run(cmd, **kw)


# ---------------------------------------------------------------- helpers
def smoothstep(x, a, b):
    t = min(max((x - a) / (b - a), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def h_shift(a, b, maxs=40):
    n = len(a)
    full = np.correlate(a, b, mode="full")[n - 1 - maxs: n + maxs]
    cnt = n - np.abs(np.arange(-maxs, maxs + 1))
    return int(np.argmax(full / cnt)) - maxs


def _row_median(vh, m, min_cnt):
    x = np.where(m, vh, 255).astype(np.uint8)
    x.sort(axis=1)
    cnt = m.sum(axis=1)
    ok = cnt >= min_cnt
    p = np.full(len(cnt), np.nan, np.float32)
    rows = np.nonzero(ok)[0]
    p[rows] = x[rows, cnt[rows] // 2]
    return p, ok


def _fill(p, ok, fallback=None):
    rows = np.nonzero(ok)[0]
    if len(rows) < 20:
        return fallback
    idx = np.arange(len(p))
    q = np.interp(idx, rows, p[rows]).astype(np.float32)
    return cv2.medianBlur(q.reshape(-1, 1), 5).ravel()


def _level(p):
    return max(float(np.percentile(p, 90)), 1.0)


def fill_holes(m):
    inv = (~m).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    if n <= 1:
        return m
    h, w = m.shape
    x, y, bw, bh, area = stats[:, 0], stats[:, 1], stats[:, 2], stats[:, 3], stats[:, 4]
    touches = (x == 0) | (y == 0) | (x + bw >= w) | (y + bh >= h)
    hole = (~touches) & (area <= HOLE_MAX * h * w)
    hole[0] = False
    return m | hole[lab]


def sample_color(img_bgr, x, y, r=6):
    """Цвет экрана в точке (x, y — доли 0..1): медиана HSV в квадрате вокруг."""
    H, W = img_bgr.shape[:2]
    cx, cy = int(x * (W - 1)), int(y * (H - 1))
    r = max(2, int(r * W / 1920))
    patch = img_bgr[max(0, cy - r):cy + r + 1, max(0, cx - r):cx + r + 1]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    h = int(np.median(hsv[:, 0])); s = int(np.median(hsv[:, 1])); v = int(np.median(hsv[:, 2]))
    b, g, rr = [int(t) for t in np.median(patch.reshape(-1, 3), axis=0)]
    return {"hsv": [h, s, v], "hex": "#%02x%02x%02x" % (rr, g, b)}


# ---------------------------------------------------------------- процессор
class _Frame:
    __slots__ = ("idx", "img", "work", "hs", "vh", "vq", "prof", "frac", "cp", "dx", "lum", "shot")


class Processor:
    """Подаём кадры по одному (push), получаем обработанные с задержкой WIN кадров.

    Каждый результат — кортеж (index, исходный_кадр, обработанный_кадр, stats).
    """

    def __init__(self, settings=None):
        self.s = settings_from(settings)
        self.buf = deque()
        self.center = 0
        self.n_in = 0
        self._region_cache = {}
        self._shot = 0
        self._prev_vq = None

    # ---------- цветовая маска экрана
    def _color_mask(self, hsv):
        h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        vr = (v >= VAL_MIN) & (v <= VAL_MAX)
        c = self.s["color"]
        if c is None:
            return (h >= 95) & (h <= 135) & (s >= 50) & vr
        h0, s0, v0 = c
        if s0 >= 45:   # цветной экран
            dh = np.abs(h.astype(np.int16) - h0)
            dh = np.minimum(dh, 180 - dh)
            return (dh <= 18) & (s >= max(35, int(0.45 * s0))) & vr
        # белый/серый экран
        return (s <= max(40, s0 + 35)) & vr

    def _tmed_min(self):
        c = self.s["color"]
        return 60.0 if c is None else float(min(60.0, 0.45 * c[2]))

    def _region(self, shape):
        r = self.s["region"]
        if not r:
            return None
        if self.s["direction"] == "v":
            r = [r[1], r[0], r[3], r[2]]
        key = (shape, tuple(r))
        if key not in self._region_cache:
            h, w = shape
            m = np.zeros(shape, bool)
            m[int(r[1] * h):int(np.ceil(r[3] * h)), int(r[0] * w):int(np.ceil(r[2] * w))] = True
            self._region_cache[key] = m
        return self._region_cache[key]

    # ---------- подготовка кадра
    def _prepare(self, img):
        fr = _Frame()
        fr.idx = self.n_in
        fr.img = img
        work = cv2.transpose(img) if self.s["direction"] == "v" else img
        fr.work = work
        small = cv2.resize(work, (work.shape[1] // 2, work.shape[0] // 2), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        hs = self._color_mask(hsv)
        reg = self._region(hs.shape)
        if reg is not None:
            hs &= reg
        fr.hs = hs
        fr.vh = hsv[..., 2]
        fr.vq = cv2.resize(fr.vh, (fr.vh.shape[1] // 2, fr.vh.shape[0] // 2), interpolation=cv2.INTER_AREA)
        fr.prof = None
        fr.frac = 0.0
        c = fr.vq.astype(np.float32).mean(axis=0)
        c = c - cv2.GaussianBlur(c.reshape(1, -1), (0, 0), 15).ravel()
        fr.cp = c / (c.std() + 1e-3)
        fr.dx = 0
        # общая яркость для «мерцания кадра»: по пикселям вне экрана (если включены полосы)
        g = cv2.resize(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (fr.vq.shape[1], fr.vq.shape[0]),
                       interpolation=cv2.INTER_AREA).astype(np.float32)
        if self.s["bands"]:
            hsq = cv2.resize(hs.astype(np.uint8), (g.shape[1], g.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
            other = ~hsq
            fr.lum = float(g[other].mean()) if other.mean() > 0.15 else None
        else:
            fr.lum = float(g.mean())
        # смена плана
        if self._prev_vq is not None and np.abs(fr.vq.astype(np.int16) - self._prev_vq).mean() > 30:
            self._shot += 1
        self._prev_vq = fr.vq.astype(np.int16)
        fr.shot = self._shot
        return fr

    def _mask(self, fr, frames):
        st = [f.vq for f in (frames[::2] if len(frames) > 9 else frames)]
        tmed = np.median(np.stack(st), axis=0).astype(np.float32)
        tmed = cv2.resize(tmed, (fr.vh.shape[1], fr.vh.shape[0]), interpolation=cv2.INTER_LINEAR)
        return fr.hs & (tmed >= self._tmed_min()) & (fr.vh.astype(np.float32) >= REL_MIN * tmed)

    def _measure(self, fr, m):
        Wd = m.shape[1]
        reg = self._region(m.shape)
        area = float(reg.sum()) if reg is not None else float(m.size)
        fr.frac = float(m.sum()) / max(area, 1.0)
        if reg is not None:
            cols_in = reg.any(axis=0)
            Wr = max(int(cols_in.sum()), 1)
        else:
            Wr = Wd
        p, ok = _row_median(fr.vh, m, ROW_MIN_FRAC * Wr)
        full = _fill(p, ok)
        if full is None:
            fr.prof = None
            return
        cols = []
        sw = Wd // STRIPS
        for i in range(STRIPS):
            sl = slice(i * sw, (i + 1) * sw if i < STRIPS - 1 else Wd)
            ps, oks = _row_median(fr.vh[:, sl], m[:, sl], 0.15 * sw)
            f = _fill(ps, oks, None)
            if f is None:
                f = full
            else:
                f = np.where(oks | (cv2.dilate(oks.astype(np.uint8).reshape(-1, 1), np.ones((9, 1))).ravel() > 0), f, full)
            cols.append(f)
        fr.prof = np.stack(cols, axis=1).astype(np.float32)

    def _gain(self, cur, window, ci):
        if cur.prof is None:
            return None
        idx = [i for i, f in enumerate(window) if f.prof is not None]
        if len(idx) < 3:
            return None
        lev = {i: _level(window[i].prof) for i in idx}

        def slow(i):
            near = [lev[j] for j in idx if abs(j - i) <= 3]
            return float(np.percentile(near, 85))
        norms = np.stack([window[i].prof / slow(i) for i in idx])
        ref = np.percentile(norms, PCT, axis=0).astype(np.float32) * slow(ci)
        ref = cv2.GaussianBlur(ref, (1, 0), sigmaX=0.1, sigmaY=6)
        g = ref / np.maximum(cur.prof, 1.0)
        g = 1.0 + np.maximum(g - 1.0 - DEADZONE, 0.0) * (1.0 + DEADZONE)
        g = np.clip(g, 1.0, self.s["ceiling"]).astype(np.float32)
        return cv2.GaussianBlur(g, (1, 0), sigmaX=0.1, sigmaY=1.5)

    def _flicker_gain(self, cur, window):
        if not self.s["flicker"] or cur.lum is None:
            return 1.0
        lums = [f.lum for f in window if f.shot == cur.shot and f.lum is not None]
        if len(lums) < 5:
            return 1.0
        ref = float(np.median(lums))
        r = ref / max(cur.lum, 1.0)
        if abs(r - 1.0) < 0.004:
            return 1.0
        return float(np.clip(1.0 + (r - 1.0) * self.s["flicker_strength"], 0.85, 1.18))

    # ---------- обработка центрального кадра окна
    def _process_center(self):
        window = list(self.buf)
        ci = self.center
        cur = window[ci]
        stats = {"frac": round(cur.frac, 3), "gain_max": 1.0, "flicker": 1.0, "active": False}
        work = cur.work
        H2, W2 = cur.vh.shape
        kmap = None
        wmap = None
        if self.s["bands"]:
            frac = float(np.mean([f.frac for f in window]))
            weight = smoothstep(frac, GATE_LO, GATE_HI) * self.s["strength"]
            lo, hi = max(0, ci - 4), min(len(window), ci + 5)
            motion = max(f.dx for f in window[lo:hi])
            weight *= 1.0 - smoothstep(motion, MOTION_LO, MOTION_HI)
            if weight > 0.01:
                g = self._gain(cur, window, ci)
                m = fill_holes(self._mask(cur, window))
                mf = cv2.erode(m.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(np.float32)
                mf = cv2.GaussianBlur(mf, (0, 0), 1.2)
                if g is not None:
                    gmap = cv2.resize(g, (W2, H2), interpolation=cv2.INTER_LINEAR)
                    kmap = 1.0 + (gmap - 1.0) * mf * weight
                    stats["gain_max"] = round(float(g.max()), 3)
                    stats["active"] = True
                else:
                    gmap = np.ones((H2, W2), np.float32)
                if self.s["smooth"] > 0:
                    wmap = np.clip(self.s["smooth"] * (0.35 + 1.6 * (gmap - 1.0)), 0.0, 1.0) * mf * min(1.0, weight / max(self.s["strength"], 1e-3))
        gf = self._flicker_gain(cur, window)
        stats["flicker"] = round(gf, 4)

        if kmap is None and wmap is None and gf == 1.0:
            return cur.idx, cur.img, cur.img, stats

        out = work.astype(np.float32)
        if kmap is not None or gf != 1.0:
            k = kmap if kmap is not None else np.ones((H2, W2), np.float32)
            k = cv2.resize(k * gf, (work.shape[1], work.shape[0]), interpolation=cv2.INTER_LINEAR)
            out = cv2.multiply(out, cv2.merge([k, k, k]))
        if wmap is not None and float(wmap.max()) > 0.01:
            sigma = 0.5 + 1.3 * max(work.shape[0], work.shape[1]) / 1920.0
            blur = cv2.GaussianBlur(out, (0, 0), sigma)
            w = cv2.resize(wmap, (work.shape[1], work.shape[0]), interpolation=cv2.INTER_LINEAR)
            w3 = cv2.merge([w, w, w])
            out = out + (blur - out) * w3
        out = np.clip(out, 0, 255).astype(np.uint8)
        if self.s["direction"] == "v":
            out = cv2.transpose(out)
        return cur.idx, cur.img, out, stats

    # ---------- поток
    def _add(self, img):
        fr = self._prepare(img)
        self.n_in += 1
        if self.buf:
            fr.dx = abs(h_shift(self.buf[-1].cp, fr.cp))
        self.buf.append(fr)
        tail = list(self.buf)[-(WIN + 1):]
        self._measure(fr, self._mask(fr, tail))

    def push(self, img):
        self._add(img)
        res = []
        # центр догоняет, пока впереди есть WIN кадров
        while self.center < len(self.buf) and len(self.buf) - 1 - self.center >= WIN:
            res.append(self._process_center())
            self.center += 1
            if self.center > WIN:
                self.buf.popleft(); self.center -= 1
        return res

    # ---------- превью: анализ один раз, рендер любого кадра быстро
    def analyze_list(self, frames):
        """Подготовить и замерить все кадры (без рендера). Возвращает список для render_index."""
        prepared = []
        for img in frames:
            fr = self._prepare(img)
            self.n_in += 1
            if prepared:
                fr.dx = abs(h_shift(prepared[-1].cp, fr.cp))
            prepared.append(fr)
            tail = prepared[-(WIN + 1):]
            self._measure(fr, self._mask(fr, tail))
        return prepared

    def render_index(self, prepared, k):
        lo = max(0, k - WIN)
        self.buf = deque(prepared[lo:k + WIN + 1])
        self.center = k - lo
        return self._process_center()

    def flush(self):
        res = []
        while self.center < len(self.buf):
            res.append(self._process_center())
            self.center += 1
            if self.center > WIN:
                self.buf.popleft(); self.center -= 1
        return res


def process_list(frames, settings, want=None):
    """Обработать список кадров целиком; want — индекс нужного кадра (или None — все)."""
    p = Processor(settings)
    out = {}
    for f in frames:
        for i, a, b, st in p.push(f):
            if want is None or i == want:
                out[i] = (a, b, st)
    for i, a, b, st in p.flush():
        if want is None or i == want:
            out[i] = (a, b, st)
    return out


# ---------------------------------------------------------------- видео
def probe(path):
    import imageio_ffmpeg
    gen = imageio_ffmpeg.read_frames(path)
    meta = gen.__next__()
    gen.close()
    W, H = meta["size"]
    fps = float(meta.get("fps") or 25.0)
    dur = float(meta.get("duration") or 0.0)
    return {"w": int(W), "h": int(H), "fps": fps, "duration": dur, "frames": int(round(dur * fps))}


def read_frames(path, start, count, W, H):
    """Прочитать count кадров начиная с start секунд."""
    ff = get_ffmpeg()
    cmd = [ff, "-loglevel", "error", "-ss", "%.4f" % max(start, 0.0), "-i", path,
           "-map", "0:v:0", "-frames:v", str(int(count)), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = _popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    data, _ = p.communicate()
    fs = W * H * 3
    n = len(data) // fs
    return [np.frombuffer(data[i * fs:(i + 1) * fs], np.uint8).reshape(H, W, 3).copy() for i in range(n)]


_HW_CACHE = {}


def hw_encoder():
    """Аппаратный H.264-кодировщик, если он реально работает на этой машине."""
    if "h264" in _HW_CACHE:
        return _HW_CACHE["h264"]
    cand = []
    if sys.platform.startswith("win"):
        cand = ["h264_nvenc", "h264_qsv", "h264_amf"]
    elif sys.platform == "darwin":
        cand = ["h264_videotoolbox"]
    ff = get_ffmpeg()
    found = None
    for c in cand:
        try:
            r = _run([ff, "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=640x360:d=0.2",
                      "-c:v", c, "-f", "null", "-"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            if r.returncode == 0:
                found = c
                break
        except Exception:
            pass
    _HW_CACHE["h264"] = found
    return found


def _venc(fmt, W, H):
    if fmt == "prores":
        return ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"], ["-c:a", "pcm_s16le"], ".mov"
    br = "%dM" % max(20, int(60 * (W * H) / (1920 * 1080)))
    hw = hw_encoder()
    if hw == "h264_nvenc":
        v = ["-c:v", hw, "-preset", "p5", "-rc", "vbr", "-cq", "16", "-b:v", br, "-maxrate", br]
    elif hw == "h264_videotoolbox":
        v = ["-c:v", hw, "-b:v", br]
    elif hw in ("h264_qsv", "h264_amf"):
        v = ["-c:v", hw, "-b:v", br]
    else:
        v = ["-c:v", "libx264", "-preset", "fast", "-crf", "14"]
    return v + ["-pix_fmt", "yuv420p"], ["-c:a", "aac", "-b:a", "320k"], ".mp4"


def output_name(inp, settings, fmt="mp4", suffix=""):
    base = os.path.splitext(inp)[0]
    ext = ".mov" if fmt == "prores" else ".mp4"
    s = settings_from(settings)
    tag = "_jilo_s%g_p%g" % (s["strength"], s["ceiling"])
    cand = base + tag + suffix + ext
    n = 2
    while os.path.exists(cand):
        cand = base + tag + suffix + "_%d" % n + ext
        n += 1
    return cand


def process_file(inp, out, settings, fmt="mp4", start=None, seconds=None, compare=False,
                 progress=None, cancel=None):
    """Обработать файл. progress(done, total, speed_fps) вызывается периодически.
    cancel() -> True — остановить. Возвращает dict с итогом."""
    info = probe(inp)
    W, H, fps = info["w"], info["h"], info["fps"]
    total = info["frames"]
    cut = []
    if start:
        cut += ["-ss", str(start)]
    if seconds:
        cut += ["-t", str(seconds)]
        total = int(round(float(seconds) * fps))
    ff = get_ffmpeg()
    vargs, aargs, ext = _venc(fmt, W, H)
    tmp = out + ".video_tmp" + ext
    dec = _popen([ff, "-loglevel", "error", "-threads", "0"] + cut + ["-i", inp, "-map", "0:v:0",
                 "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=W * H * 3 * 4)
    ow = W * 2 if compare else W
    enc = _popen([ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "%dx%d" % (ow, H),
                  "-r", "%g" % fps, "-i", "-"] + vargs + [tmp], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    proc = Processor(settings)
    fs = W * H * 3
    written = 0
    t0 = time.time()
    last = 0.0
    cancelled = False
    err = None

    def emit(items):
        nonlocal written, last
        for _, a, b, _ in items:
            frame = np.hstack([a, b]) if compare else b
            enc.stdin.write(frame.tobytes())
            written += 1
        now = time.time()
        if progress and now - last > 0.4:
            last = now
            progress(written, total, written / max(now - t0, 1e-3))

    try:
        while True:
            if cancel and cancel():
                cancelled = True
                break
            raw = dec.stdout.read(fs)
            if len(raw) < fs:
                break
            emit(proc.push(np.frombuffer(raw, np.uint8).reshape(H, W, 3).copy()))
        if not cancelled:
            emit(proc.flush())
    except (BrokenPipeError, OSError) as e:
        err = "Ошибка кодирования: %s" % e
    finally:
        try:
            dec.kill()
        except Exception:
            pass
        if cancelled:
            try:
                enc.kill()
            except Exception:
                pass
        try:
            enc.stdin.close()
        except Exception:
            pass
        enc.wait()

    if cancelled:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return {"ok": False, "cancelled": True}
    if enc.returncode != 0 or err:
        msg = (enc.stderr.read().decode("utf-8", "ignore") if enc.stderr else "")[-400:]
        try:
            os.remove(tmp)
        except OSError:
            pass
        return {"ok": False, "error": err or ("ffmpeg: " + msg)}

    mux = [ff, "-y", "-loglevel", "error", "-i", tmp] + cut + ["-i", inp, "-map", "0:v:0", "-map", "1:a:0?",
           "-c:v", "copy"] + aargs + ["-shortest", out]
    r = _run(mux, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode == 0 and os.path.exists(out):
        try:
            os.remove(tmp)
        except OSError:
            pass
    else:
        os.replace(tmp, out)
    if progress:
        progress(written, written, written / max(time.time() - t0, 1e-3))
    return {"ok": True, "out": out, "frames": written, "seconds": round(time.time() - t0, 1)}
