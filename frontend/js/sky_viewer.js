class SkyViewer {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    this.img = new Image();
    this.isRunning = false;
    this.pollInterval = 2000; // 2 seconds
    this.timer = null;
    this.showAnnotations = true;
    this.showConstellations = false;
    this.showTracking = true;
    this.unitSystem = 'metric'; // 'metric' or 'us'
    this.stars = [];
    this.constellations = [];
    this.targets = [];

    this.img.onload = () => {
      this.render();
      document.getElementById('sky-loader')?.classList.add('hidden');
    };
  }

  setUnitSystem(unit) {
    this.unitSystem = unit;
    this.render();
  }

  start() {
    this.isRunning = true;
    this.fetchFrame();
    this.fetchAstrometryData();
  }

  stop() {
    this.isRunning = false;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  fetchFrame() {
    if (!this.isRunning) return;
    // Cache-bust timestamp
    const overlayParam = this.showAnnotations ? 'true' : 'false';
    this.img.src = `/api/sky/live.jpg?overlay=${overlayParam}&t=${Date.now()}`;

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
    this.ctx.clearRect(0, 0, w, h);

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
  }

  renderTargets(w, h) {
    this.ctx.font = '10px JetBrains Mono, monospace';

    for (const target of this.targets) {
      const px = (target.pixel_x / 1200) * w;
      const py = (target.pixel_y / 1200) * h;
      const color = target.color || '#06b6d4';
      const cat = target.category;

      this.ctx.strokeStyle = color;
      this.ctx.fillStyle = color;
      this.ctx.lineWidth = 1.5;

      if (cat === 'airplane') {
        // Cyan Aircraft Box HUD
        this.ctx.strokeRect(px - 10, py - 10, 20, 20);
        // Heading indicator line if speed exists
        this.ctx.beginPath();
        this.ctx.arc(px, py, 3, 0, 2 * Math.PI);
        this.ctx.fill();
        // Label badge with unit awareness
        let displayLabel = target.label;
        if (target.telemetry && target.telemetry.altitude_ft != null) {
          if (this.unitSystem === 'metric') {
            const altM = Math.round(target.telemetry.altitude_ft * 0.3048);
            displayLabel = `${target.telemetry.flight || target.label} (${altM}m)`;
          } else {
            displayLabel = `${target.telemetry.flight || target.label} (FL${Math.round(target.telemetry.altitude_ft / 100)})`;
          }
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
        this.ctx.fillText(`🛰 ${target.label}`, px + 13, py + 3);
      } else if (cat === 'unidentified') {
        // Red Alert HUD (Bracket crosshair)
        this.ctx.strokeStyle = '#ef4444';
        this.ctx.lineWidth = 2;
        const b = 12;
        this.ctx.beginPath();
        // Top-left bracket
        this.ctx.moveTo(px - b, py - b + 5); this.ctx.lineTo(px - b, py - b); this.ctx.lineTo(px - b + 5, py - b);
        // Top-right bracket
        this.ctx.moveTo(px + b - 5, py - b); this.ctx.lineTo(px + b, py - b); this.ctx.lineTo(px + b, py - b + 5);
        // Bottom-left bracket
        this.ctx.moveTo(px - b, py + b - 5); this.ctx.lineTo(px - b, py + b); this.ctx.lineTo(px - b + 5, py + b);
        // Bottom-right bracket
        this.ctx.moveTo(px + b - 5, py + b); this.ctx.lineTo(px + b, py + b); this.ctx.lineTo(px + b, py + b - 5);
        this.ctx.stroke();

        this.ctx.fillStyle = '#ef4444';
        this.ctx.fillText(`⚠ ${target.label}`, px + 15, py + 4);
      } else if (cat === 'celestial') {
        // Yellow subtle circle reticle
        this.ctx.beginPath();
        this.ctx.arc(px, py, 7, 0, 2 * Math.PI);
        this.ctx.stroke();
        this.ctx.fillText(target.label, px + 11, py - 4);
      } else if (cat === 'vision_ai') {
        // Orange Object box
        this.ctx.strokeRect(px - 8, py - 8, 16, 16);
        this.ctx.fillText(`👁 ${target.label}`, px + 12, py + 3);
      }
    }
  }

  renderConstellations(w, h) {
    const cx = w / 2;
    const cy = h / 2;
    const rMax = Math.min(w, h) / 2 * 0.98;

    const starMap = {};
    const projectStarToDome = (star) => {
      const alt = star.altitude_deg;
      const az = star.azimuth_deg;
      if (alt < 0) return null;
      // Zenithal polar mapping
      const theta = (90 - alt) * (Math.PI / 180);
      const r = (theta / (Math.PI / 2)) * rMax;
      const azRad = az * (Math.PI / 180);
      const px = cx + r * Math.sin(azRad);
      const py = cy - r * Math.cos(azRad);
      return { x: px, y: py };
    };

    // Index visible projected stars
    for (const s of this.stars) {
      const pt = projectStarToDome(s);
      if (pt) starMap[s.id] = pt;
    }

    // Draw constellation lines
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

    // Draw prominent star labels
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

