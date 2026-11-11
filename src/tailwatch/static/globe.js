// Dependency-free orthographic globe on a 2D canvas.
// Evidence arcs (completed visits behind reviewed signals) are the emphasised series.
// Simulated traffic is recessive, clearly labelled context and never real aircraft.

const RAD = Math.PI / 180;
const TAU = Math.PI * 2;

// Approximate metropolitan coordinates; they only animate the simulated layer.
const HUBS = [
  [-74.0, 40.7], [-0.1, 51.5], [139.7, 35.7], [103.8, 1.35], [55.3, 25.2], [8.7, 50.1],
  [-46.6, -23.5], [151.2, -33.9], [-118.2, 34.0], [-87.6, 41.9], [-79.4, 43.7], [114.2, 22.3],
  [72.9, 19.1], [28.0, -26.2], [-99.1, 19.4], [2.35, 48.9], [127.0, 37.6], [8.5, 47.4],
  [-122.4, 37.8], [-95.4, 29.8], [-58.4, -34.6], [31.2, 30.0], [77.1, 28.6], [121.5, 31.2],
  [-3.7, 40.4], [18.1, 59.3], [174.8, -36.8], [100.5, 13.8], [-123.1, 49.3], [3.4, 6.5],
];

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const wrap = lon => ((((lon + 180) % 360) + 360) % 360) - 180;

export function toVector(lon, lat) {
  const l = lon * RAD;
  const p = lat * RAD;
  const c = Math.cos(p);
  return [c * Math.sin(l), Math.sin(p), c * Math.cos(l)];
}

function slerp(a, b, t) {
  const omega = Math.acos(clamp(dot(a, b), -1, 1));
  if (omega < 1e-6) return a.slice();
  const s = Math.sin(omega);
  const wa = Math.sin((1 - t) * omega) / s;
  const wb = Math.sin(t * omega) / s;
  return [a[0] * wa + b[0] * wb, a[1] * wa + b[1] * wb, a[2] * wa + b[2] * wb];
}

function seeded(seed) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function buildArc(arc) {
  const a = toVector(arc.from.lon, arc.from.lat);
  const b = toVector(arc.to.lon, arc.to.lat);
  const omega = Math.acos(clamp(dot(a, b), -1, 1));
  const lift = Math.min(0.32, 0.05 + omega * 0.22);
  const points = [];
  for (let i = 0; i <= 64; i += 1) {
    const t = i / 64;
    const p = slerp(a, b, t);
    const h = 1 + lift * Math.sin(Math.PI * t);
    points.push([p[0] * h, p[1] * h, p[2] * h]);
  }
  const middle = slerp(a, b, 0.5);
  return {
    ...arc,
    a,
    b,
    points,
    centre: { lon: Math.atan2(middle[0], middle[2]) / RAD, lat: Math.asin(clamp(middle[1], -1, 1)) / RAD },
  };
}

function buildTraffic(count, seed) {
  const random = seeded(seed);
  const hubs = HUBS.map(([lon, lat]) => toVector(lon, lat));
  const flights = [];
  while (flights.length < count) {
    const a = hubs[Math.floor(random() * hubs.length)];
    const b = hubs[Math.floor(random() * hubs.length)];
    const omega = Math.acos(clamp(dot(a, b), -1, 1));
    if (omega < 0.2) continue;
    flights.push({ a, b, phase: random(), speed: (0.01 + random() * 0.012) / omega, lift: 0.01 + omega * 0.02 });
  }
  return flights;
}

function graticule() {
  const lines = [];
  for (let lon = -180; lon < 180; lon += 30) {
    const line = [];
    for (let lat = -90; lat <= 90; lat += 3) line.push(toVector(lon, lat));
    lines.push(line);
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    const line = [];
    for (let lon = -180; lon <= 180; lon += 3) line.push(toVector(lon, lat));
    lines.push(line);
  }
  return lines;
}

export function createGlobe(canvas, { tooltip, onSelect, onView } = {}) {
  const context = canvas.getContext('2d');
  const grid = graticule();
  const state = {
    lon: -40, lat: 24, width: 0, height: 0, dpr: 1, radius: 0, base: 0, zoom: 1, cx: 0, cy: 0,
    land: new Float32Array(0), arcs: [], traffic: [], live: [], selected: null, hover: null,
    motion: true, dragging: null, target: null, last: 0, frame: 0, clock: 0,
  };
  let colours = {};

  function readColours() {
    const style = getComputedStyle(canvas);
    const read = name => style.getPropertyValue(name).trim();
    colours = {
      sphereHigh: read('--sphere-high'), sphereLow: read('--sphere-low'), glow: read('--glow'),
      land: read('--land'), grid: read('--grid'), axis: read('--axis'), accent: read('--accent'),
      ring: read('--surface'), ink: read('--ink'), ink2: read('--ink-2'), traffic: read('--muted'),
      live: read('--live'),
    };
  }

  // Rotate world vectors into view space: z points toward the viewer.
  function basis() {
    const l = state.lon * RAD;
    const p = state.lat * RAD;
    return [Math.cos(l), Math.sin(l), Math.cos(p), Math.sin(p)];
  }

  function project(v, [cl, sl, cp, sp]) {
    const x = v[0] * cl - v[2] * sl;
    const z1 = v[0] * sl + v[2] * cl;
    const y = v[1] * cp - z1 * sp;
    const z = v[1] * sp + z1 * cp;
    const lifted = v[0] * v[0] + v[1] * v[1] + v[2] * v[2] > 1.0001;
    // A lifted point is hidden only when it is behind the sphere's disc.
    const visible = lifted ? z > 0 || x * x + y * y > 1 : z > 0;
    return [state.cx + state.radius * x, state.cy - state.radius * y, visible, z];
  }

  function polyline(points, frame) {
    let drawing = false;
    context.beginPath();
    for (const point of points) {
      const [x, y, visible] = project(point, frame);
      if (!visible) {
        drawing = false;
      } else if (drawing) {
        context.lineTo(x, y);
      } else {
        context.moveTo(x, y);
        drawing = true;
      }
    }
    context.stroke();
  }

  function marker(x, y, radius) {
    context.fillStyle = colours.ring;
    context.beginPath();
    context.arc(x, y, radius + 2, 0, TAU);
    context.fill();
    context.fillStyle = colours.accent;
    context.beginPath();
    context.arc(x, y, radius, 0, TAU);
    context.fill();
  }

  function label(text, x, y, align) {
    context.font = '600 11px ui-monospace, SFMono-Regular, Menlo, monospace';
    context.textAlign = align;
    context.textBaseline = 'middle';
    context.lineWidth = 3;
    context.strokeStyle = colours.ring;
    context.strokeText(text, x, y);
    context.fillStyle = colours.ink2;
    context.fillText(text, x, y);
  }

  function draw() {
    const { width, height, radius: R, cx, cy } = state;
    if (!width || !height) return;
    const frame = basis();
    context.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
    context.clearRect(0, 0, width, height);

    const glow = context.createRadialGradient(cx, cy, R * 0.94, cx, cy, R * 1.2);
    glow.addColorStop(0, colours.glow);
    glow.addColorStop(1, 'rgba(0,0,0,0)');
    context.fillStyle = glow;
    context.beginPath();
    context.arc(cx, cy, R * 1.2, 0, TAU);
    context.fill();
    const body = context.createRadialGradient(cx - R * 0.35, cy - R * 0.4, R * 0.05, cx, cy, R);
    body.addColorStop(0, colours.sphereHigh);
    body.addColorStop(1, colours.sphereLow);
    context.fillStyle = body;
    context.beginPath();
    context.arc(cx, cy, R, 0, TAU);
    context.fill();

    context.lineWidth = 1;
    context.strokeStyle = colours.grid;
    for (const line of grid) polyline(line, frame);

    const land = state.land;
    const size = Math.min(3, Math.max(1.5, R / 150));
    const [cl, sl, cp, sp] = frame;
    context.fillStyle = colours.land;
    for (let i = 0; i < land.length; i += 3) {
      // Inlined projection: thousands of dots per frame, no per-dot allocation.
      const z1 = land[i] * sl + land[i + 2] * cl;
      const z = land[i + 1] * sp + z1 * cp;
      if (z <= 0) continue;
      const x = cx + R * (land[i] * cl - land[i + 2] * sl);
      const y = cy - R * (land[i + 1] * cp - z1 * sp);
      // Limb darkening gives depth; land recedes further when live traffic is drawn.
      context.globalAlpha = (0.3 + 0.55 * z) * (state.live.length ? 0.6 : 1);
      context.fillRect(x - size / 2, y - size / 2, size, size);
    }
    context.globalAlpha = 1;
    context.strokeStyle = colours.axis;
    context.beginPath();
    context.arc(cx, cy, R, 0, TAU);
    context.stroke();

    context.fillStyle = colours.traffic;
    for (const flight of state.traffic) {
      const t = (flight.phase + state.clock * flight.speed) % 1;
      for (let k = 0; k < 4; k += 1) {
        const s = t - k * 0.008;
        if (s < 0) break;
        const p = slerp(flight.a, flight.b, s);
        const h = 1 + flight.lift * Math.sin(Math.PI * s);
        const [x, y, visible] = project([p[0] * h, p[1] * h, p[2] * h], frame);
        if (!visible) break;
        context.globalAlpha = 0.75 - k * 0.17;
        context.fillRect(x - 1, y - 1, 2, 2);
      }
    }
    context.globalAlpha = 1;

    if (state.live.length) drawLive(frame);

    context.lineCap = 'round';
    context.lineJoin = 'round';
    const ordered = [...state.arcs].sort((a, b) => (a.id === state.selected) - (b.id === state.selected));
    for (const arc of ordered) {
      const selected = arc.id === state.selected;
      const quiet = state.selected !== null && !selected && arc.id !== state.hover?.id;
      context.globalAlpha = quiet ? 0.4 : 1;
      context.strokeStyle = colours.accent;
      context.lineWidth = selected ? 2.5 : 2;
      polyline(arc.points, frame);
      for (const end of [arc.a, arc.b]) {
        const [x, y, visible] = project(end, frame);
        if (visible) marker(x, y, 4);
      }
      context.globalAlpha = 1;
      if (selected) {
        // Labels point away from each other so short arcs never stack them.
        const ends = [[arc.a, arc.from.code], [arc.b, arc.to.code]].map(([end, code]) => [...project(end, frame), code]);
        const leftmost = ends[0][0] <= ends[1][0] ? 0 : 1;
        ends.forEach(([x, y, visible, , code], index) => {
          if (!visible) return;
          if (index === leftmost) label(code, x - 10, y, 'right');
          else label(code, x + 10, y, 'left');
        });
        if (state.motion) {
          const index = Math.floor(((state.clock * 0.4) % 1) * (arc.points.length - 1));
          const [x, y, visible] = project(arc.points[index], frame);
          if (visible) {
            context.fillStyle = colours.ink;
            context.beginPath();
            context.arc(x, y, 3, 0, TAU);
            context.fill();
          }
        }
      }
    }
  }

  // Real, anonymous ADS-B positions, dead-reckoned from their reported track and ground
  // speed for at most two minutes. Each aircraft is a heading-oriented glyph with a trail
  // whose length is proportional to ground speed: shape, not only hue, sets them apart.
  function drawLive([cl, sl, cp, sp]) {
    const now = Date.now();
    const R = state.radius;
    const scale = Math.min(2.2, 0.75 + state.zoom * 0.35);
    const project3 = (lon, lat) => {
      const l = lon * RAD;
      const p = lat * RAD;
      const c = Math.cos(p);
      const vx = c * Math.sin(l);
      const vy = Math.sin(p);
      const vz = c * Math.cos(l);
      const z1 = vx * sl + vz * cl;
      return [state.cx + R * (vx * cl - vz * sl), state.cy - R * (vy * cp - z1 * sp), vy * sp + z1 * cp];
    };
    const trails = [];
    const glyphs = [];
    const dots = [];
    for (const plane of state.live) {
      let { lon, lat } = plane;
      const heading = plane.track >= 0;
      if (heading && plane.gs > 0) {
        const degrees = (plane.gs * Math.min(900, Math.max(0, (now - plane.at) / 1000))) / 3600 / 60;
        lat += degrees * plane.cosTrack;
        lon += (degrees * plane.sinTrack) / Math.max(0.2, Math.cos(lat * RAD));
      }
      const [x, y, z] = project3(lon, lat);
      if (z <= 0.02 || x < -20 || y < -20 || x > state.width + 20 || y > state.height + 20) continue;
      if (!heading) {
        dots.push(x, y);
        continue;
      }
      const [ax, ay] = project3(
        lon + (0.5 * plane.sinTrack) / Math.max(0.2, Math.cos(lat * RAD)),
        lat + 0.5 * plane.cosTrack,
      );
      const length = Math.hypot(ax - x, ay - y);
      if (length < 0.01) {
        dots.push(x, y);
        continue;
      }
      const ux = (ax - x) / length;
      const uy = (ay - y) / length;
      const size = scale * Math.max(0.5, z);
      const tail = Math.min(26, (plane.gs / 480) * 15) * size;
      trails.push(x, y, x - ux * tail, y - uy * tail);
      glyphs.push(x, y, ux, uy, size);
    }
    context.lineCap = 'round';
    context.strokeStyle = colours.live;
    context.lineWidth = 1;
    context.globalAlpha = 0.3;
    context.beginPath();
    for (let i = 0; i < trails.length; i += 4) {
      context.moveTo(trails[i], trails[i + 1]);
      context.lineTo(trails[i + 2], trails[i + 3]);
    }
    context.stroke();
    context.globalAlpha = 0.95;
    context.fillStyle = colours.live;
    context.beginPath();
    for (let i = 0; i < glyphs.length; i += 5) {
      const [x, y, ux, uy, k] = glyphs.slice(i, i + 5);
      // Chevron: nose ahead, swept wings behind, notch at the tail.
      context.moveTo(x + ux * 3.6 * k, y + uy * 3.6 * k);
      context.lineTo(x - ux * 2.6 * k - uy * 2.4 * k, y - uy * 2.6 * k + ux * 2.4 * k);
      context.lineTo(x - ux * 1.2 * k, y - uy * 1.2 * k);
      context.lineTo(x - ux * 2.6 * k + uy * 2.4 * k, y - uy * 2.6 * k - ux * 2.4 * k);
      context.closePath();
    }
    for (let i = 0; i < dots.length; i += 2) context.rect(dots[i] - 1, dots[i + 1] - 1, 2, 2);
    context.fill();
    context.globalAlpha = 1;
  }

  function tick(time) {
    state.frame = 0;
    const dt = state.last ? Math.min(0.1, (time - state.last) / 1000) : 0;
    state.last = time;
    if (state.motion) state.clock += dt;
    if (state.target) {
      const k = 1 - Math.exp(-dt * 3.5);
      const dLon = wrap(state.target.lon - state.lon);
      const dLat = state.target.lat - state.lat;
      state.lon = wrap(state.lon + dLon * k);
      state.lat += dLat * k;
      if (state.target.zoom) {
        state.zoom += (state.target.zoom - state.zoom) * k;
        state.radius = state.base * state.zoom;
      }
      const zoomDone = !state.target.zoom || Math.abs(state.target.zoom - state.zoom) < 0.01;
      if (Math.abs(dLon) < 0.05 && Math.abs(dLat) < 0.05 && zoomDone) state.target = null;
    } else if (state.motion && !state.dragging && !state.live.length) {
      // With live traffic the aircraft provide the motion; the view holds still.
      state.lon = wrap(state.lon + (dt * 3) / state.zoom);
    }
    draw();
    onView?.(state.lon, state.lat);
    if (state.motion || state.target || state.dragging) schedule();
    else state.last = 0;
  }

  function schedule() {
    if (!state.frame) state.frame = requestAnimationFrame(tick);
  }

  function resize() {
    const box = canvas.getBoundingClientRect();
    state.width = box.width;
    state.height = box.height;
    state.dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(box.width * state.dpr);
    canvas.height = Math.round(box.height * state.dpr);
    state.base = Math.min(box.width, box.height) * 0.43;
    state.radius = state.base * state.zoom;
    state.cx = box.width / 2;
    state.cy = box.height / 2;
    schedule();
  }

  function nearest(px, py) {
    const frame = basis();
    let best = null;
    let distance = 14; // ~28px hit target around a 2px line
    for (const arc of state.arcs) {
      for (let i = 0; i < arc.points.length; i += 2) {
        const [x, y, visible] = project(arc.points[i], frame);
        if (!visible) continue;
        const d = Math.hypot(x - px, y - py);
        if (d < distance) {
          distance = d;
          best = arc;
        }
      }
    }
    return best;
  }

  function showTooltip(arc, px, py) {
    if (!tooltip) return;
    if (!arc) {
      tooltip.hidden = true;
      return;
    }
    const title = document.createElement('strong');
    title.textContent = arc.title;
    const detail = document.createElement('span');
    detail.textContent = arc.detail;
    tooltip.replaceChildren(title, detail);
    tooltip.hidden = false;
    const x = Math.min(px + 14, state.width - tooltip.offsetWidth - 8);
    const y = Math.max(8, py - tooltip.offsetHeight - 10);
    tooltip.style.transform = `translate(${Math.max(8, x)}px, ${y}px)`;
  }

  canvas.addEventListener('pointerdown', event => {
    state.dragging = { x: event.clientX, y: event.clientY, lon: state.lon, lat: state.lat, moved: false };
    state.target = null;
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', event => {
    const box = canvas.getBoundingClientRect();
    const px = event.clientX - box.left;
    const py = event.clientY - box.top;
    const drag = state.dragging;
    if (drag) {
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      if (Math.hypot(dx, dy) > 3) drag.moved = true;
      state.lon = wrap(drag.lon - dx / state.radius / RAD);
      state.lat = clamp(drag.lat + dy / state.radius / RAD, -70, 70);
      showTooltip(null);
      schedule();
      return;
    }
    const arc = nearest(px, py);
    if (arc?.id !== state.hover?.id) {
      state.hover = arc;
      schedule();
    }
    canvas.style.cursor = arc ? 'pointer' : 'grab';
    showTooltip(arc, px, py);
  });
  canvas.addEventListener('pointerup', event => {
    const drag = state.dragging;
    state.dragging = null;
    canvas.releasePointerCapture(event.pointerId);
    if (drag && !drag.moved && state.hover) onSelect?.(state.hover.id);
    schedule();
  });
  canvas.addEventListener('pointerleave', () => {
    state.hover = null;
    showTooltip(null);
    schedule();
  });
  function zoomBy(factor) {
    state.zoom = clamp(state.zoom * factor, 1, 6);
    state.radius = state.base * state.zoom;
    if (state.target) delete state.target.zoom;
    schedule();
  }
  canvas.addEventListener(
    'wheel',
    event => {
      event.preventDefault();
      zoomBy(Math.exp(-event.deltaY * 0.0015));
    },
    { passive: false },
  );
  canvas.addEventListener('keydown', event => {
    if (event.key === '+' || event.key === '=') {
      event.preventDefault();
      zoomBy(1.25);
      return;
    }
    if (event.key === '-' || event.key === '_') {
      event.preventDefault();
      zoomBy(0.8);
      return;
    }
    const moves = { ArrowLeft: [-10, 0], ArrowRight: [10, 0], ArrowUp: [0, 8], ArrowDown: [0, -8] };
    const move = moves[event.key];
    if (!move) return;
    event.preventDefault();
    state.target = null;
    state.lon = wrap(state.lon + move[0]);
    state.lat = clamp(state.lat + move[1], -70, 70);
    schedule();
  });

  new ResizeObserver(resize).observe(canvas);
  readColours();

  return {
    setLand(points) {
      const vectors = new Float32Array((points.length / 2) * 3);
      for (let i = 0, j = 0; i < points.length; i += 2, j += 3) vectors.set(toVector(points[i], points[i + 1]), j);
      state.land = vectors;
      schedule();
    },
    setArcs(arcs) {
      state.arcs = arcs.map(buildArc);
      if (state.hover && !state.arcs.some(arc => arc.id === state.hover.id)) state.hover = null;
      schedule();
    },
    setLive(rows, receivedAt) {
      state.live = (Array.isArray(rows) ? rows : [])
        .filter(row => Array.isArray(row) && row.length >= 6 && row.slice(0, 6).every(Number.isFinite))
        .map(([lon, lat, track, gs, , age]) => ({
          lon, lat, track, gs,
          at: receivedAt - age * 1000,
          sinTrack: Math.sin(track * RAD),
          cosTrack: Math.cos(track * RAD),
        }));
      schedule();
    },
    setTraffic(enabled) {
      state.traffic = enabled ? buildTraffic(48, 20261110) : [];
      schedule();
    },
    select(id) {
      state.selected = id;
      const arc = state.arcs.find(item => item.id === id);
      if (arc) {
        const centre = { lon: arc.centre.lon, lat: clamp(arc.centre.lat, -50, 55) };
        if (state.motion) state.target = centre;
        else Object.assign(state, centre);
      }
      schedule();
    },
    zoomBy,
    setView({ lon, lat, zoom }) {
      const target = { lon, lat: clamp(lat, -70, 70), zoom: clamp(zoom ?? state.zoom, 1, 6) };
      if (state.motion) {
        state.target = target;
      } else {
        Object.assign(state, { lon: target.lon, lat: target.lat, zoom: target.zoom });
        state.radius = state.base * state.zoom;
      }
      schedule();
    },
    setMotion(enabled) {
      state.motion = enabled;
      if (!enabled) state.target = null;
      schedule();
    },
  };
}
