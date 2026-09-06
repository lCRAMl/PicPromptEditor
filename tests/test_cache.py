"""Tests von LRU-Caches, Thumbnail-Cache und Renderer."""

from __future__ import annotations

import io
import threading

import pytest
from PIL import Image

from app.cache.lru import LruCache, SizedLruCache
from app.core.paths import shard_path
from app.filesystem.thumbnail_cache import ThumbnailCache
from app.filesystem.thumbnail_renderer import ThumbnailRenderer


class TestLruCache:
    def test_evicts_least_recently_used(self) -> None:
        cache = LruCache[str, int](2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a")          # a wird damit zum juengsten Eintrag
        cache.put("c", 3)       # b muss weichen
        assert cache.get("a") == 1
        assert cache.get("b") is None
        assert cache.get("c") == 3

    def test_hit_rate(self) -> None:
        cache = LruCache[str, int](4)
        cache.put("a", 1)
        cache.get("a")
        cache.get("b")
        assert cache.hit_rate == 0.5

    def test_shrinking_capacity_drops_entries(self) -> None:
        cache = LruCache[int, int](10)
        for i in range(10):
            cache.put(i, i)
        cache.set_capacity(3)
        assert len(cache) == 3

    def test_zero_capacity_stores_nothing(self) -> None:
        cache = LruCache[str, int](0)
        cache.put("a", 1)
        assert cache.get("a") is None


class TestSizedLruCache:
    def test_evicts_by_total_size(self) -> None:
        """Bei Vorschaubildern ist die Anzahl kein sinnvolles Mass."""
        cache = SizedLruCache[str, bytes](100, len)
        cache.put("a", b"x" * 60)
        cache.put("b", b"y" * 60)  # zusammen 120 > 100, a muss weichen
        assert cache.get("a") is None
        assert cache.get("b") is not None
        assert cache.evictions == 1

    def test_oversized_entry_is_refused(self) -> None:
        """Ein Eintrag, der den ganzen Cache sprengt, darf nicht alles verdraengen."""
        cache = SizedLruCache[str, bytes](50, len)
        cache.put("small", b"x" * 10)
        cache.put("huge", b"y" * 500)
        assert cache.get("huge") is None
        assert cache.get("small") is not None

    def test_replacing_updates_the_size(self) -> None:
        cache = SizedLruCache[str, bytes](1000, len)
        cache.put("a", b"x" * 100)
        cache.put("a", b"x" * 10)
        assert cache.current_bytes == 10

    def test_lowering_the_limit_evicts(self) -> None:
        cache = SizedLruCache[int, bytes](1000, len)
        for i in range(10):
            cache.put(i, b"x" * 100)
        cache.set_max_bytes(250)
        assert cache.current_bytes <= 250

    def test_is_thread_safe(self) -> None:
        cache = SizedLruCache[int, bytes](1_000_000, len)

        def worker(base: int) -> None:
            for i in range(300):
                cache.put(base * 1000 + i, b"x" * 50)
                cache.get(base * 1000 + i)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert cache.current_bytes <= 1_000_000


class TestThumbnailCache:
    def test_sharded_layout(self, tmp_path, reporter) -> None:
        """Hunderttausend Dateien in einem Verzeichnis bremsen Windows aus."""
        cache = ThumbnailCache(tmp_path, reporter)
        relative = cache.relative_path("a1b2c3d4e5", 256)
        assert relative == shard_path("a1b2c3d4e5") + "_256.webp"
        assert relative.startswith("a1/b2/")

    def test_store_and_load(self, tmp_path, reporter) -> None:
        cache = ThumbnailCache(tmp_path, reporter)
        cache.store("key123456", 256, b"payload")
        assert cache.exists("key123456", 256)
        assert cache.load("key123456", 256) == b"payload"

    def test_sizes_are_separate_files(self, tmp_path, reporter) -> None:
        cache = ThumbnailCache(tmp_path, reporter)
        cache.store("key123456", 128, b"small")
        cache.store("key123456", 512, b"large")
        assert cache.load("key123456", 128) == b"small"
        assert cache.load("key123456", 512) == b"large"

    def test_missing_entry_returns_none(self, tmp_path, reporter) -> None:
        cache = ThumbnailCache(tmp_path, reporter)
        assert cache.load("gibtsnicht", 256) is None

    def test_store_leaves_no_temporary_file(self, tmp_path, reporter) -> None:
        """Atomar schreiben: ein Abbruch darf keine halbe Datei hinterlassen."""
        cache = ThumbnailCache(tmp_path, reporter)
        cache.store("key123456", 256, b"payload")
        assert not list(tmp_path.rglob("*.tmp"))

    def test_delete(self, tmp_path, reporter) -> None:
        cache = ThumbnailCache(tmp_path, reporter)
        cache.store("key123456", 256, b"payload")
        assert cache.delete("key123456", 256)
        assert not cache.exists("key123456", 256)
        assert not cache.delete("key123456", 256)


class TestThumbnailRenderer:
    @staticmethod
    def _image(tmp_path, name: str, size=(800, 600), mode: str = "RGB"):
        path = tmp_path / name
        Image.new(mode, size, (200, 100, 50)).save(path)
        return path

    def test_renders_all_sizes_from_one_read(self, tmp_path) -> None:
        renderer = ThumbnailRenderer("webp", 80)
        source = self._image(tmp_path, "a.jpg")
        result = renderer.render(str(source), (128, 256, 512))

        assert [t.size_px for t in result.thumbnails] == [512, 256, 128]
        assert result.source_width == 800
        for thumbnail in result.thumbnails:
            assert thumbnail.data
            assert max(thumbnail.width, thumbnail.height) <= thumbnail.size_px

    def test_smaller_sizes_are_progressively_smaller(self, tmp_path) -> None:
        renderer = ThumbnailRenderer("webp", 80)
        result = renderer.render(str(self._image(tmp_path, "a.jpg")), (128, 512))
        by_size = {t.size_px: t for t in result.thumbnails}
        assert by_size[128].width < by_size[512].width

    def test_aspect_ratio_is_kept(self, tmp_path) -> None:
        renderer = ThumbnailRenderer("webp", 80)
        result = renderer.render(str(self._image(tmp_path, "wide.jpg", (1000, 250))), (256,))
        thumbnail = result.thumbnails[0]
        assert thumbnail.width == 256
        assert thumbnail.height == 64

    def test_output_is_webp(self, tmp_path) -> None:
        renderer = ThumbnailRenderer("webp", 80)
        result = renderer.render(str(self._image(tmp_path, "a.png")), (128,))
        with Image.open(io.BytesIO(result.thumbnails[0].data)) as decoded:
            assert decoded.format == "WEBP"

    def test_png_with_alpha_survives(self, tmp_path) -> None:
        renderer = ThumbnailRenderer("webp", 80)
        path = tmp_path / "alpha.png"
        Image.new("RGBA", (300, 300), (10, 20, 30, 128)).save(path)
        assert renderer.render(str(path), (128,)).thumbnails[0].data

    def test_exif_orientation_is_applied(self, tmp_path) -> None:
        """Ohne exif_transpose stehen gedrehte Aufnahmen im Grid falsch herum."""
        path = tmp_path / "rotated.jpg"
        image = Image.new("RGB", (400, 200), (10, 20, 30))
        exif = image.getexif()
        exif[274] = 6  # Orientation: 90 Grad im Uhrzeigersinn
        image.save(path, exif=exif)

        result = ThumbnailRenderer("webp", 80).render(str(path), (256,))
        thumbnail = result.thumbnails[0]
        assert thumbnail.height > thumbnail.width, "Bild muss gedreht worden sein"

    def test_corrupt_file_raises_oserror(self, tmp_path) -> None:
        """Der Aufrufer markiert das Bild dann als ungueltig, statt zu scheitern."""
        path = tmp_path / "kaputt.jpg"
        path.write_bytes(b"das ist kein Bild")
        with pytest.raises((OSError, ValueError)):
            ThumbnailRenderer("webp", 80).render(str(path), (128,))

    def test_no_sizes_yields_nothing(self, tmp_path) -> None:
        result = ThumbnailRenderer("webp", 80).render(str(self._image(tmp_path, "a.jpg")), ())
        assert result.thumbnails == ()
