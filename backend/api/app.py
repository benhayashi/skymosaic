import datetime
import os
from pathlib import Path
from typing import Dict, List, Optional
import cv2
from fastapi import FastAPI, HTTPException, Query, Response, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import numpy as np

from backend.astrometry.celestial import (
    BRIGHT_STARS,
    CONSTELLATION_LINES,
    get_constellation_segments,
    get_sun_coordinates,
    get_visible_stars,
)
from backend.astrometry.solver import detect_star_centroids, solve_camera_pose_from_matches
from backend.config import (
    CameraConfig,
    CameraExtrinsics,
    CameraIntrinsics,
    LensModelType,
    SkyViewConfig,
    load_config,
    save_config,
)
from backend.ingestion.stream_manager import StreamManager
from backend.config import OperatingMode
from backend.pipeline.media_generator import MediaGenerator
from backend.pipeline.storage_manager import StorageManager
from backend.stitching.stitcher import MultiCameraStitcher
from backend.tracking.optical_tracker import OpticalTrackerEngine


# Global application state
CONFIG_PATH = "data/config.json"
app_config: SkyViewConfig = load_config(CONFIG_PATH)
stream_manager = StreamManager(app_config)
stitcher = MultiCameraStitcher(app_config)
tracker_engine = OpticalTrackerEngine(app_config)
storage_manager = StorageManager(app_config)
storage_manager.start()

app = FastAPI(title="SkyMosaic: Multi-Camera AllSky System", version="1.0.0")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request & Response models
class SolveCalibrationRequest(BaseModel):
    camera_id: str
    matches: List[Dict]  # [{'pixel_x': float, 'pixel_y': float, 'star_id': str}]
    timestamp_iso: Optional[str] = None
    lens_model: LensModelType = LensModelType.RECTILINEAR
    init_fov: Optional[float] = None
    img_width: int = 1280
    img_height: int = 720


class StarDetectionRequest(BaseModel):
    camera_id: Optional[str] = None
    image_path: Optional[str] = None


class GenerateMediaRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    slice_width: int = 2
    fps: int = 25
    threshold: float = 0.05


# API Routes

class ModeChangeRequest(BaseModel):
    mode: OperatingMode


@app.get("/api/config")
def get_config():
    return app_config


@app.post("/api/config")
def update_config(new_config: SkyViewConfig):
    global app_config
    app_config = new_config
    save_config(app_config, CONFIG_PATH)
    stream_manager.update_config(app_config)
    stitcher.update_config(app_config)
    tracker_engine.update_config(app_config)
    storage_manager.update_config(app_config)
    return {"status": "ok", "message": "Configuration updated successfully."}


@app.get("/api/mode")
def get_operating_mode():
    return {"mode": app_config.mode.value}


@app.post("/api/mode")
def set_operating_mode(req: ModeChangeRequest):
    app_config.mode = req.mode
    save_config(app_config, CONFIG_PATH)
    tracker_engine.update_config(app_config)
    return {"status": "ok", "mode": app_config.mode.value}


@app.get("/api/tracking/targets")
def get_tracked_targets():
    frames = stream_manager.get_all_active_frames()
    composite = stitcher.stitch_frames(frames, overlay_annotations=False)
    output = tracker_engine.process_stitched_frame(composite)
    return output


class TestADSBRequest(BaseModel):
    url: Optional[str] = None


@app.get("/api/tracking/adsb")
def get_adsb_aircraft():
    aircraft = tracker_engine.adsb_tracker.poll_aircraft()
    return {"count": len(aircraft), "aircraft": aircraft}


@app.post("/api/tracking/adsb/test")
def test_adsb_connection(req: TestADSBRequest):
    return tracker_engine.adsb_tracker.test_connection(req.url)


@app.get("/api/tracking/satellites")
def get_satellites():
    sats = tracker_engine.satellite_tracker.compute_satellite_passes()
    return {"count": len(sats), "satellites": sats}


class NTPSyncRequest(BaseModel):
    server: Optional[str] = None


@app.get("/api/time/ntp")
def get_ntp_status():
    status = stitcher.ntp_client.get_status()
    tz_str = app_config.observer.timezone_str
    status["zulu_time"] = stitcher.ntp_client.format_timestamp("zulu")
    status["local_time"] = stitcher.ntp_client.format_timestamp("local", tz_str)
    status["time_format"] = app_config.stitching.time_format
    status["overlay_timestamp"] = app_config.stitching.overlay_timestamp
    return status


@app.post("/api/time/sync")
def trigger_ntp_sync(req: Optional[NTPSyncRequest] = None):
    srv = req.server if req and req.server else app_config.stitching.ntp_server
    success = stitcher.ntp_client.sync(srv)
    status = stitcher.ntp_client.get_status()
    return {"success": success, "status": status}



@app.get("/api/weather/sky_conditions")
def get_sky_conditions():
    frames = stream_manager.get_all_active_frames()
    composite = stitcher.stitch_frames(frames, overlay_annotations=False)
    dt_utc = datetime.datetime.now(datetime.timezone.utc)
    sun_info = get_sun_coordinates(dt_utc, app_config.observer.latitude, app_config.observer.longitude)
    weather = tracker_engine.weather_plugin.process_frame(
        composite,
        metadata={
            "sun_altitude_deg": sun_info["altitude_deg"],
            "sky_state": sun_info["sky_state"],
            "is_day": sun_info["sky_state"] == "day"
        }
    )
    return {
        "sun": sun_info,
        "weather": weather
    }


@app.get("/api/events/unidentified")
def get_unidentified_events():
    events = tracker_engine.get_unidentified_events()
    return {"count": len(events), "events": events}



@app.get("/api/cameras")
def list_cameras():
    cam_list = []
    active_frames = stream_manager.get_all_active_frames()
    for cam in app_config.cameras:
        frame = active_frames.get(cam.id)
        res = f"{frame.shape[1]}x{frame.shape[0]}" if frame is not None else "No feed"
        cam_list.append({
            "config": cam,
            "has_feed": frame is not None,
            "resolution": res
        })
    return cam_list


@app.post("/api/cameras")
def add_camera(camera: CameraConfig):
    if any(c.id == camera.id for c in app_config.cameras):
        raise HTTPException(status_code=400, detail="Camera with this ID already exists.")
    app_config.cameras.append(camera)
    save_config(app_config, CONFIG_PATH)
    stream_manager.update_config(app_config)
    stitcher.update_config(app_config)
    return {"status": "ok", "camera": camera}


@app.put("/api/cameras/{camera_id}")
def update_camera(camera_id: str, updated_cam: CameraConfig):
    for i, cam in enumerate(app_config.cameras):
        if cam.id == camera_id:
            app_config.cameras[i] = updated_cam
            save_config(app_config, CONFIG_PATH)
            stream_manager.update_config(app_config)
            stitcher.update_config(app_config)
            return {"status": "ok", "camera": updated_cam}
    raise HTTPException(status_code=404, detail="Camera not found.")


@app.delete("/api/cameras/{camera_id}")
def delete_camera(camera_id: str):
    app_config.cameras = [c for c in app_config.cameras if c.id != camera_id]
    save_config(app_config, CONFIG_PATH)
    stream_manager.update_config(app_config)
    stitcher.update_config(app_config)
    return {"status": "ok"}


@app.get("/api/cameras/{camera_id}/frame")
def get_camera_frame(camera_id: str):
    frame = stream_manager.get_camera_frame(camera_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not available for this camera.")
    ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ret:
        raise HTTPException(status_code=500, detail="Failed to encode JPEG.")
    return Response(content=jpeg.tobytes(), media_type="image/jpeg")


@app.post("/api/cameras/{camera_id}/test_shot")
def take_test_shot(camera_id: str):
    path = stream_manager.save_test_shot(camera_id, output_dir=os.path.join(app_config.data_dir, "test_shots"))
    if not path:
        raise HTTPException(status_code=500, detail="Failed to capture test shot.")
    rel_path = os.path.relpath(path, start=".")
    return {"status": "ok", "file_path": rel_path, "filename": os.path.basename(path)}


@app.get("/api/astrometry/stars")
def get_astrometry_stars(timestamp_iso: Optional[str] = None):
    if timestamp_iso:
        try:
            dt = datetime.datetime.fromisoformat(timestamp_iso)
        except Exception:
            dt = datetime.datetime.now(datetime.timezone.utc)
    else:
        dt = datetime.datetime.now(datetime.timezone.utc)

    obs = app_config.observer
    visible = get_visible_stars(dt, obs.latitude, obs.longitude, min_alt_deg=-2.0)
    return {
        "timestamp_utc": dt.isoformat(),
        "observer": {"lat": obs.latitude, "lon": obs.longitude},
        "stars": visible
    }


@app.get("/api/astrometry/constellations")
def get_constellations(timestamp_iso: Optional[str] = None):
    if timestamp_iso:
        try:
            dt = datetime.datetime.fromisoformat(timestamp_iso)
        except Exception:
            dt = datetime.datetime.now(datetime.timezone.utc)
    else:
        dt = datetime.datetime.now(datetime.timezone.utc)

    obs = app_config.observer
    visible_stars = get_visible_stars(dt, obs.latitude, obs.longitude, min_alt_deg=-2.0)
    stars_dict = {s["id"]: s for s in visible_stars}
    segments = get_constellation_segments(stars_dict)
    return {"segments": [[s1, s2] for s1, s2 in segments]}


@app.post("/api/calibration/detect_stars")
def detect_stars_endpoint(req: StarDetectionRequest):
    img = None
    if req.camera_id:
        img = stream_manager.get_camera_frame(req.camera_id)
    elif req.image_path:
        img = cv2.imread(req.image_path)

    if img is None:
        raise HTTPException(status_code=400, detail="No valid image found for star detection.")

    stars = detect_star_centroids(img)
    return {"stars": stars, "count": len(stars), "width": img.shape[1], "height": img.shape[0]}


@app.post("/api/calibration/solve")
def solve_calibration_endpoint(req: SolveCalibrationRequest):
    if req.timestamp_iso:
        try:
            dt = datetime.datetime.fromisoformat(req.timestamp_iso)
        except Exception:
            dt = datetime.datetime.now(datetime.timezone.utc)
    else:
        dt = datetime.datetime.now(datetime.timezone.utc)

    obs = app_config.observer
    cam_cfg = next((c for c in app_config.cameras if c.id == req.camera_id), None)
    init_ext = cam_cfg.extrinsics if cam_cfg else None

    try:
        solution = solve_camera_pose_from_matches(
            matches=req.matches,
            dt=dt,
            lat_deg=obs.latitude,
            lon_deg=obs.longitude,
            img_w=req.img_width,
            img_h=req.img_height,
            lens_model=req.lens_model,
            init_extrinsics=init_ext,
            init_fov=req.init_fov
        )
        return solution
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/sky/live.jpg")
def get_live_stitched_frame(
    overlay: bool = True,
    timestamp: Optional[bool] = None,
    time_format: Optional[str] = None
):
    frames = stream_manager.get_all_active_frames()
    # Temporarily override stitcher timestamp settings if query params provided
    orig_ts = stitcher.config.stitching.overlay_timestamp
    orig_fmt = stitcher.config.stitching.time_format

    if timestamp is not None:
        stitcher.config.stitching.overlay_timestamp = timestamp
    if time_format is not None:
        stitcher.config.stitching.time_format = time_format

    try:
        composite = stitcher.stitch_frames(frames, overlay_annotations=overlay)
    finally:
        stitcher.config.stitching.overlay_timestamp = orig_ts
        stitcher.config.stitching.time_format = orig_fmt

    ret, jpeg = cv2.imencode(".jpg", composite, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ret:
        raise HTTPException(status_code=500, detail="Failed to encode composite.")
    return Response(content=jpeg.tobytes(), media_type="image/jpeg")



def mjpeg_stream_generator():
    while True:
        try:
            frames = stream_manager.get_all_active_frames()
            composite = stitcher.stitch_frames(frames, overlay_annotations=True)
            ret, jpeg = cv2.imencode(".jpg", composite, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ret:
                frame_bytes = jpeg.tobytes()
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")
        except Exception as e:
            print(f"Error in MJPEG stream: {e}")
        import time
        time.sleep(0.1)  # ~10 FPS stream


@app.get("/api/sky/stream")
def get_mjpeg_stream():
    return StreamingResponse(
        mjpeg_stream_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.post("/api/pipeline/capture_now")
def capture_stitched_snapshot():
    frames = stream_manager.get_all_active_frames()
    composite = stitcher.stitch_frames(frames, overlay_annotations=True)

    # Apply resolution scaling if configured
    res_str = app_config.pipeline.snapshot_resolution
    if res_str and res_str != "original" and "x" in res_str:
        try:
            target_w, target_h = [int(v) for v in res_str.split("x")]
            composite = cv2.resize(composite, (target_w, target_h), interpolation=cv2.INTER_AREA)
        except Exception as e:
            print(f"Error resizing snapshot: {e}")

    archive_dir = Path(app_config.data_dir) / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"skymosaic_{timestamp_str}.jpg"
    full_path = archive_dir / filename

    quality = max(1, min(100, app_config.pipeline.snapshot_jpeg_quality))
    cv2.imwrite(str(full_path), composite, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return {"status": "ok", "filename": filename, "path": str(full_path)}


@app.get("/api/gallery")
def get_gallery():
    data_path = Path(app_config.data_dir)
    archive_dir = data_path / "archive"
    timelapse_dir = data_path / "timelapses"
    keogram_dir = data_path / "keograms"
    startrail_dir = data_path / "startrails"

    def list_files(d: Path, exts):
        if not d.exists():
            return []
        items = []
        for ext in exts:
            for f in d.glob(ext):
                items.append({
                    "name": f.name,
                    "size_bytes": f.stat().st_size,
                    "modified": f.stat().st_mtime,
                    "url": f"/data/{f.relative_to(data_path)}"
                })
        items.sort(key=lambda x: x["modified"], reverse=True)
        return items

    return {
        "snapshots": list_files(archive_dir, ["*.jpg", "*.png"]),
        "timelapses": list_files(timelapse_dir, ["*.mp4"]),
        "keograms": list_files(keogram_dir, ["*.jpg", "*.png"]),
        "startrails": list_files(startrail_dir, ["*.jpg", "*.png"])
    }


@app.post("/api/pipeline/generate_keogram")
def trigger_generate_keogram(req: GenerateMediaRequest):
    archive_dir = Path(app_config.data_dir) / "archive"
    files = sorted(list(archive_dir.glob("*.jpg")))
    if not files:
        raise HTTPException(status_code=400, detail="No archived snapshots found to generate Keogram.")

    out_file = Path(app_config.data_dir) / "keograms" / f"keogram_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    res = MediaGenerator.generate_keogram([str(f) for f in files], str(out_file), slice_width=req.slice_width)
    if not res:
        raise HTTPException(status_code=500, detail="Failed to build Keogram.")
    return {"status": "ok", "output_path": res, "filename": out_file.name}


@app.post("/api/pipeline/generate_startrails")
def trigger_generate_startrails(req: GenerateMediaRequest):
    archive_dir = Path(app_config.data_dir) / "archive"
    files = sorted(list(archive_dir.glob("*.jpg")))
    if not files:
        raise HTTPException(status_code=400, detail="No archived snapshots found to generate Startrails.")

    out_file = Path(app_config.data_dir) / "startrails" / f"startrails_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    res = MediaGenerator.generate_startrails([str(f) for f in files], str(out_file), threshold=req.threshold)
    if not res:
        raise HTTPException(status_code=500, detail="Failed to build Startrails.")
    return {"status": "ok", "output_path": res, "filename": out_file.name}


@app.post("/api/pipeline/generate_timelapse")
def trigger_generate_timelapse(req: GenerateMediaRequest):
    archive_dir = Path(app_config.data_dir) / "archive"
    files = sorted(list(archive_dir.glob("*.jpg")))
    if len(files) < 2:
        raise HTTPException(status_code=400, detail="At least 2 archived snapshots required to generate a timelapse.")

    out_file = Path(app_config.data_dir) / "timelapses" / f"timelapse_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    target_size = None
    res_str = app_config.pipeline.timelapse_resolution
    if res_str and res_str != "original" and "x" in res_str:
        try:
            target_size = tuple(int(v) for v in res_str.split("x"))
        except Exception:
            pass

    res = MediaGenerator.generate_timelapse([str(f) for f in files], str(out_file), fps=req.fps, target_size=target_size)
    if not res:
        raise HTTPException(status_code=500, detail="Failed to build Timelapse.")
    return {"status": "ok", "output_path": res, "filename": out_file.name}


# Storage & Remote Backup Endpoints

class TestRemotePathRequest(BaseModel):
    remote_path: str


@app.get("/api/storage/status")
def get_storage_status():
    return storage_manager.get_status()


@app.post("/api/storage/test")
def test_remote_storage(req: TestRemotePathRequest):
    res = storage_manager.test_remote_connection(req.remote_path)
    return res


@app.post("/api/storage/sync")
def trigger_storage_sync():
    res = storage_manager.run_sync()
    return res


@app.post("/api/storage/prune")
def trigger_storage_prune():
    count = storage_manager.prune_old_files()
    return {"status": "ok", "pruned_files": count}


# Mount data directory for direct asset serving
data_dir_path = Path("data")
data_dir_path.mkdir(exist_ok=True)
app.mount("/data", StaticFiles(directory="data"), name="data")

# Mount frontend
frontend_dir_path = Path("frontend")
frontend_dir_path.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
