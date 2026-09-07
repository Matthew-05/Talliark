"""Build an image-only corpus PDF from SHA-verified public source images."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def _rgb_copy(path: Path) -> Image.Image:
    with Image.open(path) as image:
        converted = image.convert("RGBA") if image.mode in {"RGBA", "LA", "PA"} else image.convert("RGB")
        if converted.mode != "RGBA":
            return converted.copy()
        background = Image.new("RGB", converted.size, "white")
        background.paste(converted, mask=converted.getchannel("A"))
        return background


def build(output: Path, sources: list[Path]) -> None:
    if not sources:
        raise ValueError("At least one source image is required.")

    pages = [_rgb_copy(source) for source in sources]
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        pages[0].save(
            temporary,
            format="PDF",
            resolution=200,
            save_all=len(pages) > 1,
            append_images=pages[1:],
        )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
        for page in pages:
            page.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", action="append", required=True, type=Path)
    args = parser.parse_args()
    build(args.output, args.source)


if __name__ == "__main__":
    main()
