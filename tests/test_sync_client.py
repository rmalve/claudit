"""Unit tests for scripts/sync_client.py.

Covers symlink skipping, projects.json validation, per-file atomicity, and
the single source of truth for the version-marker filename.
"""
import json
import os
import platform
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import sync_client  # noqa: E402


# ---------------------------------------------------------------------------
# _iter_source_files — symlink skip
# ---------------------------------------------------------------------------


class TestIterSourceFilesSkipsSymlinks:
    """Symlinks are skipped with a stderr warning so cross-machine sync
    never distributes a dangling link."""

    def test_skips_symlinks_and_warns(self, tmp_path, capsys):
        regular = tmp_path / "regular.py"
        regular.write_text("# real file")

        link = tmp_path / "link.py"
        try:
            link.symlink_to(regular)
        except (OSError, NotImplementedError):
            # Windows without developer mode / admin can't create symlinks.
            pytest.skip("symlink creation not supported in this environment")

        results = list(sync_client._iter_source_files(tmp_path))
        yielded_names = {rel.name for rel, _ in results}

        assert "regular.py" in yielded_names
        assert "link.py" not in yielded_names

        captured = capsys.readouterr()
        assert "skipping symlink" in captured.err
        assert "link.py" in captured.err


# ---------------------------------------------------------------------------
# _load_projects — validation of name/root keys
# ---------------------------------------------------------------------------


def _write_projects_file(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "projects.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


class TestLoadProjectsValidation:
    """_load_projects must exit 2 with a clear message on malformed entries."""

    def test_errors_on_missing_name(self, tmp_path, capsys):
        projects_path = _write_projects_file(tmp_path, {
            "projects": [{"root": "/tmp/fake", "active": True}],
        })
        with patch.object(sync_client, "PROJECTS_FILE", projects_path):
            with pytest.raises(SystemExit) as exc:
                sync_client._load_projects(only=None)
        assert exc.value.code == 2
        assert "missing required key 'name' or 'root'" in capsys.readouterr().err

    def test_errors_on_missing_root(self, tmp_path, capsys):
        projects_path = _write_projects_file(tmp_path, {
            "projects": [{"name": "foo", "active": True}],
        })
        with patch.object(sync_client, "PROJECTS_FILE", projects_path):
            with pytest.raises(SystemExit) as exc:
                sync_client._load_projects(only=None)
        assert exc.value.code == 2
        assert "missing required key 'name' or 'root'" in capsys.readouterr().err

    def test_valid_entry_loads(self, tmp_path):
        projects_path = _write_projects_file(tmp_path, {
            "projects": [{"name": "foo", "root": "/tmp/foo", "active": True}],
        })
        with patch.object(sync_client, "PROJECTS_FILE", projects_path):
            projects = sync_client._load_projects(only=None)
        assert len(projects) == 1
        assert projects[0]["name"] == "foo"

    def test_errors_on_malformed_json(self, tmp_path, capsys):
        projects_path = tmp_path / "projects.json"
        projects_path.write_text("{not valid json", encoding="utf-8")
        with patch.object(sync_client, "PROJECTS_FILE", projects_path):
            with pytest.raises(SystemExit) as exc:
                sync_client._load_projects(only=None)
        assert exc.value.code == 2
        assert "not valid JSON" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _apply_sync — per-file atomicity
# ---------------------------------------------------------------------------


class TestApplySyncAtomicPerFile:
    """When shutil.copy2 raises mid-sync, the already-written files survive
    intact, no .sync-tmp files leak onto disk, and the version marker is
    not written (so future runs detect the incomplete state)."""

    def test_no_sync_tmp_leftovers_and_marker_not_written_on_failure(
        self, tmp_path, monkeypatch,
    ):
        source = tmp_path / "source"
        source.mkdir()
        (source / "a.py").write_text("a")
        (source / "b.py").write_text("b")
        (source / "c.py").write_text("c")

        target_obs = tmp_path / "project" / "observability"
        real_copy = sync_client.shutil.copy2
        call_count = {"n": 0}

        def flaky_copy(src, dst, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise OSError("simulated disk error")
            return real_copy(src, dst, *args, **kwargs)

        monkeypatch.setattr(sync_client.shutil, "copy2", flaky_copy)

        with pytest.raises(OSError, match="simulated disk error"):
            sync_client._apply_sync(
                source, target_obs, version="1.2.3",
                added=[Path("a.py"), Path("b.py"), Path("c.py")],
                changed=[],
            )

        assert (target_obs / "a.py").read_text() == "a"
        assert not (target_obs / "b.py").exists()
        assert not (target_obs / "c.py").exists()
        leftovers = list(target_obs.rglob(f"*{sync_client.SYNC_TMP_SUFFIX}"))
        assert leftovers == []

        version_path = target_obs.parent / sync_client.VERSION_FILE_NAME
        assert not version_path.exists()


# ---------------------------------------------------------------------------
# Version marker filename is a single source of truth
# ---------------------------------------------------------------------------


class TestVersionMarkerFilenameSingleSource:
    """sync_client.VERSION_FILE_NAME must match client/observability/__init__.py's
    _VERSION_MARKER_FILENAME constant — the client __init__ is canonical."""

    def test_script_matches_client_constant(self):
        init_path = REPO_ROOT / "client" / "observability" / "__init__.py"
        text = init_path.read_text(encoding="utf-8")
        canonical = None
        for line in text.splitlines():
            if line.lstrip().startswith("_VERSION_MARKER_FILENAME"):
                _, _, rhs = line.partition("=")
                canonical = rhs.strip().strip("\"'")
                break
        assert canonical, "_VERSION_MARKER_FILENAME not found in client __init__.py"
        assert sync_client.VERSION_FILE_NAME == canonical
