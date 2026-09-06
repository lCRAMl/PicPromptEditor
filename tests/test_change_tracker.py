"""Die Entscheidungslogik der Ueberwachung - ohne Qt und ohne Wartezeiten.

Der Tracker bekommt die Zeit uebergeben, statt sie zu lesen. Deshalb laesst
sich hier ein Vierteljahr Betrieb in Millisekunden pruefen, und kein Test haengt
an einer echten Uhr.
"""

from __future__ import annotations

from app.filesystem.change_tracker import ChangeTracker, SyncReason


def make(**kwargs) -> ChangeTracker:
    defaults = dict(debounce_seconds=1.5, periodic_seconds=900.0, watcher_enabled=True)
    defaults.update(kwargs)
    return ChangeTracker(**defaults)


# Takt -------------------------------------------------------------------------


def test_erster_takt_ist_sofort_faellig():
    tracker = make()
    request = tracker.due(0.0)
    assert request is not None
    assert request.reason is SyncReason.PERIODIC


def test_takt_wird_erst_nach_ablauf_wieder_faellig():
    tracker = make(periodic_seconds=900.0)
    tracker.reset(1000.0)

    assert tracker.due(1000.0) is None
    assert tracker.due(1899.0) is None
    assert tracker.due(1900.0) is not None


def test_takt_abschaltbar():
    tracker = make(periodic_seconds=0.0)
    tracker.reset(0.0)
    assert tracker.due(10_000.0) is None
    assert tracker.seconds_until_periodic(0.0) == float("inf")


def test_restzeit_bis_zum_takt():
    tracker = make(periodic_seconds=900.0)
    tracker.reset(100.0)
    assert tracker.seconds_until_periodic(100.0) == 900.0
    assert tracker.seconds_until_periodic(400.0) == 600.0
    # Ueberfaellig heisst null, nicht negativ.
    assert tracker.seconds_until_periodic(5000.0) == 0.0


# Entprellung ------------------------------------------------------------------


def test_watcher_wartet_die_entprellzeit_ab():
    tracker = make(periodic_seconds=0.0, debounce_seconds=1.5)
    tracker.reset(0.0)
    tracker.note_change("z:/lib", 10.0)

    assert tracker.due(11.0) is None
    request = tracker.due(11.5)
    assert request is not None
    assert request.reason is SyncReason.WATCHER
    assert request.is_targeted
    assert request.paths == ("z:/lib",)


def test_weitere_aenderung_verschiebt_das_fenster():
    """Das ist der Kern: waehrend geschrieben wird, startet kein Scan."""
    tracker = make(periodic_seconds=0.0, debounce_seconds=1.5)
    tracker.reset(0.0)

    now = 10.0
    for _ in range(200):
        tracker.note_change("z:/lib/datei", now)
        now += 0.2
        assert tracker.due(now) is None

    # Erst wenn Ruhe eingekehrt ist, wird ein einziger Abgleich faellig.
    assert tracker.due(now + 1.5) is not None


def test_viele_aenderungen_ergeben_einen_abgleich():
    tracker = make(periodic_seconds=0.0)
    tracker.reset(0.0)
    for index in range(200):
        tracker.note_change("z:/lib/ordner%d" % index, 10.0)

    request = tracker.due(12.0)
    assert request is not None
    assert len(request.paths) == 200
    tracker.note_sync_started(12.0)
    assert tracker.due(20.0) is None
    assert not tracker.has_pending


def test_abgeschalteter_watcher_sammelt_nichts():
    tracker = make(periodic_seconds=0.0, watcher_enabled=False)
    tracker.reset(0.0)
    tracker.note_change("z:/lib", 10.0)
    assert not tracker.has_pending
    assert tracker.due(100.0) is None


# Rangfolge und Sperre ---------------------------------------------------------


def test_takt_hat_vorrang_vor_dem_watcher():
    """Sonst koennte ein Dauerstrom von Ereignissen den Takt endlos verschieben."""
    tracker = make(periodic_seconds=900.0, debounce_seconds=1.5)
    tracker.reset(0.0)
    tracker.note_change("z:/lib", 899.0)

    request = tracker.due(901.0)
    assert request is not None
    assert request.reason is SyncReason.PERIODIC
    assert not request.is_targeted


def test_waehrend_eines_scans_wird_nichts_faellig():
    tracker = make(periodic_seconds=1.0)
    tracker.reset(0.0)
    tracker.suspend()
    assert tracker.due(1000.0) is None

    tracker.resume(1000.0)
    assert tracker.due(1000.5) is None
    assert tracker.due(1001.0) is not None


def test_scanbeginn_leert_die_sammlung():
    tracker = make(periodic_seconds=0.0)
    tracker.reset(0.0)
    tracker.note_change("z:/lib", 10.0)
    tracker.note_sync_started(12.0)

    assert tracker.pending_paths == ()
    assert tracker.due(100.0) is None

    tracker.note_sync_finished(15.0)
    assert tracker.due(100.0) is None  # Takt aus, nichts gesammelt


def test_abgewiesener_scan_blockiert_den_tracker_nicht_dauerhaft():
    """note_sync_started ohne folgenden Scan darf nicht fuer immer sperren."""
    tracker = make(periodic_seconds=10.0)
    tracker.reset(0.0)
    tracker.note_sync_started(0.0)
    tracker.note_sync_finished(0.0)

    assert tracker.due(5.0) is None
    assert tracker.due(10.0) is not None


def test_pending_paths_sind_sortiert_und_doppelfrei():
    tracker = make(periodic_seconds=0.0)
    tracker.reset(0.0)
    for path in ["z:/b", "z:/a", "z:/b", "z:/c"]:
        tracker.note_change(path, 1.0)
    assert tracker.pending_paths == ("z:/a", "z:/b", "z:/c")
