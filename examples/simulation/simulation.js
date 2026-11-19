'use strict';
const $ = id => document.getElementById(id);
const board = $('interaction');
const buttons = [...board.querySelectorAll('[data-target]')];
let target = null, pending = false, closed = false;
const headMode = $('head-mode');
const preview = document.body.dataset.preview === 'true';
let emergencyStop = false;
let aimRequested = null, aimActive = false;
let marker;
if (preview) {
  document.title = 'Fovea · Control con la mirada';
  $('title').textContent = 'Control con la mirada';
  $('intro').textContent = 'Sigue los puntos con los ojos. El cursor y las acciones se quedan dentro del panel.';
  $('mode-control').hidden = true;
  $('aim-tools').hidden = false;
  $('aim-shortcut').addEventListener('click', () => { aimRequested = !aimActive; send(); });
  $('instructions').textContent = 'Sigue los nueve puntos. Al final, mira el centro y mueve la cabeza suavemente. Luego mira Reanudar.';
  document.querySelector('.foot').textContent = 'Para recuperar el control usa Reanudar. Después de Esc, recarga la página para habilitar el panel. Ctrl+C en la terminal detiene la captura.';
}

function renderPreview(s) {
  if (!marker) {
    $('board').classList.add('pose-board');
    for (const [id, x, y, w, h] of s.preview_layout) {
      const button = buttons.find(b => b.dataset.target === id);
      if (!button) continue;
      if (id === 'aim_test') button.hidden = false;
      for (const [name, val] of Object.entries({left:x, top:y, width:w, height:h})) {
        button.style[name] = `${val * 100}%`;
      }
      $('board').append(button);
    }
    $('board').querySelector('[data-target="confirm"]').classList.add('gaze-confirm');
    $('board').querySelector('.empty').hidden = true;
    $('keyboard').hidden = true;
    marker = document.createElement('div');
    marker.className = 'pose-marker';
    marker.setAttribute('aria-hidden', 'true');
    $('board').append(marker);
  }
  const calibration = s.preview_calibration;
  $('board').classList.toggle('calibrating-gaze', Boolean(calibration));
  marker.classList.toggle('calibration-dot', Boolean(calibration));
  marker.classList.toggle('one-eye-marker', !calibration && s.preview_eyes < 2);
  const point = calibration ? [calibration.x, calibration.y] : s.preview_monocular || s.pointer;
  aimActive = Boolean(calibration?.validation);
  $('aim-shortcut').textContent = aimActive ? 'Cancelar prueba' : 'Medir puntería';
  $('aim-shortcut').disabled = !s.preview_ready || emergencyStop || (Boolean(calibration) && !aimActive);
  const sample = s.preview_aim_result;
  $('aim-result').hidden = !sample;
  if (sample) {
    const mean = Math.round(sample.mean * 100);
    const p90 = Math.ceil(sample.p90 * 100);
    $('aim-result').textContent = `Error medio: ${mean} %. En 9 de cada 10 lecturas, error de hasta ${p90} %.`;
  }
  marker.hidden = !point;
  if (point) {
    marker.style.left = `${point[0] * 100}%`;
    marker.style.top = `${point[1] * 100}%`;
  }
}

function previewMessage(s) {
  if (s.preview_input !== 'gaze') return 'Reinicia Fovea en la terminal para cargar el modo mirada.';
  if (s.preview_error) return s.preview_error;
  if (emergencyStop) return 'Panel detenido. Recarga para habilitarlo.';
  if (s.preview_signal) return s.preview_signal;
  if (s.preview_calibration) {
    const {index, total, motion, validation} = s.preview_calibration;
    if (validation) return `Prueba ${index} de ${total}. Mira el punto amarillo hasta que cambie.`;
    if (motion) {
      return 'Mira el centro. Mueve la cabeza un poco a los lados, arriba y abajo, sin apartar los ojos del punto.';
    }
    return `Punto ${index} de ${total}. Mira el punto hasta que cambie.`;
  }
  if (s.candidate) return 'Seleccionada. Mira Confirmar para ejecutar.';
  return s.message;
}

function render(s) {
  if (preview) renderPreview(s);
  for (let i = 0; i < 8; i++) document.querySelector(`[data-target="kbd:slot${i}"] span`).textContent = s.keyboard_slots[i] || '—';
  for (let i = 0; i < 3; i++) document.querySelector(`[data-target="kbd:predict${i}"] span`).textContent = s.keyboard_predictions[i] || '—';
  $('modifiers').textContent = s.keyboard_modifiers.join(', ') || 'ninguno';
  $('shortcut').textContent = s.keyboard_shortcut || 'ninguno';
  $('state').textContent = s.armed ? (preview ? 'Activo · vista previa' : 'Activo · simulación') : 'En pausa';
  $('state').classList.toggle('active', s.armed);
  $('message').textContent = preview ? previewMessage(s) : s.message;
  if (preview) {
    const eyes = `${s.preview_eyes ?? 0}/2 ojos`;
    const fps = s.preview_fps == null ? '' : ` · ${Math.round(s.preview_fps)} FPS`;
    const cue = s.preview_calibration;
    const progress = cue ? ` · ${Math.floor(cue.progress * 100)} %` : '';
    const motion = !cue && s.preview_motion ? ' · compensación activa' : '';
    $('signal').textContent = eyes + fps + progress + motion;
  } else {
    $('signal').textContent = s.confidence ? `Señal ${Math.round(s.confidence * 100)} %` : 'Sin señal';
  }
  const selected = buttons.find(b => b.dataset.target === s.candidate);
  $('candidate').textContent = selected ? selected.textContent.trim() : 'ninguna';
  $('dragging').textContent = s.dragging ? 'activo' : 'liberado';
  $('clicks').textContent = s.clicks;
  $('scrollX').textContent = s.scroll_x;
  $('scrollY').textContent = s.scroll_y;
  $('text').textContent = s.text || '—';
  $('history').replaceChildren(...(s.history.length ? s.history : ['Ninguna.']).map(item => {
    const li = document.createElement('li'); li.textContent = item; return li;
  }));
  for (const button of buttons) {
    const active = button.dataset.target === s.target;
    button.classList.toggle('looking', active);
    if (preview && button.dataset.target === 'confirm') {
      button.classList.toggle('confirmation-ready', Boolean(s.candidate));
    }
    button.style.setProperty('--progress', active ? `${s.progress * 100}%` : '0%');
  }
}

async function send() {
  if (pending) return;
  pending = true;
  try {
    const frame = preview
      ? {visible: !emergencyStop && !document.hidden && document.hasFocus()}
      : headMode.checked ? {target, closed} : {target};
    if (preview && frame.visible && aimRequested !== null) frame.test = true;
    const response = await fetch('/api/frame', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(frame),
    });
    if (!response.ok) throw new Error('No se pudo leer la simulación.');
    if ('test' in frame && frame.test === aimRequested) aimRequested = null;
    render(await response.json());
  } catch (error) {
    $('state').textContent = 'Sin conexión';
    $('message').textContent = error.message;
  } finally { pending = false; }
}
board.addEventListener('pointermove', event => {
  const button = event.target.closest('[data-target]');
  target = button ? button.dataset.target : '';
});
function stop() { target = null; closed = false; send(); }
headMode.addEventListener('change', () => {
  $('instructions').textContent = headMode.checked
    ? 'Apunta con el puntero. Para confirmar, mantén Espacio de 0,65 a 1,5 s y suelta. Esc detiene la simulación.'
    : 'Apunta a una opción y después a Confirmar. Esc detiene la simulación.';
  stop();
});
document.addEventListener('keydown', event => {
  if (event.code === 'Escape') { emergencyStop = preview; stop(); return; }
  if (preview) return;
  if (event.code === 'Space' && headMode.checked && target !== null) {
    event.preventDefault();
    closed = true;
    send();
  }
});
document.addEventListener('keyup', event => {
  if (event.code === 'Space' && closed) {
    event.preventDefault();
    closed = false;
    send();
  }
});
board.addEventListener('pointerleave', stop);
board.addEventListener('focusin', event => { target = event.target.dataset.target || ''; });
board.addEventListener('focusout', event => { if (!board.contains(event.relatedTarget)) stop(); });
window.addEventListener('blur', stop);
document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
send();
setInterval(send, 80);
