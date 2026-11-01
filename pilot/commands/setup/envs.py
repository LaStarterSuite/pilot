from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import BenchMode, Command


@dataclass(kw_only=True)
class SetupEnvsCommand(Command):
    """Rebuild the virtualenvs an OS upgrade broke, keeping sites and configs."""

    name: ClassVar[str] = "envs"
    help: ClassVar[str] = (
        "Recreate the admin and bench virtualenvs with the current system Python, "
        "for example after an OS upgrade."
    )
    group: ClassVar[str] = "setup"
    # Host-level recovery. With a bench only that bench's env is rebuilt.
    bench_mode: ClassVar[BenchMode] = BenchMode.OPTIONAL

    skip_confirm: bool = False

    def run(self) -> None:
        from pilot.core.bench import Bench
        from pilot.managers.environment import AdminEnvManager
        from pilot.utils import benches_dir, cli_root

        self.confirm(
            "Recreate the Pilot virtualenvs? Packages are reinstalled; sites and configs are kept.",
            skip=self.skip_confirm,
        )
        self.report("Recreating the admin environment...")
        AdminEnvManager(cli_root()).recreate()

        if self.bench:
            targets = [self.bench]
        else:
            root = benches_dir()
            names = (
                sorted(d.name for d in root.iterdir() if d.is_dir() and (d / "bench.toml").exists())
                if root.is_dir()
                else []
            )
            targets = [Bench(root / name) for name in names]
        rebuilt = 0
        for bench in targets:
            if not bench.env_path.exists():
                continue
            self.report(f"Recreating the env of bench '{bench.path.name}'...")
            bench.recreate_python_env(on_progress=self.report)
            rebuilt += 1
        if not rebuilt:
            self.report("No bench environments to recreate.")
        self.report("Done. Restart the admin service and the benches to use the new environments.")
