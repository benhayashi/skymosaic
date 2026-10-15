import datetime
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import numpy as np

from backend.astrometry.celestial import get_sun_coordinates
from backend.astrometry.solver import auto_plate_solve_camera
from backend.config import SkyViewConfig

logger = logging.getLogger("AutoCalibrator")


class AutoCalibrationManager:
    """
    Autonomous Astrometric Plate Solving Calibration Manager:
    - Provides on-demand single-camera or all-camera plate solving calibration via GPS location + NTP time.
    - Provides background periodic/nightly calibration to eliminate physical mount drift.
    - Validates astronomical night (Sun altitude <= -12°) and star count before applying pose updates.
    - Saves updated poses to config and updates active stitcher projection LUTs.
    """

    def __init__(
        self,
        config: SkyViewConfig,
        stream_manager,
        stitcher,
        on_calibration_applied: Optional[Callable[[SkyViewConfig], None]] = None,
    ):
        self.config = config
        self.stream_manager = stream_manager
        self.stitcher = stitcher
        self.on_calibration_applied = on_calibration_applied
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_run_time: Optional[datetime.datetime] = None
        self._last_status: Dict[str, Any] = {
            "status": "idle",
            "message": "Auto-calibration has not run yet.",
            "last_run": None,
            "results": {},
        }

    def start(self) -> None:
        """Starts background periodic calibration worker."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="AutoCalibrationWorker"
        )
        self._thread.start()
        logger.info("AutoCalibrationManager worker thread started.")

    def stop(self) -> None:
        """Stops background worker."""
        self._running = False

    def update_config(self, config: SkyViewConfig) -> None:
        with self._lock:
            self.config = config

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            status = dict(self._last_status)
            auto_cfg = self.config.plugins.auto_calibration
            status["enabled"] = auto_cfg.enabled
            status["check_interval_minutes"] = auto_cfg.check_interval_minutes
            status["require_night"] = auto_cfg.require_night
            status["max_rms_error_px"] = auto_cfg.max_rms_error_px
            status["min_star_matches"] = auto_cfg.min_star_matches
            status["max_cloud_cover_percent"] = auto_cfg.max_cloud_cover_percent
            return status

    def run_auto_calibration(
        self,
        camera_id: Optional[str] = None,
        force: bool = False,
        apply_updates: bool = True,
        timestamp_iso: Optional[str] = None,
        solve_distortion: bool = False,
    ) -> Dict[str, Any]:
        """
        Runs star plate solving for a single camera or all enabled cameras.
        If force=True, bypasses night/sun altitude checks (useful for test runs or manual clicks).
        """
        with self._lock:
            config = self.config
            auto_cfg = config.plugins.auto_calibration

        # Determine reference timestamp (NTP or current time)
        if timestamp_iso:
            try:
                dt = datetime.datetime.fromisoformat(timestamp_iso)
            except Exception:
                dt = self.stitcher.ntp_client.get_datetime()
        else:
            dt = self.stitcher.ntp_client.get_datetime()

        lat = config.observer.latitude
        lon = config.observer.longitude

        # Check day/night conditions if not forced
        sun_info = get_sun_coordinates(dt, lat, lon)
        sun_alt = sun_info.get("altitude_deg", 0.0)

        if not force and auto_cfg.require_night and sun_alt > -12.0:
            msg = f"Skipped: Sun altitude is {sun_alt:.1f}° ({sun_info.get('state', 'twilight')}). Astronomical night (Sun <= -12°) required."
            logger.info(msg)
            res = {
                "success": False,
                "status": "skipped",
                "message": msg,
                "sun_altitude_deg": round(sun_alt, 1),
                "timestamp": dt.isoformat(),
            }
            with self._lock:
                self._last_status["status"] = "skipped"
                self._last_status["message"] = msg
                self._last_status["last_run"] = dt.isoformat()
            return res

        # Target cameras
        if camera_id:
            targets = [c for c in config.cameras if c.id == camera_id]
            if not targets:
                return {
                    "success": False,
                    "status": "error",
                    "message": f"Camera with ID '{camera_id}' not found.",
                }
        else:
            targets = [c for c in config.cameras if c.enabled]

        if not targets:
            return {
                "success": False,
                "status": "error",
                "message": "No active cameras available for calibration.",
            }

        camera_results = {}
        any_updated = False

        for cam in targets:
            frame = self.stream_manager.get_camera_frame(cam.id)
            if frame is None:
                camera_results[cam.id] = {
                    "success": False,
                    "error": f"Failed to acquire live image from camera '{cam.id}'.",
                }
                continue

            try:
                # Generate obstacle mask if polygon_mask is defined on camera
                h_f, w_f = frame.shape[:2]
                obs_mask = None
                if len(cam.polygon_mask) >= 3:
                    from backend.stitching.warp_lut import generate_camera_polygon_mask
                    obs_mask = generate_camera_polygon_mask(cam.polygon_mask, w_f, h_f)

                solve_res = auto_plate_solve_camera(
                    image=frame,
                    dt=dt,
                    lat_deg=lat,
                    lon_deg=lon,
                    lens_model=cam.intrinsics.lens_model,
                    init_extrinsics=cam.extrinsics,
                    init_fov=cam.intrinsics.fov_deg,
                    init_k1=cam.intrinsics.k1,
                    solve_distortion=solve_distortion,
                    obstacle_mask=obs_mask,
                    min_elevation_deg=getattr(cam, "min_elevation_deg", 0.0)
                )

                if solve_res.get("success"):
                    rms = solve_res.get("rms_error_px", 999.0)
                    matches = solve_res.get("match_count", 0)

                    # Validate against configured tolerances
                    if rms <= auto_cfg.max_rms_error_px and matches >= auto_cfg.min_star_matches:
                        solve_res["applied"] = apply_updates
                        if apply_updates:
                            # Update camera extrinsics, FOV, and optional lens distortion
                            cam.extrinsics.azimuth_deg = solve_res["azimuth_deg"]
                            cam.extrinsics.altitude_deg = solve_res["altitude_deg"]
                            cam.extrinsics.roll_deg = solve_res["roll_deg"]
                            cam.intrinsics.fov_deg = solve_res["fov_deg"]
                            if solve_distortion and "k1" in solve_res:
                                cam.intrinsics.k1 = solve_res["k1"]
                            any_updated = True
                            logger.info(
                                f"Camera '{cam.id}' auto-calibrated: Az={solve_res['azimuth_deg']:.2f}°, Alt={solve_res['altitude_deg']:.2f}°, Roll={solve_res['roll_deg']:.2f}°, FOV={solve_res['fov_deg']:.2f}°, k1={solve_res.get('k1', cam.intrinsics.k1):.4f}, RMS={rms:.2f}px ({matches} stars)"
                            )
                    else:
                        solve_res["applied"] = False
                        solve_res["warning"] = (
                            f"Solution exceeded criteria: RMS {rms:.2f}px (max {auto_cfg.max_rms_error_px}px) "
                            f"or {matches} matches (min {auto_cfg.min_star_matches})."
                        )
                camera_results[cam.id] = solve_res

            except Exception as e:
                logger.error(f"Error plate solving camera '{cam.id}': {e}", exc_info=True)
                camera_results[cam.id] = {"success": False, "error": str(e)}

        # If any cameras were updated, persist and notify stitcher
        if any_updated and self.on_calibration_applied:
            try:
                self.on_calibration_applied(config)
            except Exception as e:
                logger.error(f"Error persisting auto-calibrated configuration: {e}")

        total_success = sum(1 for r in camera_results.values() if r.get("success"))
        total_applied = sum(1 for r in camera_results.values() if r.get("applied"))

        overall_msg = (
            f"Plate solving completed: {total_applied}/{len(targets)} camera(s) calibrated and updated "
            f"({total_success} successful solves)."
        )

        final_response = {
            "success": total_applied > 0 or total_success > 0,
            "status": "completed",
            "message": overall_msg,
            "timestamp": dt.isoformat(),
            "sun_altitude_deg": round(sun_alt, 1),
            "cameras_calibrated": total_applied,
            "results": camera_results,
        }

        with self._lock:
            self._last_run_time = dt
            self._last_status = {
                "status": "completed",
                "message": overall_msg,
                "last_run": dt.isoformat(),
                "results": camera_results,
            }

        return final_response

    def _worker_loop(self) -> None:
        """Periodic background thread to run auto-calibration at night."""
        while self._running:
            try:
                with self._lock:
                    auto_cfg = self.config.plugins.auto_calibration
                    enabled = auto_cfg.enabled
                    interval_min = max(5, auto_cfg.check_interval_minutes)

                if enabled:
                    now = datetime.datetime.now(datetime.timezone.utc)
                    interval_sec = interval_min * 60
                    if (
                        self._last_run_time is None
                        or (now - self._last_run_time).total_seconds() >= interval_sec
                    ):
                        logger.info("Executing scheduled background auto-calibration check...")
                        self.run_auto_calibration(camera_id=None, force=False, apply_updates=True)

            except Exception as e:
                logger.error(f"Unexpected error in auto-calibration loop: {e}", exc_info=True)

            # Sleep in short increments to allow graceful shutdown
            for _ in range(10):
                if not self._running:
                    break
                time.sleep(1.0)
