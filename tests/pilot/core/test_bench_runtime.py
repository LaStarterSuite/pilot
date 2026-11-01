from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pilot.core.bench.runtime import BenchRuntime
from tests.pilot.commands.test_commands import make_bench


def test_recreate_python_env_deletes_and_rebuilds(tmp_path: Path) -> None:
    """An OS upgrade replaces the system Python, so the venv must be rebuilt from scratch."""
    bench = make_bench(tmp_path)
    bench.python.parent.mkdir(parents=True)
    bench.python.touch()

    with patch("pilot.managers.environment.PythonEnvManager") as manager:
        BenchRuntime(bench).recreate_python_env(lambda message: None)

    assert not bench.python.exists()
    manager.return_value.create_venv.assert_called_once_with()
