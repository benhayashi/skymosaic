<p align="center">
  <img src="frontend/assets/skymosaic_logo.jpg" alt="SkyMosaic Logo" width="220" style="border-radius: 24px;">
</p>

# SkyMosaic 🌌 — Multi-Camera AllSky & Celestial Stargazer Platform

[![CI/CD](https://github.com/benhayashi/skymosaic/actions/workflows/ci.yml/badge.svg)](https://github.com/benhayashi/skymosaic/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)](https://www.docker.com/)

**SkyMosaic** is an autonomous all-sky camera and celestial observation system engineered to overcome the physical limitations of single-camera setups. When mounting a single fisheye lens on a roof peak is impossible, obstructed, or costly, SkyMosaic lets you combine multiple directional or wide-angle cameras (RTSP, USB/V4L2, IP cameras, or file-drop directories from existing [Allsky](https://github.com/AllskyTeam/allsky) installations) with differing fields of view and lens distortions into a unified, seamless 360° AllSky dome or cylindrical panorama.

---

## Key Features

- **Multi-Camera Mosaic Stitching**:
  - Precomputed Coordinate Lookup Tables (`cv2.remap` float32 LUTs) for ultra-fast, real-time rendering at 30+ FPS.
  - Multi-camera distance-transform alpha feathering along overlapping seams with gain/exposure balancing.
  - Flexible target projections: Zenithal Polar (circular all-sky dome) or Cylindrical Equirectangular.
- **Dual Operating Modes**:
  - **Feed Mode**: High-framerate continuous video streaming (RTSP/IP/USB) with live point-light motion tracking and instant aerial object identification.
  - **Timelapse / Long-Exposure Mode**: Timed integration (5s–60s) for celestial events, meteor showers, startrails, and nightly time-lapse compilation.
- **Hierarchical Multi-Source Object Identification**:
  - **Tier 1 (Celestial)**: Real-time topocentric plate solving for stars and planets (Sun, Moon, Venus, Jupiter, Saturn, Mars) with constellation lines.
  - **Tier 2 (Satellites)**: Real SGP4 propagation of CelesTrak TLEs (stations + visual group by default, cached in `data/tle/`, refreshed every 12 h, works offline from the last cache). Only sunlit satellites above the horizon are shown. A simulated ISS pass is available only if you explicitly enable simulation.
  - **Tier 3 (Airplanes & ADS-B)**: Ingests live ADS-B from a local receiver (`readsb` JSON or native Protobuf `aircraft.pb`, `dump1090`, `tar1090`) or a free remote provider (adsb.lol / airplanes.live) by observer location, converts WGS84 coordinates to local sky $(Alt, Az)$, and correlates visual lights with flight number, aircraft type, altitude, and ground speed.
  - **Tier 4 (AI Vision Plugins)**: Real-time daytime sky condition and cloud cover estimator (octas / percentage) and daytime silhouette detector (birds, drones, planes) / nighttime meteor streak detector.
  - **Tier 5 (Unidentified Lights)**: Automatic triage for bright, persistently moving lights that match no ADS-B aircraft, satellite or celestial body (global exposure changes and slow cloud drift are rejected) with trajectory vectors, angular speed (deg/s), and event archiving.
- **Augmented Reality Sky Dome HUD**: Color-coded reticles (Yellow for planets, Purple for satellites, Cyan for flights, Red for unidentified lights) with flight callsigns and telemetry cards.
- **Celestial Calibration Studio**:
  - Interactive browser GUI with live astronomical planetarium overlay synced to your exact GPS coordinates and capture timestamp.
  - Real-time sliders for Pointing Azimuth ($0^\circ–360^\circ$), Altitude ($-10^\circ–90^\circ$), Roll, and Field of View ($20^\circ–220^\circ$).
  - Multi-lens distortion models: **Rectilinear** (Brown-Conrady $k_1, k_2$), **Fisheye Equidistant** ($r = f\theta$), **Equisolid** ($r = 2f\sin(\theta/2)$), **Stereographic**, and **Orthographic**.
  - **Star Anchor Point-and-Match Solver**: Click 2 or 3 visible stars on your camera photo (e.g., Polaris, Vega, Sirius), select their catalog names, and the Powell least-squares optimizer solves the orientation and FOV automatically.
  - **Obstacle & Horizon Masking**: Draw custom polygon masks on camera test shots to eliminate rooflines, chimneys, trees, or streetlights.
- **Astronomical Product Pipelines**:
  - **Keograms**: 24-hour vertical sky strips revealing cloud cover and auroras over time.
  - **Startrails**: Progressive maximum-intensity image stacking.
  - **Timelapses**: Automatic daily/nightly H.264 MP4 video assembly.
  - **Live Streams**: Fast MJPEG stream endpoint for Home Assistant, OBS, or web browsers.
- **Modern Responsive Web GUI**:
  - Live interactive sky dome with full-screen, zoom/pan, compass headings, and constellation lines.
  - Dedicated Astronomical Red-Light Theme for night-vision preservation in the field.

---

## Deployment & Installation

SkyMosaic supports **Linux**, **macOS**, **Windows**, and **Docker**.

### Option A: Docker (Recommended for All Platforms)

Docker gives you an isolated, reproducible environment with FFmpeg and OpenCV pre-configured:

```bash
# Clone the repository
git clone https://github.com/benhayashi/skymosaic.git
cd skymosaic

# Launch container
docker compose up -d
```

Open your browser to `http://localhost:8080`.

To view logs:
```bash
docker compose logs -f
```

---

### Option B: Linux / macOS (Native)

Using the automated one-click launcher script:

```bash
# Clone repository
git clone https://github.com/benhayashi/skymosaic.git
cd skymosaic

# Run with automated environment setup
./run.sh
```

Or using [uv](https://github.com/astral-sh/uv):
```bash
uv sync
uv run python main.py --port 8080
```

---

### Option C: Windows (Native)

1. Open PowerShell or Command Prompt.
2. Clone and run:
```cmd
git clone https://github.com/benhayashi/skymosaic.git
cd skymosaic
run.bat
```

Or run via `uv`:
```cmd
uv run python main.py --port 8080
```

---

## Connecting Physical Cameras

### 1. RTSP / IP Cameras
1. In the web dashboard, open the **Cameras** tab and click **+ Add Camera Feed**.
2. Select **RTSP Stream**.
3. Enter your camera RTSP URL (e.g. `rtsp://admin:password@192.168.1.50:554/stream1`).
4. Set approximate initial pointing direction (e.g., Azimuth: 90° for East, Altitude: 45°).

### 2. USB / V4L2 Webcams & Capture Cards
1. In the **Cameras** tab, select **USB / V4L2 Device**.
2. Enter the device index (`0`) or device path (`/dev/video0`).

### 3. Existing Allsky Directory Watcher
If you already run [Allsky](https://github.com/AllskyTeam/allsky) on a Raspberry Pi:
1. Share or mount the Allsky images folder (e.g., `/home/pi/allsky/images` or via Samba/NFS).
2. Add a camera with source type **Allsky Directory Watcher** pointing to that folder. SkyMosaic automatically ingests and integrates each new frame into the composite dome.

---

## Calibration Workflow

1. Go to the **Calibration Studio** tab.
2. Select the camera feed you wish to calibrate and click **Capture Snapshot** (or **Grab Live Frame**).
3. Ensure your site latitude, longitude, and elevation are configured in **Settings**.
4. **Visual Alignment**:
   - Turn on *Planetarium Stars* and *Constellations*.
   - Adjust the **Azimuth**, **Altitude**, and **Roll** sliders until the projected star overlays align with the stars visible in your test shot.
   - Select your lens profile (Rectilinear or Fisheye variants) and dial in the **FOV**.
5. **Point-and-Match Auto-Solver**:
   - Click on 2 or 3 recognizable bright stars directly on the photo.
   - Assign their catalog names from the dropdown menu (e.g., Polaris, Vega, Arcturus).
   - Click **Solve Pose & FOV**. The Powell solver will compute the exact orientation and focal length with sub-pixel precision.
6. **Masking Obstacles**:
   - Click **Draw Mask** and click points to outline rooflines, trees, or nearby streetlights.
   - Click **Finish Mask**.
7. Click **Save Calibration & Update Dome**. The stitched AllSky view updates immediately!

---

## Endpoints & Integrations

- **Web GUI**: `http://localhost:8080`
- **Live MJPEG Stream**: `http://localhost:8080/api/sky/stream` (embed into Home Assistant, OctoPrint, OBS)
- **Live Snapshot**: `http://localhost:8080/api/sky/live.jpg`
- **Active Aerial Targets**: `http://localhost:8080/api/tracking/targets`
- **ADS-B Traffic Feed**: `http://localhost:8080/api/tracking/adsb`
- **Satellite Overpasses**: `http://localhost:8080/api/tracking/satellites`
- **Weather & Cloud Conditions**: `http://localhost:8080/api/weather/sky_conditions`
- **Unidentified Events Archive**: `http://localhost:8080/api/events/unidentified`

---

## Automated Tests

Run the test suite across all modules:

```bash
uv run pytest
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
