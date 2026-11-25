# hypr-wiggle

Make any part of your Hyprland desktop wobble like a water drop when you click it —
inspired by the *wiggle* live-wallpaper effect on vivo phones.

Pick a region (say, the branches of a tree in your wallpaper), click it, and it
ripples and jiggles like jelly. Edges are pinned, so the effect blends into the
rest of the picture.

<!-- Add a demo here, e.g.  ![demo](demo.gif)  -->

[Русская версия](README.ru.md)

## How it works

- A transparent [layer-shell](https://wayland.app/protocols/wlr-layer-shell-unstable-v1)
  window sits over your selected region, **above the wallpaper but below normal windows**.
- On click it grabs the region with `grim`, then every frame displaces the pixels
  with a damped travelling wave plus a jelly-like sway (pure NumPy, no OpenCV).
- The result is shown through GTK4 and fades out when the motion dies down.
- Several clicks stack: each one adds a new ripple.

## Requirements

Arch Linux (other distros: install the equivalents), Hyprland (any wlroots-style
compositor with layer-shell should work), Python 3.

```sh
sudo pacman -S gtk4-layer-shell python-gobject python-numpy grim slurp
```

## Install

```sh
git clone https://github.com/YOUR_USER/hypr-wiggle.git
cd hypr-wiggle
install -Dm755 wiggle.py ~/.local/bin/wiggle.py
```

Make sure `~/.local/bin` is in your `PATH`.

## Usage

```sh
wiggle.py add      # select a region with the mouse (repeat for more regions)
wiggle.py list     # show saved regions
wiggle.py clear    # remove all regions
wiggle.py          # run: left-click a region to wobble it
```

Regions are stored in `~/.config/wiggle/regions.json`.

Autostart in `hyprland.conf` (after your wallpaper):

```ini
exec-once = python ~/.local/bin/wiggle.py
```

## Tuning

Constants at the top of `wiggle.py`:

| Name | Meaning |
|------|---------|
| `AMP` / `SWAY` | ripple / jelly amplitude (px) |
| `SPEED`, `WAVELEN` | wave speed and wavelength |
| `TAU`, `TAU2`, `DUR` | decay times and total duration |
| `FREQ` | sway frequency (Hz) |
| `EDGE` | width of the pinned border (fraction of the smaller side) |
| `MAX_PIXELS` | big regions are processed at reduced size to stay smooth; lower it if it lags |
| `STEP` | displacement grid step; larger is faster but coarser |

**Tip:** select only the part you want to move (e.g. just the branches). The cost
per frame grows with the area.

## Limitations

- Clicks reach the effect only where **no window covers the region** (it lives below windows).
- Assumes a **single monitor**: region coordinates are used as offsets on the focused output.
- The region is a snapshot taken on click, so animated wallpapers freeze inside it
  while it wobbles (it fades in and out to hide the jump).
- Software rendering (e.g. a VM without 3D acceleration) is slower; use a smaller region.
- Developed and tested on Arch Linux + Hyprland in a virtual machine.

## Troubleshooting

Run with debug output (the region is tinted red so you can see the layer):

```sh
WIGGLE_DEBUG=1 wiggle.py
hyprctl layers | grep -B1 -A3 wiggle   # should list namespace: wiggle in the bottom layer
```

- `Namespace Gtk4LayerShell not available` — install `gtk4-layer-shell`.
- `libgtk4-layer-shell.so cannot be preloaded` — fix `LAYER_LIB` in the script to the
  path shown by `pacman -Ql gtk4-layer-shell | grep '\.so'`.

## License

MIT
