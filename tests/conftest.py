"""Opt-in TDG3 experiments; ordinary pytest runs remain fast and self-contained."""
from pathlib import Path

import pytest

from mnsim.scenario import load_scenario


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def pytest_addoption(parser):
    group = parser.getgroup("tdg3")
    group.addoption("--run-tdg3-integration", action="store_true",
                    help="Run the full 3600-second TDG3 experiment (several minutes).")
    group.addoption("--tdg3-replay", metavar="PATH",
                    help="Also audit an existing TDG3 replay.jsonl without modifying it.")


def pytest_configure(config):
    # Internal tests must use the runtime API, never the deprecated local estimate.
    config.addinivalue_line("filterwarnings",
        r"error:FormationElement\.active_weapon_system_count\(\) is deprecated:DeprecationWarning")
    config.addinivalue_line("markers", "tdg3_integration: full TDG3 experiment, opt-in")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-tdg3-integration"):
        skip = pytest.mark.skip(reason="enable with --run-tdg3-integration")
        for item in items:
            if "tdg3_integration" in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def tdg3_load():
    def load(with_bml=True):
        refs = {side: str(PROJECT_ROOT / "scenarios" / f"tdg3_{side.lower()}_bml.json")
                for side in ("BLUE", "RED")} if with_bml else {}
        return load_scenario(str(PROJECT_ROOT / "scenarios/tdg3.json"), bml_files=refs)
    return load


@pytest.fixture
def tdg3_sim(tdg3_load):
    return tdg3_load()


@pytest.fixture
def tdg3_replay_path(request):
    value = request.config.getoption("--tdg3-replay")
    if not value:
        pytest.skip("supply --tdg3-replay logs/replay.jsonl to audit a saved run")
    path = Path(value).resolve()
    if not path.is_file():
        pytest.fail(f"Replay file does not exist: {path}")
    return path
