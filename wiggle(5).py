#!/usr/bin/env python3
"""
wiggle — шатающаяся область экрана (как wiggle theme у vivo) для Hyprland.

  wiggle.py add     выделить область мышкой (slurp) и добавить в список
  wiggle.py list    показать сохранённые области
  wiggle.py clear   удалить все области
  wiggle.py         запустить (оверлеи для всех областей)

Зависимости (Arch):
  sudo pacman -S gtk4-layer-shell python-gobject python-numpy grim slurp
"""
import os
import sys
import json
import re
import time
import pathlib
import subprocess
import threading

CFG = pathlib.Path.home() / ".config/wiggle/regions.json"
LAYER_LIB = "/usr/lib/libgtk4-layer-shell.so"

# ---------- параметры эффекта (крути под себя) ----------
AMP = 9.0        # амплитуда бегущей волны, px
SWAY = 14.0      # амплитуда "желейного" покачивания, px
SPEED = 520.0    # скорость волны, px/сек
WAVELEN = 110.0  # длина волны, px
TAU = 0.8        # затухание волны, сек
TAU2 = 0.7       # затухание покачивания, сек
FREQ = 2.2       # частота покачивания, Гц
LEN = 700.0      # на каком расстоянии от клика эффект затухает, px
DUR = 3.2        # полная длительность, сек
EDGE = 0.07      # ширина "пришитого" края (доля от меньшей стороны)
STEP = 8         # шаг сетки смещений (больше = быстрее, грубее)
MAX_PIXELS = 450_000  # считаем эффект не более чем на стольких пикселях;
                      # большие области уменьшаются, GTK растягивает обратно.
                      # Лагает — уменьши (300_000), хочешь чётче — увеличь.
# ---------------------------------------------------------

MODE = sys.argv[1] if len(sys.argv) > 1 else "run"
DEBUG = bool(os.environ.get("WIGGLE_DEBUG"))


def dbg(*a):
    if DEBUG:
        print("[wiggle]", *a, file=sys.stderr, flush=True)


def load():
    return json.loads(CFG.read_text()) if CFG.exists() else []


def save(regions):
    CFG.parent.mkdir(parents=True, exist_ok=True)
    CFG.write_text(json.dumps(regions))


if MODE == "add":
    try:
        out = subprocess.check_output(["slurp", "-f", "%x %y %w %h"], text=True).split()
    except subprocess.CalledProcessError:
        print("Отменено")
        sys.exit(0)
    r = load()
    r.append([int(v) for v in out])
    save(r)
    print("Добавлено:", r[-1])
    sys.exit(0)
if MODE == "list":
    print(load())
    sys.exit(0)
if MODE == "clear":
    save([])
    print("Очищено")
    sys.exit(0)

# gtk4-layer-shell из питона должен быть подгружен раньше wayland-client
if LAYER_LIB not in os.environ.get("LD_PRELOAD", ""):
    os.environ["LD_PRELOAD"] = (os.environ.get("LD_PRELOAD", "") + " " + LAYER_LIB).strip()
    os.execv(sys.executable, [sys.executable] + sys.argv)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk, Gdk, GLib, Gtk4LayerShell as LS  # noqa: E402
import numpy as np  # noqa: E402


def smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def read_ppm(data):
    """grim -t ppm  ->  RGB numpy массив (без внешних библиотек)."""
    m = re.match(rb"P6\s+(\d+)\s+(\d+)\s+(\d+)\s", data)
    w, h = int(m.group(1)), int(m.group(2))
    return np.frombuffer(data, np.uint8, w * h * 3, m.end()).reshape(h, w, 3).copy()


def make_upsampler(H, W, gh, gw):
    """Заранее считает индексы/веса для увеличения грубой сетки до H x W."""
    fy = np.clip(np.arange(H, dtype=np.float32) / STEP, 0, gh - 1.001)
    fx = np.clip(np.arange(W, dtype=np.float32) / STEP, 0, gw - 1.001)
    y0, x0 = fy.astype(np.int32), fx.astype(np.int32)
    return y0, (fy - y0)[:, None], x0, (fx - x0)[None, :]


def upsample(a, U):
    """Билинейное увеличение грубой сетки смещений до размера картинки."""
    y0, wy, x0, wx = U
    rows = a[y0] * (1 - wy) + a[y0 + 1] * wy
    return rows[:, x0] * (1 - wx) + rows[:, x0 + 1] * wx


def remap(img, mx, my):
    """Билинейная деформация картинки по карте координат."""
    H, W = img.shape[:2]
    mx = np.clip(mx, 0, W - 1.001)
    my = np.clip(my, 0, H - 1.001)
    x0, y0 = mx.astype(np.int32), my.astype(np.int32)
    wx, wy = (mx - x0)[..., None], (my - y0)[..., None]
    top = img[y0, x0] * (1 - wx) + img[y0, x0 + 1] * wx
    bot = img[y0 + 1, x0] * (1 - wx) + img[y0 + 1, x0 + 1] * wx
    return (top * (1 - wy) + bot * wy).astype(np.uint8)


def downscale(img, W2, H2):
    """Один раз уменьшаем снимок (билинейно), чтобы покадровая работа была лёгкой."""
    H, W = img.shape[:2]
    mx = ((np.arange(W2, dtype=np.float32) + 0.5) * (W / W2) - 0.5)[None, :].repeat(H2, 0)
    my = ((np.arange(H2, dtype=np.float32) + 0.5) * (H / H2) - 0.5)[:, None].repeat(W2, 1)
    return remap(img, mx, my)


class Wobble(Gtk.Window):
    def __init__(self, app, x, y, w, h):
        super().__init__(application=app)
        self.add_css_class("wiggle")
        self.connect("map", lambda win: dbg("MAP: окно показано, размер",
                                            win.get_width(), "x", win.get_height()))
        self.geo = (x, y, w, h)
        self.set_default_size(w, h)

        LS.init_for_window(self)
        LS.set_namespace(self, "wiggle")
        LS.set_layer(self, LS.Layer.BOTTOM)  # выше обоев, ниже окон
        LS.set_anchor(self, LS.Edge.TOP, True)
        LS.set_anchor(self, LS.Edge.LEFT, True)
        LS.set_margin(self, LS.Edge.TOP, y)
        LS.set_margin(self, LS.Edge.LEFT, x)
        LS.set_exclusive_zone(self, -1)
        LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)

        self.pic = Gtk.Picture()
        self.pic.set_content_fit(Gtk.ContentFit.FILL)
        self.pic.set_can_shrink(True)
        self.pic.set_opacity(0.0)
        self.set_child(self.pic)

        click = Gtk.GestureClick()
        click.connect("pressed", self.on_press)
        self.add_controller(click)

        self.img = None
        self.grid = None
        self.packed = None
        self.scale = 1.0
        self.impulses = []
        self.active = False
        self.capturing = False
        self.start = 0.0
        self.end = 0.0

    # ---------- клик ----------
    def on_press(self, gesture, n_press, x, y):
        dbg("click", round(x), round(y), "active" if self.active else "idle")
        if self.capturing:
            return
        if self.active:
            self.add_impulse(x, y)
            return
        self.capturing = True
        threading.Thread(target=self.capture, args=(x, y), daemon=True).start()

    def capture(self, x, y):
        gx, gy, gw, gh = self.geo
        bgr = None
        try:
            data = subprocess.check_output(
                ["grim", "-g", f"{gx},{gy} {gw}x{gh}", "-t", "ppm", "-"]
            )
            bgr = read_ppm(data)
        except Exception as e:  # noqa: BLE001
            print("grim error:", e, file=sys.stderr)
        GLib.idle_add(self.begin, bgr, x, y)

    def begin(self, bgr, x, y):
        self.capturing = False
        if bgr is None:
            return False
        H0, W0 = bgr.shape[:2]
        sc = min(1.0, (MAX_PIXELS / float(H0 * W0)) ** 0.5)
        if sc < 1.0:
            W, H = max(16, int(W0 * sc)), max(16, int(H0 * sc))
            self.img = downscale(bgr, W, H)
            sc = W / W0
        else:
            self.img = bgr
        self.scale = sc
        H, W = self.img.shape[:2]
        # пиксель = одно число uint32 (R,G,B,255): один gather вместо трёх каналов
        rgba = np.empty((H, W, 4), np.uint8)
        rgba[..., :3] = self.img
        rgba[..., 3] = 255
        self.packed = rgba.view(np.uint32).reshape(H, W)
        dbg("снимок", W0, "x", H0, "-> считаем на", W, "x", H, "масштаб", round(sc, 2))
        if self.grid is None or self.grid[0] != (H, W):
            self.build_grid(H, W)
        self.impulses = []
        self.start = time.monotonic()
        self.add_impulse(x, y)
        self.active = True
        self.add_tick_callback(self.tick)
        return False

    def add_impulse(self, x, y):
        H, W = self.img.shape[:2]
        sx = W / float(self.get_width() or self.geo[2])
        sy = H / float(self.get_height() or self.geo[3])
        now = time.monotonic()
        self.impulses.append((x * sx, y * sy, now))
        self.end = now + DUR

    # ---------- сетка ----------
    def build_grid(self, H, W):
        gh, gw = H // STEP + 2, W // STEP + 2
        xs = (np.arange(gw) * STEP).astype(np.float32)
        ys = (np.arange(gh) * STEP).astype(np.float32)
        GX, GY = np.meshgrid(xs, ys)
        fade = max(24.0, EDGE * min(W, H))
        edge = np.minimum.reduce([GX, W - GX, GY, H - GY]) / fade
        mask = smoothstep(edge).astype(np.float32)
        U = make_upsampler(H, W, gh, gw)
        # +0.5, чтобы astype(int) давал округление до ближайшего пикселя
        BX = np.broadcast_to(np.arange(W, dtype=np.float32) + 0.5, (H, W))
        BY = np.broadcast_to((np.arange(H, dtype=np.float32) + 0.5)[:, None], (H, W))
        self.grid = ((H, W), GX, GY, mask, U, BX, BY)

    # ---------- кадр ----------
    def tick(self, widget, clock):
        now = time.monotonic()
        if now > self.end:
            self.active = False
            self.pic.set_opacity(0.0)
            self.pic.set_paintable(None)
            return False

        (H, W), GX, GY, mask, U, BX, BY = self.grid
        sc = self.scale  # все расстояния в параметрах заданы для полного размера
        dx = np.zeros_like(GX)
        dy = np.zeros_like(GY)
        k = 2 * np.pi / (WAVELEN * sc)
        om = 2 * np.pi * FREQ

        for cx, cy, t0 in self.impulses:
            dt = now - t0
            if dt > DUR:
                continue
            rx, ry = GX - cx, GY - cy
            r = np.sqrt(rx * rx + ry * ry) + 1e-3
            ux, uy = rx / r, ry / r
            front = SPEED * sc * dt

            # бегущая волна (круги на воде)
            gate = 1.0 / (1.0 + np.exp(-(front - r) / (25.0 * sc)))
            ripple = (AMP * sc) * np.exp(-dt / TAU) * np.exp(-r / (LEN * sc)) * gate \
                * np.sin(k * (r - front))

            # желейное покачивание: ближе к клику — сильнее
            env = (SWAY * sc) * np.exp(-dt / TAU2) * np.exp(-r / (LEN * sc))
            radial = env * np.sin(om * dt)
            tang = 0.4 * env * np.sin(1.3 * om * dt)

            dx += ux * (ripple + radial) - uy * tang
            dy += uy * (ripple + radial) + ux * tang

        xi = (BX + upsample(dx * mask, U)).astype(np.int32)
        yi = (BY + upsample(dy * mask, U)).astype(np.int32)
        np.clip(xi, 0, W - 1, out=xi)
        np.clip(yi, 0, H - 1, out=yi)
        out = self.packed[yi, xi]

        tex = Gdk.MemoryTexture.new(
            W, H, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(out.tobytes()), W * 4
        )
        self.pic.set_paintable(tex)

        # плавное появление/исчезновение, чтобы не было скачка относительно живых обоев
        alpha = min((now - self.start) / 0.10, (self.end - now) / 0.30, 1.0)
        self.pic.set_opacity(max(0.0, alpha))
        return True


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.wiggle")

    def do_activate(self):
        css = Gtk.CssProvider()
        # почти невидимый фон: полностью прозрачную поверхность GTK/композитор
        # могут не отрисовать и не показать. В отладке заливаем красным.
        bg = "rgba(255,0,0,0.25)" if DEBUG else "rgba(0,0,0,0.005)"
        css.load_from_string("window.wiggle { background: %s; }" % bg)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        regions = load()
        dbg("display:", type(Gdk.Display.get_default()).__name__,
            "| layer-shell supported:", LS.is_supported(), "| regions:", regions)
        if not regions:
            print("Нет областей. Сначала: wiggle.py add", file=sys.stderr)
        for x, y, w, h in regions:
            win = Wobble(self, x, y, w, h)
            win.present()
            dbg("window presented", x, y, w, h,
                "| is layer surface:", LS.is_layer_window(win))
        self.hold()


if __name__ == "__main__":
    App().run([])
