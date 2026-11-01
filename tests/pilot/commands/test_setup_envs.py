from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.commands.setup.envs import SetupEnvsCommand
from pilot.config import (
    AppConfig,
    BenchConfig,
    MariaDBConfig,
    RedisConfig,
    WorkerConfig,
    WorkerGroup,
)
from pilot.core.bench import Bench
from pilot.exceptions import BenchError


def _bench_dir(root: Path, name: str, *, with_env: bool = True) -> Bench:
    bench_dir = root / "benches" / name
    bench_dir.mkdir(parents=True)
    config = BenchConfig(
        name=name,
        python_version="3.14",
        apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(groups=[WorkerGroup(queues=["default"], count=1)]),
    )
    bench = Bench(config, bench_dir)
    config.write(bench_dir)
    if with_env:
        (bench_dir / "env").mkdir()
    return bench


def test_recreates_admin_env_and_every_bench_env(tmp_path: Path) -> None:
    b1 = _bench_dir(tmp_path, "b1")
    b2 = _bench_dir(tmp_path, "b2")
    _bench_dir(tmp_path, "bare", with_env=False)
    rebuilt: list[Bench] = []

    with (
        patch("pilot.utils.benches_dir", return_value=tmp_path / "benches"),
        patch("pilot.managers.environment.AdminEnvManager.recreate") as admin,
        patch.object(
            Bench, "recreate_python_env", lambda self, on_progress=None: rebuilt.append(self)
        ),
    ):
        SetupEnvsCommand(skip_confirm=True).run()

    admin.assert_called_once()
    assert [bench.path for bench in rebuilt] == [b1.path, b2.path]


def test_recreates_only_the_given_bench(tmp_path: Path) -> None:
    b1 = _bench_dir(tmp_path, "b1")
    _bench_dir(tmp_path, "b2")
    rebuilt: list[Bench] = []

    with (
        patch("pilot.managers.environment.AdminEnvManager.recreate") as admin,
        patch.object(
            Bench, "recreate_python_env", lambda self, on_progress=None: rebuilt.append(self)
        ),
    ):
        SetupEnvsCommand(bench=b1, skip_confirm=True).run()

    admin.assert_called_once()
    assert [bench.path for bench in rebuilt] == [b1.path]


def test_asks_for_confirmation_before_touching_anything(tmp_path: Path) -> None:
    _bench_dir(tmp_path, "b1")

    with (
        patch("pilot.managers.environment.AdminEnvManager.recreate") as admin,
        patch.object(Bench, "recreate_python_env") as recreate,
        patch("builtins.input", return_value="n"),
        pytest.raises(BenchError, match="Aborted"),
    ):
        SetupEnvsCommand().run()

    admin.assert_not_called()
    recreate.assert_not_called()
