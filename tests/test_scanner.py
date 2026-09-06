"""Tests von Scanner, Paarbildung und inkrementellem Abgleich."""

from __future__ import annotations

import os
import time

import pytest
from PIL import Image

from app.core.cancellation import CancellationToken
from app.filesystem.nas_health import BreakerState, NasHealthMonitor
from app.filesystem.scanner import Scanner, thumbnail_key
from app.repositories.folder_repo import FolderRepository
from app.repositories.image_repo import ImageRepository
from app.repositories.prompt_repo import PromptRepository, escape_fts_query


def make_pair(folder, stem: str, text: str = "warm golden sunlight", ext: str = ".jpg") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), (120, 90, 60)).save(folder / (stem + ext))
    (folder / (stem + ".txt")).write_text(text, encoding="utf-8")


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "lib"
    make_pair(root / "a", "one", "warm golden sunlight dominates the scene")
    make_pair(root / "a", "two", "cold blue moonlight dominates the scene")
    make_pair(root / "b", "three", "cinematic editorial portrait")
    return root


@pytest.fixture
def scan_setup(database, writer, reporter):
    folders = FolderRepository(database, writer)
    images = ImageRepository(database, writer)
    prompts = PromptRepository(database, writer)
    health = NasHealthMonitor(reporter)
    scanner = Scanner(folders, images, prompts, reporter, health)
    return scanner, folders, images, prompts, health


class TestThumbnailKey:
    def test_changes_when_the_file_changes(self) -> None:
        """Die Cache-Invalidierung haengt allein an diesem Schluessel."""
        base = thumbnail_key("Z:/lib/a.jpg", 1000, 111)
        assert thumbnail_key("Z:/lib/a.jpg", 1000, 111) == base
        assert thumbnail_key("Z:/lib/a.jpg", 1001, 111) != base
        assert thumbnail_key("Z:/lib/a.jpg", 1000, 112) != base
        assert thumbnail_key("Z:/lib/b.jpg", 1000, 111) != base

    def test_is_case_insensitive_on_the_path(self) -> None:
        assert thumbnail_key("Z:/Lib/A.jpg", 5, 5) == thumbnail_key("z:/lib/a.jpg", 5, 5)


class TestFirstScan:
    def test_indexes_pairs(self, scan_setup, library) -> None:
        scanner, folders, images, prompts, _ = scan_setup
        stats = scanner.scan_root(str(library), CancellationToken())

        assert images.count() == 3
        assert prompts.count() == 3
        assert stats.new_images == 6  # drei Bilder und drei Prompts
        assert stats.changed == 0

    def test_builds_the_folder_tree(self, scan_setup, library) -> None:
        scanner, folders, _images, _prompts, _ = scan_setup
        scanner.scan_root(str(library), CancellationToken())

        nodes = folders.all_folders()
        names = {n.name for n in nodes}
        assert {"lib", "a", "b"} <= names

        roots = folders.roots()
        assert len(roots) == 1 and roots[0].name == "lib"

        by_name = {n.name: n for n in nodes}
        assert by_name["a"].parent_id == by_name["lib"].id, "Elternbeziehung muss stimmen"
        assert by_name["a"].image_count == 2

    def test_links_image_and_prompt(self, scan_setup, library, database) -> None:
        scanner, _folders, _images, _prompts, _ = scan_setup
        scanner.scan_root(str(library), CancellationToken())

        rows = database.query(
            "SELECT filename, prompt_id, pair_state FROM images ORDER BY filename"
        )
        assert all(r["prompt_id"] is not None for r in rows)
        assert all(r["pair_state"] == "complete" for r in rows)

    def test_prompt_text_is_stored_locally(self, scan_setup, library, database) -> None:
        """Editor, Suche und Vergleich duerfen nie eine Promptdatei vom NAS lesen."""
        scanner, _f, _i, prompts, _ = scan_setup
        scanner.scan_root(str(library), CancellationToken())

        texts = [r["prompt_text"] for r in database.query("SELECT prompt_text FROM prompts")]
        assert any("moonlight" in t for t in texts)

    def test_descendant_ids_covers_subtree(self, scan_setup, library) -> None:
        scanner, folders, _i, _p, _ = scan_setup
        scanner.scan_root(str(library), CancellationToken())
        root = folders.roots()[0]
        assert len(folders.descendant_ids(root.id)) == 3


class TestOrphans:
    def test_image_without_prompt_is_kept(self, scan_setup, tmp_path, database) -> None:
        """Spezifikation 08: ein fehlender Prompt darf das Bild nicht kosten."""
        root = tmp_path / "lib"
        root.mkdir()
        Image.new("RGB", (8, 8), (1, 2, 3)).save(root / "lonely.jpg")

        scanner, _f, images, _p, _ = scan_setup
        scanner.scan_root(str(root), CancellationToken())

        assert images.count() == 1
        rows = database.query("SELECT kind FROM v_orphans")
        assert [r["kind"] for r in rows] == ["image_only"]

    def test_prompt_without_image_is_kept(self, scan_setup, tmp_path, database) -> None:
        root = tmp_path / "lib"
        root.mkdir()
        (root / "lonely.txt").write_text("a prompt without a picture", encoding="utf-8")

        scanner, _f, _i, prompts, _ = scan_setup
        scanner.scan_root(str(root), CancellationToken())

        assert prompts.count() == 1
        rows = database.query("SELECT kind FROM v_orphans")
        assert [r["kind"] for r in rows] == ["prompt_only"]

    def test_two_images_share_one_prompt(self, scan_setup, tmp_path, database) -> None:
        """bild.jpg und bild.png neben einem bild.txt - keine Datei geht verloren."""
        root = tmp_path / "lib"
        root.mkdir()
        Image.new("RGB", (8, 8), (1, 2, 3)).save(root / "twin.jpg")
        Image.new("RGB", (8, 8), (3, 2, 1)).save(root / "twin.png")
        (root / "twin.txt").write_text("shared prompt", encoding="utf-8")

        scanner, _f, images, _p, _ = scan_setup
        scanner.scan_root(str(root), CancellationToken())

        assert images.count() == 2
        rows = database.query("SELECT prompt_id FROM images")
        assert all(r["prompt_id"] is not None for r in rows)


class TestIncrementalScan:
    def test_second_scan_reports_nothing(self, scan_setup, library) -> None:
        scanner, *_ = scan_setup
        scanner.scan_root(str(library), CancellationToken())
        second = scanner.scan_root(str(library), CancellationToken())
        assert second.new_images == 0
        assert second.changed == 0
        assert second.removed == 0

    def test_modified_prompt_is_detected(self, scan_setup, library, database) -> None:
        scanner, *_ = scan_setup
        scanner.scan_root(str(library), CancellationToken())

        target = library / "a" / "one.txt"
        target.write_text("a completely different prompt", encoding="utf-8")
        _bump_mtime(target)

        stats = scanner.scan_root(str(library), CancellationToken())
        assert stats.changed == 1
        assert stats.new_images == 0
        stored = database.scalar(
            "SELECT prompt_text FROM prompts WHERE filename = 'one.txt'"
        )
        assert stored == "a completely different prompt"

    def test_deleted_file_is_deactivated_not_removed(
        self, scan_setup, library, database
    ) -> None:
        """Spezifikation 03 will die Historie behalten."""
        scanner, _f, images, _p, _ = scan_setup
        scanner.scan_root(str(library), CancellationToken())
        (library / "a" / "one.jpg").unlink()

        stats = scanner.scan_root(str(library), CancellationToken())
        assert stats.removed == 1
        assert images.count(present_only=True) == 2
        assert images.count(present_only=False) == 3, "Datensatz bleibt erhalten"

    def test_new_file_is_picked_up(self, scan_setup, library) -> None:
        scanner, _f, images, _p, _ = scan_setup
        scanner.scan_root(str(library), CancellationToken())
        make_pair(library / "b", "four", "a new sentence entirely")

        stats = scanner.scan_root(str(library), CancellationToken())
        assert stats.new_images == 2  # Bild plus Prompt
        assert images.count() == 4

    def test_mtime_tolerance_prevents_false_positives(
        self, database, writer, reporter, library
    ) -> None:
        """SMB rundet Zeitstempel teils auf zwei Sekunden.

        Ohne Toleranz meldet jeder Scan Aenderungen, die keine sind, und loest
        unnoetige Neuberechnungen aus.
        """
        folders = FolderRepository(database, writer)
        images = ImageRepository(database, writer)
        prompts = PromptRepository(database, writer)
        scanner = Scanner(
            folders, images, prompts, reporter, NasHealthMonitor(reporter),
            mtime_tolerance_s=2.0,
        )
        scanner.scan_root(str(library), CancellationToken())

        target = library / "a" / "one.jpg"
        info = target.stat()
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns + 1_500_000_000))

        stats = scanner.scan_root(str(library), CancellationToken())
        assert stats.changed == 0, "1,5 s Abweichung liegt in der Toleranz"

    def test_size_change_is_always_detected(self, scan_setup, library) -> None:
        """Der Zeitstempel toleriert, die Groesse nicht."""
        scanner, *_ = scan_setup
        scanner.scan_root(str(library), CancellationToken())
        target = library / "a" / "one.txt"
        info = target.stat()
        target.write_text("x" * 500, encoding="utf-8")
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))

        assert scanner.scan_root(str(library), CancellationToken()).changed == 1


class TestCancellation:
    def test_cancelled_scan_stops_early(self, scan_setup, library) -> None:
        scanner, *_ = scan_setup
        token = CancellationToken()
        token.cancel()
        stats = scanner.scan_root(str(library), token)
        assert stats.cancelled


class TestNasHealth:
    def test_breaker_opens_after_repeated_failures(self, reporter) -> None:
        health = NasHealthMonitor(reporter, failure_threshold=3, cooldown_s=60.0)
        health.register_root(r"\\nas\share")

        for _ in range(3):
            health.record_failure(r"\\nas\share\folder\file.jpg", OSError("weg"))

        assert health.state_of(r"\\nas\share\x") is BreakerState.OPEN
        assert not health.is_available(r"\\nas\share\x")

    def test_guard_fails_fast_when_open(self, reporter) -> None:
        """Der Sinn des Breakers: kein Systemaufruf, der zwanzig Sekunden haengt."""
        from app.filesystem.nas_health import TargetUnavailableError

        health = NasHealthMonitor(reporter, failure_threshold=1)
        health.register_root(r"\\nas\share")
        health.record_failure(r"\\nas\share\a.jpg", OSError("weg"))

        started = time.perf_counter()
        with pytest.raises(TargetUnavailableError):
            health.guard(r"\\nas\share\a.jpg")
        assert time.perf_counter() - started < 0.05

    def test_success_closes_the_breaker(self, reporter) -> None:
        health = NasHealthMonitor(reporter, failure_threshold=1)
        health.register_root(r"\\nas\share")
        health.record_failure(r"\\nas\share\a.jpg", OSError("weg"))
        health.record_success(r"\\nas\share\a.jpg")
        assert health.state_of(r"\\nas\share\a.jpg") is BreakerState.CLOSED

    def test_half_open_after_cooldown(self, reporter) -> None:
        health = NasHealthMonitor(reporter, failure_threshold=1, cooldown_s=0.01)
        health.register_root(r"\\nas\share")
        health.record_failure(r"\\nas\share\a.jpg", OSError("weg"))
        time.sleep(0.03)
        assert health.state_of(r"\\nas\share\a.jpg") is BreakerState.HALF_OPEN

    def test_unknown_paths_are_not_blocked(self, reporter) -> None:
        health = NasHealthMonitor(reporter)
        assert health.is_available(r"C:\lokal\datei.jpg")

    def test_scan_of_unavailable_target_does_not_raise(self, scan_setup, tmp_path) -> None:
        scanner, _f, images, _p, health = scan_setup
        missing = tmp_path / "gibt-es-nicht"
        stats = scanner.scan_root(str(missing), CancellationToken())
        assert stats.errors >= 1
        assert images.count() == 0


class TestFtsEscaping:
    @pytest.mark.parametrize(
        "raw",
        ['cartoon" OR (', "a:b", "-negative", "*", "((()))", "he said \"hi\"", "c'est"],
    )
    def test_dangerous_input_never_breaks_the_query(
        self, scan_setup, library, raw
    ) -> None:
        """Ein roher Suchstring ist keine gueltige FTS5-Syntax."""
        scanner, _f, _i, prompts, _ = scan_setup
        scanner.scan_root(str(library), CancellationToken())
        assert isinstance(prompts.search_ids(raw), list)

    def test_words_become_prefix_phrases(self) -> None:
        assert escape_fts_query("warm golden") == '"warm"* "golden"*'

    def test_empty_input_yields_empty_expression(self) -> None:
        assert escape_fts_query("   ") == ""
        assert escape_fts_query("((") == ""

    def test_search_finds_indexed_text(self, scan_setup, library) -> None:
        scanner, _f, _i, prompts, _ = scan_setup
        scanner.scan_root(str(library), CancellationToken())
        assert len(prompts.search_ids("moonlight")) == 1
        assert len(prompts.search_ids("dominates")) == 2
        assert prompts.search_ids("nichtvorhanden") == []


def _bump_mtime(path) -> None:
    """Setzt den Zeitstempel weit genug vor, um die Toleranz zu ueberschreiten."""
    info = path.stat()
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000_000))
