"""Include VCBS's non-Python runtime resources in built wheels."""

from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


RESOURCE_DIRS = (
    "schemas", "capabilities", "composer", "profiles", "experiences",
    "components", "host_adapters", "assembly", "examples", "adoption",
)


class BuildPyWithResources(build_py):
    def run(self):
        super().run()
        source_root = Path(__file__).resolve().parent
        data_root = Path(self.build_lib) / "bootstrap" / "data"
        data_root.mkdir(parents=True, exist_ok=True)
        for name in RESOURCE_DIRS:
            source = source_root / name
            if source.is_dir():
                shutil.copytree(source, data_root / name, dirs_exist_ok=True)

    def get_outputs(self, include_bytecode=1):
        outputs = super().get_outputs(include_bytecode)
        data_root = Path(self.build_lib) / "bootstrap" / "data"
        if data_root.exists():
            outputs.extend(str(path) for path in data_root.rglob("*") if path.is_file())
        return outputs


setup(cmdclass={"build_py": BuildPyWithResources})
