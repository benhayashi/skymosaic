class CalibrationStudio {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    this.testImage = new Image();
    this.hasImage = false;

    // Current camera parameters
    this.activeCamera = null;
    this.azimuth = 0.0;
    this.altitude = 45.0;
    this.roll = 0.0;
    this.fov = 85.0;
    this.lensModel = 'rectilinear';
    this.k1 = 0.0;
    this.polygonMask = [];

    // Astrometry data
    this.stars = [];
    this.constellations = [];
    this.captureTime = new Date().toISOString().slice(0, 16);

    // Matching tool & UI states
    this.starMatches = []; // [{pixel_x, pixel_y, star_id}]
    this.isDrawingMask = false;
    this.maskPoints = [];

    // Toggles
    this.showStars = true;
    this.showConstellations = true;
    this.showStarNames = true;
    this.showMask = true;

    this.initEvents();
  }

  initEvents() {
    this.canvas.addEventListener('click', (e) => this.handleCanvasClick(e));
    this.canvas.addEventListener('mousemove', (e) => this.handleCanvasMouseMove(e));

    this.testImage.onload = () => {
      this.hasImage = true;
      this.canvas.width = this.testImage.naturalWidth || 1280;
      this.canvas.height = this.testImage.naturalHeight || 720;
      this.render();
    };
  }

  setCamera(camera) {
    this.activeCamera = camera;
    this.azimuth = camera.extrinsics.azimuth_deg;
    this.altitude = camera.extrinsics.altitude_deg;
    this.roll = camera.extrinsics.roll_deg;
    this.fov = camera.intrinsics.fov_deg;
    this.lensModel = camera.intrinsics.lens_model;
    this.k1 = camera.intrinsics.k1 || 0.0;
    this.polygonMask = camera.polygon_mask ? [...camera.polygon_mask] : [];
    this.maskPoints = [...this.polygonMask];

    this.updateControlsUI();
    this.loadCameraTestFrame(camera.id);
  }

  updateControlsUI() {
    document.getElementById('slider-azimuth').value = this.azimuth;
    document.getElementById('val-azimuth').textContent = `${this.azimuth.toFixed(1)}°`;

    document.getElementById('slider-altitude').value = this.altitude;
    document.getElementById('val-altitude').textContent = `${this.altitude.toFixed(1)}°`;

    document.getElementById('slider-roll').value = this.roll;
    document.getElementById('val-roll').textContent = `${this.roll.toFixed(1)}°`;

    document.getElementById('slider-fov').value = this.fov;
    document.getElementById('val-fov').textContent = `${this.fov.toFixed(1)}°`;

    document.getElementById('calib-lens-model').value = this.lensModel;

    document.getElementById('slider-k1').value = this.k1;
    document.getElementById('val-k1').textContent = this.k1.toFixed(2);
  }

  async loadCameraTestFrame(cameraId) {
    this.testImage.src = `/api/cameras/${cameraId}/frame?t=${Date.now()}`;
    await this.fetchStars();
  }

  async fetchStars() {
    try {
      const timeVal = document.getElementById('calib-time-input')?.value;
      const isoParam = timeVal ? `?timestamp_iso=${new Date(timeVal).toISOString()}` : '';
      const [starsRes, constRes] = await Promise.all([
        fetch(`/api/astrometry/stars${isoParam}`),
        fetch(`/api/astrometry/constellations${isoParam}`)
      ]);
      if (starsRes.ok) {
        const data = await starsRes.json();
        this.stars = data.stars || [];
      }
      if (constRes.ok) {
        const data = await constRes.json();
        this.constellations = data.segments || [];
      }
      this.render();
      this.renderMatchesTable();
    } catch (e) {
      console.warn("Failed to load stars for calibration:", e);
    }
  }

  // Camera projection math in JS for real-time 60fps slider adjustments
  projectWorldToCamera(vWorld, imgW, imgH) {
    const azRad = this.azimuth * (Math.PI / 180);
    const altRad = this.altitude * (Math.PI / 180);
    const rollRad = this.roll * (Math.PI / 180);

    // Forward boresight in topocentric world frame (X=East, Y=North, Z=Up)
    const cosAlt = Math.cos(altRad);
    const sinAlt = Math.sin(altRad);
    const fwdW = [cosAlt * Math.sin(azRad), cosAlt * Math.cos(azRad), sinAlt];

    // Right and Down vectors (Roll=0)
    let rightW, downW;
    if (Math.abs(sinAlt) > 0.9999) {
      rightW = sinAlt > 0 ? [Math.cos(azRad), -Math.sin(azRad), 0] : [Math.cos(azRad), Math.sin(azRad), 0];
      // down = fwd x right
      downW = [
        fwdW[1] * rightW[2] - fwdW[2] * rightW[1],
        fwdW[2] * rightW[0] - fwdW[0] * rightW[2],
        fwdW[0] * rightW[1] - fwdW[1] * rightW[0]
      ];
    } else {
      // right = fwd x up_zenith [0, 0, 1]
      rightW = [fwdW[1], -fwdW[0], 0];
      const rNorm = Math.hypot(rightW[0], rightW[1]);
      rightW = [rightW[0] / rNorm, rightW[1] / rNorm, 0];
      // down = fwd x right
      downW = [
        fwdW[1] * rightW[2] - fwdW[2] * rightW[1],
        fwdW[2] * rightW[0] - fwdW[0] * rightW[2],
        fwdW[0] * rightW[1] - fwdW[1] * rightW[0]
      ];
    }

    // Apply roll rotation around fwdW
    if (Math.abs(rollRad) > 1e-6) {
      const cosR = Math.cos(rollRad);
      const sinR = Math.sin(rollRad);
      const r_old = [...rightW];
      const d_old = [...downW];
      rightW = [cosR * r_old[0] + sinR * d_old[0], cosR * r_old[1] + sinR * d_old[1], cosR * r_old[2] + sinR * d_old[2]];
      downW = [-sinR * r_old[0] + cosR * d_old[0], -sinR * r_old[1] + cosR * d_old[1], -sinR * r_old[2] + cosR * d_old[2]];
    }

    // Camera coordinates: dot product with rightW, downW, fwdW
    const xc = vWorld[0] * rightW[0] + vWorld[1] * rightW[1] + vWorld[2] * rightW[2];
    const yc = vWorld[0] * downW[0] + vWorld[1] * downW[1] + vWorld[2] * downW[2];
    const zc = vWorld[0] * fwdW[0] + vWorld[1] * fwdW[1] + vWorld[2] * fwdW[2];

    const cx = imgW / 2;
    const cy = imgH / 2;
    const fovRad = this.fov * (Math.PI / 180);

    let f;
    if (this.lensModel === 'rectilinear') {
      f = (imgW / 2) / Math.tan(fovRad / 2);
    } else if (this.lensModel === 'fisheye_equidistant') {
      f = (imgW / 2) / (fovRad / 2);
    } else if (this.lensModel === 'fisheye_equisolid') {
      f = (imgW / 2) / (2 * Math.sin(fovRad / 4));
    } else {
      f = (imgW / 2) / Math.tan(fovRad / 2);
    }

    const normXY = Math.hypot(xc, yc);
    if (normXY < 1e-9) {
      return zc > 0 ? { x: cx, y: cy } : null;
    }

    const theta = Math.atan2(normXY, zc);
    const phi = Math.atan2(yc, xc);

    let px, py;
    if (this.lensModel === 'rectilinear') {
      if (zc <= 0 || theta >= (89 * Math.PI / 180)) return null;
      const r_u = Math.tan(theta);
      const radial = 1 + this.k1 * (r_u * r_u);
      px = cx + f * (xc / zc) * radial;
      py = cy + f * (yc / zc) * radial;
    } else if (this.lensModel === 'fisheye_equidistant') {
      px = cx + f * theta * Math.cos(phi);
      py = cy + f * theta * Math.sin(phi);
    } else if (this.lensModel === 'fisheye_equisolid') {
      const rd = 2 * Math.sin(theta / 2);
      px = cx + f * rd * Math.cos(phi);
      py = cy + f * rd * Math.sin(phi);
    } else {
      px = cx + f * theta * Math.cos(phi);
      py = cy + f * theta * Math.sin(phi);
    }

    return { x: px, y: py };
  }

  render() {
    const w = this.canvas.width;
    const h = this.canvas.height;
    this.ctx.clearRect(0, 0, w, h);

    // 1. Draw test shot
    if (this.hasImage) {
      this.ctx.drawImage(this.testImage, 0, 0, w, h);
    } else {
      this.ctx.fillStyle = '#050608';
      this.ctx.fillRect(0, 0, w, h);
    }

    // 2. Project stars
    const projectedMap = {};
    if (this.stars.length > 0) {
      for (const s of this.stars) {
        const pt = this.projectWorldToCamera(s.unit_vector, w, h);
        if (pt && pt.x >= -50 && pt.x <= w + 50 && pt.y >= -50 && pt.y <= h + 50) {
          projectedMap[s.id] = pt;
        }
      }
    }

    // 3. Draw constellations
    if (this.showConstellations) {
      this.ctx.strokeStyle = 'rgba(6, 182, 212, 0.55)';
      this.ctx.lineWidth = 1.4;
      this.ctx.beginPath();
      for (const [s1, s2] of this.constellations) {
        const p1 = projectedMap[s1.id];
        const p2 = projectedMap[s2.id];
        if (p1 && p2) {
          this.ctx.moveTo(p1.x, p1.y);
          this.ctx.lineTo(p2.x, p2.y);
        }
      }
      this.ctx.stroke();
    }

    // 4. Draw stars
    if (this.showStars) {
      for (const s of this.stars) {
        const pt = projectedMap[s.id];
        if (!pt) continue;

        // Bright green/yellow alignment reticle
        this.ctx.strokeStyle = '#10b981';
        this.ctx.lineWidth = 1;
        this.ctx.beginPath();
        this.ctx.arc(pt.x, pt.y, 5, 0, 2 * Math.PI);
        this.ctx.stroke();

        this.ctx.fillStyle = '#34d399';
        this.ctx.beginPath();
        this.ctx.arc(pt.x, pt.y, 2, 0, 2 * Math.PI);
        this.ctx.fill();

        if (this.showStarNames && s.mag <= 2.2) {
          this.ctx.fillStyle = '#a7f3d0';
          this.ctx.font = '11px JetBrains Mono, monospace';
          this.ctx.fillText(`${s.name} (${s.mag}m)`, pt.x + 8, pt.y - 4);
        }
      }
    }

    // 5. Draw user star matches & connection lines
    for (let i = 0; i < this.starMatches.length; i++) {
      const m = this.starMatches[i];
      // Draw clicked point
      this.ctx.fillStyle = '#f59e0b';
      this.ctx.beginPath();
      this.ctx.arc(m.pixel_x, m.pixel_y, 4, 0, 2 * Math.PI);
      this.ctx.fill();

      this.ctx.strokeStyle = '#f59e0b';
      this.ctx.strokeRect(m.pixel_x - 7, m.pixel_y - 7, 14, 14);

      // Connect to projected star if exists
      const proj = projectedMap[m.star_id];
      if (proj) {
        this.ctx.strokeStyle = '#f43f5e';
        this.ctx.setLineDash([4, 4]);
        this.ctx.beginPath();
        this.ctx.moveTo(m.pixel_x, m.pixel_y);
        this.ctx.lineTo(proj.x, proj.y);
        this.ctx.stroke();
        this.ctx.setLineDash([]);
      }
    }

    // 6. Draw polygon obstacle mask
    if (this.showMask && this.maskPoints.length > 0) {
      this.ctx.fillStyle = 'rgba(239, 68, 68, 0.28)';
      this.ctx.strokeStyle = '#ef4444';
      this.ctx.lineWidth = 2;
      this.ctx.beginPath();

      for (let i = 0; i < this.maskPoints.length; i++) {
        const pt = this.maskPoints[i];
        const px = pt[0] * w;
        const py = pt[1] * h;
        if (i === 0) this.ctx.moveTo(px, py);
        else this.ctx.lineTo(px, py);
      }
      if (this.maskPoints.length >= 3) {
        this.ctx.closePath();
        this.ctx.fill();
      }
      this.ctx.stroke();

      // Draw vertices
      this.ctx.fillStyle = '#ef4444';
      for (const pt of this.maskPoints) {
        this.ctx.fillRect(pt[0] * w - 3, pt[1] * h - 3, 6, 6);
      }
    }
  }

  handleCanvasMouseMove(e) {
    const rect = this.canvas.getBoundingClientRect();
    const scaleX = this.canvas.width / rect.width;
    const scaleY = this.canvas.height / rect.height;
    const x = Math.round((e.clientX - rect.left) * scaleX);
    const y = Math.round((e.clientY - rect.top) * scaleY);

    const tooltip = document.getElementById('calib-coords-tooltip');
    if (tooltip) {
      tooltip.textContent = `X: ${x}, Y: ${y}`;
    }
  }

  handleCanvasClick(e) {
    const rect = this.canvas.getBoundingClientRect();
    const scaleX = this.canvas.width / rect.width;
    const scaleY = this.canvas.height / rect.height;
    const x = Math.round((e.clientX - rect.left) * scaleX);
    const y = Math.round((e.clientY - rect.top) * scaleY);

    if (this.isDrawingMask) {
      // Add normalized point
      this.maskPoints.push([x / this.canvas.width, y / this.canvas.height]);
      this.render();
      return;
    }

    // Add star match
    const defaultStar = this.stars[this.starMatches.length % this.stars.length]?.id || 'polaris';
    this.starMatches.push({
      pixel_x: x,
      pixel_y: y,
      star_id: defaultStar
    });

    this.render();
    this.renderMatchesTable();
  }

  renderMatchesTable() {
    const container = document.getElementById('star-matches-list');
    if (!container) return;

    if (this.starMatches.length === 0) {
      container.innerHTML = '<div style="color:var(--text-dim);padding:4px;">No anchor stars clicked yet. Click stars in the image to add.</div>';
      return;
    }

    container.innerHTML = '';
    this.starMatches.forEach((m, idx) => {
      const row = document.createElement('div');
      row.className = 'match-item';

      let options = '';
      for (const s of this.stars) {
        const selected = s.id === m.star_id ? 'selected' : '';
        options += `<option value="${s.id}" ${selected}>${s.name} (${s.mag}m)</option>`;
      }

      row.innerHTML = `
        <span>#${idx + 1} (${m.pixel_x}, ${m.pixel_y})</span>
        <select data-idx="${idx}">${options}</select>
        <button class="btn-icon" data-del="${idx}" style="color:var(--accent-danger);">&times;</button>
      `;

      row.querySelector('select').addEventListener('change', (e) => {
        this.starMatches[idx].star_id = e.target.value;
        this.render();
      });

      row.querySelector('button').addEventListener('click', (e) => {
        this.starMatches.splice(idx, 1);
        this.render();
        this.renderMatchesTable();
      });

      container.appendChild(row);
    });
  }

  async solvePose() {
    if (this.starMatches.length < 2) {
      alert("Please click at least 2 star anchors on the image to solve camera orientation.");
      return;
    }

    try {
      const solveDistortion = document.getElementById('calib-solve-distortion')?.checked || false;
      const payload = {
        camera_id: this.activeCamera.id,
        matches: this.starMatches,
        lens_model: this.lensModel,
        init_fov: this.fov,
        init_k1: this.k1,
        solve_distortion: solveDistortion,
        img_width: this.canvas.width,
        img_height: this.canvas.height
      };

      const res = await fetch('/api/calibration/solve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Solver failed.");
      }

      const sol = await res.json();
      this.azimuth = sol.azimuth_deg;
      this.altitude = sol.altitude_deg;
      this.roll = sol.roll_deg;
      this.fov = sol.fov_deg;
      if (sol.k1 !== undefined) {
        this.k1 = sol.k1;
      }

      this.updateControlsUI();
      this.render();

      return sol;
    } catch (e) {
      alert(`Optimization error: ${e.message}`);
    }
  }

  async autoPlateSolve() {
    if (!this.activeCamera) {
      alert("Please select a target camera first.");
      return;
    }

    const btn = document.getElementById('btn-calib-auto-solve');
    const origText = btn ? btn.innerHTML : '';
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '✨ Plate Solving...';
    }

    try {
      const timeVal = document.getElementById('calib-time-input')?.value;
      const solveDistortion = document.getElementById('calib-solve-distortion')?.checked || false;
      const payload = {
        camera_id: this.activeCamera.id,
        apply_to_camera: true,
        force: true,
        solve_distortion: solveDistortion,
        timestamp_iso: timeVal ? new Date(timeVal).toISOString() : null
      };

      const res = await fetch('/api/calibration/auto_solve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      const data = await res.json();
      if (!res.ok || !data.success) {
        const errMsg = data.message || data.error || (data.results && data.results[this.activeCamera.id]?.error) || "Plate solving could not find a confident solution.";
        throw new Error(errMsg);
      }

      const camRes = (data.results && data.results[this.activeCamera.id]) ? data.results[this.activeCamera.id] : data;
      this.azimuth = camRes.azimuth_deg;
      this.altitude = camRes.altitude_deg;
      this.roll = camRes.roll_deg;
      this.fov = camRes.fov_deg;
      if (camRes.k1 !== undefined) {
        this.k1 = camRes.k1;
      }

      if (camRes.matched_stars && camRes.matched_stars.length > 0) {
        this.starMatches = camRes.matched_stars.map(m => ({
          pixel_x: m.pixel_x,
          pixel_y: m.pixel_y,
          star_id: m.star_id
        }));
        this.renderMatchesTable();
      }

      this.updateControlsUI();
      this.render();

      const distMsg = solveDistortion && camRes.k1 !== undefined ? `, k1 ${camRes.k1}` : '';
      if (window.showToast) {
        showToast(`Auto-calibrated: Az ${this.azimuth}°, Alt ${this.altitude}°, FOV ${this.fov}°${distMsg}, RMS ${camRes.rms_error_px}px`, 'success');
      } else {
        alert(`Camera pose auto-solved!\nAzimuth: ${this.azimuth}°\nAltitude: ${this.altitude}°\nRoll: ${this.roll}°\nFOV: ${this.fov}°${distMsg}\nRMS Error: ${camRes.rms_error_px} px`);
      }

      if (window.loadCameras) {
        await window.loadCameras();
      }
      return camRes;
    } catch (e) {
      if (window.showToast) {
        showToast(`Auto-solve failed: ${e.message}`, 'error');
      } else {
        alert(`Auto-solve failed: ${e.message}`);
      }
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = origText;
      }
    }
  }
}
