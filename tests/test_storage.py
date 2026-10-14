import os
import tempfile
import time
from pathlib import Path
from backend.config import SkyViewConfig, StorageConfig
from backend.pipeline.storage_manager import StorageManager


def test_storage_manager_test_connection():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = SkyViewConfig()
        cfg.storage = StorageConfig(enabled=True, remote_path=tmpdir)
        mgr = StorageManager(cfg)

        res = mgr.test_remote_connection()
        assert res["accessible"] is True
        assert res["writable"] is True
        assert "free_gb" in res


def test_storage_manager_sync_and_retention():
    with tempfile.TemporaryDirectory() as local_dir, tempfile.TemporaryDirectory() as remote_dir:
        # Create local mock data folder structure
        archive_dir = Path(local_dir) / "archive"
        archive_dir.mkdir(parents=True)
        timelapse_dir = Path(local_dir) / "timelapses"
        timelapse_dir.mkdir(parents=True)

        # Create test mock files
        f1 = archive_dir / "skymosaic_20261014_010000.jpg"
        f1.write_text("image_data_1")
        f2 = timelapse_dir / "timelapse_20261014.mp4"
        f2.write_text("video_data_1")

        cfg = SkyViewConfig()
        cfg.data_dir = local_dir
        cfg.storage = StorageConfig(
            enabled=True,
            remote_path=remote_dir,
            retention_days=30,
            delete_local_after_sync=False,
            backup_snapshots=True,
            backup_timelapses=True
        )

        mgr = StorageManager(cfg)
        sync_res = mgr.run_sync()

        assert sync_res["status"] == "success"
        assert sync_res["copied_files"] == 2

        # Verify files exist in remote
        remote_f1 = Path(remote_dir) / "archive" / f1.name
        remote_f2 = Path(remote_dir) / "timelapses" / f2.name
        assert remote_f1.exists()
        assert remote_f2.exists()
        assert remote_f1.read_text() == "image_data_1"


def test_storage_manager_pruning():
    with tempfile.TemporaryDirectory() as local_dir:
        archive_dir = Path(local_dir) / "archive"
        archive_dir.mkdir(parents=True)

        old_file = archive_dir / "old_sky.jpg"
        old_file.write_text("old")
        # Set file modification time to 40 days ago
        old_time = time.time() - (40 * 86400)
        os.utime(old_file, (old_time, old_time))

        new_file = archive_dir / "new_sky.jpg"
        new_file.write_text("new")

        cfg = SkyViewConfig()
        cfg.data_dir = local_dir
        cfg.storage = StorageConfig(retention_days=30)

        mgr = StorageManager(cfg)
        pruned = mgr.prune_old_files()

        assert pruned == 1
        assert not old_file.exists()
        assert new_file.exists()
