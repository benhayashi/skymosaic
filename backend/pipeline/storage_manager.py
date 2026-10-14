import datetime
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from backend.config import SkyViewConfig, StorageConfig


class StorageManager:
    """
    Manages data retention policies and remote share backups (SMB/NFS/CIFS mount or network paths).
    Features:
      - Periodically or on-demand syncs snapshots, timelapses, keograms, and startrails to a remote share.
      - Enforces data retention policies by pruning files older than `retention_days`.
      - Optionally frees local disk space by removing local files once verified copied to remote.
    """

    def __init__(self, config: SkyViewConfig):
        self.config = config
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_sync_time: Optional[datetime.datetime] = None
        self._last_sync_status: Dict[str, Any] = {
            "status": "idle",
            "message": "No backup has been performed yet.",
            "copied_files": 0,
            "pruned_files": 0,
            "last_run": None,
            "remote_accessible": False
        }

    def start(self) -> None:
        """Starts background periodic sync & retention loop."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="StorageManagerWorker")
        self._thread.start()

    def stop(self) -> None:
        """Stops background loop."""
        self._running = False

    def update_config(self, config: SkyViewConfig) -> None:
        with self._lock:
            self.config = config

    def _worker_loop(self) -> None:
        """Periodic background worker loop."""
        while self._running:
            try:
                storage_cfg = self.config.storage
                if storage_cfg.enabled and storage_cfg.remote_path:
                    interval_sec = max(60, storage_cfg.sync_interval_minutes * 60)
                    now = datetime.datetime.now(datetime.timezone.utc)
                    if (
                        self._last_sync_time is None
                        or (now - self._last_sync_time).total_seconds() >= interval_sec
                    ):
                        self.run_sync()

                # Always enforce retention policy if configured
                if storage_cfg.retention_days > 0:
                    self.prune_old_files()

            except Exception as e:
                with self._lock:
                    self._last_sync_status = {
                        "status": "error",
                        "message": str(e),
                        "copied_files": 0,
                        "pruned_files": 0,
                        "last_run": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "remote_accessible": False
                    }
            time.sleep(30)  # Check every 30 seconds

    def test_remote_connection(self, path: Optional[str] = None) -> Dict[str, Any]:
        """Tests whether the specified or configured remote network path exists and is writable."""
        target_path_str = path or self.config.storage.remote_path
        if not target_path_str:
            return {"accessible": False, "writable": False, "error": "Remote path is empty."}

        p = Path(target_path_str)
        try:
            if not p.exists():
                # Attempt to create directory
                p.mkdir(parents=True, exist_ok=True)

            # Test write permissions
            test_file = p / f".skymosaic_write_test_{int(time.time())}.tmp"
            test_file.write_text("write_ok")
            test_file.unlink()

            # Query disk space
            stat = shutil.disk_usage(p)
            return {
                "accessible": True,
                "writable": True,
                "total_gb": round(stat.total / (1024 ** 3), 2),
                "free_gb": round(stat.free / (1024 ** 3), 2),
                "used_gb": round(stat.used / (1024 ** 3), 2),
                "path": str(p)
            }
        except Exception as e:
            return {
                "accessible": False,
                "writable": False,
                "error": str(e),
                "path": str(p)
            }

    def run_sync(self) -> Dict[str, Any]:
        """
        Executes an immediate sync to the remote share.
        Copies files from data/archive, data/timelapses, data/keograms, data/startrails.
        """
        storage_cfg = self.config.storage
        remote_path = storage_cfg.remote_path
        if not remote_path:
            return {"status": "error", "message": "Remote backup path not configured."}

        remote_dir = Path(remote_path)
        try:
            remote_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"status": "error", "message": f"Cannot access remote path: {e}"}

        data_root = Path(self.config.data_dir)
        categories = []
        if storage_cfg.backup_snapshots:
            categories.append(("archive", ["*.jpg", "*.png"]))
        if storage_cfg.backup_timelapses:
            categories.append(("timelapses", ["*.mp4"]))
        if storage_cfg.backup_keograms:
            categories.append(("keograms", ["*.jpg", "*.png"]))
        if storage_cfg.backup_startrails:
            categories.append(("startrails", ["*.jpg", "*.png"]))

        copied_count = 0
        deleted_count = 0

        for folder_name, exts in categories:
            src_folder = data_root / folder_name
            if not src_folder.exists():
                continue

            dest_folder = remote_dir / folder_name
            dest_folder.mkdir(parents=True, exist_ok=True)

            for ext in exts:
                for file_path in src_folder.glob(ext):
                    dest_file = dest_folder / file_path.name
                    # Copy if destination doesn't exist or size/mtime differs
                    if (
                        not dest_file.exists()
                        or dest_file.stat().st_size != file_path.stat().st_size
                    ):
                        shutil.copy2(file_path, dest_file)
                        copied_count += 1

                    # Optional: Free up local space once safely synced
                    if storage_cfg.delete_local_after_sync and dest_file.exists():
                        if dest_file.stat().st_size == file_path.stat().st_size:
                            file_path.unlink()
                            deleted_count += 1

        # Also backup config template
        try:
            cfg_src = data_root / "config.json"
            if cfg_src.exists():
                shutil.copy2(cfg_src, remote_dir / "config.backup.json")
        except Exception:
            pass

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        self._last_sync_time = now_utc
        res = {
            "status": "success",
            "message": f"Successfully backed up {copied_count} files to {remote_path}." + (f" Pruned {deleted_count} local files." if deleted_count > 0 else ""),
            "copied_files": copied_count,
            "deleted_local_files": deleted_count,
            "last_run": now_utc.isoformat(),
            "remote_accessible": True
        }
        with self._lock:
            self._last_sync_status = res
        return res

    def prune_old_files(self) -> int:
        """Deletes local media files older than retention_days."""
        days = self.config.storage.retention_days
        if days <= 0:
            return 0

        cutoff = time.time() - (days * 86400)
        data_root = Path(self.config.data_dir)
        folders = ["archive", "timelapses", "keograms", "startrails"]

        pruned = 0
        for fld in folders:
            p = data_root / fld
            if not p.exists():
                continue
            for file_path in p.iterdir():
                if file_path.is_file() and file_path.stat().st_mtime < cutoff:
                    try:
                        file_path.unlink()
                        pruned += 1
                    except Exception as e:
                        print(f"Error pruning file {file_path}: {e}")
        return pruned

    def get_status(self) -> Dict[str, Any]:
        """Returns current sync status and disk space stats."""
        status = dict(self._last_sync_status)
        remote_path = self.config.storage.remote_path
        if remote_path:
            test = self.test_remote_connection(remote_path)
            status["remote_accessible"] = test["accessible"]
            status["remote_info"] = test
        else:
            status["remote_accessible"] = False

        # Add local storage usage info
        data_root = Path(self.config.data_dir)
        try:
            usage = shutil.disk_usage(data_root if data_root.exists() else ".")
            status["local_storage"] = {
                "total_gb": round(usage.total / (1024 ** 3), 2),
                "free_gb": round(usage.free / (1024 ** 3), 2),
                "used_gb": round(usage.used / (1024 ** 3), 2),
            }
        except Exception:
            pass

        return status
