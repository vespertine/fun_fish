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

function render(s) {
  state = s;
  const humanTurn = !s.game_over && !s.engine_to_move;
  cg.set({
    fen: s.fen,
    orientation,
    turnColor: s.turn,
    lastMove: s.last_move || undefined,
    check: s.check,
    movable: {
      color: humanTurn ? s.human_color : undefined,
      dests: new Map(Object.entries(s.dests)),
    },
  });
  renderPlayers();
  $('#status').textContent = s.status;
  $('#status').classList.toggle('over', s.game_over);
  renderMoves(s.moves);
  renderDecision(s.decision);
  $('#undo').disabled = s.moves.length === 0;
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
  (aiColor === top ? $('.player.top') : $('.player.bottom')).appendChild(thinking);
}

function renderMoves(moves) {
  const ol = $('#moves');
  ol.innerHTML = '';
  for (let i = 0; i < moves.length; i += 2) {
    const li = document.createElement('li');
    li.innerHTML = `<span>${moves[i]}</span><span>${moves[i + 1] ?? ''}</span>`;
    ol.appendChild(li);
  }
  ol.scrollTop = ol.scrollHeight;
}

function renderDecision(decision) {
  const table = $('#candidates');
  const tbody = table.querySelector('tbody');
  tbody.innerHTML = '';
  if (!decision) {
    $('#explanation').textContent = state.moves.length
      ? 'À vous de jouer.'
      : "L'adversaire n'a pas encore joué.";
    table.hidden = true;
    cg.setAutoShapes([]);
    return;
  }
  $('#explanation').textContent = decision.explanation;
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
  const decision = state?.decision;
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
  for (const id of ['#new', '#undo']) $(id).disabled = on;
}

// ---------- actions ----------

async function newGame() {
  const s = await api('/api/new', { personality: $('#personality').value, color: $('#color').value });
  orientation = s.human_color;
  render(s);
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

function showPersonalityDescription() {
  const p = personalities.find((x) => x.key === $('#personality').value);
  $('#personality-desc').textContent = p ? p.description : '';
}

// ---------- démarrage ----------

async function init() {
  const info = await api('/api/info');
  personalities = info.personalities;
  $('#engine').textContent = info.engine;
  const select = $('#personality');
  for (const p of personalities) select.add(new Option(p.name, p.key));

  const s = await api('/api/state');
  select.value = s.personality.key;
  showPersonalityDescription();
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

  select.addEventListener('change', showPersonalityDescription);
  $('#new').addEventListener('click', newGame);
  $('#undo').addEventListener('click', undo);
  $('#flip').addEventListener('click', flip);
  $('#pgn').addEventListener('click', copyPgn);
  $('#arrows').addEventListener('change', drawArrows);

  await engineMoveIfNeeded();
}

init().catch((err) => toast(`Erreur au démarrage : ${err.message}`));
