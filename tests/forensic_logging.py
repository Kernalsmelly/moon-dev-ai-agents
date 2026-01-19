# Forensic pytest plugin: logs per-test durations and flags potential hangs (>30s)
import time
from pathlib import Path

FOR_LOG = Path("tests/forensic.log")
SUSPECTS = Path("tests/forensic_suspects.log")


def pytest_runtest_setup(item):
    item._forensic_start = time.time()


def pytest_runtest_makereport(item, call):
    # only record the call phase (actual test body)
    if call.when == "call":
        start = getattr(item, "_forensic_start", None)
        duration = (time.time() - start) if start else 0.0
        status = "PASSED" if call.excinfo is None else "FAILED"
        entry = f"{item.nodeid}\t{status}\t{duration:.2f}\n"
        if FOR_LOG.exists():
            FOR_LOG.write_text(FOR_LOG.read_text() + entry)
        else:
            FOR_LOG.write_text(entry)
        if duration > 30.0:
            note = f"POTENTIAL_HANG\t{item.nodeid}\t{duration:.2f}\n"
            if SUSPECTS.exists():
                SUSPECTS.write_text(SUSPECTS.read_text() + note)
            else:
                SUSPECTS.write_text(note)
            # immediate console notice so you see it in real-time
            print(f"\n⚠️ POTENTIAL HANG: {item.nodeid} took {duration:.1f}s (>30s)\n")
