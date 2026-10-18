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
    this.flipH = false;
    this.flipV = false;

    // Astrometry data
    this.stars = [];
    this.constellations = [];
    this.captureTime = new Date().toISOString().slice(0, 16);

    // Matching tool & UI states
    this.starMatches = []; // [{pixel_x, pixel_y, star_id}]
    this.isDrawingMask = false;
    this.drawingObstaclePoints = [];
    this.obstacles = []; // [{id, label, points: [[x,y],...]}]
    this.maskTrackingOnly = true;

    // Brush Tool state
    this.maskToolMode = 'none'; // 'none', 'polygon', 'brush', 'eraser'
    this.brushSize = 25;
    this.isBrushing = false;
    this.lastBrushPos = null;
    this.brushCanvas = document.createElement('canvas');
    this.brushCtx = this.brushCanvas.getContext('2d');

    // Toggles
    this.showStars = true;
    this.showConstellations = true;
    this.showStarNames = true;
    this.showHorizon = true;
    this.showMask = true;

    this.initEvents();
  }

  initEvents() {
    this.canvas.addEventListener('click', (e) => this.handleCanvasClick(e));
    this.canvas.addEventListener('mousemove', (e) => this.handleCanvasMouseMove(e));
    this.canvas.addEventListener('mousedown', (e) => this.handleCanvasMouseDown(e));
    window.addEventListener('mouseup', () => this.handleCanvasMouseUp());

    // Touch support for brush drawing
    this.canvas.addEventListener('touchstart', (e) => {
      if (this.maskToolMode === 'brush' || this.maskToolMode === 'eraser') {
        e.preventDefault();
        const touch = e.touches[0];
        const mouseEv = new MouseEvent('mousedown', { clientX: touch.clientX, clientY: touch.clientY, button: 0 });
        this.handleCanvasMouseDown(mouseEv);
      }
    }, { passive: false });

    this.canvas.addEventListener('touchmove', (e) => {
      if (this.maskToolMode === 'brush' || this.maskToolMode === 'eraser') {
        e.preventDefault();
        const touch = e.touches[0];
        const mouseEv = new MouseEvent('mousemove', { clientX: touch.clientX, clientY: touch.clientY });
        this.handleCanvasMouseMove(mouseEv);
      }
    }, { passive: false });

    this.canvas.addEventListener('touchend', () => {
      this.handleCanvasMouseUp();
    }, { passive: true });

    this.canvas.addEventListener('mouseleave', () => {
      this.currentMousePos = null;
      if (this.maskToolMode === 'brush' || this.maskToolMode === 'eraser') {
        this.lastBrushPos = null;
      }
      this.render();
    });

    this.testImage.onload = () => {
      this.hasImage = true;
      this.canvas.width = this.testImage.naturalWidth || 1280;
      this.canvas.height = this.testImage.naturalHeight || 720;
      this.brushCanvas.width = this.canvas.width;
      this.brushCanvas.height = this.canvas.height;
      this.syncObstaclesToBrushCanvas();
      this.render();
    };
  }

  syncObstaclesToBrushCanvas() {
    if (!this.brushCanvas.width || !this.brushCanvas.height) return;
    const w = this.brushCanvas.width;
    const h = this.brushCanvas.height;
    this.brushCtx.clearRect(0, 0, w, h);
    this.brushCtx.fillStyle = '#ef4444';

    for (const obs of this.obstacles) {
      if (obs.points && obs.points.length >= 3) {
        this.brushCtx.beginPath();
        for (let i = 0; i < obs.points.length; i++) {
          const px = obs.points[i][0] * w;
          const py = obs.points[i][1] * h;
          if (i === 0) this.brushCtx.moveTo(px, py);
          else this.brushCtx.lineTo(px, py);
        }
        this.brushCtx.closePath();
        this.brushCtx.fill();
      }
    }
  }

  setCamera(camera) {
    this.activeCamera = camera;
    this.azimuth = camera.extrinsics.azimuth_deg;
    this.altitude = camera.extrinsics.altitude_deg;
    this.roll = camera.extrinsics.roll_deg;
    this.fov = camera.intrinsics.fov_deg;
    this.lensModel = camera.intrinsics.lens_model;
    this.k1 = camera.intrinsics.k1 || 0.0;
    this.maskTrackingOnly = camera.mask_tracking_only !== undefined ? camera.mask_tracking_only : true;
    this.flipH = camera.flip_horizontal ?? false;
    this.flipV = camera.flip_vertical ?? false;

    // Load multi-obstacle polygons
    this.obstacles = [];
    if (camera.polygon_masks && camera.polygon_masks.length > 0) {
      camera.polygon_masks.forEach((poly, idx) => {
        if (poly && poly.length >= 3) {
          this.obstacles.push({
            id: `obs_${idx + 1}`,
            label: `Obstacle #${idx + 1}`,
            points: [...poly]
          });
        }
      });
    } else if (camera.polygon_mask && camera.polygon_mask.length >= 3) {
      this.obstacles.push({
        id: 'obs_1',
        label: 'Obstacle #1',
        points: [...camera.polygon_mask]
      });
    }

    this.isDrawingMask = false;
    this.drawingObstaclePoints = [];

    const chk = document.getElementById('calib-mask-tracking-only');
    if (chk) chk.checked = this.maskTrackingOnly;

    this.syncObstaclesToBrushCanvas();
    this.updateControlsUI();
    this.renderObstacleList();
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

    const chkH = document.getElementById('calib-flip-h');
    if (chkH) chkH.checked = this.flipH;
    const chkV = document.getElementById('calib-flip-v');
    if (chkV) chkV.checked = this.flipV;
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
    } else if (this.lensModel === 'panoramic_cylindrical') {
      f = imgW / Math.max(1e-4, fovRad);
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
    } else if (this.lensModel === 'panoramic_cylindrical') {
      const az_c = Math.atan2(xc, zc);
      if (Math.abs(az_c) > (fovRad / 2 + 0.15)) return null;
      const d_xz = Math.hypot(xc, zc);
      const alt_c = Math.atan2(yc, Math.max(d_xz, 1e-9));
      const r2 = az_c * az_c + alt_c * alt_c;
      const radial = 1 + this.k1 * r2;
      px = cx + f * az_c * radial;
      py = cy + f * Math.tan(alt_c) * radial;
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

  getDisplayScale() {
    if (this.canvas) {
      const rect = this.canvas.getBoundingClientRect();
      if (rect && rect.width > 0) {
        return Math.max(1.0, this.canvas.width / rect.width);
      }
    }
    return Math.max(1.0, (this.canvas && this.canvas.width ? this.canvas.width : 1280) / 960);
  }

  render() {
    const w = this.canvas.width;
    const h = this.canvas.height;
    const scale = this.getDisplayScale();
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
      this.ctx.lineWidth = Math.max(1.5, Math.round(1.5 * scale));
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
        const starReticle = Math.max(4, Math.round(5 * scale));
        this.ctx.strokeStyle = '#10b981';
        this.ctx.lineWidth = Math.max(1.5, Math.round(1.5 * scale));
        this.ctx.beginPath();
        this.ctx.arc(pt.x, pt.y, starReticle, 0, 2 * Math.PI);
        this.ctx.stroke();

        this.ctx.fillStyle = '#34d399';
        this.ctx.beginPath();
        this.ctx.arc(pt.x, pt.y, Math.max(2, Math.round(2 * scale)), 0, 2 * Math.PI);
        this.ctx.fill();

        if (this.showStarNames && s.mag <= 2.2) {
          this.ctx.fillStyle = '#a7f3d0';
          this.ctx.font = `${Math.max(10, Math.round(11 * scale))}px JetBrains Mono, monospace`;
          this.ctx.fillText(`${s.name} (${s.mag}m)`, pt.x + Math.round(8 * scale), pt.y - Math.round(4 * scale));
        }
      }
    }

    // 5. Draw user star matches & connection lines
    for (let i = 0; i < this.starMatches.length; i++) {
      const m = this.starMatches[i];
      // Draw clicked point
      this.ctx.fillStyle = '#f59e0b';
      this.ctx.beginPath();
      this.ctx.arc(m.pixel_x, m.pixel_y, Math.max(4, Math.round(5 * scale)), 0, 2 * Math.PI);
      this.ctx.fill();

      const boxSz = Math.max(12, Math.round(14 * scale));
      this.ctx.strokeStyle = '#f59e0b';
      this.ctx.lineWidth = Math.max(1.5, Math.round(1.5 * scale));
      this.ctx.strokeRect(m.pixel_x - boxSz / 2, m.pixel_y - boxSz / 2, boxSz, boxSz);

      // Connect to projected star if exists
      const proj = projectedMap[m.star_id];
      if (proj) {
        this.ctx.strokeStyle = '#f43f5e';
        this.ctx.lineWidth = Math.max(1.5, Math.round(2 * scale));
        this.ctx.setLineDash([Math.round(6 * scale), Math.round(4 * scale)]);
        this.ctx.beginPath();
        this.ctx.moveTo(m.pixel_x, m.pixel_y);
        this.ctx.lineTo(proj.x, proj.y);
        this.ctx.stroke();
        this.ctx.setLineDash([]);
      }
    }

    // 6. Draw 0° Horizon Line (elevation = 0.0°)
    if (this.showHorizon) {
      this.renderHorizonLine(w, h);
    }

    // 7. Draw all configured obstacle polygons
    if (this.showMask) {
      // Live brush strokes preview when drawing
      if (this.isBrushing && this.brushCanvas && this.brushCanvas.width > 0) {
        this.ctx.save();
        this.ctx.globalAlpha = 0.45;
        this.ctx.drawImage(this.brushCanvas, 0, 0, w, h);
        this.ctx.restore();
      }

      const colors = ['#ef4444', '#0ea5e9', '#f59e0b', '#10b981', '#8b5cf6', '#ec4899'];
      this.obstacles.forEach((obs, idx) => {
        if (!obs.points || obs.points.length < 3) return;
        const color = colors[idx % colors.length];

        this.ctx.save();
        this.ctx.fillStyle = `${color}59`; // Distinct 35% opacity shaded mask area
        this.ctx.strokeStyle = color;
        // High-visibility stroke scaled to screen resolution (approx 3.5 display pixels)
        this.ctx.lineWidth = Math.max(3, Math.round(3.5 * scale));
        this.ctx.beginPath();

        for (let i = 0; i < obs.points.length; i++) {
          const pt = obs.points[i];
          const px = pt[0] * w;
          const py = pt[1] * h;
          if (i === 0) this.ctx.moveTo(px, py);
          else this.ctx.lineTo(px, py);
        }
        this.ctx.closePath();
        this.ctx.fill();
        this.ctx.stroke();

        // Draw vertex handles scaled to display resolution
        const handleSz = Math.max(6, Math.round(8 * scale));
        this.ctx.fillStyle = color;
        for (const pt of obs.points) {
          this.ctx.fillRect(pt[0] * w - handleSz / 2, pt[1] * h - handleSz / 2, handleSz, handleSz);
        }

        // Draw obstacle label badge scaled to display resolution
        if (obs.points.length > 0) {
          const firstPx = obs.points[0][0] * w;
          const firstPy = obs.points[0][1] * h;
          const badgeSize = Math.max(11, Math.round(12 * scale));
          this.ctx.font = `bold ${badgeSize}px JetBrains Mono, monospace`;
          this.ctx.fillStyle = '#ffffff';
          this.ctx.shadowColor = 'rgba(0,0,0,0.85)';
          this.ctx.shadowBlur = Math.round(4 * scale);
          this.ctx.fillText(`[#${idx + 1}] ${obs.label || 'Obstacle'}`, Math.max(10, firstPx - 10), Math.max(16, firstPy - Math.round(6 * scale)));
        }
        this.ctx.restore();
      });

      // Draw active in-progress polygon with translucent shaded fill & resolution-scaled lines
      if (this.isDrawingMask && this.drawingObstaclePoints.length > 0) {
        this.ctx.save();
        this.ctx.beginPath();
        const firstPt = this.drawingObstaclePoints[0];
        this.ctx.moveTo(firstPt[0] * w, firstPt[1] * h);

        for (let i = 1; i < this.drawingObstaclePoints.length; i++) {
          const pt = this.drawingObstaclePoints[i];
          this.ctx.lineTo(pt[0] * w, pt[1] * h);
        }

        if (this.currentMousePos) {
          this.ctx.lineTo(this.currentMousePos.x, this.currentMousePos.y);
        }
        this.ctx.closePath();

        // Visible translucent red shaded mask area over camera view
        this.ctx.fillStyle = 'rgba(239, 68, 68, 0.35)';
        this.ctx.fill();

        // Bold line width scaled to display resolution (approx 3.5 display pixels)
        this.ctx.strokeStyle = '#ef4444';
        this.ctx.lineWidth = Math.max(3, Math.round(3.5 * scale));
        this.ctx.setLineDash([Math.round(8 * scale), Math.round(6 * scale)]);
        this.ctx.stroke();
        this.ctx.setLineDash([]);

        // Vertices scaled to display resolution
        const vRadius = Math.max(5, Math.round(6 * scale));
        const startRadius = Math.max(8, Math.round(9 * scale));
        const vertLineWidth = Math.max(2, Math.round(2 * scale));

        for (let i = 0; i < this.drawingObstaclePoints.length; i++) {
          const pt = this.drawingObstaclePoints[i];
          const px = pt[0] * w;
          const py = pt[1] * h;
          this.ctx.beginPath();
          this.ctx.arc(px, py, i === 0 ? startRadius : vRadius, 0, 2 * Math.PI);
          this.ctx.fillStyle = i === 0 ? '#fbbf24' : '#ef4444';
          this.ctx.fill();
          this.ctx.strokeStyle = '#ffffff';
          this.ctx.lineWidth = vertLineWidth;
          this.ctx.stroke();

          if (i === 0 && this.drawingObstaclePoints.length >= 3) {
            const closeFontSize = Math.max(11, Math.round(12 * scale));
            this.ctx.font = `bold ${closeFontSize}px JetBrains Mono, monospace`;
            this.ctx.fillStyle = '#fbbf24';
            this.ctx.shadowColor = 'rgba(0,0,0,0.85)';
            this.ctx.shadowBlur = Math.round(4 * scale);
            this.ctx.fillText('Click to close', px + Math.round(12 * scale), py - Math.round(8 * scale));
          }
        }
        this.ctx.restore();
      }

      // Draw brush preview indicator scaled to display size
      if (this.lastBrushPos && (this.maskToolMode === 'brush' || this.maskToolMode === 'eraser')) {
        const effectiveBrushRadius = this.brushSize * scale;
        this.ctx.save();
        this.ctx.beginPath();
        this.ctx.arc(this.lastBrushPos.x, this.lastBrushPos.y, effectiveBrushRadius, 0, 2 * Math.PI);
        this.ctx.strokeStyle = this.maskToolMode === 'brush' ? '#ef4444' : '#38bdf8';
        this.ctx.lineWidth = Math.max(2.5, Math.round(2.5 * scale));
        this.ctx.setLineDash([Math.round(5 * scale), Math.round(5 * scale)]);
        this.ctx.stroke();
        this.ctx.fillStyle = this.maskToolMode === 'brush' ? 'rgba(239, 68, 68, 0.2)' : 'rgba(56, 189, 248, 0.2)';
        this.ctx.fill();
        this.ctx.restore();
      }
    }
  }

  setMaskTool(mode) {
    this.maskToolMode = mode;
    this.isDrawingMask = (mode === 'polygon');
    this.drawingObstaclePoints = [];
    this.currentMousePos = null;
    if (this.canvas) {
      if (mode === 'brush' || mode === 'eraser') {
        this.canvas.style.cursor = 'crosshair';
      } else if (mode === 'polygon') {
        this.canvas.style.cursor = 'cell';
      } else {
        this.canvas.style.cursor = 'default';
      }
    }
    this.render();
  }

  setBrushSize(sz) {
    this.brushSize = Math.max(5, Math.min(400, parseInt(sz, 10) || 25));
  }

  handleCanvasMouseDown(e) {
    if (this.maskToolMode !== 'brush' && this.maskToolMode !== 'eraser') return;
    const rect = this.canvas.getBoundingClientRect();
    const scaleX = this.canvas.width / rect.width;
    const scaleY = this.canvas.height / rect.height;
    const x = (e.clientX - rect.left) * scaleX;
    const y = (e.clientY - rect.top) * scaleY;

    this.isBrushing = true;
    this.lastBrushPos = { x, y };
    this.paintBrushStroke(x, y);
    this.render();
  }

  paintBrushStroke(x, y) {
    if (!this.brushCanvas.width) return;
    const scale = this.getDisplayScale();
    const effectiveRadius = this.brushSize * scale;

    this.brushCtx.save();
    if (this.maskToolMode === 'eraser') {
      this.brushCtx.globalCompositeOperation = 'destination-out';
      this.brushCtx.fillStyle = '#000000';
      this.brushCtx.strokeStyle = '#000000';
    } else {
      this.brushCtx.globalCompositeOperation = 'source-over';
      this.brushCtx.fillStyle = '#ef4444';
      this.brushCtx.strokeStyle = '#ef4444';
    }

    this.brushCtx.lineWidth = effectiveRadius * 2;
    this.brushCtx.lineCap = 'round';
    this.brushCtx.lineJoin = 'round';

    this.brushCtx.beginPath();
    if (this.lastBrushPos) {
      this.brushCtx.moveTo(this.lastBrushPos.x, this.lastBrushPos.y);
      this.brushCtx.lineTo(x, y);
      this.brushCtx.stroke();
    }
    this.brushCtx.beginPath();
    this.brushCtx.arc(x, y, effectiveRadius, 0, 2 * Math.PI);
    this.brushCtx.fill();
    this.brushCtx.restore();

    this.lastBrushPos = { x, y };
  }

  handleCanvasMouseUp() {
    if (this.isBrushing) {
      this.isBrushing = false;
      this.convertBrushToPolygons();
    }
  }

  convertBrushToPolygons() {
    const w = this.brushCanvas.width;
    const h = this.brushCanvas.height;
    if (!w || !h) return;

    try {
      const imgData = this.brushCtx.getImageData(0, 0, w, h);
      const data = imgData.data;

      // Extract binary single-channel alpha/luminance buffer for simple contour tracing
      // Scale down image to 400px width for fast polygon extraction
      const scale = Math.min(1.0, 480.0 / w);
      const sw = Math.round(w * scale);
      const sh = Math.round(h * scale);

      const smallCanvas = document.createElement('canvas');
      smallCanvas.width = sw;
      smallCanvas.height = sh;
      const sctx = smallCanvas.getContext('2d');
      sctx.drawImage(this.brushCanvas, 0, 0, sw, sh);
      const sData = sctx.getImageData(0, 0, sw, sh).data;

      // Trace non-zero alpha bounding components into bounding polygon approximations
      // Simple grid-box cluster contour approximation:
      const grid = 8;
      const cols = Math.ceil(sw / grid);
      const rows = Math.ceil(sh / grid);
      const occupied = new Uint8Array(cols * rows);

      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          let count = 0;
          for (let gy = 0; gy < grid; gy++) {
            const py = r * grid + gy;
            if (py >= sh) break;
            for (let gx = 0; gx < grid; gx++) {
              const px = c * grid + gx;
              if (px >= sw) break;
              const idx = (py * sw + px) * 4;
              if (sData[idx + 3] > 60) count++;
            }
          }
          if (count > (grid * grid * 0.25)) {
            occupied[r * cols + c] = 1;
          }
        }
      }

      // Find bounding segments of occupied blocks
      const visited = new Uint8Array(cols * rows);
      const clusters = [];

      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const idx = r * cols + c;
          if (occupied[idx] && !visited[idx]) {
            // Flood fill cluster
            const queue = [[r, c]];
            visited[idx] = 1;
            let minR = r, maxR = r, minC = c, maxC = c;
            const pts = [];

            while (queue.length > 0) {
              const [cr, cc] = queue.shift();
              minR = Math.min(minR, cr);
              maxR = Math.max(maxR, cr);
              minC = Math.min(minC, cc);
              maxC = Math.max(maxC, cc);

              const neighbors = [[cr - 1, cc], [cr + 1, cc], [cr, cc - 1], [cr, cc + 1]];
              for (const [nr, nc] of neighbors) {
                if (nr >= 0 && nr < rows && nc >= 0 && nc < cols) {
                  const nidx = nr * cols + nc;
                  if (occupied[nidx] && !visited[nidx]) {
                    visited[nidx] = 1;
                    queue.push([nr, nc]);
                  }
                }
              }
            }

            // Create coarse convex polygon box from cluster
            const normX1 = Math.round(((minC * grid) / sw) * 10000) / 10000;
            const normY1 = Math.round(((minR * grid) / sh) * 10000) / 10000;
            const normX2 = Math.round((Math.min(sw, (maxC + 1) * grid) / sw) * 10000) / 10000;
            const normY2 = Math.round((Math.min(sh, (maxR + 1) * grid) / sh) * 10000) / 10000;

            clusters.push([
              [normX1, normY1],
              [normX2, normY1],
              [normX2, normY2],
              [normX1, normY2]
            ]);
          }
        }
      }

      if (clusters.length > 0) {
        // Replace previous brush-based obstacles or append
        this.obstacles = this.obstacles.filter(o => !o.id.startsWith('brush_'));
        clusters.forEach((poly, i) => {
          this.obstacles.push({
            id: `brush_${Date.now()}_${i + 1}`,
            label: `Brush Obstacle #${i + 1}`,
            points: poly
          });
        });
        this.renderObstacleList();
      } else {
        const prevCount = this.obstacles.length;
        this.obstacles = this.obstacles.filter(o => !o.id.startsWith('brush_'));
        if (this.obstacles.length !== prevCount) {
          this.renderObstacleList();
        }
      }
    } catch (e) {
      console.warn("Failed to extract polygons from brush canvas:", e);
    }
    this.render();
  }

  async autoMaskAI() {
    if (!this.activeCamera) return;
    const btn = document.getElementById('btn-auto-mask-ai');
    const orig = btn ? btn.innerHTML : '';
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '🤖 Solving AI Mask...';
    }

    try {
      const res = await fetch(`/api/cameras/${this.activeCamera.id}/auto_mask_ai`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'AI vision masking failed');
      }
      const data = await res.json();
      if (!data.obstacles || data.obstacles.length === 0) {
        if (window.showToast) {
          showToast('AI found no foreground obstacles in field of view.', 'info');
        } else {
          alert('AI found no foreground obstacles in field of view.');
        }
        return;
      }

      data.obstacles.forEach((obs, idx) => {
        this.obstacles.push({
          id: `ai_${Date.now()}_${idx}`,
          label: obs.label || `AI Obstacle #${this.obstacles.length + 1}`,
          points: obs.polygon
        });
      });

      this.syncObstaclesToBrushCanvas();
      this.renderObstacleList();
      this.render();
      if (window.showToast) {
        showToast(`AI auto-detected ${data.obstacles.length} obstacle(s) (${data.model})`, 'success');
      }
    } catch (e) {
      if (window.showToast) {
        showToast(`AI Masking Error: ${e.message}`, 'error');
      } else {
        alert(`AI Masking Error: ${e.message}`);
      }
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = orig;
      }
    }
  }

  renderHorizonLine(w, h) {
    const scale = this.getDisplayScale();
    // Computes topocentric horizon points across 360° azimuth at altitude = 0.0°
    this.ctx.strokeStyle = '#06b6d4';
    this.ctx.lineWidth = Math.max(2.5, Math.round(2.5 * scale));
    this.ctx.setLineDash([Math.round(8 * scale), Math.round(5 * scale)]);
    this.ctx.beginPath();

    let started = false;
    let lastPt = null;

    for (let azDeg = 0; azDeg <= 360; azDeg += 2) {
      const azRad = azDeg * (Math.PI / 180);
      // Unit vector at Alt=0.0°: X=sin(az), Y=cos(az), Z=0.0
      const vWorld = [Math.sin(azRad), Math.cos(azRad), 0.0];
      const pt = this.projectWorldToCamera(vWorld, w, h);

      if (pt && pt.x >= -100 && pt.x <= w + 100 && pt.y >= -100 && pt.y <= h + 100) {
        if (!started) {
          this.ctx.moveTo(pt.x, pt.y);
          started = true;
        } else if (lastPt && Math.hypot(pt.x - lastPt.x, pt.y - lastPt.y) < (250 * scale)) {
          this.ctx.lineTo(pt.x, pt.y);
        } else {
          this.ctx.moveTo(pt.x, pt.y);
        }
        lastPt = pt;
      } else {
        lastPt = null;
      }
    }
    this.ctx.stroke();
    this.ctx.setLineDash([]);

    // Label 0° Horizon
    if (lastPt) {
      this.ctx.fillStyle = '#06b6d4';
      const fSize = Math.max(11, Math.round(12 * scale));
      this.ctx.font = `bold ${fSize}px JetBrains Mono, monospace`;
      this.ctx.fillText("0° HORIZON", Math.min(w - Math.round(110 * scale), Math.max(10, lastPt.x)), Math.min(h - 10, Math.max(20, lastPt.y - Math.round(8 * scale))));
    }
  }

  autoMaskGround() {
    // Automatically creates an obstacle polygon covering everything below 0° elevation
    const w = this.canvas.width;
    const h = this.canvas.height;
    const pts = [];

    // Scan horizon points along camera field from left to right
    const horizonCurve = [];
    for (let azDeg = 0; azDeg <= 360; azDeg += 1) {
      const azRad = azDeg * (Math.PI / 180);
      const vWorld = [Math.sin(azRad), Math.cos(azRad), 0.0];
      const pt = this.projectWorldToCamera(vWorld, w, h);
      if (pt && pt.x >= -50 && pt.x <= w + 50 && pt.y >= -50 && pt.y <= h + 50) {
        horizonCurve.push({ x: pt.x / w, y: pt.y / h });
      }
    }

    if (horizonCurve.length < 2) {
      alert("Could not project 0° horizon line on current camera view. Check Azimuth and Altitude orientation.");
      return;
    }

    // Sort horizon points by x coordinate
    horizonCurve.sort((a, b) => a.x - b.x);

    // Build closed polygon: Left-edge bottom -> horizon curve -> Right-edge bottom -> close
    pts.push([0.0, 1.0]); // bottom-left
    if (horizonCurve[0].x > 0.0) {
      pts.push([0.0, horizonCurve[0].y]);
    }

    for (const hp of horizonCurve) {
      pts.push([Math.max(0.0, Math.min(1.0, hp.x)), Math.max(0.0, Math.min(1.0, hp.y))]);
    }

    const last = horizonCurve[horizonCurve.length - 1];
    if (last.x < 1.0) {
      pts.push([1.0, last.y]);
    }
    pts.push([1.0, 1.0]); // bottom-right

    const existingIdx = this.obstacles.findIndex(o => o.id === 'ground_0deg');
    const groundObs = {
      id: 'ground_0deg',
      label: 'Ground (<0° Alt)',
      points: pts
    };
    if (existingIdx >= 0) {
      this.obstacles[existingIdx] = groundObs;
    } else {
      this.obstacles.unshift(groundObs);
    }

    this.renderObstacleList();
    this.render();
  }

  async autoDetectObstacles() {
    if (!this.activeCamera) return;
    const btn = document.getElementById('btn-auto-detect-obstacles');
    const orig = btn ? btn.innerHTML : '';
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '🌲 Detecting...';
    }

    try {
      const res = await fetch('/api/calibration/detect_obstacles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ camera_id: this.activeCamera.id })
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Obstacle detection failed');
      }
      const data = await res.json();
      if (!data.obstacles || data.obstacles.length === 0) {
        if (window.showToast) {
          showToast('No high-contrast obstacles found in sky view', 'info');
        } else {
          alert('No high-contrast obstacles found in sky view.');
        }
        return;
      }

      data.obstacles.forEach((obs, idx) => {
        this.obstacles.push({
          id: `auto_${Date.now()}_${idx}`,
          label: obs.label || `Obstacle #${this.obstacles.length + 1}`,
          points: obs.polygon
        });
      });

      this.renderObstacleList();
      this.render();
      if (window.showToast) {
        showToast(`Auto-detected ${data.obstacles.length} obstacle(s) (trees/poles)`, 'success');
      }
    } catch (e) {
      if (window.showToast) {
        showToast(`Obstacle detection error: ${e.message}`, 'error');
      } else {
        alert(`Obstacle detection error: ${e.message}`);
      }
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = orig;
      }
    }
  }

  renderObstacleList() {
    const container = document.getElementById('obstacle-list-container');
    if (!container) return;

    if (!this.obstacles || this.obstacles.length === 0) {
      container.innerHTML = '<div style="color:var(--text-dim);font-size:0.8rem;padding:4px;">No obstacles configured yet. Click "+ Draw Obstacle" or "Auto-Detect".</div>';
      return;
    }

    container.innerHTML = '';
    const colors = ['#ef4444', '#f59e0b', '#10b981', '#06b6d4', '#8b5cf6', '#ec4899'];

    this.obstacles.forEach((obs, idx) => {
      const color = colors[idx % colors.length];
      const row = document.createElement('div');
      row.className = 'obstacle-item';
      row.style.cssText = `display:flex; justify-content:space-between; align-items:center; padding:4px 8px; margin-bottom:4px; border-radius:4px; background:rgba(30,41,59,0.6); border-left:3px solid ${color}; font-size:0.8rem;`;

      row.innerHTML = `
        <span style="font-weight:500; color:var(--text-bright); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:180px;">
          #${idx + 1} ${obs.label || 'Obstacle'} (${obs.points.length} pts)
        </span>
        <button class="btn-icon" data-del-obs="${idx}" title="Delete obstacle" style="color:var(--accent-danger); cursor:pointer; background:none; border:none; padding:0 4px; font-size:1.1rem; line-height:1;">
          &times;
        </button>
      `;

      row.querySelector('[data-del-obs]').addEventListener('click', () => {
        this.obstacles.splice(idx, 1);
        this.renderObstacleList();
        this.render();
      });

      container.appendChild(row);
    });
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

    if (this.maskToolMode === 'brush' || this.maskToolMode === 'eraser') {
      this.lastBrushPos = { x, y };
      if (this.isBrushing) {
        this.paintBrushStroke(x, y);
      }
      this.render();
    } else if (this.isDrawingMask) {
      this.currentMousePos = { x, y };
      this.render();
    }
  }

  handleCanvasClick(e) {
    const rect = this.canvas.getBoundingClientRect();
    const scaleX = this.canvas.width / rect.width;
    const scaleY = this.canvas.height / rect.height;
    const x = Math.round((e.clientX - rect.left) * scaleX);
    const y = Math.round((e.clientY - rect.top) * scaleY);

    if (this.isDrawingMask) {
      // If clicking near first point (closing loop) and >= 3 points exist
      if (this.drawingObstaclePoints.length >= 3) {
        const firstPt = this.drawingObstaclePoints[0];
        const firstPx = firstPt[0] * this.canvas.width;
        const firstPy = firstPt[1] * this.canvas.height;
        const scale = this.getDisplayScale();
        const closeTolerance = Math.max(24, Math.round(24 * scale));
        if (Math.hypot(x - firstPx, y - firstPy) < closeTolerance) {
          const btnPoly = document.getElementById('btn-start-mask');
          if (btnPoly) {
            btnPoly.click();
            return;
          }
        }
      }

      // Add normalized point to current drawing obstacle
      this.drawingObstaclePoints.push([
        Math.round((x / this.canvas.width) * 10000) / 10000,
        Math.round((y / this.canvas.height) * 10000) / 10000
      ]);
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
