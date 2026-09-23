import shutil
import subprocess
from pathlib import Path

import pytest

from repomentor.repository import SnapshotStore


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    shutil.copytree(Path(__file__).resolve().parent / "fixtures/mini-repo", path)
    (path / "tests").mkdir()
    (path / "tests/helper.test.ts").write_text(
        'import { helper } from "../src/utils/helper.js";\nhelper();\n'
    )
    (path / "src/core.ts").write_text('export const runCore = () => "core";\n')
    for args in (
        ["init"],
        ["config", "core.autocrlf", "false"],
        ["add", "."],
        [
            "-c",
            "user.name=RepoMentorTest",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path


@pytest.fixture
def reader(tmp_path, repo):
    return SnapshotStore(tmp_path / "snapshots").import_repository(str(repo))
