"""Ordnerbaum: die Zahl in Klammern kommt aus dem Unterordner "ai".

In dieser Bibliothek liegen 99,6 Prozent aller Prompts in einem Unterordner
"ai"; die Bildzahl des Elternordners zaehlt dagegen Beiwerk. Der Baum zeigt
deshalb die Zahl aus name/ai.

Dazu der neue Kontextmenuepunkt "In neuem Tab oeffnen", der den frueheren
"Inhalt mit Unterordnern anzeigen" ersetzt.
"""

from __future__ import annotations

import pytest
from PIL import Image

from app.dto.models import FolderNode
from app.gui.library.folder_tree import FolderTree
from tests.conftest import pump_until


def node(name: str, *, bilder: int = 0, ai: int = 0, ai_prompts: int = 0) -> FolderNode:
    return FolderNode(
        id=1,
        path="Z:/lib/" + name,
        name=name,
        parent_id=None,
        depth=1,
        image_count=bilder,
        prompt_count=0,
        last_scan=None,
        ai_image_count=ai,
        ai_prompt_count=ai_prompts,
    )


class TestBeschriftung:
    @pytest.fixture
    def baum(self, qapp):
        tree = FolderTree()
        yield tree
        tree.deleteLater()

    def test_zahl_stammt_aus_dem_ai_ordner(self, baum) -> None:
        assert baum._label(node("Alyson", bilder=345, ai=3481)) == "Alyson  (3481)"

    def test_ohne_ai_gilt_die_eigene_zahl(self, baum) -> None:
        """Sonst stuende ausgerechnet bei den ai-Ordnern selbst nichts."""
        assert baum._label(node("ai", bilder=3481)) == "ai  (3481)"

    def test_leerer_ordner_ohne_klammer(self, baum) -> None:
        assert baum._label(node("leer")) == "leer"

    def test_abschaltbar(self, baum) -> None:
        baum.set_show_counts(False)
        assert baum._label(node("Alyson", bilder=345, ai=3481)) == "Alyson"

    def test_tooltip_nennt_beide_zahlen(self, baum) -> None:
        text = baum._tooltip(node("Alyson", bilder=345, ai=3481, ai_prompts=1533))
        assert "ai: 3481 Bilder, 1533 Prompts" in text
        assert "in diesem Ordner: 345 Bilder" in text


class TestKontextmenue:
    def test_der_rekursive_eintrag_ist_weg(self) -> None:
        import app.gui.library.folder_tree as modul

        with open(modul.__file__, encoding="utf-8") as handle:
            quelltext = handle.read()
        assert "open_recursive" not in quelltext
        assert "Inhalt mit Unterordnern" not in quelltext

    def test_neuer_eintrag_vorhanden(self) -> None:
        import app.gui.library.folder_tree as modul

        with open(modul.__file__, encoding="utf-8") as handle:
            assert "In neuem Tab oeffnen" in handle.read()


class TestZahlenAusDerDatenbank:
    def test_repository_liefert_die_ai_zahlen(self, qapp, application, tmp_path) -> None:
        lib = tmp_path / "lib"
        (lib / "Person" / "ai").mkdir(parents=True)
        # Zwei Bilder neben dem ai-Ordner, fuenf Paare darin.
        for index in range(2):
            Image.new("RGB", (16, 16), (10, 20, 30)).save(
                lib / "Person" / ("daneben%d.jpg" % index)
            )
        for index in range(5):
            Image.new("RGB", (16, 16), (index * 40, 80, 140)).save(
                lib / "Person" / "ai" / ("bild%d.jpg" % index)
            )
            (lib / "Person" / "ai" / ("bild%d.txt" % index)).write_text(
                "Prompt %d" % index, encoding="utf-8"
            )

        application.settings.set("library.roots", [str(lib)])
        application.settings.set("index.scan_on_startup", False)
        application.build_services()
        fertig: list = []
        application.index_service.scan_finished.connect(fertig.append)
        application.index_service.start_scan()
        assert pump_until(qapp, lambda: fertig, 60000)

        knoten = {n.name: n for n in application.folders.all_folders()}
        person = knoten["Person"]
        assert person.image_count == 2, "eigene Bilder"
        assert person.ai_image_count == 5, "Bilder im ai-Unterordner"
        assert person.ai_prompt_count == 5

        # Der ai-Ordner selbst hat keinen weiteren ai-Unterordner.
        assert knoten["ai"].ai_image_count == 0
        assert knoten["ai"].image_count == 5

    def test_ohne_ai_unterordner_bleibt_null(self, qapp, application, tmp_path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        Image.new("RGB", (16, 16), (1, 2, 3)).save(lib / "einzeln.jpg")
        application.settings.set("library.roots", [str(lib)])
        application.settings.set("index.scan_on_startup", False)
        application.build_services()
        fertig: list = []
        application.index_service.scan_finished.connect(fertig.append)
        application.index_service.start_scan()
        assert pump_until(qapp, lambda: fertig, 60000)

        wurzel = application.folders.all_folders()[0]
        assert wurzel.ai_image_count == 0
        assert wurzel.image_count == 1
