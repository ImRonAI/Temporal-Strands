"""Apply the approved async adaptation to pinned strands-agents-tools Think."""

import importlib.util
import subprocess
from pathlib import Path


def main() -> None:
    package = importlib.util.find_spec("strands_tools")
    if package is None or not package.submodule_search_locations:
        raise SystemExit("Install orchestrator/requirements.txt before patching Think")
    root = Path(next(iter(package.submodule_search_locations)))
    patch = Path(__file__).resolve().parents[1] / "orchestrator/patches/think-async.patch"
    command = ["patch", "--batch", "--force", "-p1", "-d", str(root), "-i", str(patch)]
    if subprocess.run([*command, "--reverse", "--dry-run"], capture_output=True).returncode == 0:
        return
    subprocess.run([*command, "--forward", "--dry-run"], check=True)
    subprocess.run([*command, "--forward"], check=True)


if __name__ == "__main__":
    main()
