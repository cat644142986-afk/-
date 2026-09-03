import { BotEngine } from '../vendor/bloub/engine.js';
import { EXPRESSION_BY_ID } from '../vendor/bloub/expressions.js';
import { SHAPE_BY_ID } from '../vendor/bloub/skins.js';
import { STATE_BY_ID } from '../vendor/bloub/states.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const BOT_SCALE = 100;

const STATIC_SAMPLE_OFFSET = Object.freeze({
  idle: 0.8,
  thinking: 0.8,
  wink: 0.55,
  wide: 0.5,
  alert: 0.55,
  sleep: 0.35,
  orbit: 0.8,
  burst: 0.32,
  exclaim: 0.55,
});

const SERIOUS_STATES = new Set(['error', 'attention', 'active', 'paused', 'queued', 'complete']);
const ARC_COLORS = Object.freeze([
  'var(--presence-arc-a)',
  'var(--presence-arc-b)',
  'var(--presence-arc-c)',
]);

export function taskPresencePresentation(state, options = {}) {
  const semanticState = String(state || 'idle');
  const age = Math.max(0, Number(options.age) || 0);
  const available = options.available !== false;
  const interaction = String(options.interaction || 'rest');

  if (!available) return { pose: 'sleep', expression: 'somnolent' };
  if (semanticState === 'error') {
    return age < 0.82
      ? { pose: 'exclaim', expression: 'effraye' }
      : { pose: 'idle', expression: 'triste' };
  }
  if (semanticState === 'attention') {
    return age < 0.78
      ? { pose: 'alert', expression: 'surpris' }
      : { pose: 'idle', expression: 'mefiant' };
  }
  if (semanticState === 'complete') {
    if (age < 0.46) return { pose: 'burst', expression: 'excite' };
    if (age < 1.18) return { pose: 'wink', expression: 'heureux' };
    return { pose: 'idle', expression: 'heureux' };
  }
  if (semanticState === 'active') return { pose: 'thinking', expression: 'attentif' };
  if (semanticState === 'paused') return { pose: 'sleep', expression: 'somnolent' };
  if (semanticState === 'queued') return { pose: 'orbit', expression: 'curieux' };

  if (interaction === 'drag') return { pose: 'wide', expression: 'excite' };
  if (interaction === 'pressed') return { pose: 'wide', expression: 'surpris' };
  if (interaction === 'hover' || interaction === 'focus') {
    return { pose: 'idle', expression: 'curieux' };
  }
  return { pose: 'idle', expression: 'attentif' };
}

function setAttribute(node, name, value) {
  if (!node) return;
  if (value === null || value === undefined || value === false) node.removeAttribute(name);
  else node.setAttribute(name, String(value));
}

function svgElement(name) {
  return document.createElementNS(SVG_NS, name);
}

function setVisible(node, visible) {
  if (node) node.style.display = visible ? '' : 'none';
}

function fileDrag(event) {
  return Array.from(event?.dataTransfer?.types || []).includes('Files');
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function noopController() {
  return Object.freeze({
    destroy() {},
    setMotion() {},
    setTaskState() {},
  });
}

export function createTaskPresenceController(control) {
  if (!control) return noopController();

  const root = document.documentElement;
  const scene = control.querySelector('[data-presence-scene]');
  const defs = scene?.querySelector('[data-presence-defs]');
  const maskBody = scene?.querySelector('[data-presence-mask-body]');
  const maskEyes = Array.from(scene?.querySelectorAll('[data-presence-eye]') || []);
  const maskNotch = scene?.querySelector('[data-presence-notch]');
  const paperBody = scene?.querySelector('[data-presence-paper]');
  const bodyGroup = scene?.querySelector('[data-presence-body-group]');
  const arcsBackRoot = scene?.querySelector('[data-presence-arcs-back]');
  const arcsFrontRoot = scene?.querySelector('[data-presence-arcs-front]');
  const dotsBackRoot = scene?.querySelector('[data-presence-dots-back]');
  const dotsFrontRoot = scene?.querySelector('[data-presence-dots-front]');
  const notification = scene?.querySelector('[data-presence-notification]');

  if (
    !scene || !defs || !maskBody || maskEyes.length !== 2 || !paperBody || !bodyGroup
    || !arcsBackRoot || !arcsFrontRoot || !dotsBackRoot || !dotsFrontRoot || !notification
  ) {
    return noopController();
  }

  const initialExpression = EXPRESSION_BY_ID.get('attentif') || null;
  const shape = SHAPE_BY_ID.get('galet')?.radii || null;
  const engine = new BotEngine(BOT_SCALE, 'idle', shape, initialExpression);
  const gradients = [];
  const backArcs = [];
  const frontArcs = [];
  const backDots = [];
  const frontDots = [];

  let clock = 0;
  let lastFrameMs = 0;
  let frameRequest = 0;
  let semanticState = control.dataset.taskState || 'idle';
  let available = true;
  let semanticSince = 0;
  let currentPose = 'idle';
  let currentExpression = 'attentif';
  let pointer = null;
  let aiming = false;
  let hovering = false;
  let focused = false;
  let pressed = false;
  let dragDepth = 0;
  let destroyed = false;

  function reducedMotion() {
    return root.dataset.motion === 'reduced'
      || control.dataset.taskMotion === 'paused'
      || document.visibilityState === 'hidden';
  }

  function interaction() {
    if (!SERIOUS_STATES.has(semanticState) && dragDepth > 0) return 'drag';
    if (!SERIOUS_STATES.has(semanticState) && pressed) return 'pressed';
    if (!SERIOUS_STATES.has(semanticState) && hovering) return 'hover';
    if (!SERIOUS_STATES.has(semanticState) && focused) return 'focus';
    return 'rest';
  }

  function ensureArc(index) {
    if (gradients[index]) {
      return {
        gradient: gradients[index],
        back: backArcs[index],
        front: frontArcs[index],
      };
    }
    const gradient = svgElement('linearGradient');
    const back = svgElement('path');
    const front = svgElement('path');
    const id = `task-presence-gradient-${index}`;
    gradient.id = id;
    gradient.setAttribute('gradientUnits', 'userSpaceOnUse');
    back.setAttribute('stroke', `url(#${id})`);
    front.setAttribute('stroke', `url(#${id})`);
    defs.appendChild(gradient);
    arcsBackRoot.appendChild(back);
    arcsFrontRoot.appendChild(front);
    gradients[index] = gradient;
    backArcs[index] = back;
    frontArcs[index] = front;
    return { gradient, back, front };
  }

  function renderGradient(gradient, spec) {
    setAttribute(gradient, 'x1', spec.x1);
    setAttribute(gradient, 'y1', spec.y1);
    setAttribute(gradient, 'x2', spec.x2);
    setAttribute(gradient, 'y2', spec.y2);
    const colors = Array.isArray(spec.stops) ? spec.stops : [];
    while (gradient.children.length < colors.length) gradient.appendChild(svgElement('stop'));
    Array.from(gradient.children).forEach((stop, index) => {
      const visible = index < colors.length;
      setVisible(stop, visible);
      if (!visible) return;
      setAttribute(stop, 'offset', colors.length > 1 ? index / (colors.length - 1) : 0);
      setAttribute(stop, 'stop-color', ARC_COLORS[index % ARC_COLORS.length]);
    });
  }

  function renderArcs(arcs) {
    arcs.forEach((arc, index) => {
      const nodes = ensureArc(index);
      setVisible(nodes.gradient, true);
      setVisible(nodes.back, true);
      setVisible(nodes.front, true);
      renderGradient(nodes.gradient, arc.grad);
      setAttribute(nodes.back, 'd', arc.back);
      setAttribute(nodes.front, 'd', arc.front);
      setAttribute(nodes.back, 'stroke-width', arc.width);
      setAttribute(nodes.front, 'stroke-width', arc.width);
      setAttribute(nodes.back, 'opacity', arc.opacity);
      setAttribute(nodes.front, 'opacity', arc.opacity);
    });
    for (let index = arcs.length; index < gradients.length; index += 1) {
      setVisible(gradients[index], false);
      setVisible(backArcs[index], false);
      setVisible(frontArcs[index], false);
    }
  }

  function ensureDot(cache, parent, index) {
    if (cache[index]) return cache[index];
    const group = svgElement('g');
    const circle = svgElement('circle');
    const path = svgElement('path');
    group.append(circle, path);
    parent.appendChild(group);
    cache[index] = { group, circle, path };
    return cache[index];
  }

  function renderDotList(parent, cache, dots) {
    dots.forEach((dot, index) => {
      const nodes = ensureDot(cache, parent, index);
      const usePath = Boolean(dot.d);
      setVisible(nodes.group, true);
      setVisible(nodes.circle, !usePath);
      setVisible(nodes.path, usePath);
      setAttribute(nodes.group, 'fill', dot.color || 'currentColor');
      setAttribute(nodes.group, 'opacity', dot.opacity);
      if (usePath) {
        setAttribute(nodes.path, 'd', dot.d);
        setAttribute(nodes.path, 'transform', `translate(${dot.x} ${dot.y}) rotate(${dot.rot || 0}) scale(${BOT_SCALE})`);
      } else {
        setAttribute(nodes.circle, 'cx', dot.x);
        setAttribute(nodes.circle, 'cy', dot.y);
        setAttribute(nodes.circle, 'r', dot.r);
      }
    });
    for (let index = dots.length; index < cache.length; index += 1) {
      setVisible(cache[index].group, false);
    }
  }

  function renderFrame(frame) {
    setAttribute(maskBody, 'd', frame.bodyPath);
    setAttribute(paperBody, 'd', frame.bodyPath);
    setAttribute(bodyGroup, 'opacity', frame.bodyAlpha);
    for (let index = 0; index < maskEyes.length; index += 1) {
      const eye = frame.eyes[index];
      setVisible(maskEyes[index], Boolean(eye));
      if (!eye) continue;
      setAttribute(maskEyes[index], 'd', eye.d);
      setAttribute(maskEyes[index], 'transform', eye.matrix);
      setAttribute(maskEyes[index], 'opacity', eye.alpha);
    }
    setVisible(maskNotch, Boolean(frame.notch));
    if (frame.notch) {
      setAttribute(maskNotch, 'cx', frame.notch.x);
      setAttribute(maskNotch, 'cy', frame.notch.y);
      setAttribute(maskNotch, 'r', frame.notch.r);
    }
    renderArcs(frame.arcs);
    renderDotList(dotsBackRoot, backDots, frame.dotsBehind ? frame.dots : []);
    renderDotList(dotsFrontRoot, frontDots, frame.dotsBehind ? [] : frame.dots);
    setVisible(notification, Boolean(frame.notif));
    if (frame.notif) {
      setAttribute(notification, 'cx', frame.notif.x);
      setAttribute(notification, 'cy', frame.notif.y);
      setAttribute(notification, 'r', frame.notif.r);
    }
  }

  function applyPresentation(presentation) {
    if (presentation.expression !== currentExpression) {
      currentExpression = presentation.expression;
      engine.setExpression(EXPRESSION_BY_ID.get(currentExpression) || initialExpression, clock);
    }
    if (presentation.pose !== currentPose) {
      currentPose = presentation.pose;
      engine.setState(currentPose, clock);
    }
    control.dataset.presencePose = currentPose;
    control.dataset.presenceExpression = currentExpression;
    control.dataset.presenceInteraction = interaction();
  }

  function updateGaze() {
    const stateDefinition = STATE_BY_ID.get(currentPose);
    if (!pointer || !stateDefinition?.baseFace) {
      if (aiming) engine.setLook(null, clock);
      aiming = false;
      return;
    }
    const box = scene.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const nx = clamp((pointer.x - (box.left + box.width / 2)) / Math.max(1, window.innerWidth / 2), -1, 1);
    const ny = clamp((pointer.y - (box.top + box.height / 2)) / Math.max(1, window.innerHeight / 2), -1, 1);
    engine.setLook({
      yaw: nx * 16,
      pitch: 9 - ny * 13,
      mix: 0.72,
      spin: 0,
      wander: 0.12,
    }, clock);
    aiming = true;
  }

  function renderCurrent(staticFrame = false) {
    const presentation = taskPresencePresentation(semanticState, {
      age: clock - semanticSince,
      available,
      interaction: interaction(),
    });
    applyPresentation(presentation);
    if (!staticFrame) updateGaze();
    const sampleTime = staticFrame
      ? clock + (STATIC_SAMPLE_OFFSET[currentPose] || 0.7)
      : clock;
    renderFrame(engine.sample(sampleTime));
  }

  function tick(frameMs) {
    frameRequest = 0;
    if (destroyed || reducedMotion()) {
      lastFrameMs = 0;
      renderCurrent(true);
      return;
    }
    const delta = lastFrameMs ? Math.min((frameMs - lastFrameMs) / 1000, 0.064) : 0;
    lastFrameMs = frameMs;
    clock += delta;
    renderCurrent(false);
    frameRequest = requestAnimationFrame(tick);
  }

  function schedule() {
    if (destroyed) return;
    if (reducedMotion()) {
      if (frameRequest) cancelAnimationFrame(frameRequest);
      frameRequest = 0;
      lastFrameMs = 0;
      renderCurrent(true);
      return;
    }
    if (!frameRequest) frameRequest = requestAnimationFrame(tick);
  }

  function setInteractionFlag(name, value) {
    if (name === 'hover') hovering = value;
    if (name === 'focus') focused = value;
    if (name === 'pressed') pressed = value;
    schedule();
  }

  function onPointerMove(event) {
    if (event.pointerType === 'touch') return;
    pointer = { x: event.clientX, y: event.clientY };
    schedule();
  }

  function onPointerLeaveDocument() {
    pointer = null;
    schedule();
  }

  function onDragEnter(event) {
    if (!fileDrag(event)) return;
    dragDepth += 1;
    schedule();
  }

  function onDragLeave(event) {
    if (!fileDrag(event)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    schedule();
  }

  function endDrag() {
    if (!dragDepth) return;
    dragDepth = 0;
    schedule();
  }

  const onEnter = () => setInteractionFlag('hover', true);
  const onLeave = () => setInteractionFlag('hover', false);
  const onFocus = () => setInteractionFlag('focus', true);
  const onBlur = () => setInteractionFlag('focus', false);
  const onDown = () => setInteractionFlag('pressed', true);
  const onUp = () => setInteractionFlag('pressed', false);
  const onCancel = () => setInteractionFlag('pressed', false);
  const onKeyDown = (event) => {
    if (event.key === 'Enter' || event.key === ' ') setInteractionFlag('pressed', true);
  };
  const onKeyUp = (event) => {
    if (event.key === 'Enter' || event.key === ' ') setInteractionFlag('pressed', false);
  };
  const onWindowBlur = () => {
    pointer = null;
    pressed = false;
    schedule();
  };
  const motionObserver = new MutationObserver(schedule);

  control.addEventListener('pointerenter', onEnter);
  control.addEventListener('pointerleave', onLeave);
  control.addEventListener('pointerdown', onDown);
  control.addEventListener('focus', onFocus);
  control.addEventListener('blur', onBlur);
  control.addEventListener('keydown', onKeyDown);
  control.addEventListener('keyup', onKeyUp);
  window.addEventListener('pointermove', onPointerMove, { passive: true });
  window.addEventListener('pointerup', onUp, { passive: true });
  window.addEventListener('pointercancel', onCancel, { passive: true });
  window.addEventListener('blur', onWindowBlur);
  document.addEventListener('pointerleave', onPointerLeaveDocument);
  document.addEventListener('dragenter', onDragEnter);
  document.addEventListener('dragleave', onDragLeave);
  document.addEventListener('drop', endDrag);
  document.addEventListener('dragend', endDrag);
  motionObserver.observe(root, { attributes: true, attributeFilter: ['data-motion'] });

  renderCurrent(true);
  schedule();

  return {
    setTaskState(nextState, options = {}) {
      const normalized = String(nextState || 'idle');
      const nextAvailable = options.available !== false;
      if (normalized !== semanticState || nextAvailable !== available) {
        semanticState = normalized;
        available = nextAvailable;
        semanticSince = clock;
      }
      control.dataset.taskState = semanticState;
      control.dataset.taskAvailable = available ? 'true' : 'false';
      schedule();
    },
    setMotion(mode) {
      control.dataset.taskMotion = mode === 'paused' ? 'paused' : 'running';
      schedule();
    },
    destroy() {
      destroyed = true;
      if (frameRequest) cancelAnimationFrame(frameRequest);
      motionObserver.disconnect();
      control.removeEventListener('pointerenter', onEnter);
      control.removeEventListener('pointerleave', onLeave);
      control.removeEventListener('pointerdown', onDown);
      control.removeEventListener('focus', onFocus);
      control.removeEventListener('blur', onBlur);
      control.removeEventListener('keydown', onKeyDown);
      control.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onCancel);
      window.removeEventListener('blur', onWindowBlur);
      document.removeEventListener('pointerleave', onPointerLeaveDocument);
      document.removeEventListener('dragenter', onDragEnter);
      document.removeEventListener('dragleave', onDragLeave);
      document.removeEventListener('drop', endDrag);
      document.removeEventListener('dragend', endDrag);
    },
  };
}
