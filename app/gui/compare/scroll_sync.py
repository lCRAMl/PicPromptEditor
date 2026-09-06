"""Gleichlauf der beiden Editorflaechen.

Der Vorgaenger rechnete anteilig um:

    ziel = ziel_min + runde(wert / quell_reichweite * ziel_reichweite)

Das ist an genau zwei Punkten richtig - ganz oben und ganz unten - und
dazwischen falsch. Waren die beiden Dokumente unterschiedlich hoch, wanderte
die eine Seite mit jedem Bildlauf weiter gegen die andere. Oben buendig, nach
etwas Scrollen nicht mehr: das war der gemeldete Fehler.

Die Loesung liegt nicht hier, sondern in `row_heights.py`. Sind beide
Dokumente Zeile fuer Zeile gleich hoch, ist der Gleichlauf eine schlichte
Uebernahme desselben Pixelwerts. Diese Datei muss dann nur noch zwei Dinge
koennen:

1. den Wert unveraendert weiterreichen,
2. die Rueckkopplung verhindern - ohne Sperre loest das Setzen des einen
   Rollbalkens das Setzen des anderen aus, und beide schaukeln sich auf.

Der anteilige Weg bleibt als Notnagel: solange der Hoehenausgleich noch nicht
gelaufen ist (unmittelbar nach dem Setzen des Textes, vor dem ersten Layout),
sind die Reichweiten verschieden. Dann ist anteilig immer noch besser als gar
nichts - aber es ist ausdruecklich der Ausnahmefall, nicht der Regelfall.
"""

from __future__ import annotations

from contextlib import contextmanager


def synchronized_value(
    value: int,
    source_max: int,
    target_max: int,
    *,
    source_min: int = 0,
    target_min: int = 0,
) -> int:
    """Die Rollposition der Gegenseite.

    Gleiche Reichweite - der Regelfall nach dem Hoehenausgleich - bedeutet
    dieselbe Zahl. Kein Runden, keine Fliesskommarechnung, kein Fehler, der
    sich ueber viele Aufrufe aufsummieren koennte.

    Unterschiedliche Reichweiten kommen nur vor, solange das Layout noch nicht
    fertig ist. Dann wird anteilig gerechnet.
    """
    if source_min == target_min and source_max == target_max:
        return value

    reichweite = source_max - source_min
    if reichweite <= 0:
        return target_min
    anteil = (value - source_min) / reichweite
    return target_min + round(anteil * (target_max - target_min))


class ScrollLink:
    """Verbindet zwei Rollbalkenpaare gegen Rueckkopplung.

    Die Sperre ist ein einzelnes Merkzeichen fuer beide Richtungen. Zwei
    getrennte Sperren waeren die naheliegende Loesung und wuerden nicht
    funktionieren: setzt A gerade B, loest B seinerseits das Setzen von A aus,
    und mit getrennten Sperren ist dieser zweite Weg nicht gesperrt.
    """

    def __init__(self) -> None:
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def transfer(self, source, target, value: int) -> bool:
        """Uebertraegt eine Position. Liefert, ob tatsaechlich uebertragen wurde."""
        if self._busy:
            return False
        self._busy = True
        try:
            target.setValue(
                synchronized_value(
                    value,
                    source.maximum(),
                    target.maximum(),
                    source_min=source.minimum(),
                    target_min=target.minimum(),
                )
            )
        finally:
            self._busy = False
        return True

    @contextmanager
    def held(self):
        """Sperrt fuer die Dauer eines Blocks.

        Fuer Uebertragungen, die nicht ueber `transfer` laufen - etwa das
        waagerechte Scrollen, das absolut uebernommen wird und nicht anteilig.
        Ist bereits gesperrt, wird der Block uebersprungen; der Aufrufer prueft
        das ueber das gelieferte Kennzeichen.
        """
        if self._busy:
            yield False
            return
        self._busy = True
        try:
            yield True
        finally:
            self._busy = False

    def release(self) -> None:
        """Gibt die Sperre frei.

        Notausstieg. Bleibt sie durch eine Ausnahme im Empfaenger haengen,
        waere der Gleichlauf fuer den Rest der Sitzung tot - ohne dass etwas
        darauf hinwiese.
        """
        self._busy = False
