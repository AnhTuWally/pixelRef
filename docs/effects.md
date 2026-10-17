# Effects

Any element on the board can have a **stack of effects**: images, notes,
shapes, arrows, pen strokes and plugin types. The `effects` plugin provides
grayscale, invert, brightness/contrast, threshold, posterize, blur and edge
detect, and other plugins can add more.

## Using effects

- **Attribute Editor → Effects**: *Add effect* (by category), then per effect:
  on/off, its settings, **Mix** (how much of it is laid over what's under it),
  move up/down (effects apply top to bottom), reset, remove. Drag a setting's
  field (or its label) left/right to change it while watching the canvas; Esc
  puts it back.
- **Your defaults**: *⋯ → Save as my default for new …* keeps an effect's
  settings for every new effect of that type (kept across restarts);
  right-click one setting to save just that one. **⟲** resets an effect to your
  default; *⋯ → Reset to factory defaults* ignores it.
- **Copy / Paste** puts one element's stack on others. **Mute** keeps a
  stack, with all its settings, but doesn't draw it. **Effects → Show
  Effects** hides every effect at once, for a quick before/after (not saved).
- The canvas menu has **Effects** (add, copy, paste, mute, remove all).
  Command search (`Ctrl+K`) finds every *Add Effect: …*.
- `Shift+G` toggles a grayscale effect on the selected images.
- An image's annotations are never affected by its effects.
- Groups can't have effects yet (filtering their composited result comes
  later).

From scripts (see [commands.md](commands.md)):

```python
pxcmd.effects.add("edge_detect", "Sketch", style="lines", strength=3)
pxcmd.effects.set("Sketch", "edge_detect", threshold=0.2, mix=0.8)
pxcmd.effects.move("Sketch", "edge_detect", 0)
pxcmd.effects.toggle("edge_detect", "Sketch")        # one effect on/off
pxcmd.effects.mute(["Sketch", "Photo"])              # the whole stack
pxcmd.effects.copy("Sketch"); pxcmd.effects.paste(["A", "B"])
pxcmd.effects.list("Sketch")
pxcmd.effects.save_default("Sketch", "edge_detect")  # new edge detects start like this one
pxcmd.effects.reset("Sketch", "edge_detect")         # back to your default (factory=True: ignore it)
pxcmd.effects.clear_default("edge_detect")
```

The stack is the node's `effects` attribute: a list of
`{"id", "type", "enabled", "mix", "params"}`, where `params` holds the values
set on that effect (your defaults included: they're written in when it's
added, never looked up when drawing, so a scene looks the same everywhere);
anything missing is the type's factory default. Effects of a type no plugin
provides stay in the list and in files, and are skipped when drawing.

## Speed and caching

- **Images are processed at their own pixels**, not at the zoom you see them
  at, so a result is the same at every zoom and can be kept. Pictures over 40
  megapixels are processed at 40 megapixels. Other elements are rendered at
  the zoom they're seen at (rounded to a power of two) and processed from that.
- **Cheap effects on small things are processed while drawing.** Heavy
  effects (blur, edge detect) and big images go to background threads.
  Meanwhile the element shows its previous look (or its plain content) with a
  small blue dot. Dragging a setting processes only the last value.
- **Results are reused**: from memory (512 MB), then the disk cache (the
  user's cache folder, 2 GB, oldest removed first), then the **scene file**.
  Saving writes the processed images that are worth keeping (heavy stacks,
  big images) into the scene's `effect_cache` section, as lossless WebP. A
  board opened on another computer, or by someone without the plugin behind
  an effect, still shows the result. *Effects → Keep Processed Effects in
  Scene File* switches that off; *Forget Processed Effects* clears it.
- Moving, scaling or editing an element never reprocesses its effects.
- Exports and *copy as image* wait for every result. Thumbnails and the
  minimap never wait.

### Animated GIFs and WebPs

While an animation plays, only cheap effects run on each frame; heavy ones
are skipped. Paused, the frame on screen gets the whole stack. **Process all
frames** (in the Effects section of an animated image) processes every frame
in the background and keeps the results, so playback shows the full stack.
The section shows the progress.

## Adding effects in a plugin

An effect type is data (headless) plus a processor (Qt):

```python
from pixelref.plugin_api import Attr
from pixelref.plugin_api.contracts import COST_HEAVY, EffectType

PIXELATE = EffectType("pixelate", "Pixelate", (
    Attr("size", "float", 8.0, minimum=1.0, maximum=200.0, doc="Block size in canvas pixels"),
), category="Stylize", cost=COST_HEAVY, version=1)

def activate(ctx):
    ctx.contribute("effects.types", PIXELATE)

def activate_ui(ui):
    from PySide6.QtCore import Qt

    def pixelate(image, params, fx):        # QImage (ARGB32) in, QImage out
        block = max(1.0, params["size"] * fx.pixels_per_unit)
        small = image.scaled(max(1, int(image.width() / block)), max(1, int(image.height() / block)))
        return small.scaled(image.width(), image.height(), Qt.IgnoreAspectRatio, Qt.FastTransformation)

    ui.contribute("effects.processors", ("pixelate", pixelate))
```

- Parameters are `Attr` specs: they're validated and clamped, and they build
  the Attribute Editor rows.
- `cost=COST_HEAVY` (`"heavy"`) sends the effect to the background and keeps
  its results on disk and in scenes; the default, `COST_CHEAP`, runs while
  drawing and on every frame of an animation. `version` is part of the cache
  key: bump it when the algorithm changes.
- Processors run on worker threads. Use `QImage` and `QPainter` on images
  only, never `QPixmap` or widgets. Sizes are in canvas pixels: multiply by
  `fx.pixels_per_unit`. `fx.playing` is true during animation playback.
- A processor that needs numpy goes in its own plugin with `[python]
  requires = ["numpy"]`, and imports numpy inside the function (see
  [plugins.md](plugins.md#python-packages)).
