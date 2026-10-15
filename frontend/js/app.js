// Global SkyView Application Controller

let appConfig = null;
let camerasList = [];
let skyViewer = null;
let calibrationStudio = null;
let currentUnitSystem = localStorage.getItem('skyview_unit_system') || 'metric'; // 'metric' or 'us'

document.addEventListener('DOMContentLoaded', async () => {
  initNightMode();
  initTabs();
  initUnitSystem();

  skyViewer = new SkyViewer('sky-canvas');
  calibrationStudio = new CalibrationStudio('calib-canvas');
  skyViewer.setUnitSystem(currentUnitSystem);

  await loadSystemConfig();
  await loadCameras();

  initCalibrationEvents();
  initActionButtons();
  initSettingsEvents();
  initModalEvents();
  initModeSwitcher();
  initGeolocationControls();
  startTrackingPoller();

  // Start live sky view
  skyViewer.start();
  updateTelemetry();
  setInterval(updateTelemetry, 5000);
});

// Unit system initialization and formatting helpers
function initUnitSystem() {
  const btnMetric = document.getElementById('btn-unit-metric');
  const btnUs = document.getElementById('btn-unit-us');
  const selectUnit = document.getElementById('set-unit-system');

  const setUnit = (unit, syncSelect = true) => {
    currentUnitSystem = unit;
    localStorage.setItem('skyview_unit_system', unit);

    if (btnMetric && btnUs) {
      if (unit === 'metric') {
        btnMetric.classList.add('active');
        btnUs.classList.remove('active');
      } else {
        btnUs.classList.add('active');
        btnMetric.classList.remove('active');
      }
    }

    if (syncSelect && selectUnit) {
      selectUnit.value = unit;
    }

    const elevLabel = document.getElementById('label-elevation-unit');
    if (elevLabel) elevLabel.textContent = unit === 'metric' ? 'meters' : 'feet';

    const adsbRangeLabel = document.getElementById('label-adsb-range-unit');
    if (adsbRangeLabel) adsbRangeLabel.textContent = unit === 'metric' ? 'km' : 'miles';

    if (skyViewer) {
      skyViewer.setUnitSystem(unit);
    }
    updateTelemetry();
    if (window.lastTargets) {
      updateTargetsList(window.lastTargets);
    }
  };

  btnMetric?.addEventListener('click', () => {
    setUnit('metric');
    showToast('Display units set to Metric (km, m, km/h)', 'info');
  });

  btnUs?.addEventListener('click', () => {
    setUnit('us');
    showToast('Display units set to US Customary (mi, ft, mph / kts)', 'info');
  });

  selectUnit?.addEventListener('change', (e) => {
    setUnit(e.target.value, false);
  });

  setUnit(currentUnitSystem);
}

// Distance / altitude formatting helper
function formatElevation(meters) {
  if (currentUnitSystem === 'us') {
    const feet = Math.round(meters * 3.28084);
    return `${feet} ft`;
  }
  return `${Math.round(meters)} m`;
}

function formatAltitude(altMeters, altFt) {
  if (currentUnitSystem === 'us') {
    const ft = altFt != null ? altFt : Math.round(altMeters * 3.28084);
    return `${ft.toLocaleString()} ft`;
  }
  const m = altMeters != null ? altMeters : Math.round(altFt * 0.3048);
  return `${m.toLocaleString()} m`;
}

function formatSpeed(speedKts) {
  if (currentUnitSystem === 'us') {
    const mph = Math.round(speedKts * 1.15078);
    return `${speedKts} kts (${mph} mph)`;
  }
  const kmh = Math.round(speedKts * 1.852);
  return `${kmh} km/h (${speedKts} kts)`;
}

function formatRange(rangeKm) {
  if (currentUnitSystem === 'us') {
    const miles = (rangeKm * 0.621371).toFixed(1);
    return `${miles} mi`;
  }
  return `${rangeKm.toFixed(1)} km`;
}


// Toast notification helper
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Night mode toggle
function initNightMode() {
  const btn = document.getElementById('btn-night-mode');
  const isNight = localStorage.getItem('skyview_night_mode') === 'true';
  if (isNight) document.body.classList.add('theme-night-red');

  btn?.addEventListener('click', () => {
    const active = document.body.classList.toggle('theme-night-red');
    localStorage.setItem('skyview_night_mode', active);
  });
}

// Tab navigation
function initTabs() {
  const tabButtons = document.querySelectorAll('.tab-btn');
  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      tabButtons.forEach(b => {
        b.classList.remove('active');
        b.setAttribute('aria-selected', 'false');
      });
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));

      btn.classList.add('active');
      btn.setAttribute('aria-selected', 'true');
      const targetId = btn.getAttribute('data-tab');
      document.getElementById(targetId)?.classList.add('active');

      if (targetId === 'tab-skyview') {
        skyViewer.render();
      } else if (targetId === 'tab-calibration') {
        calibrationStudio.render();
      } else if (targetId === 'tab-gallery') {
        loadGallery();
      }
    });
  });

  // Gallery subtabs
  const subtabs = document.querySelectorAll('.gallery-subtab');
  subtabs.forEach(st => {
    st.addEventListener('click', () => {
      subtabs.forEach(s => s.classList.remove('active'));
      document.querySelectorAll('.gallery-content').forEach(c => c.classList.remove('active'));

      st.classList.add('active');
      const subtargetId = st.getAttribute('data-subtab');
      document.getElementById(subtargetId)?.classList.add('active');
    });
  });
}

// Config & Cameras API
async function loadSystemConfig() {
  try {
    const res = await fetch('/api/config');
    if (res.ok) {
      appConfig = await res.json();
      populateSettingsForm();
      if (typeof window.syncModeUI === 'function') {
        window.syncModeUI(appConfig.mode);
      }
    }
  } catch (e) {
    showToast('Failed to load system configuration', 'error');
  }
}

async function loadCameras() {
  try {
    const res = await fetch('/api/cameras');
    if (!res.ok) return;

    camerasList = await res.json();
    document.getElementById('camera-count-badge').textContent = camerasList.length;

    renderCamerasList();
    renderMiniCameraList();
    populateCalibrationCameraSelect();

    if (camerasList.length > 0 && !calibrationStudio.activeCamera) {
      calibrationStudio.setCamera(camerasList[0].config);
    }
  } catch (e) {
    console.error("Failed to load cameras:", e);
  }
}

function renderMiniCameraList() {
  const container = document.getElementById('sidebar-camera-list');
  if (!container) return;

  container.innerHTML = '';
  camerasList.forEach(item => {
    const cam = item.config;
    const row = document.createElement('div');
    row.className = 'mini-cam-row';
    row.innerHTML = `
      <span>${cam.name}</span>
      <span style="color:var(--text-dim);font-family:var(--font-mono);">${cam.extrinsics.azimuth_deg}° Az / ${cam.extrinsics.altitude_deg}° Alt</span>
    `;
    container.appendChild(row);
  });
}

function renderCamerasList() {
  const container = document.getElementById('cameras-grid');
  if (!container) return;

  container.innerHTML = '';
  camerasList.forEach(item => {
    const cam = item.config;
    const card = document.createElement('div');
    card.className = 'camera-card';
    card.innerHTML = `
      <img src="/api/cameras/${cam.id}/frame?t=${Date.now()}" class="camera-preview-thumb" alt="${cam.name}">
      <div class="camera-card-body">
        <div class="camera-card-title">
          <span>${cam.name}</span>
          <span class="status-indicator online"></span>
        </div>
        <div class="camera-meta">
          <div>ID: ${cam.id} | Type: ${cam.source_type}</div>
          <div>Pointing: ${cam.extrinsics.azimuth_deg}° Az, ${cam.extrinsics.altitude_deg}° Alt, ${cam.extrinsics.roll_deg}° Roll</div>
          <div>Lens: ${cam.intrinsics.lens_model} (${cam.intrinsics.fov_deg}° FOV)</div>
        </div>
        <div class="camera-actions">
          <button class="btn btn-primary btn-sm" style="background:linear-gradient(135deg, #2563eb, #7c3aed);" onclick="quickAutoCalibrateCamera('${cam.id}')">✨ Auto-Solve</button>
          <button class="btn btn-secondary btn-sm" onclick="openCalibrationFor('${cam.id}')">Calibrate</button>
          <button class="btn btn-outline btn-sm" style="color:var(--accent-danger);" onclick="deleteCameraFeed('${cam.id}')">Delete</button>
        </div>
      </div>
    `;
    container.appendChild(card);
  });
}

window.quickAutoCalibrateCamera = async function(cameraId) {
  const target = camerasList.find(c => c.config.id === cameraId);
  const name = target ? target.config.name : cameraId;
  showToast(`Starting auto-plate solve for ${name}...`, 'info');
  try {
    const res = await fetch('/api/calibration/auto_solve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ camera_id: cameraId, apply_to_camera: true, force: true })
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      const errMsg = data.message || data.error || (data.results && data.results[cameraId]?.error) || "Auto-calibration failed.";
      showToast(`Auto-solve failed: ${errMsg}`, 'error');
      return;
    }
    const camRes = (data.results && data.results[cameraId]) ? data.results[cameraId] : data;
    showToast(`✓ Auto-calibrated ${name}! Az: ${camRes.azimuth_deg}°, Alt: ${camRes.altitude_deg}°, RMS: ${camRes.rms_error_px}px (${camRes.match_count || camRes.matched_stars?.length} stars)`, 'success');
    await loadCameras();
    skyViewer.render();
  } catch (e) {
    showToast(`Network error auto-calibrating: ${e.message}`, 'error');
  }
};

function populateCalibrationCameraSelect() {
  const sel = document.getElementById('calib-camera-select');
  if (!sel) return;

  sel.innerHTML = '';
  camerasList.forEach(item => {
    const opt = document.createElement('option');
    opt.value = item.config.id;
    opt.textContent = `${item.config.name} (${item.config.id})`;
    sel.appendChild(opt);
  });
}

window.openCalibrationFor = function(cameraId) {
  const target = camerasList.find(c => c.config.id === cameraId);
  if (target) {
    document.querySelector('.tab-btn[data-tab="tab-calibration"]')?.click();
    document.getElementById('calib-camera-select').value = cameraId;
    calibrationStudio.setCamera(target.config);
  }
};

window.deleteCameraFeed = async function(cameraId) {
  if (!confirm(`Are you sure you want to delete camera '${cameraId}'?`)) return;
  try {
    const res = await fetch(`/api/cameras/${cameraId}`, { method: 'DELETE' });
    if (res.ok) {
      showToast('Camera deleted successfully', 'success');
      await loadCameras();
      skyViewer.render();
    }
  } catch (e) {
    showToast('Failed to delete camera', 'error');
  }
};

// Calibration Studio UI events
function initCalibrationEvents() {
  document.getElementById('calib-camera-select')?.addEventListener('change', (e) => {
    const target = camerasList.find(c => c.config.id === e.target.value);
    if (target) calibrationStudio.setCamera(target.config);
  });

  document.getElementById('btn-calib-grab-frame')?.addEventListener('click', () => {
    if (calibrationStudio.activeCamera) {
      calibrationStudio.loadCameraTestFrame(calibrationStudio.activeCamera.id);
      showToast('Latest frame grabbed', 'info');
    }
  });

  document.getElementById('btn-calib-test-shot')?.addEventListener('click', async () => {
    if (!calibrationStudio.activeCamera) return;
    try {
      const res = await fetch(`/api/cameras/${calibrationStudio.activeCamera.id}/test_shot`, { method: 'POST' });
      if (res.ok) {
        showToast('Snapshot saved to test_shots directory', 'success');
      }
    } catch (e) {
      showToast('Failed to save snapshot', 'error');
    }
  });

  document.getElementById('btn-calib-time-now')?.addEventListener('click', () => {
    const now = new Date();
    document.getElementById('calib-time-input').value = now.toISOString().slice(0, 16);
    calibrationStudio.fetchStars();
  });

  document.getElementById('calib-time-input')?.addEventListener('change', () => {
    calibrationStudio.fetchStars();
  });

  // Sliders real-time binding
  const bindSlider = (id, valId, prop, isFloat = true, step = 1) => {
    const slider = document.getElementById(id);
    const label = document.getElementById(valId);
    slider?.addEventListener('input', (e) => {
      const val = isFloat ? parseFloat(e.target.value) : parseInt(e.target.value);
      label.textContent = isFloat ? `${val.toFixed(step === 0.01 ? 2 : 1)}°` : `${val}`;
      if (step === 0.01) label.textContent = val.toFixed(2);
      calibrationStudio[prop] = val;
      calibrationStudio.render();
    });
  };

  bindSlider('slider-azimuth', 'val-azimuth', 'azimuth');
  bindSlider('slider-altitude', 'val-altitude', 'altitude');
  bindSlider('slider-roll', 'val-roll', 'roll');
  bindSlider('slider-fov', 'val-fov', 'fov');
  bindSlider('slider-k1', 'val-k1', 'k1', true, 0.01);

  document.getElementById('calib-lens-model')?.addEventListener('change', (e) => {
    calibrationStudio.lensModel = e.target.value;
    calibrationStudio.render();
  });

  // Toggles
  document.getElementById('calib-toggle-stars')?.addEventListener('change', (e) => {
    calibrationStudio.showStars = e.target.checked;
    calibrationStudio.render();
  });
  document.getElementById('calib-toggle-constellations')?.addEventListener('change', (e) => {
    calibrationStudio.showConstellations = e.target.checked;
    calibrationStudio.render();
  });
  document.getElementById('calib-toggle-names')?.addEventListener('change', (e) => {
    calibrationStudio.showStarNames = e.target.checked;
    calibrationStudio.render();
  });
  document.getElementById('calib-toggle-horizon')?.addEventListener('change', (e) => {
    calibrationStudio.showHorizon = e.target.checked;
    calibrationStudio.render();
  });
  document.getElementById('calib-toggle-mask')?.addEventListener('change', (e) => {
    calibrationStudio.showMask = e.target.checked;
    calibrationStudio.render();
  });

  // Star Matching
  document.getElementById('btn-detect-stars')?.addEventListener('click', async () => {
    if (!calibrationStudio.activeCamera) return;
    try {
      const res = await fetch('/api/calibration/detect_stars', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ camera_id: calibrationStudio.activeCamera.id })
      });
      if (res.ok) {
        const data = await res.json();
        showToast(`Detected ${data.count} star peaks!`, 'success');
      }
    } catch (e) {
      showToast('Error detecting stars', 'error');
    }
  });

  document.getElementById('btn-solve-pose')?.addEventListener('click', async () => {
    const sol = await calibrationStudio.solvePose();
    if (sol && sol.success) {
      showToast(`Pose solved! RMS Error: ${sol.rms_error_px} px`, 'success');
    }
  });

  document.getElementById('btn-calib-auto-solve')?.addEventListener('click', async () => {
    await calibrationStudio.autoPlateSolve();
  });

  // Mask drawing, auto-detection & auto ground mask
  document.getElementById('btn-auto-detect-obstacles')?.addEventListener('click', async () => {
    await calibrationStudio.autoDetectObstacles();
  });

  document.getElementById('btn-auto-mask-ground')?.addEventListener('click', () => {
    calibrationStudio.autoMaskGround();
    showToast('Sub-horizon ground (&lt;0° Alt) added as obstacle', 'info');
  });

  document.getElementById('btn-start-mask')?.addEventListener('click', (e) => {
    if (!calibrationStudio.isDrawingMask) {
      calibrationStudio.isDrawingMask = true;
      calibrationStudio.drawingObstaclePoints = [];
      e.target.textContent = 'Finish Obstacle';
      e.target.classList.replace('btn-secondary', 'btn-primary');
      showToast('Click points around obstacle in viewport', 'info');
      calibrationStudio.render();
    } else {
      if (calibrationStudio.drawingObstaclePoints.length >= 3) {
        calibrationStudio.obstacles.push({
          id: `obs_${Date.now()}`,
          label: `Obstacle #${calibrationStudio.obstacles.length + 1}`,
          points: [...calibrationStudio.drawingObstaclePoints]
        });
        showToast('Obstacle polygon added', 'success');
      } else if (calibrationStudio.drawingObstaclePoints.length > 0) {
        showToast('Obstacle requires at least 3 points', 'warning');
      }
      calibrationStudio.isDrawingMask = false;
      calibrationStudio.drawingObstaclePoints = [];
      e.target.textContent = '+ Draw Obstacle';
      e.target.classList.replace('btn-primary', 'btn-secondary');
      calibrationStudio.renderObstacleList();
      calibrationStudio.render();
    }
  });

  document.getElementById('btn-clear-mask')?.addEventListener('click', () => {
    calibrationStudio.obstacles = [];
    calibrationStudio.drawingObstaclePoints = [];
    calibrationStudio.isDrawingMask = false;
    const btn = document.getElementById('btn-start-mask');
    if (btn) {
      btn.textContent = '+ Draw Obstacle';
      btn.classList.replace('btn-primary', 'btn-secondary');
    }
    calibrationStudio.renderObstacleList();
    calibrationStudio.render();
    showToast('All obstacles cleared', 'info');
  });

  // Save Calibration
  document.getElementById('btn-save-camera-calib')?.addEventListener('click', async () => {
    if (!calibrationStudio.activeCamera) return;

    const cam = calibrationStudio.activeCamera;
    cam.extrinsics.azimuth_deg = calibrationStudio.azimuth;
    cam.extrinsics.altitude_deg = calibrationStudio.altitude;
    cam.extrinsics.roll_deg = calibrationStudio.roll;
    cam.intrinsics.fov_deg = calibrationStudio.fov;
    cam.intrinsics.lens_model = calibrationStudio.lensModel;
    cam.intrinsics.k1 = calibrationStudio.k1;

    // Save multi-obstacle polygons and tracking-only mode
    cam.polygon_masks = calibrationStudio.obstacles.map(o => o.points);
    cam.polygon_mask = calibrationStudio.obstacles.length > 0 ? [...calibrationStudio.obstacles[0].points] : [];
    cam.mask_tracking_only = document.getElementById('calib-mask-tracking-only')?.checked ?? true;

    try {
      const res = await fetch(`/api/cameras/${cam.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cam)
      });
      if (res.ok) {
        showToast(`Saved calibration for ${cam.name}! Dome updated.`, 'success');
        await loadCameras();
        skyViewer.render();
      } else {
        throw new Error('Save failed');
      }
    } catch (e) {
      showToast('Failed to save calibration', 'error');
    }
  });
}

// Action buttons (Snapshot, Timelapse, Keogram, Startrails)
function initActionButtons() {
  document.getElementById('btn-capture-now')?.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/pipeline/capture_now', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        showToast(`Stitched snapshot saved: ${data.filename}`, 'success');
      }
    } catch (e) {
      showToast('Failed to capture snapshot', 'error');
    }
  });

  document.getElementById('btn-trigger-timelapse')?.addEventListener('click', async () => {
    showToast('Generating MP4 Timelapse in background...', 'info');
    try {
      const res = await fetch('/api/pipeline/generate_timelapse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fps: 25 })
      });
      if (res.ok) {
        showToast('Timelapse generated successfully! Check Archive tab.', 'success');
      } else {
        const err = await res.json();
        showToast(err.detail || 'Timelapse failed', 'error');
      }
    } catch (e) {
      showToast('Error generating timelapse', 'error');
    }
  });

  document.getElementById('btn-trigger-keogram')?.addEventListener('click', async () => {
    showToast('Generating Keogram...', 'info');
    try {
      const res = await fetch('/api/pipeline/generate_keogram', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slice_width: 2 })
      });
      if (res.ok) {
        showToast('Keogram generated successfully! Check Archive tab.', 'success');
      } else {
        const err = await res.json();
        showToast(err.detail || 'Keogram failed', 'error');
      }
    } catch (e) {
      showToast('Error generating keogram', 'error');
    }
  });

  document.getElementById('btn-trigger-startrails')?.addEventListener('click', async () => {
    showToast('Stacking Startrails...', 'info');
    try {
      const res = await fetch('/api/pipeline/generate_startrails', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ threshold: 0.05 })
      });
      if (res.ok) {
        showToast('Startrails image created! Check Archive tab.', 'success');
      } else {
        const err = await res.json();
        showToast(err.detail || 'Startrails failed', 'error');
      }
    } catch (e) {
      showToast('Error stacking startrails', 'error');
    }
  });

  document.getElementById('toggle-tracking')?.addEventListener('change', (e) => {
    skyViewer.showTracking = e.target.checked;
    skyViewer.render();
  });

  document.getElementById('toggle-annotations')?.addEventListener('change', (e) => {
    skyViewer.showAnnotations = e.target.checked;
    skyViewer.fetchFrame();
  });

  document.getElementById('toggle-constellations')?.addEventListener('change', (e) => {
    skyViewer.showConstellations = e.target.checked;
    skyViewer.render();
  });

  // Timestamp and Time Standard (Zulu vs Local)
  const toggleTimestampEl = document.getElementById('toggle-timestamp');
  toggleTimestampEl?.addEventListener('change', (e) => {
    skyViewer.setShowTimestamp(e.target.checked);
    const cbSettings = document.getElementById('set-overlay-timestamp');
    if (cbSettings) cbSettings.checked = e.target.checked;
  });

  const btnZulu = document.getElementById('btn-time-zulu');
  const btnLocal = document.getElementById('btn-time-local');
  const selectTimeFormat = document.getElementById('set-time-format');

  const setTimeStandard = (fmt, syncSettings = true) => {
    skyViewer.setTimeFormat(fmt);
    localStorage.setItem('skyview_time_format', fmt);

    if (btnZulu && btnLocal) {
      if (fmt === 'zulu') {
        btnZulu.classList.add('active');
        btnLocal.classList.remove('active');
      } else {
        btnLocal.classList.add('active');
        btnZulu.classList.remove('active');
      }
    }

    if (syncSettings && selectTimeFormat) {
      selectTimeFormat.value = fmt;
    }
    showToast(`Time standard switched to ${fmt === 'zulu' ? 'Zulu Time (UTC / Z)' : 'Local Observer Time'}`, 'info');
  };

  btnZulu?.addEventListener('click', () => setTimeStandard('zulu'));
  btnLocal?.addEventListener('click', () => setTimeStandard('local'));
  selectTimeFormat?.addEventListener('change', (e) => setTimeStandard(e.target.value, false));

  document.getElementById('btn-fullscreen')?.addEventListener('click', () => {
    const canvas = document.getElementById('sky-canvas');
    if (!document.fullscreenElement) {
      canvas.requestFullscreen().catch(err => alert(`Fullscreen error: ${err.message}`));
    } else {
      document.exitFullscreen();
    }
  });

  initZoomControls();
  initDvrControls();
}

// Canvas Pan, Zoom & Rotation HUD controls
function initZoomControls() {
  document.getElementById('btn-zoom-in')?.addEventListener('click', () => {
    skyViewer?.zoomIn();
  });

  document.getElementById('btn-zoom-out')?.addEventListener('click', () => {
    skyViewer?.zoomOut();
  });

  document.getElementById('btn-rotate-ccw')?.addEventListener('click', () => {
    skyViewer?.rotateBy(-15);
  });

  document.getElementById('btn-rotate-cw')?.addEventListener('click', () => {
    skyViewer?.rotateBy(15);
  });

  document.getElementById('btn-zoom-reset')?.addEventListener('click', () => {
    skyViewer?.resetZoom();
  });
}

// DVR Playback & Time-Shift Rewind Controls
function initDvrControls() {
  const btnPlayPause = document.getElementById('btn-dvr-play-pause');
  const btnRewind30 = document.getElementById('btn-dvr-rewind-30');
  const btnRewind10 = document.getElementById('btn-dvr-rewind-10');
  const btnForward10 = document.getElementById('btn-dvr-forward-10');
  const btnJumpLive = document.getElementById('btn-dvr-jump-live');
  const liveBadge = document.getElementById('dvr-live-badge');
  const slider = document.getElementById('dvr-scrub-slider');
  const offsetText = document.getElementById('dvr-current-offset-text');
  const speedBtn = document.getElementById('dvr-speed-btn');
  const speedMenu = document.querySelector('.dvr-speed-menu');

  btnPlayPause?.addEventListener('click', () => {
    skyViewer?.togglePlayPause();
  });

  btnRewind30?.addEventListener('click', () => {
    skyViewer?.stepRelative(-30);
  });

  btnRewind10?.addEventListener('click', () => {
    skyViewer?.stepRelative(-10);
  });

  btnForward10?.addEventListener('click', () => {
    skyViewer?.stepRelative(10);
  });

  btnJumpLive?.addEventListener('click', () => {
    skyViewer?.jumpToLive();
  });

  liveBadge?.addEventListener('click', () => {
    skyViewer?.jumpToLive();
  });

  slider?.addEventListener('input', (e) => {
    const val = parseFloat(e.target.value);
    if (offsetText) {
      if (val >= -0.5) {
        offsetText.textContent = 'LIVE';
        offsetText.style.color = 'var(--accent-emerald)';
      } else {
        const absSec = Math.abs(Math.round(val));
        const m = Math.floor(absSec / 60);
        const s = absSec % 60;
        offsetText.textContent = `-${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
        offsetText.style.color = 'var(--accent-amber)';
      }
    }
    skyViewer?.seekToOffset(val, false);
  });

  slider?.addEventListener('change', (e) => {
    const val = parseFloat(e.target.value);
    skyViewer?.seekToOffset(val, true);
  });

  speedBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    speedMenu?.classList.toggle('show');
  });

  document.querySelectorAll('.dvr-speed-menu button').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const speed = parseFloat(e.target.dataset.speed);
      skyViewer?.setPlaybackRate(speed);
      document.querySelectorAll('.dvr-speed-menu button').forEach(b => b.classList.remove('active'));
      e.target.classList.add('active');
      speedMenu?.classList.remove('show');
    });
  });

  window.addEventListener('click', () => {
    speedMenu?.classList.remove('show');
  });
}



// Mode Switcher (Feed vs Long-Exposure / Timelapse vs Test Preview)
function initModeSwitcher() {
  const btnFeed = document.getElementById('btn-mode-feed');
  const btnTimelapse = document.getElementById('btn-mode-timelapse');
  const btnTesting = document.getElementById('btn-mode-testing');
  const badge = document.getElementById('live-mode-badge');

  const setModeUI = (mode) => {
    btnFeed?.classList.remove('active');
    btnTimelapse?.classList.remove('active');
    btnTesting?.classList.remove('active');

    if (mode === 'feed') {
      btnFeed?.classList.add('active');
      if (badge) {
        badge.textContent = 'FEED MODE (30 FPS)';
        badge.style.color = 'var(--text-main)';
      }
      skyViewer.pollInterval = 1000;
    } else if (mode === 'live_testing') {
      btnTesting?.classList.add('active');
      if (badge) {
        badge.textContent = 'TEST PREVIEW (RECORDING PAUSED)';
        badge.style.color = 'var(--accent-warning)';
      }
      skyViewer.pollInterval = 1000;
    } else {
      btnTimelapse?.classList.add('active');
      if (badge) {
        badge.textContent = 'TIMELAPSE / LONG-EXPOSURE MODE';
      skyViewer.pollInterval = 5000;
    }

    const runningModeLabel = document.getElementById('label-current-running-mode');
    if (runningModeLabel) {
      if (mode === 'live_testing') {
        runningModeLabel.textContent = 'Test Preview (No disk writes)';
        runningModeLabel.style.color = 'var(--accent-success)';
      } else if (mode === 'feed') {
        runningModeLabel.textContent = 'Live Feed (Recording)';
        runningModeLabel.style.color = 'var(--accent-primary)';
      } else if (mode === 'long_exposure') {
        runningModeLabel.textContent = 'Long Exposure / Timelapse';
        runningModeLabel.style.color = 'var(--accent-amber)';
      }
    }
  };

  window.syncModeUI = setModeUI;
  if (appConfig) setModeUI(appConfig.mode);

  btnFeed?.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'feed' })
      });
      if (res.ok) {
        setModeUI('feed');
        showToast('Switched to Feed Mode (Live Video & Tracking)', 'info');
      }
    } catch (e) {
      showToast('Failed to switch mode', 'error');
    }
  });

  btnTimelapse?.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'long_exposure' })
      });
      if (res.ok) {
        setModeUI('long_exposure');
        showToast('Switched to Timelapse / Long-Exposure Mode', 'info');
      }
    } catch (e) {
      showToast('Failed to switch mode', 'error');
    }
  });

  btnTesting?.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'live_testing' })
      });
      if (res.ok) {
        setModeUI('live_testing');
        showToast('Switched to Test Preview Mode: Live view active, recording PAUSED.', 'info');
      }
    } catch (e) {
      showToast('Failed to switch mode', 'error');
    }
  });
}

// Optical Tracking & Radar Poller
function startTrackingPoller() {
  const pollTargets = async () => {
    try {
      const [targetsRes, weatherRes] = await Promise.all([
        fetch('/api/tracking/targets'),
        fetch('/api/weather/sky_conditions')
      ]);

      if (targetsRes.ok) {
        const data = await targetsRes.json();
        skyViewer.targets = data.targets || [];
        skyViewer.render();
        updateTargetsList(data.targets || []);
      }

      if (weatherRes.ok) {
        const data = await weatherRes.json();
        updateWeatherBanner(data.weather, data.sun);
      }
    } catch (e) {
      // Quiet fail during network reloads
    }
  };

  pollTargets();
  setInterval(pollTargets, 1800);
}

function updateTargetsList(targets) {
  window.lastTargets = targets;
  const container = document.getElementById('sidebar-targets-list');
  const countBadge = document.getElementById('badge-target-count');
  const weathTargets = document.getElementById('weath-targets');

  if (countBadge) countBadge.textContent = targets.length;
  if (weathTargets) weathTargets.textContent = `${targets.length} targets`;
  if (!container) return;

  if (targets.length === 0) {
    container.innerHTML = '<div style="color:var(--text-dim);padding:8px;font-size:0.75rem;">No active targets in field of view.</div>';
    return;
  }

  container.innerHTML = '';
  targets.forEach(t => {
    const row = document.createElement('div');
    row.className = `target-item cat-${t.category}`;

    let icon = '🟡';
    if (t.category === 'airplane') icon = '✈';
    else if (t.category === 'satellite') icon = '🛰';
    else if (t.category === 'unidentified') icon = '⚠';
    else if (t.category === 'vision_ai') icon = '👁';

    let extraInfo = `${t.altitude_deg.toFixed(1)}° Alt, ${t.azimuth_deg.toFixed(1)}° Az`;
    if (t.telemetry && t.telemetry.altitude_ft != null) {
      extraInfo += ` | ${formatAltitude(null, t.telemetry.altitude_ft)}`;
    }
    if (t.speed_kts) {
      extraInfo += ` | ${formatSpeed(t.speed_kts)}`;
    }
    if (t.telemetry && t.telemetry.range_km != null) {
      extraInfo += ` | Range: ${formatRange(t.telemetry.range_km)}`;
    }
    if (t.speed_deg_s > 0) {
      extraInfo += ` | ${t.speed_deg_s}°/s`;
    }

    row.innerHTML = `
      <div>
        <div style="font-weight:600;">${icon} ${t.label}</div>
        <div class="target-meta-sub">${extraInfo}</div>
      </div>
      <span class="badge-pill" style="background:${t.color};color:#000;">${t.category.toUpperCase()}</span>
    `;
    container.appendChild(row);
  });
}

function updateWeatherBanner(weather, sun) {
  if (!weather || !sun) return;
  const condEl = document.getElementById('weath-condition');
  const cloudEl = document.getElementById('weath-clouds');
  const sunEl = document.getElementById('weath-sun-alt');
  const clarityEl = document.getElementById('weath-clarity');

  if (condEl) condEl.textContent = weather.condition || 'Analyzing...';
  if (cloudEl) cloudEl.textContent = `${weather.cloud_cover_percent}% (${weather.cloud_cover_octas})`;
  if (sunEl) {
    const stateStr = sun.sky_state ? sun.sky_state.replace('_', ' ').toUpperCase() : '';
    sunEl.textContent = `${sun.altitude_deg}° (${stateStr})`;
  }
  if (clarityEl) clarityEl.textContent = `${weather.clarity_score}%`;
}

// Gallery loader
async function loadGallery() {
  try {
    const [galleryRes, uapRes] = await Promise.all([
      fetch('/api/gallery'),
      fetch('/api/events/unidentified')
    ]);

    if (galleryRes.ok) {
      const data = await galleryRes.json();
      document.getElementById('count-snapshots').textContent = data.snapshots.length;
      document.getElementById('count-timelapses').textContent = data.timelapses.length;
      document.getElementById('count-keograms').textContent = data.keograms.length;
      document.getElementById('count-startrails').textContent = data.startrails.length;

      const renderGrid = (containerId, items, isVideo = false) => {
        const c = document.getElementById(containerId);
        if (!c) return;
        if (items.length === 0) {
          c.innerHTML = '<div style="color:var(--text-dim);padding:20px;">No files generated yet.</div>';
          return;
        }
        c.innerHTML = '';
        items.forEach(item => {
          const card = document.createElement('div');
          card.className = 'gallery-card';
          if (isVideo) {
            card.innerHTML = `
              <video src="${item.url}" controls class="gallery-thumb"></video>
              <div class="gallery-card-body">
                <div class="gallery-filename">${item.name}</div>
                <a href="${item.url}" download class="badge-link">Download MP4</a>
              </div>
            `;
          } else {
            card.innerHTML = `
              <a href="${item.url}" target="_blank">
                <img src="${item.url}" class="gallery-thumb" alt="${item.name}" loading="lazy">
              </a>
              <div class="gallery-card-body">
                <div class="gallery-filename">${item.name}</div>
                <a href="${item.url}" download class="badge-link">Download Image</a>
              </div>
            `;
          }
          c.appendChild(card);
        });
      };

      renderGrid('gallery-grid-snapshots', data.snapshots);
      renderGrid('gallery-grid-timelapses', data.timelapses, true);
      renderGrid('gallery-grid-keograms', data.keograms);
      renderGrid('gallery-grid-startrails', data.startrails);
    }

    if (uapRes.ok) {
      const uapData = await uapRes.json();
      const countEl = document.getElementById('count-unidentified');
      if (countEl) countEl.textContent = uapData.count;

      const uapContainer = document.getElementById('gallery-grid-unidentified');
      if (uapContainer) {
        if (uapData.events.length === 0) {
          uapContainer.innerHTML = '<div style="color:var(--text-dim);padding:20px;">No anomalous or unidentified aerial events recorded.</div>';
        } else {
          uapContainer.innerHTML = '';
          uapData.events.forEach(ev => {
            const row = document.createElement('div');
            row.className = 'uap-event-card';
            row.innerHTML = `
              <div>
                <div class="uap-event-title">⚠ UNIDENTIFIED AERIAL TARGET #${ev.track_id}</div>
                <div class="uap-event-meta">
                  Timestamp: ${ev.timestamp} | Point: (${ev.pixel_x}, ${ev.pixel_y})<br>
                  Pointing: ${ev.altitude_deg}° Alt, ${ev.azimuth_deg}° Az | Angular Speed: ${ev.angular_speed_deg_s}°/sec
                </div>
              </div>
              <span class="badge-pill" style="background:var(--accent-danger);color:#fff;">UNIDENTIFIED</span>
            `;
            uapContainer.appendChild(row);
          });
        }
      }
    }
  } catch (e) {
    showToast('Failed to load gallery', 'error');
  }
}


// Settings form
function populateSettingsForm() {
  if (!appConfig) return;

  // Startup operating mode
  const startupSelect = document.getElementById('set-startup-mode');
  if (startupSelect) {
    startupSelect.value = appConfig.startup_mode || 'live_testing';
  }
  const runningModeLabel = document.getElementById('label-current-running-mode');
  if (runningModeLabel) {
    const cur = appConfig.mode || 'live_testing';
    if (cur === 'live_testing') {
      runningModeLabel.textContent = 'Test Preview (No disk writes)';
      runningModeLabel.style.color = 'var(--accent-success)';
    } else if (cur === 'feed') {
      runningModeLabel.textContent = 'Live Feed (Recording)';
      runningModeLabel.style.color = 'var(--accent-primary)';
    } else if (cur === 'long_exposure') {
      runningModeLabel.textContent = 'Long Exposure / Timelapse';
      runningModeLabel.style.color = 'var(--accent-amber)';
    }
  }

  document.getElementById('set-lat').value = appConfig.observer.latitude;
  document.getElementById('set-lon').value = appConfig.observer.longitude;
  const elevInput = document.getElementById('set-elevation');
  if (elevInput) {
    elevInput.value = currentUnitSystem === 'us' ? Math.round(appConfig.observer.elevation_m * 3.28084) : Math.round(appConfig.observer.elevation_m);
  }
  document.getElementById('set-tz').value = appConfig.observer.timezone_str;
  const unitSelect = document.getElementById('set-unit-system');
  if (unitSelect) unitSelect.value = currentUnitSystem;

  document.getElementById('set-projection').value = appConfig.stitching.projection;
  document.getElementById('set-canvas-size').value = appConfig.stitching.canvas_size;
  document.getElementById('set-feather').value = appConfig.stitching.feather_pixels;
  document.getElementById('set-horizon-clip').value = appConfig.stitching.horizon_mask_radius;

  document.getElementById('set-capture-interval').value = appConfig.pipeline.capture_interval_sec;
  document.getElementById('set-snapshot-res').value = appConfig.pipeline.snapshot_resolution || 'original';
  document.getElementById('set-snapshot-quality').value = appConfig.pipeline.snapshot_jpeg_quality || 85;
  document.getElementById('set-timelapse-res').value = appConfig.pipeline.timelapse_resolution || 'original';
  document.getElementById('set-timelapse-fps').value = appConfig.pipeline.timelapse_fps;
  document.getElementById('set-startrails-thresh').value = appConfig.pipeline.startrails_threshold;

  if (appConfig.storage) {
    document.getElementById('set-storage-remote-path').value = appConfig.storage.remote_path || '';
    document.getElementById('set-storage-retention').value = appConfig.storage.retention_days ?? 30;
    document.getElementById('set-storage-sync-interval').value = appConfig.storage.sync_interval_minutes ?? 60;
    document.getElementById('set-storage-enabled').checked = !!appConfig.storage.enabled;
    document.getElementById('set-storage-delete-local').checked = !!appConfig.storage.delete_local_after_sync;
  }

  if (appConfig.adsb) {
    document.getElementById('set-adsb-url').value = appConfig.adsb.url || '';
    document.getElementById('set-adsb-max-range').value = appConfig.adsb.max_range_km ?? 250;
    document.getElementById('set-adsb-simulation').checked = !!appConfig.adsb.use_simulation;
    document.getElementById('set-adsb-enabled').checked = !!appConfig.adsb.enabled;
  }

  if (appConfig.stitching) {
    document.getElementById('set-overlay-timestamp').checked = appConfig.stitching.overlay_timestamp !== false;
    document.getElementById('set-time-format').value = appConfig.stitching.time_format || 'zulu';
    document.getElementById('set-ntp-enabled').checked = appConfig.stitching.ntp_enabled !== false;
    document.getElementById('set-ntp-server').value = appConfig.stitching.ntp_server || 'pool.ntp.org';
    
    // Sync toolbar button state
    const fmt = appConfig.stitching.time_format || 'zulu';
    const btnZulu = document.getElementById('btn-time-zulu');
    const btnLocal = document.getElementById('btn-time-local');
    if (btnZulu && btnLocal) {
      if (fmt === 'zulu') {
        btnZulu.classList.add('active');
        btnLocal.classList.remove('active');
      } else {
        btnLocal.classList.add('active');
        btnZulu.classList.remove('active');
      }
    }
    const cbToolbar = document.getElementById('toggle-timestamp');
    if (cbToolbar) cbToolbar.checked = appConfig.stitching.overlay_timestamp !== false;
  }

  const autoCalib = appConfig.plugins?.auto_calibration || appConfig.ai_plugins?.auto_calibration;
  if (autoCalib) {
    document.getElementById('set-autocalib-enabled').checked = !!autoCalib.enabled;
    document.getElementById('set-autocalib-interval').value = autoCalib.check_interval_minutes ?? 60;
    document.getElementById('set-autocalib-require-night').checked = autoCalib.require_night !== false;
    document.getElementById('set-autocalib-min-stars').value = autoCalib.min_star_matches ?? 3;
    document.getElementById('set-autocalib-max-rms').value = autoCalib.max_rms_error_px ?? 8.0;
  }

  fetchStorageStatus();
  fetchADSBStatus();
  fetchNTPStatus();
  fetchAutoCalibStatus();
}

async function fetchAutoCalibStatus() {
  const badge = document.getElementById('autocalib-status-badge');
  const details = document.getElementById('autocalib-status-details');
  if (!badge) return;

  try {
    const res = await fetch('/api/calibration/auto_status');
    if (res.ok) {
      const data = await res.json();
      if (data.enabled) {
        badge.textContent = `Auto-Calibration Active (Every ${data.check_interval_minutes}m)`;
        badge.style.color = 'var(--accent-success)';
        badge.style.borderColor = 'var(--accent-success)';
      } else {
        badge.textContent = 'Auto-Calibration Disabled';
        badge.style.color = 'var(--text-dim)';
        badge.style.borderColor = 'var(--border-color)';
      }

      if (details) {
        let msg = `Status: <strong>${data.status}</strong> | Last Check: ${data.last_run ? new Date(data.last_run).toLocaleString() : 'Never'}`;
        if (data.message) msg += `<br><span style="color:var(--text-secondary);">${data.message}</span>`;
        details.innerHTML = msg;
      }
    }
  } catch (_) {
    badge.textContent = 'Auto-Calibrator Offline';
    badge.style.color = 'var(--text-dim)';
  }
}

async function fetchNTPStatus() {
  const badge = document.getElementById('ntp-status-badge');
  const details = document.getElementById('ntp-status-details');
  if (!badge) return;

  try {
    const res = await fetch('/api/time/ntp');
    if (res.ok) {
      const data = await res.json();
      if (data.synced) {
        badge.textContent = `NTP Synced (Offset: ${data.offset_ms}ms)`;
        badge.style.color = 'var(--accent-success)';
        badge.style.borderColor = 'var(--accent-success)';
        if (details) {
          details.innerHTML = `Active NTP Server: <code>${data.server}</code> | Clock Offset: <strong>${data.offset_ms} ms</strong> | Zulu: ${data.zulu_time} | Local: ${data.local_time}`;
        }
      } else {
        badge.textContent = data.error ? 'NTP Error / Unsynced' : 'NTP Not Synchronized';
        badge.style.color = 'var(--accent-warning)';
        badge.style.borderColor = 'var(--accent-warning)';
        if (details) {
          details.textContent = data.error ? `Sync error: ${data.error}. Using local system clock.` : 'Using local system clock.';
        }
      }
    }
  } catch (_) {
    badge.textContent = 'NTP Service Offline';
    badge.style.color = 'var(--text-dim)';
  }
}


async function fetchADSBStatus() {
  const badge = document.getElementById('adsb-status-badge');
  if (!badge) return;
  if (appConfig?.adsb?.use_simulation) {
    badge.textContent = 'Simulated Traffic Active';
    badge.style.color = 'var(--accent-info)';
    badge.style.borderColor = 'var(--accent-info)';
    return;
  }

  try {
    const res = await fetch('/api/tracking/adsb/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: appConfig?.adsb?.url || '' })
    });
    const data = await res.json();
    if (data.reachable) {
      badge.textContent = `Live Feed Online (${data.aircraft_count} flights)`;
      badge.style.color = 'var(--accent-success)';
      badge.style.borderColor = 'var(--accent-success)';
    } else {
      badge.textContent = 'Receiver Disconnected';
      badge.style.color = 'var(--accent-danger)';
      badge.style.borderColor = 'var(--accent-danger)';
    }
  } catch (_) {
    badge.textContent = 'SDR Offline';
    badge.style.color = 'var(--text-dim)';
  }
}

async function fetchStorageStatus() {
  const badge = document.getElementById('storage-status-badge');
  if (!badge) return;
  try {
    const res = await fetch('/api/storage/status');
    if (res.ok) {
      const data = await res.json();
      if (data.remote_accessible) {
        badge.textContent = `Online: ${data.remote_info?.free_gb || '?'} GB free`;
        badge.style.color = 'var(--accent-success)';
        badge.style.borderColor = 'var(--accent-success)';
      } else if (appConfig?.storage?.remote_path) {
        badge.textContent = 'Remote Share Unreachable';
        badge.style.color = 'var(--accent-danger)';
        badge.style.borderColor = 'var(--accent-danger)';
      } else {
        badge.textContent = 'Remote Backup Disabled';
        badge.style.color = 'var(--text-dim)';
        badge.style.borderColor = 'var(--border-color)';
      }
    }
  } catch (_) {}
}

function initSettingsEvents() {
  document.getElementById('btn-test-remote-storage')?.addEventListener('click', async () => {
    const remotePath = document.getElementById('set-storage-remote-path').value.trim();
    if (!remotePath) {
      showToast('Please enter a remote share directory path first', 'error');
      return;
    }
    const statusEl = document.getElementById('storage-action-status');
    if (statusEl) statusEl.textContent = 'Testing connection...';

    try {
      const res = await fetch('/api/storage/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ remote_path: remotePath })
      });
      const data = await res.json();
      if (data.accessible && data.writable) {
        showToast(`Remote path accessible! Free space: ${data.free_gb} GB`, 'success');
        if (statusEl) statusEl.textContent = `Accessible (${data.free_gb} GB free)`;
      } else {
        showToast(`Remote error: ${data.error || 'Path inaccessible'}`, 'error');
        if (statusEl) statusEl.textContent = `Inaccessible: ${data.error}`;
      }
      fetchStorageStatus();
    } catch (e) {
      showToast('Network error testing storage', 'error');
    }
  });

  document.getElementById('btn-manual-sync-now')?.addEventListener('click', async () => {
    const statusEl = document.getElementById('storage-action-status');
    if (statusEl) statusEl.textContent = 'Syncing files to remote storage...';
    try {
      const res = await fetch('/api/storage/sync', { method: 'POST' });
      const data = await res.json();
      if (data.status === 'success') {
        showToast(data.message, 'success');
        if (statusEl) statusEl.textContent = `Synced: ${data.copied_files} copied.`;
      } else {
        showToast(data.message || 'Sync failed', 'error');
        if (statusEl) statusEl.textContent = `Sync error: ${data.message}`;
      }
      fetchStorageStatus();
    } catch (e) {
      showToast('Error triggering sync', 'error');
    }
  });

  document.getElementById('btn-manual-prune-now')?.addEventListener('click', async () => {
    const statusEl = document.getElementById('storage-action-status');
    if (statusEl) statusEl.textContent = 'Pruning expired files...';
    try {
      const res = await fetch('/api/storage/prune', { method: 'POST' });
      const data = await res.json();
      showToast(`Retention enforced. Pruned ${data.pruned_files} files older than retention policy.`, 'info');
      if (statusEl) statusEl.textContent = `Pruned ${data.pruned_files} files.`;
      await loadGallery();
    } catch (e) {
      showToast('Error pruning files', 'error');
    }
  });

  document.getElementById('btn-save-all-settings')?.addEventListener('click', async () => {
    if (!appConfig) return;

    // Save startup operating mode
    const startupSelect = document.getElementById('set-startup-mode');
    if (startupSelect) {
      appConfig.startup_mode = startupSelect.value;
    }

    appConfig.observer.latitude = parseFloat(document.getElementById('set-lat').value);
    appConfig.observer.longitude = parseFloat(document.getElementById('set-lon').value);
    const elevVal = parseFloat(document.getElementById('set-elevation').value);
    appConfig.observer.elevation_m = currentUnitSystem === 'us' ? elevVal / 3.28084 : elevVal;
    appConfig.observer.timezone_str = document.getElementById('set-tz').value;

    appConfig.stitching.projection = document.getElementById('set-projection').value;
    appConfig.stitching.canvas_size = parseInt(document.getElementById('set-canvas-size').value);
    appConfig.stitching.feather_pixels = parseInt(document.getElementById('set-feather').value);
    appConfig.stitching.horizon_mask_radius = parseFloat(document.getElementById('set-horizon-clip').value);

    appConfig.pipeline.capture_interval_sec = parseFloat(document.getElementById('set-capture-interval').value);
    appConfig.pipeline.snapshot_resolution = document.getElementById('set-snapshot-res').value;
    appConfig.pipeline.snapshot_jpeg_quality = parseInt(document.getElementById('set-snapshot-quality').value);
    appConfig.pipeline.timelapse_resolution = document.getElementById('set-timelapse-res').value;
    appConfig.pipeline.timelapse_fps = parseInt(document.getElementById('set-timelapse-fps').value);
    appConfig.pipeline.startrails_threshold = parseFloat(document.getElementById('set-startrails-thresh').value);

    if (!appConfig.storage) appConfig.storage = {};
    appConfig.storage.remote_path = document.getElementById('set-storage-remote-path').value.trim();
    appConfig.storage.retention_days = parseInt(document.getElementById('set-storage-retention').value) || 0;
    appConfig.storage.sync_interval_minutes = parseInt(document.getElementById('set-storage-sync-interval').value) || 60;
    appConfig.storage.enabled = document.getElementById('set-storage-enabled').checked;
    appConfig.storage.delete_local_after_sync = document.getElementById('set-storage-delete-local').checked;

    if (!appConfig.adsb) appConfig.adsb = {};
    appConfig.adsb.url = document.getElementById('set-adsb-url').value.trim();
    appConfig.adsb.max_range_km = parseFloat(document.getElementById('set-adsb-max-range').value) || 250.0;
    appConfig.adsb.use_simulation = document.getElementById('set-adsb-simulation').checked;
    appConfig.adsb.enabled = document.getElementById('set-adsb-enabled').checked;

    if (!appConfig.stitching) appConfig.stitching = {};
    appConfig.stitching.overlay_timestamp = document.getElementById('set-overlay-timestamp').checked;
    appConfig.stitching.time_format = document.getElementById('set-time-format').value;
    appConfig.stitching.ntp_enabled = document.getElementById('set-ntp-enabled').checked;
    appConfig.stitching.ntp_server = document.getElementById('set-ntp-server').value.trim() || 'pool.ntp.org';

    if (!appConfig.plugins) appConfig.plugins = {};
    if (!appConfig.plugins.auto_calibration) appConfig.plugins.auto_calibration = {};
    appConfig.plugins.auto_calibration.enabled = document.getElementById('set-autocalib-enabled').checked;
    appConfig.plugins.auto_calibration.check_interval_minutes = parseInt(document.getElementById('set-autocalib-interval').value) || 60;
    appConfig.plugins.auto_calibration.require_night = document.getElementById('set-autocalib-require-night').checked;
    appConfig.plugins.auto_calibration.min_star_matches = parseInt(document.getElementById('set-autocalib-min-stars').value) || 3;
    appConfig.plugins.auto_calibration.max_rms_error_px = parseFloat(document.getElementById('set-autocalib-max-rms').value) || 8.0;

    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(appConfig)
      });
      if (res.ok) {
        showToast('Settings saved successfully', 'success');
        skyViewer.render();
        updateTelemetry();
        fetchStorageStatus();
        fetchADSBStatus();
        fetchNTPStatus();
        fetchAutoCalibStatus();
      }
    } catch (e) {
      showToast('Failed to save settings', 'error');
    }
  });

  document.getElementById('btn-trigger-autocalib-now')?.addEventListener('click', async () => {
    const btn = document.getElementById('btn-trigger-autocalib-now');
    const origText = btn ? btn.textContent : '';
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Solving Cameras...';
    }
    showToast('Executing full sky plate solving across active cameras...', 'info');

    try {
      const res = await fetch('/api/calibration/auto_solve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apply_to_camera: true, force: true })
      });
      const data = await res.json();
      if (data.success) {
        showToast(`✓ ${data.message}`, 'success');
        await loadCameras();
        skyViewer.render();
      } else {
        showToast(data.message || 'Auto-calibration failed', 'error');
      }
      fetchAutoCalibStatus();
    } catch (e) {
      showToast(`Error running auto-calibration: ${e.message}`, 'error');
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = origText;
      }
    }
  });

  document.getElementById('btn-sync-ntp-now')?.addEventListener('click', async () => {
    const srv = document.getElementById('set-ntp-server').value.trim() || 'pool.ntp.org';
    const badge = document.getElementById('ntp-status-badge');
    if (badge) badge.textContent = 'Syncing NTP...';

    try {
      const res = await fetch('/api/time/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server: srv })
      });
      const data = await res.json();
      if (data.success) {
        showToast(`Synchronized with NTP server ${srv}! Offset: ${data.status.offset_ms}ms`, 'success');
      } else {
        showToast(`NTP sync failed: ${data.status.error || 'Server unreachable'}`, 'error');
      }
      fetchNTPStatus();
      skyViewer.fetchFrame();
    } catch (e) {
      showToast('Network error triggering NTP sync', 'error');
    }
  });

  document.getElementById('btn-test-adsb-conn')?.addEventListener('click', async () => {
    const url = document.getElementById('set-adsb-url').value.trim();
    const resultEl = document.getElementById('adsb-test-result');
    if (resultEl) resultEl.textContent = 'Testing connection to ADS-B receiver...';

    try {
      const res = await fetch('/api/tracking/adsb/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url })
      });
      const data = await res.json();
      if (data.reachable) {
        showToast(`Connected to ADS-B receiver! ${data.aircraft_count} active flights.`, 'success');
        if (resultEl) resultEl.innerHTML = `<span style="color:var(--accent-success);">Connected to ${data.url_tested} (${data.aircraft_count} aircraft tracked). Version: ${data.version}</span>`;
      } else {
        showToast(`ADS-B error: ${data.error || 'Receiver unreachable'}`, 'error');
        if (resultEl) resultEl.innerHTML = `<span style="color:var(--accent-danger);">Failed to reach ${data.url_tested}: ${data.error}</span>`;
      }
      fetchADSBStatus();
    } catch (e) {
      showToast('Network error testing ADS-B connection', 'error');
    }
  });
}

// Add Camera Modal
function initModalEvents() {
  const dialog = document.getElementById('modal-add-camera');
  const errorBanner = document.getElementById('modal-cam-error');

  document.getElementById('btn-add-camera-dialog')?.addEventListener('click', () => {
    if (errorBanner) {
      errorBanner.style.display = 'none';
      errorBanner.textContent = '';
    }
    dialog?.showModal();
  });
  document.getElementById('btn-close-modal')?.addEventListener('click', () => dialog?.close());
  document.getElementById('btn-cancel-modal')?.addEventListener('click', () => dialog?.close());

  document.getElementById('form-add-camera')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (errorBanner) {
      errorBanner.style.display = 'none';
      errorBanner.textContent = '';
    }

    const id = document.getElementById('new-cam-id').value.trim();
    const name = document.getElementById('new-cam-name').value.trim();
    const type = document.getElementById('new-cam-type').value;
    const url = document.getElementById('new-cam-url').value.trim();
    const az = parseFloat(document.getElementById('new-cam-az').value);
    const alt = parseFloat(document.getElementById('new-cam-alt').value);

    const submitBtn = e.target.querySelector('button[type="submit"]');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Adding...';
    }

    const newCam = {
      id: id,
      name: name,
      source_type: type,
      url_or_path: url,
      enabled: true,
      extrinsics: { azimuth_deg: az, altitude_deg: alt, roll_deg: 0.0 },
      intrinsics: { lens_model: 'rectilinear', fov_deg: 85.0, fov_axis: 'horizontal', k1: 0.0 },
      polygon_mask: []
    };

    try {
      const res = await fetch('/api/cameras', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newCam)
      });
      if (res.ok) {
        showToast(`Camera '${name}' added!`, 'success');
        dialog?.close();
        e.target.reset();
        await loadCameras();
        skyViewer.render();
      } else {
        let msg = 'Failed to add camera';
        try {
          const err = await res.json();
          if (typeof err.detail === 'string') {
            msg = err.detail;
          } else if (Array.isArray(err.detail)) {
            msg = err.detail.map(d => `${d.loc?.slice(-1)[0] || 'field'}: ${d.msg}`).join(', ');
          }
        } catch (_) {}
        if (errorBanner) {
          errorBanner.textContent = `⚠️ ${msg}`;
          errorBanner.style.display = 'block';
        }
        showToast(msg, 'error');
      }
    } catch (err) {
      const errMsg = err.message || 'Network error adding camera';
      if (errorBanner) {
        errorBanner.textContent = `⚠️ ${errMsg}`;
        errorBanner.style.display = 'block';
      }
      showToast(errMsg, 'error');
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Add Camera';
      }
    }
  });
}

// Geolocation Acquisition (Device GPS & Network IP)
function initGeolocationControls() {
  const btnGps = document.getElementById('btn-detect-gps');
  const btnNet = document.getElementById('btn-detect-network');
  const statusText = document.getElementById('location-status-text');
  const sourceBadge = document.getElementById('location-source-badge');

  const applyCoordinates = (lat, lon, elevationM = null, tz = null, source = 'Device GPS', accuracyM = null) => {
    document.getElementById('set-lat').value = lat.toFixed(5);
    document.getElementById('set-lon').value = lon.toFixed(5);

    if (elevationM != null && !isNaN(elevationM)) {
      if (currentUnitSystem === 'us') {
        document.getElementById('set-elevation').value = Math.round(elevationM * 3.28084);
      } else {
        document.getElementById('set-elevation').value = Math.round(elevationM);
      }
    }

    if (tz) {
      document.getElementById('set-tz').value = tz;
    } else {
      try {
        const detectedTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
        if (detectedTz) document.getElementById('set-tz').value = detectedTz;
      } catch (_) {}
    }

    if (sourceBadge) {
      sourceBadge.textContent = source;
      sourceBadge.style.color = 'var(--accent-success)';
      sourceBadge.style.borderColor = 'var(--accent-success)';
    }

    let msg = `Acquired ${lat.toFixed(5)}°, ${lon.toFixed(5)}° via ${source}`;
    if (accuracyM != null) {
      msg += ` (±${Math.round(accuracyM)}m)`;
    }
    if (statusText) statusText.textContent = msg;
    showToast(msg, 'success');

    if (observerMap && observerMarker) {
      updateMapMarker(lat, lon, false);
    }
  };

  // Leaflet Interactive Map Picker
  let observerMap = null;
  let observerMarker = null;
  let streetLayer = null;
  let satLayer = null;

  const btnToggleMap = document.getElementById('btn-toggle-map-picker');
  const mapPanel = document.getElementById('map-picker-panel');
  const btnStreet = document.getElementById('btn-map-layer-street');
  const btnSat = document.getElementById('btn-map-layer-sat');
  const mapCoordsText = document.getElementById('map-pin-coords');

  const updateMapMarker = (lat, lon, zoomTo = false) => {
    if (!observerMap || typeof L === 'undefined') return;
    if (!observerMarker) {
      observerMarker = L.marker([lat, lon], { draggable: true }).addTo(observerMap);
      observerMarker.on('dragend', (e) => {
        const pos = e.target.getLatLng();
        applyCoordinates(pos.lat, pos.lng, null, null, 'Interactive Map Pin');
        updateMapCoordsText(pos.lat, pos.lng);
      });
    } else {
      observerMarker.setLatLng([lat, lon]);
    }

    if (zoomTo) {
      observerMap.setView([lat, lon], Math.max(16, observerMap.getZoom()));
    }
    updateMapCoordsText(lat, lon);
  };

  const updateMapCoordsText = (lat, lon) => {
    if (mapCoordsText) {
      const latStr = `${Math.abs(lat).toFixed(5)}° ${lat >= 0 ? 'N' : 'S'}`;
      const lonStr = `${Math.abs(lon).toFixed(5)}° ${lon >= 0 ? 'E' : 'W'}`;
      mapCoordsText.textContent = `Coordinates: ${latStr}, ${lonStr}`;
    }
  };

  const initObserverMap = () => {
    if (observerMap || typeof L === 'undefined') return;

    const lat = parseFloat(document.getElementById('set-lat').value) || appConfig?.observer?.latitude || 40.7128;
    const lon = parseFloat(document.getElementById('set-lon').value) || appConfig?.observer?.longitude || -74.0060;

    observerMap = L.map('observer-map').setView([lat, lon], 16);

    streetLayer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap'
    });

    satLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 19,
      attribution: '&copy; Esri, Maxar'
    });

    streetLayer.addTo(observerMap);
    updateMapMarker(lat, lon);

    observerMap.on('click', (e) => {
      const { lat, lng } = e.latlng;
      updateMapMarker(lat, lng);
      applyCoordinates(lat, lng, null, null, 'Interactive Map Pin');
    });

    btnStreet?.addEventListener('click', () => {
      if (!observerMap) return;
      observerMap.removeLayer(satLayer);
      streetLayer.addTo(observerMap);
      btnStreet.classList.replace('btn-outline', 'btn-primary');
      btnSat?.classList.replace('btn-primary', 'btn-outline');
    });

    btnSat?.addEventListener('click', () => {
      if (!observerMap) return;
      observerMap.removeLayer(streetLayer);
      satLayer.addTo(observerMap);
      btnSat.classList.replace('btn-outline', 'btn-primary');
      btnStreet?.classList.replace('btn-primary', 'btn-outline');
    });
  };

  btnToggleMap?.addEventListener('click', () => {
    if (!mapPanel) return;
    const isOpen = mapPanel.style.display !== 'none';
    if (isOpen) {
      mapPanel.style.display = 'none';
      btnToggleMap.classList.remove('btn-secondary');
      btnToggleMap.classList.add('btn-outline');
    } else {
      mapPanel.style.display = 'block';
      btnToggleMap.classList.remove('btn-outline');
      btnToggleMap.classList.add('btn-secondary');
      if (!observerMap) {
        initObserverMap();
      } else {
        const curLat = parseFloat(document.getElementById('set-lat').value);
        const curLon = parseFloat(document.getElementById('set-lon').value);
        if (!isNaN(curLat) && !isNaN(curLon)) {
          updateMapMarker(curLat, curLon, true);
        }
      }
      setTimeout(() => {
        if (observerMap) observerMap.invalidateSize();
      }, 150);
    }
  });

  // Sync typed inputs with map pin
  ['set-lat', 'set-lon'].forEach(id => {
    document.getElementById(id)?.addEventListener('input', () => {
      const curLat = parseFloat(document.getElementById('set-lat').value);
      const curLon = parseFloat(document.getElementById('set-lon').value);
      if (!isNaN(curLat) && !isNaN(curLon) && observerMap) {
        updateMapMarker(curLat, curLon, false);
      }
    });
  });

  btnGps?.addEventListener('click', () => {
    if (!('geolocation' in navigator)) {
      showToast('Geolocation is not supported by your browser.', 'error');
      return;
    }

    if (statusText) statusText.textContent = 'Acquiring high-accuracy device location...';
    btnGps.disabled = true;

    navigator.geolocation.getCurrentPosition(
      (position) => {
        btnGps.disabled = false;
        const coords = position.coords;
        let elev = coords.altitude;
        applyCoordinates(coords.latitude, coords.longitude, elev, null, 'Device GPS', coords.accuracy);
      },
      (error) => {
        btnGps.disabled = false;
        let errDesc = 'Location access denied or unavailable.';
        if (error.code === error.PERMISSION_DENIED) {
          errDesc = 'Location permission denied. Check browser or OS settings, or use Network detection.';
        } else if (error.code === error.POSITION_UNAVAILABLE) {
          errDesc = 'Position unavailable. Trying network IP detection fallback...';
          // Automatic network fallback
          detectNetworkLocation();
          return;
        } else if (error.code === error.TIMEOUT) {
          errDesc = 'Location request timed out. Trying network IP detection...';
          detectNetworkLocation();
          return;
        }
        if (statusText) statusText.textContent = errDesc;
        showToast(errDesc, 'error');
      },
      {
        enableHighAccuracy: true,
        timeout: 10000,
        maximumAge: 0
      }
    );
  });

  const detectNetworkLocation = async () => {
    if (statusText) statusText.textContent = 'Querying network IP geolocation...';
    if (btnNet) btnNet.disabled = true;

    try {
      // Try reliable public IP geolocation services
      let res = await fetch('https://ipapi.co/json/').catch(() => null);
      if (!res || !res.ok) {
        res = await fetch('https://ipwho.is/').catch(() => null);
      }

      if (res && res.ok) {
        const data = await res.json();
        const lat = parseFloat(data.latitude || data.lat);
        const lon = parseFloat(data.longitude || data.lon);
        const tz = data.timezone || (data.timezone ? data.timezone.id : null);
        const city = data.city ? `${data.city}, ${data.country_code || data.country}` : 'Network IP';

        if (!isNaN(lat) && !isNaN(lon)) {
          applyCoordinates(lat, lon, null, tz, `Network IP (${city})`, null);
          return;
        }
      }
      throw new Error('Could not resolve location from network IP.');
    } catch (e) {
      const msg = e.message || 'Network location detection failed.';
      if (statusText) statusText.textContent = msg;
      showToast(msg, 'error');
    } finally {
      if (btnNet) btnNet.disabled = false;
    }
  };

  btnNet?.addEventListener('click', detectNetworkLocation);
}

// Telemetry updater
function updateTelemetry() {
  if (!appConfig) return;
  const obs = appConfig.observer;
  const latStr = `${Math.abs(obs.latitude).toFixed(2)}° ${obs.latitude >= 0 ? 'N' : 'S'}`;
  const lonStr = `${Math.abs(obs.longitude).toFixed(2)}° ${obs.longitude >= 0 ? 'E' : 'W'}`;
  const elevFormatted = formatElevation(obs.elevation_m);
  document.getElementById('tel-observer').textContent = `${latStr}, ${lonStr} (${elevFormatted})`;

  // Approximate Local Sidereal Time
  const now = new Date();
  const utcHours = now.getUTCHours() + now.getUTCMinutes() / 60 + now.getUTCSeconds() / 3600;
  // Day of year approximation for LST
  const startOfYear = new Date(Date.UTC(now.getUTCFullYear(), 0, 1));
  const dayOfYear = (now - startOfYear) / 86400000;
  const gmst = (18.697374558 + 24.06570982441908 * dayOfYear + utcHours) % 24;
  const lstHours = (gmst + obs.longitude / 15.0 + 24) % 24;

  const h = Math.floor(lstHours).toString().padStart(2, '0');
  const m = Math.floor((lstHours * 60) % 60).toString().padStart(2, '0');
  const s = Math.floor((lstHours * 3600) % 60).toString().padStart(2, '0');
  document.getElementById('tel-lst').textContent = `${h}:${m}:${s}`;
  document.getElementById('tel-cams').textContent = `${camerasList.length} cameras`;
  document.getElementById('tel-proj').textContent =
    appConfig.stitching.projection === 'zenithal_polar' ? 'Zenithal Polar Dome' : 'Equirectangular Panorama';
}

