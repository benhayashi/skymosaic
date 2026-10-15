import collections
import datetime
import logging
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Tuple


class DVRFrame:
    __slots__ = ("timestamp_utc", "jpeg_bytes", "targets_count", "weather_state")

    def __init__(
        self,
        timestamp_utc: datetime.datetime,
        jpeg_bytes: bytes,
        targets_count: int = 0,
        weather_state: str = ""
    ):
        self.timestamp_utc = timestamp_utc
        self.jpeg_bytes = jpeg_bytes
        self.targets_count = targets_count
        self.weather_state = weather_state


class DVRManager:
    """
    Manages rolling ring buffer of live stitched frames for instant DVR rewind and playback.
    Enables users to pause the live stream, rewind by arbitrary seconds or minutes,
    and scrub backwards through recent footage without loss of real-time capture.
    """

    def __init__(self, max_frames: int = 1800, archive_dir: Optional[str] = None):
        self.max_frames = max_frames
        self.archive_dir = Path(archive_dir) if archive_dir else None
        self._buffer: collections.deque[DVRFrame] = collections.deque(maxlen=max_frames)
        self._lock = threading.Lock()
        self._last_added_time: Optional[datetime.datetime] = None
        self._recording_running = False
        self._recording_thread: Optional[threading.Thread] = None

    def start_auto_recording(self, stream_manager: Any, stitcher: Any, interval_sec: float = 1.5) -> None:
        """Starts a background loop to continuously record stitched frames into the DVR ring buffer."""
        if self._recording_running:
            return
        self._recording_running = True

        def _record_loop():
            import time
            import cv2
            while self._recording_running:
                try:
                    now = datetime.datetime.now(datetime.timezone.utc)
                    # Only record if no frame was added within interval_sec
                    with self._lock:
                        last = self._last_added_time
                    if last is None or (now - last).total_seconds() >= interval_sec:
                        frames = stream_manager.get_all_active_frames()
                        if frames:
                            composite = stitcher.stitch_frames(frames, overlay_annotations=True)
                            ret, jpeg = cv2.imencode(".jpg", composite, [cv2.IMWRITE_JPEG_QUALITY, 80])
                            if ret:
                                self.add_frame(jpeg.tobytes(), now)
                except Exception:
                    logging.getLogger(__name__).exception("DVR recorder cycle failed")
                time.sleep(interval_sec)

        self._recording_thread = threading.Thread(target=_record_loop, daemon=True, name="DVRRecorder")
        self._recording_thread.start()

    def stop_auto_recording(self) -> None:
        self._recording_running = False


    def add_frame(
        self,
        jpeg_bytes: bytes,
        dt_utc: Optional[datetime.datetime] = None,
        targets_count: int = 0,
        weather_state: str = ""
    ) -> None:
        """Adds a live stitched JPEG frame to the rolling ring buffer."""
        if dt_utc is None:
            dt_utc = datetime.datetime.now(datetime.timezone.utc)
        elif dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=datetime.timezone.utc)

        # Suppress adding duplicates if less than 0.25s apart
        with self._lock:
            if self._last_added_time is not None:
                delta = (dt_utc - self._last_added_time).total_seconds()
                if delta < 0.25:
                    return

            frame = DVRFrame(
                timestamp_utc=dt_utc,
                jpeg_bytes=jpeg_bytes,
                targets_count=targets_count,
                weather_state=weather_state
            )
            self._buffer.append(frame)
            self._last_added_time = dt_utc

    def get_timeline(self, max_keyframes: int = 120) -> Dict[str, Any]:
        """Returns metadata about the currently available DVR time-shift buffer."""
        now = datetime.datetime.now(datetime.timezone.utc)
        with self._lock:
            if not self._buffer:
                return {
                    "enabled": True,
                    "frame_count": 0,
                    "buffer_duration_sec": 0.0,
                    "start_time": now.isoformat(),
                    "end_time": now.isoformat(),
                    "keyframes": []
                }

            start_dt = self._buffer[0].timestamp_utc
            end_dt = self._buffer[-1].timestamp_utc
            total_duration = max(0.0, (end_dt - start_dt).total_seconds())

            # Sample keyframes evenly across buffer
            count = len(self._buffer)
            step = max(1, count // max_keyframes)
            keyframes: List[Dict[str, Any]] = []

            for i in range(0, count, step):
                f = self._buffer[i]
                offset_sec = (f.timestamp_utc - end_dt).total_seconds()
                keyframes.append({
                    "timestamp": f.timestamp_utc.isoformat(),
                    "offset_sec": round(offset_sec, 2),
                    "targets": f.targets_count,
                    "weather": f.weather_state
                })

            # Always ensure the last frame (Live) is present
            if keyframes and keyframes[-1]["timestamp"] != end_dt.isoformat():
                keyframes.append({
                    "timestamp": end_dt.isoformat(),
                    "offset_sec": 0.0,
                    "targets": self._buffer[-1].targets_count,
                    "weather": self._buffer[-1].weather_state
                })

            return {
                "enabled": True,
                "frame_count": count,
                "buffer_duration_sec": round(total_duration, 2),
                "start_time": start_dt.isoformat(),
                "end_time": end_dt.isoformat(),
                "keyframes": keyframes
            }

    def get_frame_by_offset(self, offset_sec: float) -> Optional[Tuple[bytes, datetime.datetime]]:
        """
        Retrieves frame closest to relative offset in seconds (e.g. 0.0 = live, -60.0 = 1 minute ago).
        """
        with self._lock:
            if not self._buffer:
                return None

            latest_dt = self._buffer[-1].timestamp_utc
            target_dt = latest_dt + datetime.timedelta(seconds=offset_sec)
            return self._find_closest_in_buffer(target_dt)

    def get_frame_by_timestamp(self, target_dt: datetime.datetime) -> Optional[Tuple[bytes, datetime.datetime]]:
        """Retrieves frame closest to specified ISO/UTC datetime."""
        if target_dt.tzinfo is None:
            target_dt = target_dt.replace(tzinfo=datetime.timezone.utc)

        with self._lock:
            if self._buffer:
                # If target is within or near buffer range
                first_dt = self._buffer[0].timestamp_utc
                last_dt = self._buffer[-1].timestamp_utc

                if target_dt >= first_dt - datetime.timedelta(seconds=5):
                    return self._find_closest_in_buffer(target_dt)

        # Fallback to historical disk archive if before buffer
        if self.archive_dir and self.archive_dir.exists():
            return self._find_closest_in_archive(target_dt)

        return None

    def _find_closest_in_buffer(self, target_dt: datetime.datetime) -> Optional[Tuple[bytes, datetime.datetime]]:
        """Finds closest frame in the ring buffer."""
        if not self._buffer:
            return None
        best_frame = min(self._buffer, key=lambda f: abs((f.timestamp_utc - target_dt).total_seconds()))
        return best_frame.jpeg_bytes, best_frame.timestamp_utc


    def _find_closest_in_archive(self, target_dt: datetime.datetime) -> Optional[Tuple[bytes, datetime.datetime]]:
        """Finds closest saved JPEG in data/archive/ directory."""
        if not self.archive_dir or not self.archive_dir.exists():
            return None

        files = sorted(self.archive_dir.glob("skymosaic_*.jpg"))
        if not files:
            return None

        best_file = None
        best_dt = None
        min_diff = float("inf")

        for f in files:
            # Parse timestamp: skymosaic_YYYYMMDD_HHMMSS.jpg
            name = f.stem
            parts = name.split("_")
            if len(parts) >= 3:
                date_str = f"{parts[1]}_{parts[2]}"
                try:
                    file_dt = datetime.datetime.strptime(date_str, "%Y%m%d_%H%M%S").replace(
                        tzinfo=datetime.timezone.utc
                    )
                    diff = abs((file_dt - target_dt).total_seconds())
                    if diff < min_diff:
                        min_diff = diff
                        best_file = f
                        best_dt = file_dt
                except Exception:
                    continue

        if best_file and best_file.exists():
            try:
                data = best_file.read_bytes()
                return data, best_dt or target_dt
            except Exception:
                return None

        return None
