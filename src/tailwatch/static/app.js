// Tailwatch research terminal. Every API value is untrusted and inserted with textContent.
import { createGlobe } from './globe.js';

const $ = selector => document.querySelector(selector);

const STATEMENT =
  'Observed activity is not proof of a meeting or deal. Signed contributions explain the ' +
  "model's raw logit; they are not causal effects or probability shares.";
const condition = (status, value, contribution) => ({
  status,
  value,
  in_model: contribution !== undefined,
  contribution: contribution ?? null,
});
const place = (code, latitude, longitude) => ({ code, latitude, longitude });

// Fictional companies, SYN- airport codes and synthetic visits. Nothing here is real.
const SAMPLES = [
  {
    episode_id: 'demo-1', visitor: '11111111-1111-4111-8111-111111111111',
    host: '22222222-2222-4222-8222-222222222222', synthetic: true,
    created_at: '2026-01-02T13:30:00Z', outcome: 'pending',
    public: {
      visitor_name: 'Northstar Industrials', host_name: 'Harbor Systems',
      description: 'Synthetic four-hour dwell at an airport near a fictional host office, during a quiet period for insider purchases.',
      as_of: '2026-01-02T00:00:00Z', synthetic: true, calibrated_probability: null,
      contributions: { flight: 0.381, silence: 0.204, semantic_similarity: 0.097 },
      conditions: {
        flight: condition('observed', 0.279, 0.381), novelty: condition('observed', 1),
        intensity: condition('insufficient_history', null), silence: condition('observed', 2.0, 0.204),
        semantic_similarity: condition('observed', 0.912, 0.097),
        flight_silence_interaction: condition('observed', 0.559),
      },
      visits: [{ evidence_id: 'synthetic:visit:1', origin: place('SYN-NST', 41.98, -87.9), destination: place('SYN-HBR', 42.36, -71.01), arrived_at: '2026-01-01T00:00:00Z', departed_at: '2026-01-01T04:00:00Z', synthetic: true }],
      evidence: ['synthetic:visit:1 · arrival and next departure observed at the same airport', 'No real company, aircraft, insider or transaction is represented.'],
      withheld_evidence: 0, statement: STATEMENT,
    },
  },
  {
    episode_id: 'demo-2', visitor: '44444444-4444-4444-8444-444444444444',
    host: '55555555-5555-4555-8555-555555555555', synthetic: true,
    created_at: '2026-01-02T13:10:00Z', outcome: 'pending',
    public: {
      visitor_name: 'Kestrel Fabrication', host_name: 'Orchard Lane Instruments',
      description: 'Synthetic cross-border visit. Recent insider buying slightly lowers the raw logit; one context signal is stale.',
      as_of: '2026-01-02T00:00:00Z', synthetic: true, calibrated_probability: null,
      contributions: { flight: 0.226, silence: -0.081, semantic_similarity: 0.153 },
      conditions: {
        flight: condition('observed', 0.164, 0.226), novelty: condition('ambiguous', null),
        intensity: condition('observed', 1.4), silence: condition('observed', 0.4, -0.081),
        semantic_similarity: condition('observed', 0.944, 0.153),
        flight_silence_interaction: condition('stale', null),
      },
      visits: [{ evidence_id: 'synthetic:visit:2', origin: place('SYN-KST', 51.47, -0.45), destination: place('SYN-OLI', 47.46, 8.55), arrived_at: '2025-12-30T09:00:00Z', departed_at: '2025-12-30T15:30:00Z', synthetic: true }],
      evidence: ['synthetic:visit:2 · six-hour dwell between an arrival and the next departure'],
      withheld_evidence: 0, statement: STATEMENT,
    },
  },
  {
    episode_id: 'demo-3', visitor: '33333333-3333-4333-8333-333333333333',
    host: '11111111-1111-4111-8111-111111111111', synthetic: true,
    created_at: '2026-01-02T12:45:00Z', outcome: 'pending',
    public: {
      visitor_name: 'Fieldwork Technologies', host_name: 'Northstar Industrials',
      description: 'Synthetic signal whose visit evidence is not cleared for display, so no location is drawn on the globe.',
      as_of: '2026-01-02T00:00:00Z', synthetic: true, calibrated_probability: null,
      contributions: { flight: 0.142, silence: 0.118, semantic_similarity: -0.064 },
      conditions: {
        flight: condition('observed', 0.105, 0.142), novelty: condition('observed', 0),
        intensity: condition('missing_source', null), silence: condition('observed', 1.3, 0.118),
        semantic_similarity: condition('observed', 0.611, -0.064),
        flight_silence_interaction: condition('observed', 0.137),
      },
      visits: [], evidence: [], withheld_evidence: 1, statement: STATEMENT,
    },
  },
];

const FEATURES = {
  flight: 'Flight dwell', novelty: 'Host novelty', intensity: 'Travel intensity',
  silence: 'Buying silence', buying: 'Insider buying',
  semantic_similarity: 'Text similarity', flight_silence_interaction: 'Flight × silence',
};
// Status colours always travel with an icon and a text label.
const AVAILABILITY = {
  observed: ['●', 'Observed', 'good'],
  stale: ['◐', 'Stale', 'warning'],
  ambiguous: ['◆', 'Ambiguous', 'warning'],
  insufficient_history: ['○', 'Insufficient history', 'muted'],
  missing_source: ['○', 'Missing source', 'muted'],
  not_applicable: ['–', 'Not applicable', 'muted'],
};
const OUTCOMES = {
  pending: ['◷', 'Pending follow-up', 'muted'],
  announced: ['✓', 'Announced', 'good'],
  withdrawn: ['◆', 'Withdrawn after announcement', 'serious'],
  no_deal: ['○', 'No deal within horizon', 'muted'],
};

let focused;
const state = {
  demo: true, key: '', items: [], watch: new Set(), view: 'all', cursor: null,
  selected: null, motion: !matchMedia('(prefers-reduced-motion: reduce)').matches, listView: false,
  live: { available: false, traffic: false, filings: false }, traffic: null, filings: null, timers: [],
};
const LIVE_NOTICE = 'Live research feeds: real ADS-B air traffic (anonymous, privacy-filtered) and the latest SEC Form 4 filings. No predictions are made; reviewed signals appear only when the gated pipeline produces them.';
const nyTime = value => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '--:--' : new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date);
};

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function chip([icon, text, tone]) {
  const element = node('span', undefined, `chip ${tone}`);
  element.append(node('span', icon, 'chip-icon'), node('span', text));
  return element;
}

function status(text) {
  $('#status').textContent = text;
  setTimeout(() => {
    if ($('#status').textContent === text) $('#status').textContent = '';
  }, 6000);
}

// Head and tail keep sequential identifiers distinguishable when no name is published.
const shortId = value => `${String(value).slice(0, 4)}…${String(value).slice(-4)}`;
const names = item => [
  item.public?.visitor_name || shortId(item.visitor),
  item.public?.host_name || shortId(item.host),
];
const utc = value => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'unknown time' : `${date.toISOString().slice(0, 16).replace('T', ' ')}Z`;
};
const number = value => (typeof value === 'number' && Number.isFinite(value) ? value : null);
const signed = value => `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(3)}`;

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { Authorization: `Bearer ${state.key}`, 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  if (!response.ok) throw Error(response.status === 401 ? 'Your key is invalid or revoked.' : `Request failed (${response.status}).`);
  return response.json();
}

function visible() {
  const query = $('#search').value.trim().toLowerCase();
  return state.items.filter(item => {
    if (state.view === 'watchlist' && !state.watch.has(item.visitor) && !state.watch.has(item.host)) return false;
    return !query || JSON.stringify(item.public || {}).toLowerCase().includes(query);
  });
}

function visitsOf(item) {
  return (Array.isArray(item.public?.visits) ? item.public.visits : []).filter(
    v => number(v?.origin?.latitude) !== null && number(v?.origin?.longitude) !== null &&
      number(v?.destination?.latitude) !== null && number(v?.destination?.longitude) !== null,
  );
}

function dwell(visit) {
  const hours = (new Date(visit.departed_at) - new Date(visit.arrived_at)) / 3.6e6;
  return Number.isFinite(hours) && hours > 0 ? `${hours.toFixed(1)} h dwell` : 'dwell unknown';
}

// ----- globe ---------------------------------------------------------------

const globe = createGlobe($('#globe'), {
  tooltip: $('#globe-tooltip'),
  onSelect: id => select(id),
  onView: (() => {
    let previous = '';
    return (lon, lat) => {
      const text = `LON ${lon >= 0 ? '+' : '−'}${Math.abs(lon).toFixed(1).padStart(5, '0')}° LAT ${lat >= 0 ? '+' : '−'}${Math.abs(lat).toFixed(1).padStart(4, '0')}°`;
      if (text !== previous) $('#hud-view').textContent = previous = text;
    };
  })(),
});
fetch('/assets/land.json')
  .then(response => (response.ok ? response.json() : Promise.reject(Error('land'))))
  .then(data => globe.setLand(data.points))
  .catch(() => status('Land outlines unavailable; evidence arcs still render.'));

function renderGlobe(items) {
  const arcs = [];
  const list = $('#visit-list');
  list.replaceChildren();
  for (const item of items) {
    const [visitor, host] = names(item);
    for (const visit of visitsOf(item)) {
      const detail = `${visit.origin.code} → ${visit.destination.code} · arrived ${utc(visit.arrived_at)} · ${dwell(visit)}${visit.synthetic ? ' · synthetic' : ''}`;
      arcs.push({
        id: item.episode_id,
        title: `${visitor} → ${host}`,
        detail,
        from: { lon: visit.origin.longitude, lat: visit.origin.latitude, code: visit.origin.code },
        to: { lon: visit.destination.longitude, lat: visit.destination.latitude, code: visit.destination.code },
      });
      const entry = node('li');
      const button = node('button', `${visitor} → ${host}`, 'visit-button');
      button.type = 'button';
      button.setAttribute('aria-pressed', String(item.episode_id === state.selected));
      button.addEventListener('click', () => select(item.episode_id));
      entry.append(button, node('span', detail, 'visit-detail'));
      list.append(entry);
    }
  }
  if (!list.children.length) list.append(node('li', 'No displayable completed visits in this view.', 'visit-empty'));
  const withheld = items.filter(item => !visitsOf(item).length).length;
  globe.setArcs(arcs);
  $('#hud-arcs').textContent = `${arcs.length} VISIT${arcs.length === 1 ? '' : 'S'}`;
  $('#globe-note').textContent = withheld
    ? `${withheld} signal${withheld === 1 ? ' has' : 's have'} no displayable visit location; nothing is inferred for ${withheld === 1 ? 'it' : 'them'}.`
    : 'Only visits whose evidence may be displayed are drawn.';
}

// ----- conditions ----------------------------------------------------------

function renderConditions(item, scale) {
  const body = $('#conditions-body');
  const gates = $('#gates');
  body.replaceChildren();
  gates.replaceChildren();
  $('#open-evidence').disabled = !item;
  document.querySelector('.conditions').classList.toggle('empty-state', !item);
  $('#watch-selected').disabled = !item;
  if (!item) {
    $('#conditions-pair').textContent = 'No signal selected';
    $('#conditions-meta').textContent = state.demo || state.items.length ? 'Select a signal to inspect its inputs.' : 'No reviewed signals yet. Live feeds are research inputs, not signals or predictions.';
    return;
  }
  const p = item.public || {};
  const [visitor, host] = names(item);
  $('#conditions-pair').textContent = `${visitor} → ${host}`;
  const probability = number(p.calibrated_probability);
  $('#conditions-meta').textContent = [
    `Feature cutoff ${p.as_of ? utc(p.as_of) : 'unknown'}`,
    item.synthetic ? 'synthetic run' : 'release model',
    probability === null ? 'calibrated probability unavailable' : `calibrated model estimate ${(probability * 100).toFixed(1)}%`,
  ].join(' · ');
  $('#watch-selected').textContent = state.watch.has(item.host) ? '★ Watching host' : '☆ Watch host';

  const entries = Object.entries(p.conditions && typeof p.conditions === 'object' ? p.conditions : {});
  if (!entries.length && p.contributions) {
    for (const [name, value] of Object.entries(p.contributions)) entries.push([name, { status: 'observed', value: null, in_model: true, contribution: value }]);
  }
  if (!entries.length) {
    body.append(node('p', 'No conditions were published with this alert.', 'muted-text'));
  } else {
    entries.sort(([a, x], [b, y]) => Number(Boolean(y?.in_model)) - Number(Boolean(x?.in_model)) || a.localeCompare(b));
    const table = node('table', undefined, 'conditions-table');
    const caption = node('caption', 'Signal availability and signed raw-logit contributions', 'sr-only');
    const head = node('thead');
    const headRow = node('tr');
    for (const title of ['Signal', 'State', 'Value', 'Contribution']) headRow.append(node('th', title));
    head.append(headRow);
    const rows = node('tbody');
    for (const [name, entry] of entries) {
      const row = node('tr');
      row.append(node('th', FEATURES[name] || name.replaceAll('_', ' ')));
      const stateCell = node('td');
      stateCell.append(chip(AVAILABILITY[entry?.status] || ['?', String(entry?.status || 'unknown'), 'muted']));
      row.append(stateCell);
      const value = number(entry?.value);
      row.append(node('td', value === null ? '—' : value.toFixed(3), 'numeric'));
      const contributionCell = node('td', undefined, 'contribution');
      const contribution = number(entry?.contribution);
      if (!entry?.in_model || contribution === null) {
        contributionCell.append(node('span', 'not in model', 'muted-text'));
      } else {
        const track = node('span', undefined, 'diverge');
        track.setAttribute('aria-hidden', 'true');
        const bar = node('span', undefined, contribution >= 0 ? 'bar positive' : 'bar negative');
        bar.style.width = `${Math.max(2, (Math.abs(contribution) / scale) * 50)}%`;
        track.append(bar);
        contributionCell.append(track, node('span', signed(contribution), 'numeric'));
      }
      row.append(contributionCell);
      rows.append(row);
    }
    table.append(caption, head, rows);
    const legend = node('p', undefined, 'diverge-legend');
    const raise = node('span', undefined, 'key');
    raise.append(node('span', undefined, 'key-bar positive'), document.createTextNode('raises raw logit'));
    const lower = node('span', undefined, 'key');
    lower.append(node('span', undefined, 'key-bar negative'), document.createTextNode('lowers raw logit'));
    legend.append(raise, lower, node('span', 'Shared scale across visible signals', 'legend-note'));
    body.append(table, legend);
  }

  const gate = (label, value) => {
    const entry = node('li');
    entry.append(node('span', label, 'gate-label'), chip(value));
    gates.append(entry);
  };
  gate('Human review', ['✓', 'Approved by analyst', 'good']);
  gate('Model validation', item.synthetic ? ['▲', 'Not validated · synthetic', 'warning'] : ['✓', 'Active release model', 'good']);
  gate('Delivery', state.demo ? ['○', 'Disabled · no release', 'muted'] : ['○', 'Verified subscriptions only', 'muted']);
  gate('Outcome', OUTCOMES[item.outcome] || OUTCOMES.pending);
}

// ----- signal desk ---------------------------------------------------------

function renderRows(items) {
  const cards = $('#cards');
  cards.replaceChildren();
  for (const item of items) {
    const p = item.public || {};
    const [visitor, host] = names(item);
    const selected = item.episode_id === state.selected;
    const row = node('article', undefined, `row${selected ? ' selected' : ''}`);
    const pick = node('button', undefined, 'row-pick');
    pick.type = 'button';
    pick.setAttribute('aria-pressed', String(selected));
    pick.append(node('span', visitor, 'company'), node('span', '→', 'arrow'), node('span', host, 'company'));
    pick.addEventListener('click', () => select(item.episode_id));
    const tags = node('div', undefined, 'row-tags');
    tags.append(
      node('span', item.synthetic ? 'SYNTHETIC' : 'REVIEWED', 'tag'),
      node('span', `OUTCOME ${String(item.outcome || 'pending').toUpperCase().replaceAll('_', ' ')}`, 'tag'),
      node('time', utc(item.created_at), 'row-time'),
    );
    const conditions = Object.values(p.conditions || {});
    const observed = conditions.filter(c => c?.status === 'observed').length;
    const facts = node('p', undefined, 'row-facts');
    facts.append(
      node('span', conditions.length ? `${observed}/${conditions.length} signals observed` : 'conditions not published'),
      node('span', visitsOf(item).length ? `${visitsOf(item).length} displayable visit` : 'location withheld'),
    );
    const actions = node('div', undefined, 'row-actions');
    const evidence = node('button', 'Evidence file →', 'link-button');
    evidence.type = 'button';
    evidence.addEventListener('click', () => openDetail(item));
    const watch = node('button', state.watch.has(item.host) ? '★ Watching' : '☆ Watch', 'link-button');
    watch.type = 'button';
    watch.setAttribute('aria-label', `${state.watch.has(item.host) ? 'Stop watching' : 'Watch'} ${host}`);
    watch.addEventListener('click', () => toggleWatch(item.host));
    actions.append(evidence, watch);
    row.append(tags, pick, node('p', p.description || p.headline || 'Reviewed signal; consult the retained evidence.', 'row-text'), facts, actions);
    cards.append(row);
  }
  if (!cards.children.length && !(state.view === 'track' && state.demo)) {
    cards.append(node('p', state.view === 'watchlist'
      ? 'No watched companies match this view. Add one from the signal desk.'
      : 'No reviewed signals match this view. Missing data is not a negative signal.', 'empty'));
  }
}

function renderTape(items) {
  const events = [];
  if (state.demo) {
    events.push(['READINESS', 'synthetic session 2026-01-02 complete'], ['MODEL', 'not activated · sealed evaluation required']);
  }
  for (const item of [...items].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))) {
    const [visitor, host] = names(item);
    events.push(['APPROVED', `${visitor} → ${host} · ${utc(item.created_at)}`]);
    if (item.outcome && item.outcome !== 'pending') events.push([`OUTCOME ${item.outcome.toUpperCase()}`, `${visitor} → ${host}`]);
    if (!visitsOf(item).length) events.push(['LOCATION', `${visitor} → ${host} · withheld, not inferred`]);
  }
  const traffic = state.traffic;
  if (traffic?.enabled) events.push(['AIR', `${traffic.aircraft.length.toLocaleString()} aircraft en route across ${traffic.hubs.length} hubs · ADSB.lol`]);
  for (const filing of (state.filings?.filings || []).slice(0, 12)) {
    const details = filing.details || {};
    const code = details.status === 'parsed' && details.headline_code ? ` · code ${details.headline_code}` : '';
    const owners = (filing.reporting || []).slice(0, 2).join(', ') || 'reporting owner pending';
    events.push([`FORM ${filing.form}`, `${filing.issuer_name || 'issuer pending'} · ${owners}${code} · ${nyTime(filing.filed_at)} ET`]);
  }
  events.push(['DELIVERY', state.demo ? 'disabled · no active release' : 'verified subscriptions only']);
  const tape = $('#tape');
  tape.replaceChildren();
  // A second, hidden copy makes the scrolling loop seamless; assistive tech reads one.
  for (const copy of [false, true]) {
    for (const [kind, text] of events) {
      const entry = node('li');
      if (copy) entry.setAttribute('aria-hidden', 'true');
      entry.append(node('span', kind, 'tape-kind'), node('span', text));
      tape.append(entry);
    }
  }
}

function render() {
  const items = visible();
  if (!items.some(item => item.episode_id === state.selected)) state.selected = items[0]?.episode_id ?? null;
  const resolved = state.items.filter(item => item.outcome && item.outcome !== 'pending').length;
  $('#count').textContent = String(state.items.length);
  $('#outcomes').textContent = `${resolved} of ${state.items.length}`;
  $('#model-status').textContent = state.demo ? 'Not activated' : 'Release-gated';
  $('#model-note').textContent = state.demo ? 'Sealed evaluation required' : 'Real alerts need the active release model';
  $('#delivery-status').textContent = state.demo ? 'Disabled' : 'Operator managed';
  $('#delivery-note').textContent = state.demo ? 'No external notifications' : 'Verified subscriptions required';
  const titles = { all: ['SIGNAL DESK', 'Reviewed signals'], watchlist: ['WATCHLIST', 'Your watched companies'], track: ['TRACK RECORD', 'Original predictions and outcomes'] };
  [$('#view-label').textContent, $('#section-title').textContent] = titles[state.view];
  $('#track').hidden = state.view !== 'track' || !state.demo;
  $('#cards').hidden = state.view === 'track' && state.demo;
  $('#load-more').hidden = !state.cursor;
  $('#legend-simulated').hidden = !state.demo;
  $('#legend-live').hidden = !(state.traffic?.enabled && !state.demo);
  $('#live-tile').hidden = state.demo || !state.live.available;
  $('#filings-panel').hidden = state.demo || !state.live.filings || !state.key;
  const scale = Math.max(0.001, ...state.items.flatMap(item => Object.values(item.public?.contributions || {}).map(v => Math.abs(number(v) ?? 0))));
  renderRows(state.view === 'track' && state.demo ? [] : items);
  renderGlobe(items);
  renderConditions(items.find(item => item.episode_id === state.selected), scale);
  renderTape(state.items);
  if (state.selected !== focused) {
    // Re-centre only when the selection changes, never on every filter keystroke.
    focused = state.selected;
    globe.select(focused);
  }
}

function select(id) {
  state.selected = id;
  render();
  const [visitor, host] = names(state.items.find(item => item.episode_id === id) || {});
  if (visitor) status(`Selected ${visitor} → ${host}.`);
}

async function openDetail(item) {
  const p = item.public || {};
  const [visitor, host] = names(item);
  $('#detail-title').textContent = `${visitor} → ${host}`;
  const body = $('#detail-body');
  body.replaceChildren(node('p', p.statement || 'Observed activity is not proof of a meeting or deal.'));
  for (const evidence of p.evidence || []) body.append(node('blockquote', typeof evidence === 'string' ? evidence : JSON.stringify(evidence)));
  if (number(p.withheld_evidence)) body.append(node('p', `${p.withheld_evidence} evidence item(s) withheld: display rights not cleared.`, 'muted-text'));
  for (const visit of visitsOf(item)) body.append(node('div', `${visit.origin.code} → ${visit.destination.code} · arrived ${utc(visit.arrived_at)} · departed ${utc(visit.departed_at)} · ${dwell(visit)}`, 'history-event'));
  body.append(node('h3', 'Original prediction & outcome history'));
  try {
    const history = state.demo
      ? { items: [{ kind: 'approved', recorded_at: item.created_at }, { kind: 'outcome', outcome: 'pending', recorded_at: item.created_at }] }
      : await api(`/alerts/${encodeURIComponent(item.episode_id)}/history`);
    for (const event of history.items) body.append(node('div', `${event.kind}${event.outcome ? ` · ${event.outcome}` : ''} · ${utc(event.recorded_at)}`, 'history-event'));
  } catch (error) {
    body.append(node('p', error.message));
  }
  body.append(node('p', state.demo ? 'No calibrated probability or validated return is available.' : 'Only explicitly supplied model results should be interpreted; missing values remain unavailable.', 'muted-text'));
  $('#detail').showModal();
}

async function toggleWatch(company) {
  const next = new Set(state.watch);
  if (next.has(company)) next.delete(company);
  else next.add(company);
  try {
    if (!state.demo) await api('/watchlist', { method: 'PUT', body: JSON.stringify({ companies: [...next] }) });
    state.watch = next;
    render();
    status(next.has(company) ? 'Company added to watchlist.' : 'Company removed from watchlist.');
  } catch (error) {
    status(error.message);
  }
}

function renderLive() {
  const traffic = state.traffic;
  const filings = state.filings;
  const parts = [];
  if (traffic) parts.push(`${traffic.hubs.length} hubs reporting`);
  if (filings) parts.push(`${filings.filings.length} Form 4 filings`);
  $('#live-value').textContent = traffic ? `${traffic.aircraft.length.toLocaleString()} aircraft` : filings ? 'SEC only' : 'Connecting…';
  $('#live-note').textContent = parts.join(' · ') || 'Waiting for the first poll';
  if (traffic) {
    const arcs = $('#hud-arcs').textContent.split(' · ')[0];
    $('#hud-arcs').textContent = `${arcs} · ${traffic.aircraft.length.toLocaleString()} AIRCRAFT`;
    const health = traffic.status === 'live' ? '' : ` Air feed ${traffic.status.replaceAll('_', ' ')}; showing the last fresh data.`;
    $('#globe-note').textContent = `${traffic.source}. ${traffic.coverage}; ${traffic.privacy}.${health}`;
  }
  renderFilings();
}

function renderFilings() {
  const list = $('#filings');
  const data = state.filings;
  list.replaceChildren();
  if (!data) return;
  const updated = data.updated_at ? `updated ${nyTime(data.updated_at)} ET` : 'waiting for the first poll';
  $('#filings-meta').textContent = `${data.status.replaceAll('_', ' ')} · ${updated}`;
  for (const filing of data.filings) {
    const details = filing.details || {};
    const row = node('li', undefined, 'filing');
    row.append(node('time', nyTime(filing.filed_at)), node('span', `FORM ${filing.form}`, 'tag form-tag'));
    const who = node('span', filing.issuer_name || 'Issuer pending', 'issuer');
    who.append(node('span', (filing.reporting || []).join(', ') || 'Reporting owner pending', 'owners'));
    row.append(who);
    const detail = node('span', undefined, 'detail');
    if (details.status === 'parsed') {
      const first = details.transactions?.[0];
      detail.append(node('strong', details.headline_code || '—'), document.createTextNode(` ${details.headline_label}`));
      if (first?.shares) {
        const price = first.price ? ` @ $${Number(first.price).toLocaleString(undefined, { maximumFractionDigits: 4 })}` : '';
        detail.append(document.createTextNode(` · ${Number(first.shares).toLocaleString()} sh${price}`));
      }
      if (details.plan_10b5_1 === true) detail.append(document.createTextNode(' · 10b5-1 plan'));
      const roles = details.owners?.[0]?.roles?.join(', ');
      if (roles) detail.append(document.createTextNode(` · ${roles}`));
    } else {
      detail.textContent = details.status === 'unavailable' ? 'Details unavailable' : 'Details pending';
    }
    const link = node('a', 'SEC filing ↗');
    link.href = filing.link;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    row.append(detail, link);
    list.append(row);
  }
  if (!data.filings.length) list.append(node('li', 'No filings received yet.', 'empty'));
}

async function pollTraffic() {
  try {
    const data = await api('/live/traffic');
    state.traffic = data;
    globe.setLive(data.aircraft, Date.now());
    render();
    renderLive();
  } catch (error) {
    status(`Live air traffic: ${error.message}`);
  }
}

async function pollFilings() {
  try {
    state.filings = await api('/live/filings');
    render();
    renderLive();
  } catch (error) {
    status(`Live SEC filings: ${error.message}`);
  }
}

function stopLive() {
  for (const timer of state.timers) clearInterval(timer);
  state.timers = [];
  state.traffic = null;
  state.filings = null;
  globe.setLive([], Date.now());
}

function startLive() {
  stopLive();
  if (state.live.traffic) {
    pollTraffic();
    state.timers.push(setInterval(pollTraffic, 15000));
  }
  if (state.live.filings) {
    pollFilings();
    state.timers.push(setInterval(pollFilings, 30000));
  }
}

async function refresh(append = false) {
  try {
    if (state.demo) {
      state.items = structuredClone(SAMPLES);
      state.cursor = null;
    } else {
      const path = (state.view === 'track' ? '/track-record' : '/alerts') + (append && state.cursor ? `?after=${encodeURIComponent(state.cursor)}` : '');
      const data = await api(path);
      state.items = append ? [...state.items, ...data.items] : data.items;
      state.cursor = data.next_cursor;
      if (!append) state.watch = new Set((await api('/watchlist')).companies);
    }
    globe.setTraffic(state.demo);
    render();
    status(state.demo ? 'Synthetic workspace refreshed.' : 'Workspace refreshed.');
  } catch (error) {
    status(error.message);
  }
}

function setMotion(enabled) {
  state.motion = enabled;
  document.body.classList.toggle('motion-off', !enabled);
  $('#motion').setAttribute('aria-pressed', String(!enabled));
  $('#motion').textContent = enabled ? 'Pause motion' : 'Resume motion';
  globe.setMotion(enabled);
}

function setListView(enabled) {
  state.listView = enabled;
  $('#globe-stage').hidden = enabled;
  $('#visit-list').hidden = !enabled;
  $('#view-globe').setAttribute('aria-pressed', String(!enabled));
  $('#view-list').setAttribute('aria-pressed', String(enabled));
}

function tickClocks() {
  const now = new Date();
  $('#clock-utc').textContent = now.toISOString().slice(11, 19);
  $('#clock-ny').textContent = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).format(now);
}

for (const button of document.querySelectorAll('[data-view]')) {
  button.addEventListener('click', () => {
    state.view = button.dataset.view;
    for (const other of document.querySelectorAll('[data-view]')) {
      other.classList.toggle('active', other === button);
      if (other === button) other.setAttribute('aria-current', 'page');
      else other.removeAttribute('aria-current');
    }
    if (state.demo) render();
    else refresh();
  });
}
$('#search').addEventListener('input', render);
$('#refresh').addEventListener('click', () => refresh());
$('#load-more').addEventListener('click', () => refresh(true));
$('#motion').addEventListener('click', () => setMotion(!state.motion));
$('#view-globe').addEventListener('click', () => setListView(false));
$('#view-list').addEventListener('click', () => setListView(true));
$('#open-evidence').addEventListener('click', () => {
  const item = state.items.find(entry => entry.episode_id === state.selected);
  if (item) openDetail(item);
});
$('#watch-selected').addEventListener('click', () => {
  const item = state.items.find(entry => entry.episode_id === state.selected);
  if (item) toggleWatch(item.host);
});
$('#connect').addEventListener('click', () => $('#settings').showModal());
$('#close-settings').addEventListener('click', () => $('#settings').close());
$('#close-detail').addEventListener('click', () => $('#detail').close());
$('#connect-form').addEventListener('submit', async event => {
  event.preventDefault();
  state.key = $('#api-key').value;
  $('#api-key').value = '';
  Object.assign(state, { demo: false, items: [], watch: new Set(), selected: null });
  $('#mode').textContent = state.live.available ? 'LIVE RESEARCH · NO PREDICTIONS' : 'CONNECTED WORKSPACE';
  $('#notice').textContent = state.live.available
    ? LIVE_NOTICE
    : 'Connected to your tenant workspace. Missing inputs stay explicit; real alerts need validated release evidence. No simulated traffic is shown.';
  $('#settings').close();
  await refresh();
  if (state.live.available) startLive();
});
$('#demo').addEventListener('click', () => {
  stopLive();
  Object.assign(state, { demo: true, key: '', watch: new Set(), selected: null });
  $('#api-key').value = '';
  $('#mode').textContent = 'SYNTHETIC DEMO';
  $('#notice').textContent = 'Fictional companies and synthetic observations. Simulated traffic is not real aircraft. No predictive model is active, and nothing here is an investment recommendation.';
  $('#settings').close();
  refresh();
});
matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', event => setMotion(!event.matches));

// A live research server never falls back to synthetic data: it asks for a key instead.
async function boot() {
  try {
    const response = await fetch('/live/status');
    const data = response.ok ? await response.json() : {};
    if (data.enabled) state.live = { available: true, traffic: Boolean(data.traffic), filings: Boolean(data.filings) };
  } catch {
    // Synthetic preview servers have no live routes.
  }
  if (!state.live.available) {
    refresh();
    return;
  }
  Object.assign(state, { demo: false, items: [] });
  globe.setTraffic(false);
  $('#mode').textContent = 'LIVE SERVER · CONNECT';
  $('#notice').textContent = 'This server streams live research feeds: real ADS-B air traffic and SEC Form 4 filings. Connect with your API key to view them. Nothing here is a prediction.';
  $('#connect-note').textContent = 'This server runs live research feeds. Connect with a customer or analyst API key to view air traffic and SEC filings. The key stays in memory only.';
  render();
  $('#settings').showModal();
}

setMotion(state.motion);
tickClocks();
setInterval(tickClocks, 1000);
boot();
