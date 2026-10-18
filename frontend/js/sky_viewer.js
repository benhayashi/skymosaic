class SkyViewer {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    this.img = new Image();
    this.isRunning = false;
    this.pollInterval = 1000; // 1 second
    this.showAnnotations = true;
    this.showConstellations = false;
    this.showTracking = true;
    this.showTimestamp = true;
    this.timeFormat = 'zulu'; // 'zulu' or 'local'
    this.unitSystem = 'metric'; // 'metric' or 'us'
    this.stars = [];
    this.constellations = [];
    this.targets = [];

    // --- Pan, Zoom & Rotation State ---
    this.zoom = 1.0;
    this.minZoom = 0.8;
    this.maxZoom = 10.0;
    this.panX = 0.0;
    this.panY = 0.0;
    this.rotationDeg = 0.0; // 0 = North up, positive = clockwise
    // dragMode options:
    // 'smart': Inside dome = Pan / Translate, Outside dome = Rotate
    // 'smart_reverse': Inside dome = Rotate, Outside dome = Pan / Translate
    // 'pan': Pan everywhere
    // 'rotate': Rotate everywhere
    this.dragMode = 'smart';
    this.isDragging = false;
    this.isRotating = false;
    this.activeDragAction = null; // 'pan' or 'rotate' for the ongoing gesture
    this.dragStartX = 0;
    this.dragStartY = 0;
    this.panBaseX = 0;
    this.panBaseY = 0;
    this.rotateStartAngle = 0;
    this.rotateBaseDeg = 0;
    this.lastTouchDist = null;
    this.lastTouchAngle = null;

    // --- Weather & Adaptive Visibility State ---
    this.skyVisibilityMode = 'adaptive'; // 'adaptive' or 'always'
    this.sunAltitude = null;
    this.metarData = null;
    this.weatherData = null;

    // --- DVR & Playback State ---
    this.isLive = true;
    this.isPlaying = false;
    this.playbackRate = 1.0; // 0.5, 1.0, 2.0, 4.0
    this.dvrOffsetSec = 0.0; // 0 = live, negative = past
    this.currentFrameTimestamp = null;
    this.bufferDurationSec = 1800; // 30 minutes default
    this.clientFrameCache = []; // Ring buffer of recent frames [{ offsetSec, timestamp, img }]
    this.maxCacheSize = 90;
    this.dvrTimelineTimer = null;

    this.initImageLoader();
    this.initPanZoomEvents();
  }

  setDragMode(mode) {
    this.dragMode = mode;
    this.updateZoomUI();
    this.updateCursor();
  }

  toggleDragMode() {
    // Cycle between standard dual-zone ('smart': inside pan, outside rotate)
    // and inverted dual-zone ('smart_reverse': inside rotate, outside pan)
    if (this.dragMode === 'smart') {
      this.setDragMode('smart_reverse');
    } else if (this.dragMode === 'smart_reverse') {
      this.setDragMode('pan');
    } else if (this.dragMode === 'pan') {
      this.setDragMode('rotate');
    } else {
      this.setDragMode('smart');
    }
  }

  setWeatherConditions(info) {
    if (info) {
      if (info.sun && info.sun.altitude_deg != null) {
        this.sunAltitude = info.sun.altitude_deg;
      }
      if (info.metar) {
        this.metarData = info.metar;
      }
      if (info.weather) {
        this.weatherData = info.weather;
      }
      if (info.sky_visibility_mode) {
        this.skyVisibilityMode = info.sky_visibility_mode;
      }
    }
    this.render();
  }

  initImageLoader() {
    this.img.onload = () => {
      this.render();
      document.getElementById('sky-loader')?.classList.add('hidden');

      // Cache this frame if in live mode
      if (this.isLive && this.img.src.startsWith('blob:') === false) {
        this.cacheCurrentFrame(0.0, new Date().toISOString(), this.img.src);
      }
    };
  }

  cacheCurrentFrame(offsetSec, timestamp, src) {
    // Keep client-side cache of recent frames for zero-latency slider scrubbing
    if (this.clientFrameCache.length >= this.maxCacheSize) {
      this.clientFrameCache.shift();
    }
    // Update existing offsets slightly
    this.clientFrameCache.push({
      offsetSec: offsetSec,
      timestamp: timestamp,
      src: src
    });
  }

  initPanZoomEvents() {
    if (!this.canvas) return;

    // 1. Mouse Wheel Zoom (Anchored to Cursor Position)
    this.canvas.addEventListener('wheel', (e) => {
      e.preventDefault();

      const rect = this.canvas.getBoundingClientRect();
      const scaleX = this.canvas.width / rect.width;
      const scaleY = this.canvas.height / rect.height;

      // Cursor position in internal canvas coordinates
      const sx = (e.clientX - rect.left) * scaleX;
      const sy = (e.clientY - rect.top) * scaleY;

      // Center of canvas
      const cx = this.canvas.width / 2;
      const cy = this.canvas.height / 2;

      const zoomFactor = e.deltaY < 0 ? 1.18 : (1.0 / 1.18);
      const oldZoom = this.zoom;
      let newZoom = Math.max(this.minZoom, Math.min(this.maxZoom, this.zoom * zoomFactor));

      // Snap near 1.0x
      if (Math.abs(newZoom - 1.0) < 0.04) {
        newZoom = 1.0;
      }

      if (newZoom !== oldZoom) {
        if (newZoom === 1.0) {
          this.panX = 0;
          this.panY = 0;
        } else {
          // Keep screen position sx, sy anchored to the same point on the sky dome
          // Formula is invariant under dome rotation:
          this.panX = (sx - cx) - (newZoom / oldZoom) * (sx - cx - this.panX);
          this.panY = (sy - cy) - (newZoom / oldZoom) * (sy - cy - this.panY);
        }
        this.zoom = newZoom;
        this.updateZoomUI();
        this.render();
      }
    }, { passive: false });

    // Prevent context menu on right click so user can secondary-drag
    this.canvas.addEventListener('contextmenu', (e) => {
      e.preventDefault();
    });

    // Helper: Determine if coordinates are inside dome horizon circle
    const isInsideDome = (clientX, clientY) => {
      const rect = this.canvas.getBoundingClientRect();
      const scaleX = this.canvas.width / rect.width;
      const scaleY = this.canvas.height / rect.height;
      const sx = (clientX - rect.left) * scaleX;
      const sy = (clientY - rect.top) * scaleY;
      const cx = this.canvas.width / 2 + this.panX;
      const cy = this.canvas.height / 2 + this.panY;
      const domeRadius = (this.canvas.width / 2) * 0.95 * this.zoom;
      return Math.hypot(sx - cx, sy - cy) <= domeRadius;
    };

    // Helper: Resolve effective action ('pan' or 'rotate') based on mode and location
    const resolveAction = (clientX, clientY, isShiftAltOrSecondary) => {
      const inside = isInsideDome(clientX, clientY);
      let action = 'pan';

      if (this.dragMode === 'smart') {
        // Mode A (Default): Inside = Pan/Translate, Outside = Rotate
        action = inside ? 'pan' : 'rotate';
      } else if (this.dragMode === 'smart_reverse') {
        // Mode B (Vice-Versa): Inside = Rotate, Outside = Pan/Translate
        action = inside ? 'rotate' : 'pan';
      } else if (this.dragMode === 'pan') {
        action = 'pan';
      } else if (this.dragMode === 'rotate') {
        action = 'rotate';
      }

      // Modifier key or right/middle click toggles action
      if (isShiftAltOrSecondary) {
        action = (action === 'pan') ? 'rotate' : 'pan';
      }
      return action;
    };

    // 2. Mouse Drag (Dual-Zone Pan & Rotate)
    this.canvas.addEventListener('mousedown', (e) => {
      const isSecondary = (e.shiftKey || e.altKey || e.button === 2 || e.button === 1);
      const action = resolveAction(e.clientX, e.clientY, isSecondary);
      this.activeDragAction = action;

      const rect = this.canvas.getBoundingClientRect();
      const scaleX = this.canvas.width / rect.width;
      const scaleY = this.canvas.height / rect.height;
      const sx = (e.clientX - rect.left) * scaleX;
      const sy = (e.clientY - rect.top) * scaleY;
      const cx = this.canvas.width / 2 + this.panX;
      const cy = this.canvas.height / 2 + this.panY;

      if (action === 'rotate') {
        e.preventDefault();
        this.isRotating = true;
        this.isDragging = false;
        this.rotateStartAngle = Math.atan2(sy - cy, sx - cx);
        this.rotateBaseDeg = this.rotationDeg;
        this.canvas.style.cursor = 'grabbing';
      } else {
        // Pan
        this.isDragging = true;
        this.isRotating = false;
        this.dragStartX = e.clientX;
        this.dragStartY = e.clientY;
        this.panBaseX = this.panX;
        this.panBaseY = this.panY;
        this.canvas.style.cursor = 'grabbing';
      }
    });

    // Update hover cursor based on active dual zone
    this.canvas.addEventListener('mousemove', (e) => {
      if (!this.isDragging && !this.isRotating) {
        const action = resolveAction(e.clientX, e.clientY, false);
        this.canvas.style.cursor = (action === 'rotate') ? 'crosshair' : 'grab';
      }
    });

    window.addEventListener('mousemove', (e) => {
      if (this.isRotating) {
        const rect = this.canvas.getBoundingClientRect();
        const scaleX = this.canvas.width / rect.width;
        const scaleY = this.canvas.height / rect.height;
        const sx = (e.clientX - rect.left) * scaleX;
        const sy = (e.clientY - rect.top) * scaleY;
        const cx = this.canvas.width / 2 + this.panX;
        const cy = this.canvas.height / 2 + this.panY;

        const currentAngle = Math.atan2(sy - cy, sx - cx);
        const deltaAngleDeg = (currentAngle - this.rotateStartAngle) * (180.0 / Math.PI);
        this.setRotation(this.rotateBaseDeg + deltaAngleDeg);
      } else if (this.isDragging) {
        const rect = this.canvas.getBoundingClientRect();
        const scaleX = this.canvas.width / rect.width;
        const scaleY = this.canvas.height / rect.height;
        const dx = (e.clientX - this.dragStartX) * scaleX;
        const dy = (e.clientY - this.dragStartY) * scaleY;
        this.panX = this.panBaseX + dx;
        this.panY = this.panBaseY + dy;
        this.render();
      }
    });

    window.addEventListener('mouseup', () => {
      if (this.isDragging || this.isRotating) {
        this.isDragging = false;
        this.isRotating = false;
        this.activeDragAction = null;
        this.updateCursor();
      }
    });

    // 3. Double-Click to Reset Zoom, Pan & Rotation
    this.canvas.addEventListener('dblclick', () => {
      this.resetZoom();
    });

    // 4. Touch Gestures:
    // Single finger = Smart Dual-Zone (or configured drag mode)
    // Two fingers = Pinch-to-zoom AND twist rotation simultaneously
    this.canvas.addEventListener('touchstart', (e) => {
      if (e.touches.length === 1) {
        const touch = e.touches[0];
        const action = resolveAction(touch.clientX, touch.clientY, false);
        this.activeDragAction = action;

        const rect = this.canvas.getBoundingClientRect();
        const scaleX = this.canvas.width / rect.width;
        const scaleY = this.canvas.height / rect.height;
        const sx = (touch.clientX - rect.left) * scaleX;
        const sy = (touch.clientY - rect.top) * scaleY;
        const cx = this.canvas.width / 2 + this.panX;
        const cy = this.canvas.height / 2 + this.panY;

        if (action === 'rotate') {
          this.isRotating = true;
          this.isDragging = false;
          this.rotateStartAngle = Math.atan2(sy - cy, sx - cx);
          this.rotateBaseDeg = this.rotationDeg;
        } else {
          this.isDragging = true;
          this.isRotating = false;
          this.dragStartX = touch.clientX;
          this.dragStartY = touch.clientY;
          this.panBaseX = this.panX;
          this.panBaseY = this.panY;
        }
      } else if (e.touches.length === 2) {
        this.isDragging = false;
        this.isRotating = false;
        this.activeDragAction = null;
        const dx = e.touches[1].clientX - e.touches[0].clientX;
        const dy = e.touches[1].clientY - e.touches[0].clientY;
        this.lastTouchDist = Math.hypot(dx, dy);
        this.lastTouchAngle = Math.atan2(dy, dx);
        this.rotateBaseDeg = this.rotationDeg;
      }
    }, { passive: true });

    this.canvas.addEventListener('touchmove', (e) => {
      if (e.touches.length === 1) {
        const touch = e.touches[0];
        if (this.isRotating) {
          const rect = this.canvas.getBoundingClientRect();
          const scaleX = this.canvas.width / rect.width;
          const scaleY = this.canvas.height / rect.height;
          const sx = (touch.clientX - rect.left) * scaleX;
          const sy = (touch.clientY - rect.top) * scaleY;
          const cx = this.canvas.width / 2 + this.panX;
          const cy = this.canvas.height / 2 + this.panY;
          const currentAngle = Math.atan2(sy - cy, sx - cx);
          const deltaAngleDeg = (currentAngle - this.rotateStartAngle) * (180.0 / Math.PI);
          this.setRotation(this.rotateBaseDeg + deltaAngleDeg);
        } else if (this.isDragging) {
          const rect = this.canvas.getBoundingClientRect();
          const scaleX = this.canvas.width / rect.width;
          const scaleY = this.canvas.height / rect.height;
          const dx = (touch.clientX - this.dragStartX) * scaleX;
          const dy = (touch.clientY - this.dragStartY) * scaleY;
          this.panX = this.panBaseX + dx;
          this.panY = this.panBaseY + dy;
          this.render();
        }
      } else if (e.touches.length === 2) {
        const dx = e.touches[1].clientX - e.touches[0].clientX;
        const dy = e.touches[1].clientY - e.touches[0].clientY;

        // Pinch-to-zoom
        if (this.lastTouchDist) {
          const curDist = Math.hypot(dx, dy);
          const factor = curDist / this.lastTouchDist;
          this.zoom = Math.max(this.minZoom, Math.min(this.maxZoom, this.zoom * factor));
          this.lastTouchDist = curDist;
        }

        // Two-finger twist rotation
        if (this.lastTouchAngle !== null) {
          const curAngle = Math.atan2(dy, dx);
          let deltaAngleDeg = (curAngle - this.lastTouchAngle) * (180.0 / Math.PI);
          this.setRotation(this.rotationDeg + deltaAngleDeg);
          this.lastTouchAngle = curAngle;
        }

        this.updateZoomUI();
        this.render();
      }
    }, { passive: true });

    this.canvas.addEventListener('touchend', () => {
      this.isDragging = false;
      this.isRotating = false;
      this.lastTouchDist = null;
      this.lastTouchAngle = null;
      this.updateCursor();
    }, { passive: true });
  }

  updateCursor() {
    if (!this.canvas) return;
    this.canvas.style.cursor = 'grab';
  }

  zoomIn() {
    this.setZoom(Math.min(this.maxZoom, this.zoom * 1.3));
  }

  zoomOut() {
    this.setZoom(Math.max(this.minZoom, this.zoom / 1.3));
  }

  setZoom(newZoom) {
    const oldZoom = this.zoom;
    this.zoom = Math.max(this.minZoom, Math.min(this.maxZoom, newZoom));
    if (this.zoom === 1.0) {
      this.panX = 0;
      this.panY = 0;
    }
    this.updateZoomUI();
    this.render();
  }

  rotateBy(degrees) {
    this.setRotation(this.rotationDeg + degrees);
  }

  setRotation(degrees) {
    // Normalize to 0 - 360 range
    let norm = degrees % 360;
    if (norm < 0) norm += 360;
    // Snap near 0 / 360
    if (Math.abs(norm) < 0.75 || Math.abs(norm - 360) < 0.75) {
      norm = 0.0;
    }
    this.rotationDeg = norm;
    this.updateZoomUI();
    this.render();
  }

  resetRotation() {
    this.setRotation(0.0);
  }

  resetZoom() {
    this.zoom = 1.0;
    this.panX = 0.0;
    this.panY = 0.0;
    this.rotationDeg = 0.0;
    this.updateZoomUI();
    this.render();
  }

  updateZoomUI() {
    this.updateCursor();
    const badge = document.getElementById('val-canvas-zoom');
    if (badge) {
      badge.textContent = `${this.zoom.toFixed(1)}x`;
      badge.style.display = this.zoom !== 1.0 ? 'inline-block' : 'none';
    }

    const headingBadge = document.getElementById('val-canvas-heading');
    if (headingBadge) {
      const deg = Math.round(this.rotationDeg);
      let cardinal = 'N';
      if (deg >= 23 && deg < 68) cardinal = 'NE';
      else if (deg >= 68 && deg < 113) cardinal = 'E';
      else if (deg >= 113 && deg < 158) cardinal = 'SE';
      else if (deg >= 158 && deg < 203) cardinal = 'S';
      else if (deg >= 203 && deg < 248) cardinal = 'SW';
      else if (deg >= 248 && deg < 293) cardinal = 'W';
      else if (deg >= 293 && deg < 338) cardinal = 'NW';

      headingBadge.textContent = `${deg}° ${cardinal}`;
      headingBadge.style.opacity = deg !== 0 ? '1.0' : '0.75';
    }

    const resetBtn = document.getElementById('btn-zoom-reset');
    if (resetBtn) {
      const isModified = (this.zoom !== 1.0 || this.panX !== 0 || this.panY !== 0 || this.rotationDeg !== 0);
      resetBtn.style.opacity = isModified ? '1.0' : '0.4';
    }

    const modeBtn = document.getElementById('btn-drag-mode');
    if (modeBtn) {
      if (this.dragMode === 'smart') {
        modeBtn.innerHTML = '✋⭕';
        modeBtn.title = 'Mouse Drag: Dual-Zone (Inside: Pan ✋ | Outside: Rotate 🔄). Click to invert to (Inside: Rotate | Outside: Pan).';
        modeBtn.classList.remove('pan-active', 'rotate-active');
        modeBtn.classList.add('smart-active');
      } else if (this.dragMode === 'smart_reverse') {
        modeBtn.innerHTML = '🔄⭕';
        modeBtn.title = 'Mouse Drag: Inverted Dual-Zone (Inside: Rotate 🔄 | Outside: Pan ✋). Click to cycle mode.';
        modeBtn.classList.remove('pan-active', 'rotate-active');
        modeBtn.classList.add('smart-active');
      } else if (this.dragMode === 'rotate') {
        modeBtn.innerHTML = '🔄';
        modeBtn.title = 'Mouse Drag: Rotate Dome Everywhere. Click to cycle mode.';
        modeBtn.classList.remove('pan-active', 'smart-active');
        modeBtn.classList.add('rotate-active');
      } else {
        modeBtn.innerHTML = '✋';
        modeBtn.title = 'Mouse Drag: Pan / Translate Everywhere. Click to cycle mode.';
        modeBtn.classList.remove('rotate-active', 'smart-active');
        modeBtn.classList.add('pan-active');
      }
    }
  }

  setUnitSystem(unit) {
    this.unitSystem = unit;
    this.render();
  }

  setTimeFormat(fmt) {
    this.timeFormat = fmt;
    this.fetchFrame();
  }

  setShowTimestamp(show) {
    this.showTimestamp = show;
    this.fetchFrame();
  }

  start() {
    this.isRunning = true;
    this.fetchFrame();
    this.fetchAstrometryData();
    this.pollDvrTimeline();
  }

  stop() {
    this.isRunning = false;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.dvrTimelineTimer) {
      clearTimeout(this.dvrTimelineTimer);
      this.dvrTimelineTimer = null;
    }
  }

  // --- DVR & Time-Shift Functions ---

  async pollDvrTimeline() {
    try {
      const res = await fetch('/api/sky/dvr/timeline');
      if (res.ok) {
        const data = await res.json();
        if (data.buffer_duration_sec > 0) {
          this.bufferDurationSec = Math.max(60, data.buffer_duration_sec);
          this.updateDvrTimelineUI(data);
        }
      }
    } catch (_) {}

    if (this.isRunning) {
      this.dvrTimelineTimer = setTimeout(() => this.pollDvrTimeline(), 5000);
    }
  }

  updateDvrTimelineUI(data) {
    const slider = document.getElementById('dvr-scrub-slider');
    const startLabel = document.getElementById('dvr-start-time-label');
    if (slider) {
      const totalSec = Math.round(data.buffer_duration_sec || 1800);
      slider.min = -totalSec;
      slider.max = 0;
      if (this.isLive) {
        slider.value = 0;
      }
    }
    if (startLabel && data.buffer_duration_sec > 0) {
      const mins = Math.round(data.buffer_duration_sec / 60);
      startLabel.textContent = `-${mins}m`;
    }
  }

  seekToOffset(offsetSec, updateSlider = true) {
    const num = parseFloat(offsetSec);
    if (isNaN(num)) return;

    if (num >= -0.5) {
      this.jumpToLive();
      return;
    }

    this.isLive = false;
    this.dvrOffsetSec = num;

    if (updateSlider) {
      const slider = document.getElementById('dvr-scrub-slider');
      if (slider) slider.value = Math.round(this.dvrOffsetSec);
    }

    this.updateDvrPlayerUI();

    // Fetch frame for this offset
    const overlayParam = this.showAnnotations ? 'true' : 'false';
    const tsParam = this.showTimestamp ? 'true' : 'false';
    this.img.src = `/api/sky/dvr/frame?offset_sec=${this.dvrOffsetSec.toFixed(1)}&overlay=${overlayParam}&timestamp=${tsParam}&t=${Date.now()}`;
  }

  jumpToLive() {
    this.isLive = true;
    this.isPlaying = false;
    this.dvrOffsetSec = 0.0;
    const slider = document.getElementById('dvr-scrub-slider');
    if (slider) slider.value = 0;

    this.updateDvrPlayerUI();
    this.fetchFrame();
  }

  togglePlayPause() {
    if (this.isLive) {
      // Pause at current moment
      this.isLive = false;
      this.isPlaying = false;
      this.dvrOffsetSec = 0.0;
    } else {
      this.isPlaying = !this.isPlaying;
    }
    this.updateDvrPlayerUI();
  }

  stepRelative(seconds) {
    if (this.isLive && seconds < 0) {
      this.isLive = false;
      this.isPlaying = false;
      this.dvrOffsetSec = seconds;
    } else {
      this.dvrOffsetSec = Math.min(0, this.dvrOffsetSec + seconds);
    }

    if (this.dvrOffsetSec >= 0) {
      this.jumpToLive();
    } else {
      this.seekToOffset(this.dvrOffsetSec, true);
    }
  }

  setPlaybackRate(rate) {
    this.playbackRate = parseFloat(rate) || 1.0;
    const rateBtn = document.getElementById('dvr-speed-btn');
    if (rateBtn) rateBtn.textContent = `${this.playbackRate}x`;
  }

  updateDvrPlayerUI() {
    const badge = document.getElementById('dvr-live-badge');
    const playBtn = document.getElementById('btn-dvr-play-pause');
    const offsetText = document.getElementById('dvr-current-offset-text');

    if (this.isLive) {
      if (badge) {
        badge.className = 'dvr-badge live';
        badge.innerHTML = '<span class="live-dot"></span> LIVE';
      }
      if (playBtn) {
        playBtn.innerHTML = '⏸';
        playBtn.title = 'Pause Live Stream (Enter DVR Mode)';
      }
      if (offsetText) {
        offsetText.textContent = 'LIVE';
        offsetText.style.color = 'var(--accent-emerald)';
      }
    } else {
      if (badge) {
        badge.className = 'dvr-badge rewound';
        badge.innerHTML = '⏮ REWIND (Click for Live)';
      }
      if (playBtn) {
        playBtn.innerHTML = this.isPlaying ? '⏸' : '▶';
        playBtn.title = this.isPlaying ? 'Pause Playback' : 'Play Rewound Footage';
      }
      if (offsetText) {
        const absSec = Math.abs(Math.round(this.dvrOffsetSec));
        const m = Math.floor(absSec / 60);
        const s = absSec % 60;
        const formatted = `-${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
        offsetText.textContent = formatted;
        offsetText.style.color = 'var(--accent-amber)';
      }
    }
  }

  fetchFrame() {
    if (!this.isRunning) return;

    if (this.isLive) {
      // Live stream mode
      const overlayParam = this.showAnnotations ? 'true' : 'false';
      const tsParam = this.showTimestamp ? 'true' : 'false';
      this.img.src = `/api/sky/live.jpg?overlay=${overlayParam}&timestamp=${tsParam}&time_format=${this.timeFormat}&t=${Date.now()}`;
    } else if (this.isPlaying) {
      // Historical playback advancing forward
      const advanceSec = (this.pollInterval / 1000.0) * this.playbackRate;
      this.dvrOffsetSec += advanceSec;

      if (this.dvrOffsetSec >= 0) {
        this.jumpToLive();
        return;
      }
      this.seekToOffset(this.dvrOffsetSec, true);
    }

    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.fetchFrame();
    }, this.pollInterval);
  }

  async fetchAstrometryData() {
    try {
      const [starsRes, constRes] = await Promise.all([
        fetch('/api/astrometry/stars'),
        fetch('/api/astrometry/constellations')
      ]);
      if (starsRes.ok) {
        const data = await starsRes.json();
        this.stars = data.stars || [];
      }
      if (constRes.ok) {
        const data = await constRes.json();
        this.constellations = data.segments || [];
      }
    } catch (e) {
      console.warn("Failed to fetch celestial overlay data:", e);
    }
  }

  render() {
    const w = this.canvas.width;
    const h = this.canvas.height;

    this.ctx.save();
    this.ctx.clearRect(0, 0, w, h);

    // Apply Pan, Zoom & Rotation transformations centered on the canvas
    const cx = w / 2;
    const cy = h / 2;
    this.ctx.translate(cx + this.panX, cy + this.panY);
    if (this.rotationDeg !== 0) {
      this.ctx.rotate(this.rotationDeg * (Math.PI / 180.0));
    }
    this.ctx.scale(this.zoom, this.zoom);
    this.ctx.translate(-cx, -cy);

    // 1. Draw stitched image
    if (this.img.complete && this.img.naturalWidth > 0) {
      this.ctx.drawImage(this.img, 0, 0, w, h);
    }

    // 2. Draw dynamic constellation overlay if enabled
    if (this.showConstellations && this.stars.length > 0) {
      this.renderConstellations(w, h);
    }

    // 3. Draw augmented reality target tracking reticles if enabled
    if (this.showTracking && this.targets.length > 0) {
      this.renderTargets(w, h);
    }

    this.ctx.restore();
  }

  renderTargets(w, h) {
    this.ctx.font = '10px JetBrains Mono, monospace';

    for (const target of this.targets) {
      // In adaptive mode, if explicitly marked not visible, style as obscured/dimmed
      const isVisible = target.visible !== false;
      const isAdaptive = this.skyVisibilityMode === 'adaptive';
      const isObscured = isAdaptive && !isVisible;

      const px = (target.pixel_x / (this.targetCanvasW || 1200)) * w;
      const py = (target.pixel_y / (this.targetCanvasH || 1200)) * h;
      let color = target.color || '#06b6d4';
      if (isObscured) {
        color = '#64748b'; // Dim slate gray for obscured targets
      }
      const cat = target.category;

      this.ctx.strokeStyle = color;
      this.ctx.fillStyle = color;
      this.ctx.lineWidth = 1.5;

      if (isObscured) {
        this.ctx.setLineDash([3, 3]);
      } else {
        this.ctx.setLineDash([]);
      }

      if (cat === 'airplane') {
        // Cyan Aircraft Box HUD
        this.ctx.strokeRect(px - 10, py - 10, 20, 20);
        this.ctx.beginPath();
        this.ctx.arc(px, py, 3, 0, 2 * Math.PI);
        this.ctx.fill();

        let displayLabel = target.label;
        let altText = '';
        let distText = '';

        if (target.telemetry && target.telemetry.altitude_ft != null) {
          if (this.unitSystem === 'metric') {
            const altM = Math.round(target.telemetry.altitude_ft * 0.3048);
            altText = `${altM}m`;
          } else {
            altText = `FL${Math.round(target.telemetry.altitude_ft / 100)}`;
          }
        }

        if (target.telemetry && (target.telemetry.range_miles != null || target.telemetry.range_km != null)) {
          if (this.unitSystem === 'us') {
            const mi = target.telemetry.range_miles != null ? target.telemetry.range_miles.toFixed(1) : (target.telemetry.range_km * 0.621371).toFixed(1);
            distText = `${mi}mi`;
          } else {
            const km = target.telemetry.range_km != null ? target.telemetry.range_km.toFixed(1) : (target.telemetry.range_miles * 1.60934).toFixed(1);
            distText = `${km}km`;
          }
        }

        const flightName = target.telemetry?.flight || target.label;
        const details = [altText, distText].filter(Boolean).join(' • ');
        displayLabel = details ? `${flightName} (${details})` : flightName;
        if (isObscured) {
          displayLabel += ' [☁ Obscured]';
        }

        this.ctx.fillText(`✈ ${displayLabel}`, px + 14, py + 3);
      } else if (cat === 'satellite') {
        // Purple Diamond HUD
        this.ctx.beginPath();
        this.ctx.moveTo(px, py - 9);
        this.ctx.lineTo(px + 9, py);
        this.ctx.lineTo(px, py + 9);
        this.ctx.lineTo(px - 9, py);
        this.ctx.closePath();
        this.ctx.stroke();
        const satLabel = isObscured ? `${target.label} [☁ Obscured]` : target.label;
        this.ctx.fillText(`🛰 ${satLabel}`, px + 13, py + 3);
      } else if (cat === 'unidentified') {
        // Red Alert HUD (Bracket crosshair)
        this.ctx.strokeStyle = isObscured ? '#64748b' : '#ef4444';
        this.ctx.lineWidth = 2;
        const b = 12;
        this.ctx.beginPath();
        this.ctx.moveTo(px - b, py - b + 5); this.ctx.lineTo(px - b, py - b); this.ctx.lineTo(px - b + 5, py - b);
        this.ctx.moveTo(px + b - 5, py - b); this.ctx.lineTo(px + b, py - b); this.ctx.lineTo(px + b, py - b + 5);
        this.ctx.moveTo(px - b, py + b - 5); this.ctx.lineTo(px - b, py + b); this.ctx.lineTo(px - b + 5, py + b);
        this.ctx.moveTo(px + b - 5, py + b); this.ctx.lineTo(px + b, py + b); this.ctx.lineTo(px + b, py + b - 5);
        this.ctx.stroke();

        this.ctx.fillStyle = isObscured ? '#64748b' : '#ef4444';
        this.ctx.fillText(`⚠ ${target.label}`, px + 15, py + 4);
      } else if (cat === 'celestial') {
        this.ctx.beginPath();
        this.ctx.arc(px, py, 7, 0, 2 * Math.PI);
        this.ctx.stroke();
        this.ctx.fillText(target.label, px + 11, py - 4);
      } else if (cat === 'vision_ai') {
        this.ctx.strokeRect(px - 8, py - 8, 16, 16);
        this.ctx.fillText(`👁 ${target.label}`, px + 12, py + 3);
      }

      this.ctx.setLineDash([]);
    }
  }

  renderConstellations(w, h) {
    // In adaptive mode, check if stars should be hidden due to daytime (Sun > -6°) or overcast
    if (this.skyVisibilityMode === 'adaptive') {
      if (this.sunAltitude != null && this.sunAltitude > -6.0) {
        // Daytime: Stars are washed out by daylight
        return;
      }
      if (this.metarData && (this.metarData.cover === 'OVC' || this.metarData.cover === 'VV')) {
        // Heavy overcast sky obscures celestial stars
        return;
      }
    }

    const cx = w / 2;
    const cy = h / 2;
    const rMax = Math.min(w, h) / 2 * 0.98;

    const starMap = {};
    const projectStarToDome = (star) => {
      const alt = star.altitude_deg;
      const az = star.azimuth_deg;
      if (alt < 0) return null;
      const theta = (90 - alt) * (Math.PI / 180);
      const r = (theta / (Math.PI / 2)) * rMax;
      const azRad = az * (Math.PI / 180);
      const px = cx + r * Math.sin(azRad);
      const py = cy - r * Math.cos(azRad);
      return { x: px, y: py };
    };

    for (const s of this.stars) {
      const pt = projectStarToDome(s);
      if (pt) starMap[s.id] = pt;
    }

    this.ctx.strokeStyle = 'rgba(99, 102, 241, 0.45)';
    this.ctx.lineWidth = 1.2;
    this.ctx.beginPath();
    for (const [s1, s2] of this.constellations) {
      const p1 = starMap[s1.id];
      const p2 = starMap[s2.id];
      if (p1 && p2) {
        this.ctx.moveTo(p1.x, p1.y);
        this.ctx.lineTo(p2.x, p2.y);
      }
    }
    this.ctx.stroke();

    this.ctx.fillStyle = 'rgba(255, 255, 255, 0.75)';
    this.ctx.font = '10px JetBrains Mono, monospace';
    for (const s of this.stars) {
      if (s.mag <= 1.5 && starMap[s.id]) {
        const pt = starMap[s.id];
        this.ctx.fillText(s.name, pt.x + 5, pt.y - 4);
      }
    }
  }
}
