"""``pyfsr connector icon`` / :mod:`pyfsr.connector_icon`: SVG -> the connector's two icon PNGs."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

Image = pytest.importorskip("PIL.Image")

from pyfsr.cli.__main__ import main  # noqa: E402
from pyfsr.connector_icon import LARGE, SMALL, build_icon, make_connector_icons, rasterize_svg  # noqa: E402

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    '<rect x="2" y="1" width="6" height="8" fill="#307fe2"/></svg>'
)


def _draw(svg: Path, png: Path, size: int) -> None:
    """A stand-in rasteriser: a blue block on white, ignoring the SVG's content."""
    img = Image.new("RGB", (size, size), (255, 255, 255))
    img.paste((48, 127, 226), (size // 4, size // 10, size * 3 // 4, size * 9 // 10))
    img.save(png)


def _connector(tmp_path: Path, *, with_wordmark: bool) -> Path:
    root = tmp_path / "conn"
    (root / "images").mkdir(parents=True)
    (root / "info.json").write_text(json.dumps({"icon_large_name": "x_large.png", "icon_small_name": "x_small.png"}))
    if with_wordmark:
        for spec, name in ((LARGE, "x_large.png"), (SMALL, "x_small.png")):
            old = Image.new("RGB", (spec.size, spec.size), (255, 255, 255))
            (top, bottom), (left, right) = spec.wordmark_rows, spec.wordmark_cols
            old.paste((20, 20, 20), (left, top, right, bottom))
            old.save(root / "images" / name)
    return root


def test_writes_both_icons_at_the_sizes_info_json_names(tmp_path):
    root = _connector(tmp_path, with_wordmark=False)
    written = make_connector_icons(tmp_path / "a.svg", root, rasterizer=_draw)
    assert [p.name for p in written] == ["x_large.png", "x_small.png"]
    assert Image.open(root / "images/x_large.png").size == (80, 80)
    assert Image.open(root / "images/x_small.png").size == (32, 32)


def test_wordmark_is_copied_from_the_existing_icons_and_survives_a_rerun(tmp_path):
    root = _connector(tmp_path, with_wordmark=True)
    make_connector_icons(tmp_path / "a.svg", root, rasterizer=_draw)
    first = Image.open(root / "images/x_large.png").convert("RGB")
    (top, bottom), (left, right) = LARGE.wordmark_rows, LARGE.wordmark_cols
    assert first.getpixel(((left + right) // 2, (top + bottom) // 2)) == (20, 20, 20)
    # Artwork sits above the wordmark, centred, not over it.
    assert first.getpixel((40, 30)) == (48, 127, 226)
    make_connector_icons(tmp_path / "a.svg", root, rasterizer=_draw)  # stable: the wordmark is still there
    assert Image.open(root / "images/x_large.png").convert("RGB").tobytes() == first.tobytes()


def test_no_wordmark_centres_the_artwork(tmp_path):
    root = _connector(tmp_path, with_wordmark=True)
    make_connector_icons(tmp_path / "a.svg", root, wordmark=False, rasterizer=_draw)
    icon = Image.open(root / "images/x_large.png").convert("RGB")
    (top, bottom), (left, right) = LARGE.wordmark_rows, LARGE.wordmark_cols
    assert icon.getpixel(((left + right) // 2, (top + bottom) // 2)) != (20, 20, 20)


def test_wordmark_from_another_folder(tmp_path):
    donor = _connector(tmp_path / "donor", with_wordmark=True)
    root = _connector(tmp_path / "new", with_wordmark=False)
    make_connector_icons(tmp_path / "a.svg", root, wordmark_from=donor, rasterizer=_draw)
    (top, bottom), (left, right) = LARGE.wordmark_rows, LARGE.wordmark_cols
    got = Image.open(root / "images/x_large.png").convert("RGB")
    assert got.getpixel(((left + right) // 2, (top + bottom) // 2)) == (20, 20, 20)


def test_blank_render_is_an_error():
    blank = Image.new("RGB", (50, 50), (255, 255, 255))
    with pytest.raises(ValueError, match="blank"):
        build_icon(blank, LARGE)


def test_missing_icon_names_in_info_json(tmp_path):
    root = tmp_path / "c"
    root.mkdir()
    (root / "info.json").write_text("{}")
    with pytest.raises(ValueError, match="icon_large_name"):
        make_connector_icons(tmp_path / "a.svg", root, rasterizer=_draw)


def test_transparent_render_is_flattened_onto_white(tmp_path):
    def draw(svg: Path, png: Path, size: int) -> None:
        Image.new("RGBA", (size, size), (0, 0, 0, 0)).save(png)

    img = rasterize_svg(tmp_path / "a.svg", size=20, rasterizer=draw)
    assert img.getpixel((5, 5)) == (255, 255, 255)


def test_cli_reports_a_missing_info_json(tmp_path, capsys):
    svg = tmp_path / "a.svg"
    svg.write_text(SVG)
    assert main(["connector", "icon", str(svg), "--connector", str(tmp_path / "nope")]) == 1
    assert "error:" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("qlmanage"), reason="needs macOS Quick Look")
def test_cli_end_to_end_with_quicklook(tmp_path, capsys):
    root = _connector(tmp_path, with_wordmark=False)
    svg = tmp_path / "a.svg"
    svg.write_text(SVG)
    assert main(["connector", "icon", str(svg), "--connector", str(root), "--no-wordmark"]) == 0
    assert "wrote" in capsys.readouterr().out
    assert Image.open(root / "images/x_large.png").size == (80, 80)
