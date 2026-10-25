"""Release ZIP permission guard (roadmap B-151).

The freeze drill's first run failed at its first step:
`./demo.sh: Permission denied`. The release script wrote every
entry with the archive-default 0644, so the shell scripts a
cold reader is told to run landed non-executable — in every
train ZIP built before this guard existed. The fix carries
each source file's Unix mode into its ZipInfo. These tests pin
that mapping, and pin that the tree's entry-point scripts are
executable at the source (a bit the script can only carry if
it is set), so the drill's find can never quietly return.
"""

import importlib.util
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "make_release_zip", ROOT / "scripts" / "make_release_zip.py"
)
make_release_zip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_release_zip)


def test_zip_info_carries_an_executable_mode():
    info = make_release_zip._zip_info("gpa-backend/demo.sh", 0o100770, 1700000000.0)
    assert stat.S_IMODE(info.external_attr >> 16) == 0o770
    assert info.create_system == 3  # Unix: the mode is meaningful.


def test_zip_info_carries_a_plain_mode():
    info = make_release_zip._zip_info("gpa-backend/README.md", 0o100644, 1700000000.0)
    assert stat.S_IMODE(info.external_attr >> 16) == 0o644


def test_entry_point_scripts_are_executable_at_the_source():
    for name in ("demo.sh", "run.sh", "test.sh"):
        mode = (ROOT / name).stat().st_mode
        assert mode & stat.S_IXUSR, f"{name} must be executable in the tree"
