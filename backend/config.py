import json
import os
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class LensModelType(str, Enum):
    RECTILINEAR = "rectilinear"
    FISHEYE_EQUIDISTANT = "fisheye_equidistant"  # r = f * theta
    FISHEYE_EQUISOLID = "fisheye_equisolid"      # r = 2 * f * sin(theta / 2)
    FISHEYE_STEREOGRAPHIC = "fisheye_stereographic"  # r = 2 * f * tan(theta / 2)
    FISHEYE_ORTHOGRAPHIC = "fisheye_orthographic"    # r = f * sin(theta)


class CameraSourceType(str, Enum):
    RTSP = "rtsp"
    USB = "usb"
    DIRECTORY = "directory"
    SYNTHETIC = "synthetic"


class StitchTargetProjection(str, Enum):
    ZENITHAL_POLAR = "zenithal_polar"    # Standard circular allsky dome
    EQUIRECTANGULAR = "equirectangular"  # 360x90 degree cylinder


class ObserverConfig(BaseModel):
    name: str = "Primary Station"
    latitude: float = Field(default=40.7128, description="Latitude in decimal degrees (+ North)")
    longitude: float = Field(default=-74.0060, description="Longitude in decimal degrees (+ East, - West)")
    elevation_m: float = Field(default=10.0, description="Elevation above sea level in meters")
    timezone_str: str = Field(default="UTC", description="Observer timezone string, e.g. UTC or America/New_York")


class CameraExtrinsics(BaseModel):
    azimuth_deg: float = Field(default=0.0, description="Pointing azimuth in degrees (0=North, 90=East, 180=South, 270=West)")
    altitude_deg: float = Field(default=45.0, description="Pointing altitude/elevation in degrees (0=Horizon, 90=Zenith)")
    roll_deg: float = Field(default=0.0, description="Rotation around optical axis in degrees clockwise")


class CameraIntrinsics(BaseModel):
    lens_model: LensModelType = LensModelType.RECTILINEAR
    fov_deg: float = Field(default=85.0, description="Diagonal or horizontal field of view in degrees")
    fov_axis: str = Field(default="horizontal", description="'horizontal' or 'diagonal'")
    center_offset_x: float = Field(default=0.0, description="Optical center offset X in fraction of image width (-0.5 to 0.5)")
    center_offset_y: float = Field(default=0.0, description="Optical center offset Y in fraction of image height (-0.5 to 0.5)")
    k1: float = Field(default=0.0, description="Radial distortion coefficient 1")
    k2: float = Field(default=0.0, description="Radial distortion coefficient 2")
    p1: float = Field(default=0.0, description="Tangential distortion coefficient 1")
    p2: float = Field(default=0.0, description="Tangential distortion coefficient 2")


class CameraConfig(BaseModel):
    id: str
    name: str
    source_type: CameraSourceType = CameraSourceType.SYNTHETIC
    url_or_path: str = ""
    enabled: bool = True
    extrinsics: CameraExtrinsics = Field(default_factory=CameraExtrinsics)
    intrinsics: CameraIntrinsics = Field(default_factory=CameraIntrinsics)
    polygon_mask: List[List[float]] = Field(
        default_factory=list,
        description="List of [x, y] normalized vertices (0.0 to 1.0) defining masked/ignored areas (ground/obstructions)"
    )
    gain_scale: float = Field(default=1.0, description="Brightness gain multiplier for exposure matching")


class StitchingConfig(BaseModel):
    projection: StitchTargetProjection = StitchTargetProjection.ZENITHAL_POLAR
    canvas_size: int = Field(default=1200, description="Diameter or width/height of the stitched allsky canvas in pixels")
    feather_pixels: int = Field(default=25, description="Overlap feathering width in pixels")
    exposure_balance: bool = Field(default=True, description="Enable automatic cross-camera brightness normalization")
    horizon_mask_radius: float = Field(default=0.98, description="Fraction of radius to clip at dome horizon")
    overlay_timestamp: bool = Field(default=True, description="Render live timestamp overlay onto final stitched view")
    time_format: str = Field(default="zulu", description="'zulu' (UTC / Z) or 'local' (Observer timezone)")
    ntp_enabled: bool = Field(default=True, description="Synchronize timestamp from Network Time Protocol (NTP)")
    ntp_server: str = Field(default="pool.ntp.org", description="NTP server address")



class OperatingMode(str, Enum):
    FEED = "feed"                    # Real-time high-FPS stream with live object tracking & recording
    LONG_EXPOSURE = "long_exposure"  # Longer duration stacked exposures for celestial events
    LIVE_TESTING = "live_testing"    # Test preview mode: live stream and overlays active, ZERO disk writes / recording paused


class ADSBConfig(BaseModel):
    enabled: bool = True
    url: str = Field(default="http://localhost:8080/data/aircraft.json", description="dump1090/readsb aircraft.json endpoint")
    max_range_km: float = Field(default=250.0, description="Maximum detection radius in kilometers")
    use_simulation: bool = Field(default=True, description="Enable simulated air traffic if no local SDR feed is connected")


class SatelliteConfig(BaseModel):
    enabled: bool = True
    max_magnitude: float = Field(default=4.5, description="Maximum visible visual magnitude to track")
    update_interval_sec: float = Field(default=2.0, description="Ephemeris update interval in seconds")
    use_simulation: bool = Field(default=True, description="Enable simulated satellite passes (ISS, Starlink, etc.)")


class TrackingConfig(BaseModel):
    enabled: bool = True
    min_motion_pixels: float = Field(default=2.5, description="Minimum frame-to-frame pixel movement for motion detection")
    match_tolerance_deg: float = Field(default=2.5, description="Angular tolerance in degrees for celestial/aircraft matching")
    uap_auto_record: bool = Field(default=True, description="Automatically record and archive unclassified / anomalous sky events")


class AIPluginsConfig(BaseModel):
    weather_enabled: bool = Field(default=True, description="Enable automated sky condition and cloud cover vision estimator")
    daytime_detector_enabled: bool = Field(default=True, description="Enable daytime object detection (birds, drones, planes)")
    meteor_detector_enabled: bool = Field(default=True, description="Enable fast transient meteor and fireball streak detector")


class StorageConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable automatic remote share syncing and retention enforcement")
    remote_path: str = Field(default="", description="Path to remote network share, mounted SMB directory, or local backup directory")
    sync_interval_minutes: int = Field(default=60, description="Backup sync interval in minutes")
    retention_days: int = Field(default=30, description="Local snapshot and media retention in days (0 = keep indefinitely)")
    delete_local_after_sync: bool = Field(default=False, description="Delete local copies once verified copied to remote share")
    backup_snapshots: bool = Field(default=True, description="Sync nightly snapshots to remote storage")
    backup_timelapses: bool = Field(default=True, description="Sync timelapses to remote storage")
    backup_keograms: bool = Field(default=True, description="Sync keograms to remote storage")
    backup_startrails: bool = Field(default=True, description="Sync startrail images to remote storage")


class PipelineConfig(BaseModel):
    capture_interval_sec: float = Field(default=10.0, description="Interval between snapshot captures in long-exposure mode")
    long_exposure_duration_sec: float = Field(default=15.0, description="Exposure duration in seconds for celestial events")
    snapshot_resolution: str = Field(default="original", description="'original', '1920x1080', '1280x720', or '800x600'")
    snapshot_jpeg_quality: int = Field(default=85, description="JPEG quality (1-100) for saved snapshots")
    timelapse_fps: int = Field(default=25, description="Frames per second for generated timelapse videos")
    timelapse_resolution: str = Field(default="original", description="'original', '1920x1080', '1280x720', or '800x600'")
    keogram_slice_axis: str = Field(default="vertical", description="'vertical' column or 'radial' circular slice")
    startrails_threshold: float = Field(default=0.05, description="Luminance threshold to accumulate stars")


class SkyViewConfig(BaseModel):
    mode: OperatingMode = Field(default=OperatingMode.FEED, description="Operating mode: 'feed' or 'long_exposure'")
    observer: ObserverConfig = Field(default_factory=ObserverConfig)
    cameras: List[CameraConfig] = Field(default_factory=list)
    stitching: StitchingConfig = Field(default_factory=StitchingConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    adsb: ADSBConfig = Field(default_factory=ADSBConfig)
    satellites: SatelliteConfig = Field(default_factory=SatelliteConfig)
    tracking: TrackingConfig = Field(default_factory=TrackingConfig)
    plugins: AIPluginsConfig = Field(default_factory=AIPluginsConfig)
    data_dir: str = Field(default="data", description="Directory for storing test shots, masks, archives, and outputs")



def get_default_config() -> SkyViewConfig:
    """Creates a sensible default configuration with 3 simulated multi-angle cameras covering the sky."""
    cfg = SkyViewConfig()
    cfg.cameras = [
        CameraConfig(
            id="cam_north",
            name="North Sky Camera",
            source_type=CameraSourceType.SYNTHETIC,
            url_or_path="",
            enabled=True,
            extrinsics=CameraExtrinsics(azimuth_deg=0.0, altitude_deg=45.0, roll_deg=0.0),
            intrinsics=CameraIntrinsics(
                lens_model=LensModelType.RECTILINEAR,
                fov_deg=85.0,
                fov_axis="horizontal",
                k1=-0.05
            ),
            polygon_mask=[[0.0, 0.85], [1.0, 0.85], [1.0, 1.0], [0.0, 1.0]]  # Mask bottom ground
        ),
        CameraConfig(
            id="cam_southeast",
            name="South-East Sky Camera",
            source_type=CameraSourceType.SYNTHETIC,
            url_or_path="",
            enabled=True,
            extrinsics=CameraExtrinsics(azimuth_deg=120.0, altitude_deg=45.0, roll_deg=0.0),
            intrinsics=CameraIntrinsics(
                lens_model=LensModelType.RECTILINEAR,
                fov_deg=85.0,
                fov_axis="horizontal",
                k1=-0.05
            ),
            polygon_mask=[[0.0, 0.85], [1.0, 0.85], [1.0, 1.0], [0.0, 1.0]]
        ),
        CameraConfig(
            id="cam_southwest",
            name="South-West Sky Camera",
            source_type=CameraSourceType.SYNTHETIC,
            url_or_path="",
            enabled=True,
            extrinsics=CameraExtrinsics(azimuth_deg=240.0, altitude_deg=45.0, roll_deg=0.0),
            intrinsics=CameraIntrinsics(
                lens_model=LensModelType.RECTILINEAR,
                fov_deg=85.0,
                fov_axis="horizontal",
                k1=-0.05
            ),
            polygon_mask=[[0.0, 0.85], [1.0, 0.85], [1.0, 1.0], [0.0, 1.0]]
        ),
    ]
    return cfg


def load_config(config_path: str = "data/config.json") -> SkyViewConfig:
    p = Path(config_path)
    if not p.exists():
        cfg = get_default_config()
        save_config(cfg, config_path)
        return cfg
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return SkyViewConfig.model_validate(data)
    except Exception as e:
        print(f"Warning: Failed to load config from {config_path} ({e}). Using defaults.")
        return get_default_config()


def save_config(cfg: SkyViewConfig, config_path: str = "data/config.json") -> None:
    p = Path(config_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(cfg.model_dump_json(indent=2))
