"""Make a FortiSOAR connector's two icon PNGs from an SVG.

A connector ships ``icon_large_name`` (80x80) and ``icon_small_name`` (32x32) PNGs,
named in its ``info.json``. Fortinet's icon library (https://icons.fortinet.com)
hands out SVGs, so this turns one into the pair FortiSOAR wants, laid out like
the stock Fortinet connector icons: the artwork on a white square, with the
FORTINET wordmark beneath it.

    from pyfsr.connector_icon import make_connector_icons
    make_connector_icons("FortiAIGate.svg", "my-connector/")

or ``pyfsr connector icon FortiAIGate.svg --connector my-connector/``.

Two pieces are deliberately not bundled:

* **A rasteriser.** The SVG is drawn by the first of ``rsvg-convert``, ``cairosvg``,
  macOS Quick Look (``qlmanage``) or headless Chrome/Chromium that is installed.
* **The wordmark.** It is Fortinet's trademark, so it is copied from an icon you
  already have (the connector's current PNGs, or ``wordmark_from=``), never shipped
  here. Without one the icon is just the artwork, centred.

Needs Pillow (``pip install 'pyfsr[icons]'``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

__all__ = ["IconSpec", "LARGE", "SMALL", "make_connector_icons", "rasterize_svg", "build_icon"]

# A rasteriser draws an SVG into a PNG of roughly ``size`` px a side.
Rasterizer = Callable[[Path, Path, int], None]

_WHITE = (255, 255, 255)


@dataclass(frozen=True)
class IconSpec:
    """Geometry of one icon size, in its own pixels (matches the stock Fortinet icons)."""

    size: int
    art_top: int
    art_height: int
    # Rows and columns the FORTINET wordmark occupies, as (top, bottom) and (left, right).
    wordmark_rows: tuple[int, int]
    wordmark_cols: tuple[int, int]


LARGE = IconSpec(size=80, art_top=7, art_height=54, wordmark_rows=(66, 74), wordmark_cols=(10, 70))
SMALL = IconSpec(size=32, art_top=2, art_height=21, wordmark_rows=(25, 30), wordmark_cols=(2, 30))


def _pil() -> Image:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError("connector icons need Pillow: pip install 'pyfsr[icons]'") from exc
    return Image  # type: ignore[return-value]


# -- rasterisers ---------------------------------------------------------------


def _rsvg(svg: Path, png: Path, size: int) -> None:
    subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), "-o", str(png), str(svg)], check=True)


def _cairosvg(svg: Path, png: Path, size: int) -> None:
    import cairosvg  # type: ignore[import-not-found]

    cairosvg.svg2png(url=str(svg), write_to=str(png), output_width=size, output_height=size)


def _quicklook(svg: Path, png: Path, size: int) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["qlmanage", "-t", "-s", str(size), "-o", tmp, str(svg)], check=True, capture_output=True)
        made = Path(tmp) / f"{svg.name}.png"
        if not made.is_file():
            raise RuntimeError("qlmanage produced no thumbnail")
        shutil.move(str(made), png)


def _chrome_binary() -> str | None:
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        if found := shutil.which(name):
            return found
    mac = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return str(mac) if mac.is_file() else None


def _chrome(svg: Path, png: Path, size: int) -> None:
    binary = _chrome_binary()
    if binary is None:
        raise RuntimeError("no Chrome/Chromium found")
    subprocess.run(
        [
            binary,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--window-size={size},{size}",
            f"--screenshot={png}",
            svg.resolve().as_uri(),
        ],
        check=True,
        capture_output=True,
    )


def _available() -> list[tuple[str, Rasterizer]]:
    found: list[tuple[str, Rasterizer]] = []
    if shutil.which("rsvg-convert"):
        found.append(("rsvg-convert", _rsvg))
    try:
        import cairosvg  # noqa: F401  # type: ignore[import-not-found]

        found.append(("cairosvg", _cairosvg))
    except Exception:  # missing package, or its native cairo library
        pass
    if sys.platform == "darwin" and shutil.which("qlmanage"):
        found.append(("qlmanage", _quicklook))
    if _chrome_binary():
        found.append(("chrome", _chrome))
    return found


def rasterize_svg(svg: str | Path, *, size: int = 800, rasterizer: Rasterizer | None = None) -> Image:
    """Draw ``svg`` to a ``size``-px RGB image on white, using the first available rasteriser."""
    Image = _pil()
    svg = Path(svg)
    backends: list[tuple[str, Rasterizer]] = [("custom", rasterizer)] if rasterizer else _available()
    if not backends:
        raise RuntimeError(
            "no SVG rasteriser found: install librsvg (rsvg-convert), pip install cairosvg, or use macOS / Chrome"
        )
    errors = []
    for name, draw in backends:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "icon.png"
            try:
                draw(svg, out, size)
                art = Image.open(out)
                art.load()
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                continue
            # Transparent output (rsvg, cairosvg) goes onto white; opaque is unchanged.
            rgba = art.convert("RGBA")
            flat = Image.new("RGB", rgba.size, _WHITE)
            flat.paste(rgba, mask=rgba.split()[3])
            return flat
    raise RuntimeError("could not rasterise the SVG: " + "; ".join(errors))


# -- composition ---------------------------------------------------------------


def _trim(img: Image) -> Image:
    """Crop to the non-white artwork."""
    from PIL import ImageChops

    box = ImageChops.difference(img, _pil().new("RGB", img.size, _WHITE)).getbbox()
    if box is None:
        raise ValueError("the SVG rendered blank")
    return img.crop(box)


def _wordmark(icon_png: Path, spec: IconSpec) -> Image | None:
    """The wordmark strip of an existing icon, or None if there isn't one there."""
    Image = _pil()
    try:
        src = Image.open(icon_png).convert("RGB")
    except (OSError, ValueError):
        return None
    if src.size != (spec.size, spec.size):
        return None
    (top, bottom), (left, right) = spec.wordmark_rows, spec.wordmark_cols
    strip = src.crop((left, top, right, bottom))
    # A blank strip means this icon had no wordmark to take.
    return strip if _trim_or_none(strip) is not None else None


def _trim_or_none(img: Image) -> Image | None:
    try:
        return _trim(img)
    except ValueError:
        return None


def build_icon(art: Image, spec: IconSpec, wordmark: Image | None = None) -> Image:
    """Place the artwork (and wordmark, if given) on a white ``spec.size`` square."""
    Image = _pil()
    art = _trim(art)
    height = spec.art_height if wordmark is not None else spec.size - 2 * spec.art_top
    scaled = art.resize((max(1, round(art.width * height / art.height)), height), Image.LANCZOS)
    if scaled.width > spec.size - 4:  # a very wide mark: fit the width instead
        scaled = art.resize((spec.size - 4, max(1, round(art.height * (spec.size - 4) / art.width))), Image.LANCZOS)
    canvas = Image.new("RGB", (spec.size, spec.size), _WHITE)
    top = spec.art_top if wordmark is not None else (spec.size - scaled.height) // 2
    canvas.paste(scaled, ((spec.size - scaled.width) // 2, top))
    if wordmark is not None:
        canvas.paste(wordmark, (spec.wordmark_cols[0], spec.wordmark_rows[0]))
    return canvas


def _icon_names(connector_dir: Path) -> tuple[str, str]:
    info = json.loads((connector_dir / "info.json").read_text(encoding="utf-8"))
    try:
        return info["icon_large_name"], info["icon_small_name"]
    except KeyError as exc:
        raise ValueError(f"{connector_dir / 'info.json'} has no {exc.args[0]}") from None


def make_connector_icons(
    svg: str | Path,
    connector: str | Path,
    *,
    wordmark_from: str | Path | None = None,
    wordmark: bool = True,
    rasterizer: Rasterizer | None = None,
) -> list[Path]:
    """Write the large and small icon PNGs for the connector at ``connector``.

    ``connector`` is the connector folder (the one holding ``info.json``); the PNGs
    go to its ``images/`` using the names ``info.json`` gives. The wordmark comes from
    the PNGs already there, or from ``wordmark_from`` (a folder with the same file
    names, or a connector folder), so re-running is stable. ``wordmark=False`` leaves
    it off. Returns the paths written.
    """
    connector = Path(connector)
    large_name, small_name = _icon_names(connector)
    images = connector / "images"
    images.mkdir(exist_ok=True)
    donors = [Path(wordmark_from) if wordmark_from else connector]
    donors = [d / "images" if (d / "images").is_dir() else d for d in donors]

    art = rasterize_svg(svg, rasterizer=rasterizer)
    written: list[Path] = []
    for spec, name in ((LARGE, large_name), (SMALL, small_name)):
        strip = None
        if wordmark:
            strip = next((s for d in donors if (s := _wordmark(d / name, spec)) is not None), None)
        out = images / name
        build_icon(art, spec, strip).save(out)
        written.append(out)
    return written
