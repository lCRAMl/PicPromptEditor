"""Erzeugung von Vorschaubildern mit Pillow.

Drei Optimierungen, die in Spezifikation 07 nicht stehen und je Bild deutlich
zaehlen:

* Ein NAS-Zugriff statt drei. Das Original wird einmal gelesen, daraus die
  groesste Stufe erzeugt und die kleineren im Speicher heruntergerechnet.
* Image.draft() bei JPEG. Der Decoder liefert direkt eine reduzierte Aufloesung,
  was den Dekodiervorgang um Faktor vier bis acht verkuerzt. Fuer ein
  Vorschaubild ist die volle Aufloesung ohnehin verschenkt.
* ImageOps.exif_transpose(). Ohne das stehen mit dem Handy oder gedreht
  aufgenommene Bilder im Grid falsch herum, waehrend der Windows-Viewer sie
  korrekt zeigt.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps

from app.core.paths import long_path

# Pillow warnt bei sehr grossen Bildern und bricht bei Verdacht auf eine
# Dekompressionsbombe ab. Fuer eine Bibliothek generierter Bilder ist die
# Voreinstellung zu streng; 512 Megapixel bleiben trotzdem eine Grenze.
Image.MAX_IMAGE_PIXELS = 512_000_000


@dataclass(frozen=True)
class RenderedThumbnail:
    size_px: int
    data: bytes
    width: int
    height: int


@dataclass(frozen=True)
class RenderResult:
    thumbnails: tuple[RenderedThumbnail, ...]
    source_width: int
    source_height: int


class ThumbnailRenderer:
    def __init__(self, image_format: str = "webp", quality: int = 82) -> None:
        self._format = image_format.upper()
        self._quality = quality

    def render(self, source_path: str, sizes: tuple[int, ...]) -> RenderResult:
        """Erzeugt alle angeforderten Groessenstufen aus einem einzigen Lesevorgang.

        Wirft OSError, wenn die Datei fehlt oder beschaedigt ist. Der Aufrufer
        markiert das Bild dann als ungueltig, statt den Job scheitern zu lassen.
        """
        if not sizes:
            return RenderResult((), 0, 0)
        ordered = tuple(sorted(set(sizes), reverse=True))
        largest = ordered[0]

        with Image.open(long_path(source_path)) as source:
            source_width, source_height = source.size
            # draft() ist nur bei JPEG wirksam und wird sonst ignoriert.
            source.draft("RGB", (largest, largest))
            image = ImageOps.exif_transpose(source)
            if image is None:
                image = source
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")

            base = image.copy()

        base.thumbnail((largest, largest), Image.Resampling.LANCZOS)

        rendered: list[RenderedThumbnail] = []
        current = base
        for size in ordered:
            if current.width > size or current.height > size:
                current = current.copy()
                current.thumbnail((size, size), Image.Resampling.LANCZOS)
            rendered.append(
                RenderedThumbnail(size, self._encode(current), current.width, current.height)
            )
        return RenderResult(tuple(rendered), source_width, source_height)

    def _encode(self, image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        params: dict[str, object] = {"quality": self._quality}
        if self._format == "WEBP":
            params["method"] = 4
        elif self._format == "JPEG" and image.mode == "RGBA":
            image = image.convert("RGB")
        image.save(buffer, format=self._format, **params)
        return buffer.getvalue()
