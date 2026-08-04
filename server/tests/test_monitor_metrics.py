from types import SimpleNamespace

from home_os.modules.monitor.routes import (
    _disk_activity_percent,
    _root_disk_name,
    _summarize_playback_records,
)


def test_root_disk_name_resolves_physical_sata_disk():
    partitions = [SimpleNamespace(device="/dev/sda2", mountpoint="/")]
    counters = {"sda": object(), "sda2": object()}

    assert _root_disk_name(partitions=partitions, counters=counters) == "sda"


def test_root_disk_name_resolves_physical_nvme_disk():
    partitions = [SimpleNamespace(device="/dev/nvme0n1p2", mountpoint="/")]
    counters = {"nvme0n1": object(), "nvme0n1p2": object()}

    assert _root_disk_name(partitions=partitions, counters=counters) == "nvme0n1"


def test_disk_activity_percent_uses_busy_time_delta():
    before = SimpleNamespace(busy_time=1_000)
    after = SimpleNamespace(busy_time=1_125)

    assert _disk_activity_percent(before, after, 0.5) == 25.0


def test_disk_activity_percent_clamps_to_valid_range():
    before = SimpleNamespace(busy_time=1_000)

    assert _disk_activity_percent(before, SimpleNamespace(busy_time=900), 0.5) == 0.0
    assert _disk_activity_percent(before, SimpleNamespace(busy_time=2_000), 0.5) == 100


def test_playback_summary_reports_average_p95_failures_and_fallbacks():
    records = [
        SimpleNamespace(
            success=True,
            source_ready_ms=20,
            audible_ms=200,
            fallback_used=False,
        ),
        SimpleNamespace(
            success=True,
            source_ready_ms=40,
            audible_ms=600,
            fallback_used=True,
        ),
        SimpleNamespace(
            success=False,
            source_ready_ms=None,
            audible_ms=None,
            fallback_used=False,
        ),
    ]

    summary = _summarize_playback_records(records)

    assert summary == {
        "samples": 3,
        "successes": 2,
        "avg_source_ready_ms": 30,
        "avg_audible_ms": 400,
        "p95_audible_ms": 600,
        "failure_rate": 33.3,
        "fallback_rate": 33.3,
    }
