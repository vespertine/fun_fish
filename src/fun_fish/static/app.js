import { Chessground } from './vendor/chessground/chessground.min.js';

const $ = (sel) => document.querySelector(sel);

let cg;
let state = null;
let personalities = [];
let orientation = 'white';

async function api(path, body) {
  const init = body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, init);
  const data = await res.json();
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : res.statusText);
  return data;
}

function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 4000);
}

function fmtScore(score, mate) {
  if (mate !== null && mate !== undefined) return mate > 0 ? `#${mate}` : `#-${-mate}`;
  const v = score / 100;
  return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(2);
}

// ---------- rendu ----------

// Demi-coup affiché : null = position en cours ; sinon on revoit l'historique (0 = position de départ).
let viewPly = null;
let shownDecision = null;

function render(s) {
  state = s;
  viewPly = null;
  show();
  $('#undo').disabled = s.moves.length === 0;
  $('#retry').disabled = !s.can_retry;
}

function show() {
  const history = state.history;
  const ply = viewPly ?? history.length;
  const live = ply === history.length;
  const entry = ply > 0 ? history[ply - 1] : null;

  if (live) {
    const humanTurn = !state.game_over && !state.engine_to_move;
    cg.set({
      fen: state.fen,
      orientation,
      turnColor: state.turn,
      lastMove: state.last_move || undefined,
      check: state.check,
      movable: {
        color: humanTurn ? state.human_color : undefined,
        dests: new Map(Object.entries(state.dests)),
      },
    });
  } else {
    const fen = entry ? entry.fen : state.start_fen;
    cg.set({
      fen,
      orientation,
      turnColor: fen.split(' ')[1] === 'w' ? 'white' : 'black',
      lastMove: entry ? entry.last_move : undefined,
      check: entry ? entry.check : false,
      movable: { color: undefined, dests: new Map() },
    });
  }

  renderPlayers();
  const status = $('#status');
  status.textContent = live
    ? state.status
    : `Historique : demi-coup ${ply} sur ${history.length}. → ou Fin pour revenir à la partie.`;
  status.classList.toggle('over', live && state.game_over);
  status.classList.toggle('browsing', !live);
  renderMoves(history, live ? null : ply);
  renderDecision(entry, live);
}

function renderPlayers() {
  const ai = state.personality.name;
  const aiColor = state.human_color === 'white' ? 'black' : 'white';
  const top = orientation === 'white' ? 'black' : 'white';
  const label = (color) => (color === aiColor ? ai : 'Vous');
  $('#top-name').textContent = label(top);
  $('#bottom-name').textContent = label(top === 'white' ? 'black' : 'white');
  $('#top-dot').className = `dot ${top}`;
  $('#bottom-dot').className = `dot ${top === 'white' ? 'black' : 'white'}`;
  // l'indicateur « réfléchit » suit le joueur IA, en haut ou en bas
  const thinking = $('#thinking');
  const row = aiColor === top ? $('.player.top') : $('.player.bottom');
  row.insertBefore(thinking, row.querySelector('.corner'));
}

function renderMoves(history, activePly) {
  const ol = $('#moves');
  ol.innerHTML = '';
  let active = null;
  for (let i = 0; i < history.length; i += 2) {
    const li = document.createElement('li');
    for (const ply of [i + 1, i + 2]) {
      const span = document.createElement('span');
      const entry = history[ply - 1];
      if (entry) {
        span.textContent = entry.san;
        span.className = `move ${entry.by}`;
        span.dataset.ply = ply;
        span.title = entry.by === 'ai' ? "Revoir ce coup et le raisonnement de l'IA" : 'Revoir cette position';
        if (ply === activePly) { span.classList.add('active'); active = span; }
        span.addEventListener('click', () => goTo(ply));
      }
      li.appendChild(span);
    }
    ol.appendChild(li);
  }
  if (active) active.scrollIntoView({ block: 'nearest' });
  else ol.scrollTop = ol.scrollHeight;
}

function showNoDecision(text) {
  shownDecision = null;
  $('#explanation').textContent = text;
  $('#candidates').hidden = true;
  $('#reality').hidden = true;
  cg.setAutoShapes([]);
}

function renderDecision(entry, live) {
  const table = $('#candidates');
  const tbody = table.querySelector('tbody');
  tbody.innerHTML = '';
  const decision = entry?.decision ?? null;
  if (!decision) {
    if (!entry) showNoDecision(live ? "L'adversaire n'a pas encore joué." : 'Position de départ.');
    else if (live) showNoDecision(state.engine_to_move ? "L'adversaire réfléchit…" : 'À vous de jouer.');
    else showNoDecision(entry.by === 'human' ? `Votre coup : ${entry.san}.` : `${entry.san} (raisonnement non conservé).`);
    return;
  }
  shownDecision = decision;
  $('#explanation').textContent = decision.explanation;
  const reality = $('#reality');
  reality.hidden = !decision.reality;
  reality.textContent = decision.reality || '';
  reality.classList.toggle('mistake', /^(Erreur|Imprécision)/.test(decision.reality || ''));
  table.hidden = false;
  for (const c of decision.candidates) {
    const tr = document.createElement('tr');
    if (c.chosen) tr.className = 'chosen';
    if (c.probability === null) tr.classList.add('discarded');
    const proba = c.probability === null ? '–' : `${Math.round(c.probability * 100)} %`;
    tr.innerHTML = `<td>${c.san}</td><td class="num">${fmtScore(c.score, c.mate)}</td>`
      + `<td class="num">${proba}</td><td class="note">${c.note}</td>`;
    tbody.appendChild(tr);
  }
  drawArrows();
}

function drawArrows() {
  const decision = shownDecision;
  if (!decision || !$('#arrows').checked) {
    cg.setAutoShapes([]);
    return;
  }
  const shapes = decision.candidates
    .filter((c) => !c.chosen && c.probability !== null && c.probability >= 0.05)
    .sort((a, b) => b.probability - a.probability)
    .slice(0, 3)
    .map((c) => ({ orig: c.uci.slice(0, 2), dest: c.uci.slice(2, 4), brush: 'paleBlue' }));
  shapes.push({ orig: decision.uci.slice(0, 2), dest: decision.uci.slice(2, 4), brush: 'green' });
  cg.setAutoShapes(shapes);
}

// ---------- coups ----------

function askPromotion(color) {
  return new Promise((resolve) => {
    const overlay = $('#promotion');
    const box = overlay.querySelector('.promotion-choices');
    const glyphs = color === 'white'
      ? { q: '♕', r: '♖', b: '♗', n: '♘' }
      : { q: '♛', r: '♜', b: '♝', n: '♞' };
    box.innerHTML = '';
    const close = (value) => { overlay.hidden = true; overlay.onclick = null; resolve(value); };
    for (const [letter, glyph] of Object.entries(glyphs)) {
      const btn = document.createElement('button');
      btn.textContent = glyph;
      btn.onclick = (e) => { e.stopPropagation(); close(letter); };
      box.appendChild(btn);
    }
    overlay.onclick = () => close(null);
    overlay.hidden = false;
  });
}

async function onUserMove(orig, dest) {
  let uci = orig + dest;
  const piece = cg.state.pieces.get(dest);
  if (piece?.role === 'pawn' && (dest[1] === '8' || dest[1] === '1')) {
    const promo = await askPromotion(piece.color);
    if (!promo) { render(state); return; }
    uci += promo;
  }
  try {
    render(await api('/api/move', { uci }));
  } catch (err) {
    toast(err.message);
    render(await api('/api/state'));
    return;
  }
  await engineMoveIfNeeded();
}

async function engineMoveIfNeeded() {
  if (!state.engine_to_move) return;
  setThinking(true);
  try {
    render(await api('/api/engine-move', {}));
  } catch (err) {
    toast(err.message);
  } finally {
    setThinking(false);
  }
}

function setThinking(on) {
  $('#thinking').hidden = !on;
  for (const id of ['#new', '#undo', '#retry', '#apply-params']) $(id).disabled = on;
  if (!on && state) $('#retry').disabled = !state.can_retry;
}

// ---------- navigation dans l'historique ----------

function goTo(ply) {
  const n = state.history.length;
  const target = Math.max(0, Math.min(n, ply));
  viewPly = target === n ? null : target;
  show();
}

function onNavigationKey(e) {
  if (e.altKey || e.ctrlKey || e.metaKey) return;
  if (e.target.closest('input, select, textarea')) return;
  if (!$('#promotion').hidden) return;
  const current = viewPly ?? state.history.length;
  const moves = { ArrowLeft: current - 1, ArrowRight: current + 1, Home: 0, End: state.history.length };
  if (!(e.key in moves)) return;
  e.preventDefault();
  goTo(moves[e.key]);
}

// ---------- actions ----------

async function newGame() {
  const key = $('#personality').value;
  const params = readParams();
  saveParams(key, params);
  try {
    const s = await api('/api/new', { personality: key, color: $('#color').value, params });
    orientation = s.human_color;
    render(s);
  } catch (err) {
    toast(err.message);
    return;
  }
  await engineMoveIfNeeded();
}

async function applyParams() {
  const key = $('#personality').value;
  const params = readParams();
  try {
    render(await api('/api/personality', { personality: key, params }));
  } catch (err) {
    toast(err.message);
    return;
  }
  saveParams(key, params);
  toast(`Réglages appliqués : ${state.personality.name}.`);
  await engineMoveIfNeeded();
}

async function retry() {
  try {
    render(await api('/api/retry', {}));
  } catch (err) {
    toast(err.message);
    return;
  }
  await engineMoveIfNeeded();
}

async function undo() {
  render(await api('/api/undo', {}));
}

function flip() {
  orientation = orientation === 'white' ? 'black' : 'white';
  cg.set({ orientation });
  renderPlayers();
}

async function copyPgn() {
  try {
    await navigator.clipboard.writeText(state.pgn);
    toast('PGN copié dans le presse-papiers.');
  } catch {
    toast("Impossible d'accéder au presse-papiers.");
  }
}

async function debugExport() {
  try {
    const response = await fetch('/api/debug-export');
    if (!response.ok) throw new Error(response.statusText);
    const data = await response.json();
    const stamp = data.exported_at.replace(/[:T]/g, '-');
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `fun_fish-debug-${stamp}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
    toast('Export de debug téléchargé (fun-fish replay FICHIER pour le rejouer).');
  } catch {
    toast("Impossible d'exporter la partie.");
  }
}

function showPersonalityDescription() {
  const p = personalities.find((x) => x.key === $('#personality').value);
  $('#personality-desc').textContent = p ? p.description : '';
}

// ---------- réglages ----------

const storageKey = (key) => `fun_fish.params.${key}`;

function loadParams(key) {
  try {
    return JSON.parse(localStorage.getItem(storageKey(key)) || 'null');
  } catch {
    return null;
  }
}

function saveParams(key, params) {
  try { localStorage.setItem(storageKey(key), JSON.stringify(params)); } catch { /* stockage indisponible */ }
}

function clearParams(key) {
  try { localStorage.removeItem(storageKey(key)); } catch { /* stockage indisponible */ }
}

function renderParams(key, values) {
  const p = personalities.find((x) => x.key === key);
  const box = $('#params');
  box.innerHTML = '';
  for (const spec of p?.params ?? []) {
    const row = document.createElement('label');
    row.className = 'param';
    row.title = spec.help;
    const value = values?.[spec.name] ?? spec.default;
    const changed = value !== spec.default;
    row.innerHTML = `<span class="param-name${changed ? ' changed' : ''}">${spec.name}</span>`
      + `<input type="number" step="${spec.type === 'int' ? 1 : 'any'}" data-name="${spec.name}"`
      + ` data-default="${spec.default}" value="${value}">`
      + `<span class="param-help">${spec.help}</span>`;
    row.querySelector('input').addEventListener('input', (e) => {
      row.querySelector('.param-name').classList.toggle('changed', Number(e.target.value) !== spec.default);
    });
    box.appendChild(row);
  }
  $('#settings').hidden = !(p?.params?.length);
}

function readParams() {
  const params = {};
  for (const input of document.querySelectorAll('#params input')) {
    if (input.value !== '') params[input.dataset.name] = Number(input.value);
  }
  return params;
}

function onPersonalityChange() {
  const key = $('#personality').value;
  showPersonalityDescription();
  renderParams(key, loadParams(key));
}

function resetParams() {
  const key = $('#personality').value;
  clearParams(key);
  renderParams(key, null);
}

// ---------- démarrage ----------

async function init() {
  const info = await api('/api/info');
  personalities = info.personalities;
  $('#engine').textContent = info.engine;
  if (info.mode === 'play') {
    document.body.classList.add('play');
    $('#arrows').checked = false; // pas de flèches : elles montreraient ses candidats
  }
  const select = $('#personality');
  for (const p of personalities) select.add(new Option(p.name, p.key));
  $('#personality-choice').hidden = personalities.length < 2; // un seul adversaire : rien à choisir

  const s = await api('/api/state');
  select.value = s.personality.key;
  showPersonalityDescription();
  renderParams(s.personality.key, s.personality.params);
  orientation = s.human_color;

  cg = Chessground($('#board'), {
    fen: s.fen,
    orientation,
    coordinates: true,
    animation: { enabled: true, duration: 180 },
    highlight: { lastMove: true, check: true },
    movable: { free: false, showDests: true, events: { after: onUserMove } },
    premovable: { enabled: false },
    drawable: { enabled: true },
  });
  render(s);

  select.addEventListener('change', onPersonalityChange);
  $('#apply-params').addEventListener('click', applyParams);
  $('#reset-params').addEventListener('click', resetParams);
  $('#retry').addEventListener('click', retry);
  $('#new').addEventListener('click', newGame);
  $('#undo').addEventListener('click', undo);
  $('#flip').addEventListener('click', flip);
  $('#pgn').addEventListener('click', copyPgn);
  $('#debug-export').addEventListener('click', debugExport);
  $('#arrows').addEventListener('change', drawArrows);
  document.addEventListener('keydown', onNavigationKey);

  await engineMoveIfNeeded();
}

init().catch((err) => toast(`Erreur au démarrage : ${err.message}`));
