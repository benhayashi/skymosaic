import datetime
import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class CompositeWorker:
    """
    Single background pipeline thread: stitches the active camera frames ONCE per cycle and shares
    the result with every consumer (live JPEG, MJPEG stream, weather, DVR, tracker).

    - The clean composite is cached; overlays are drawn on a copy on demand (cheap).
    - The tracker runs on its own slower cadence in this thread, so frame-differencing state is
      never shared between HTTP requests/clients and always sees a steady frame interval.
    """

    def __init__(self, stream_manager, stitcher, tracker_engine, dvr_manager,
                 composite_interval: float = 0.2, tracking_interval: float = 1.0, dvr_interval: float = 1.5):
        self.stream_manager = stream_manager
        self.stitcher = stitcher
        self.tracker_engine = tracker_engine
        self.dvr_manager = dvr_manager
        self.composite_interval = composite_interval
        self.tracking_interval = tracking_interval
        self.dvr_interval = dvr_interval

        self._lock = threading.Lock()
        self._clean: Optional[np.ndarray] = None
        self._clean_time = 0.0
        self._generation = 0
        self._jpeg_cache: Dict[Tuple[bool, int], bytes] = {}
        self._targets: Dict[str, Any] = {"targets": [], "targets_count": 0, "unidentified_count": 0, "stale": True}
        self._targets_time = 0.0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.last_error: Optional[str] = None

    # ---------------- lifecycle ----------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="CompositeWorker")
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    # ---------------- accessors ----------------
    def get_clean_composite(self) -> Optional[np.ndarray]:
        """Returns a copy of the latest clean composite (no overlays), stitching on-demand if none yet."""
        with self._lock:
            if self._clean is not None:
                return self._clean.copy()
        frames = self.stream_manager.get_all_active_frames()
        return self.stitcher.stitch_frames(frames, overlay_annotations=False)

    def get_annotated_composite(self) -> Optional[np.ndarray]:
        clean = self.get_clean_composite()
        return self.stitcher.render_dome_annotations(clean) if clean is not None else None

    def get_jpeg(self, overlay: bool = True, quality: int = 88, use_cache: bool = True) -> Optional[bytes]:
        key = (overlay, quality)
        with self._lock:
            gen = self._generation
            cached = self._jpeg_cache.get(key)
            if use_cache and cached is not None and self._jpeg_cache.get("gen") == gen:
                return cached
        image = self.get_annotated_composite() if overlay else self.get_clean_composite()
        if image is None:
            return None
        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            return None
        data = jpeg.tobytes()
        if use_cache:
            with self._lock:
                if self._jpeg_cache.get("gen") != gen:
                    self._jpeg_cache = {"gen": gen}
                self._jpeg_cache[key] = data
        return data

    def get_targets(self) -> Dict[str, Any]:
        with self._lock:
            result = dict(self._targets)
        result["age_sec"] = round(time.time() - self._targets_time, 2) if self._targets_time else None
        return result

    # ---------------- loop ----------------
    def _loop(self) -> None:
        last_track = 0.0
        last_dvr = 0.0
        while self._running:
            started = time.time()
            try:
                frames = self.stream_manager.get_all_active_frames()
                if frames:
                    self.tracker_engine.set_camera_sizes({cid: (f.shape[1], f.shape[0]) for cid, f in frames.items()})
                    clean = self.stitcher.stitch_frames(frames, overlay_annotations=False)
                    with self._lock:
                        self._clean = clean
                        self._clean_time = started
                        self._generation += 1

                    if started - last_track >= self.tracking_interval:
                        last_track = started
                        result = self.tracker_engine.process_stitched_frame(clean)
                        result["stale"] = False
                        with self._lock:
                            self._targets = result
                            self._targets_time = time.time()

                    if started - last_dvr >= self.dvr_interval:
                        last_dvr = started
                        data = self.get_jpeg(overlay=True, quality=80, use_cache=False)
                        if data:
                            self.dvr_manager.add_frame(data, datetime.datetime.now(datetime.timezone.utc))
                self.last_error = None
            except Exception as e:  # keep the pipeline alive, but never fail silently
                msg = f"{type(e).__name__}: {e}"
                if msg != self.last_error:
                    logger.exception("CompositeWorker cycle failed")
                self.last_error = msg
            elapsed = time.time() - started
            time.sleep(max(0.02, self.composite_interval - elapsed))
