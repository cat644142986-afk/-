var __defProp = Object.defineProperty;
var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, { enumerable: true, configurable: true, writable: true, value }) : obj[key] = value;
var __publicField = (obj, key, value) => __defNormalProp(obj, typeof key !== "symbol" ? key + "" : key, value);
import { arcRender } from "./decor.js";
import { blendExpression } from "./expressions.js";
import { decalageDesYeux } from "./eyefit.js";
import { blinkScale, eyePoses, liveliness } from "./face.js";
import { clamp, easings, lerp, r2 } from "./math.js";
import {
  blend,
  capsulePath,
  closedPath,
  radiusAtAngle,
  toPoints
} from "./shape.js";
import { STATE_BY_ID } from "./states.js";
const NO_LOOK = { yaw: 0, pitch: 0, mix: 0, spin: 0, wander: 1 };
const lerpLook = (a, b, t) => ({
  yaw: lerp(a.yaw, b.yaw, t),
  pitch: lerp(a.pitch, b.pitch, t),
  mix: lerp(a.mix, b.mix, t),
  spin: lerp(a.spin, b.spin, t),
  wander: lerp(a.wander, b.wander, t)
});
const lerpEye = (a, b, t) => ({
  w: lerp(a.w, b.w, t),
  h: lerp(a.h, b.h, t),
  open: lerp(a.open, b.open, t),
  tilt: lerp(a.tilt ?? 0, b.tilt ?? 0, t)
});
function blendPose(a, b, t) {
  const out = 1 - t;
  return {
    sil: blend(a.sil, b.sil, t),
    offX: lerp(a.offX, b.offX, t),
    offY: lerp(a.offY, b.offY, t),
    gaze: {
      yaw: lerp(a.gaze.yaw, b.gaze.yaw, t),
      pitch: lerp(a.gaze.pitch, b.gaze.pitch, t),
      roll: lerp(a.gaze.roll, b.gaze.roll, t)
    },
    split: lerp(a.split, b.split, t),
    eyes: [lerpEye(a.eyes[0], b.eyes[0], t), lerpEye(a.eyes[1], b.eyes[1], t)],
    eyeAlpha: lerp(a.eyeAlpha, b.eyeAlpha, t),
    bodyAlpha: lerp(a.bodyAlpha, b.bodyAlpha, t),
    dots: [
      ...a.dots.map((d) => ({ ...d, opacity: d.opacity * out })),
      ...b.dots.map((d) => ({ ...d, opacity: d.opacity * t }))
    ],
    arcs: [
      ...a.arcs.map((r) => ({ ...r, id: `a${r.id}`, opacity: r.opacity * out })),
      ...b.arcs.map((r) => ({ ...r, id: `b${r.id}`, opacity: r.opacity * t }))
    ],
    // la pastille appartient a un seul des deux etats, elle ne se melange pas
    notif: t < 0.5 ? a.notif : b.notif,
    dotsBehind: t < 0.5 ? a.dotsBehind : b.dotsBehind
  };
}
const _BotEngine = class _BotEngine {
  constructor(scale = 100, initial = "idle", shape = null, expression = null) {
    /** rayon de la boule au repos, en unites de viewBox */
    __publicField(this, "scale");
    __publicField(this, "cur");
    __publicField(this, "prev", null);
    /**
     * Pose de depart FIGEE, posee seulement quand un changement d'etat arrive alors qu'un
     * fondu est deja en cours. Cf. `setState`.
     */
    __publicField(this, "departFige", null);
    __publicField(this, "tCur", 0);
    __publicField(this, "tPrev", 0);
    __publicField(this, "blinkAt", -10);
    __publicField(this, "pts", []);
    __publicField(this, "shape", null);
    __publicField(this, "shapePrev", null);
    __publicField(this, "shapeAt", -10);
    __publicField(this, "expr", null);
    __publicField(this, "exprPrev", null);
    __publicField(this, "exprAt", -10);
    __publicField(this, "look", NO_LOOK);
    __publicField(this, "lookPrev", NO_LOOK);
    __publicField(this, "lookAt", -10);
    /** duree de rattrapage en cours ; voir `LOOK_MORPH`, sa valeur par defaut */
    __publicField(this, "lookMorph", 0.24);
    this.scale = scale;
    this.cur = initial;
    this.shape = shape;
    this.expr = expression;
  }
  /**
   * Expression de repos choisie dans le personnalisateur. Comme la forme, elle
   * glisse vers la nouvelle valeur au lieu de sauter.
   */
  setExpression(expression, now = 0) {
    if (expression === this.expr) return;
    this.exprPrev = this.expr;
    this.expr = expression;
    this.exprAt = now;
  }
  /** Expression effective a l'instant `now`, morph en cours compris. */
  exprAtTime(now) {
    const to = this.expr;
    const from = this.exprPrev;
    if (!to || !from) return to;
    const k = (now - this.exprAt) / _BotEngine.SHAPE_MORPH;
    if (k >= 1) return to;
    return blendExpression(from, to, easings.easeOutQuint(clamp(k)));
  }
  /**
   * Forme choisie dans le personnalisateur. Elle ne remplace le corps que sur
   * les etats au repos (`baseBody`) : sur les autres, la silhouette EST
   * l'animation et ne doit pas etre ecrasee.
   *
   * Le changement se fait en morph, pas d'un coup : comme toutes les formes sont
   * echantillonnees aux memes angles, il suffit d'interpoler les rayons.
   */
  setShape(radii, now = 0) {
    if (radii === this.shape) return;
    this.shapePrev = this.shape;
    this.shape = radii;
    this.shapeAt = now;
  }
  /**
   * Forme effective a l'instant `now`, morph en cours compris.
   *
   * Ne remet PAS `shapePrev` a null en fin de morph : `sample` doit rester une
   * fonction pure du temps, donc relire une date passee doit redonner l'image
   * intermediaire. On garde juste une reference de plus.
   */
  shapeAtTime(now) {
    const to = this.shape;
    const from = this.shapePrev;
    if (!to || !from) return to;
    const k = (now - this.shapeAt) / _BotEngine.SHAPE_MORPH;
    if (k >= 1) return to;
    const t = easings.easeOutQuint(clamp(k));
    return to.map((r, i) => lerp(from[i] ?? r, r, t));
  }
  /**
   * Nouvelle cible de regard, `null` pour revenir a celui de l'etat.
   *
   * Elle repart de la valeur COURANTE, et non de la cible precedente comme
   * `setShape` : cette methode est appelee a chaque mouvement de pointeur, et
   * repartir de l'ancienne cible ferait reculer le regard d'un cran avant
   * chaque rattrapage — le suivi tremblerait au lieu de glisser.
   *
   * Meme contrat que `setShape` par ailleurs : l'etat externe entre par un
   * setter horodate, jamais par une variable lue pendant `sample`, sinon le
   * moteur cesse d'etre une fonction pure du temps.
   */
  setLook(look, now, morph = _BotEngine.LOOK_MORPH) {
    if (look && !Number.isFinite(look.yaw + look.pitch + look.mix + look.spin + look.wander)) {
      return;
    }
    this.lookPrev = this.lookAtTime(now);
    this.look = look ?? NO_LOOK;
    this.lookAt = now;
    this.lookMorph = morph;
  }
  /** Regard effectif a l'instant `now`, rattrapage en cours compris. */
  lookAtTime(now) {
    const k = (now - this.lookAt) / this.lookMorph;
    if (k >= 1) return this.look;
    return lerpLook(this.lookPrev, this.look, easings.easeOutQuint(clamp(k)));
  }
  posed(def, t, shape, expr) {
    let pose = def.pose(t);
    if (def.baseBody && shape) {
      pose = { ...pose, sil: { ...pose.sil, radii: shape } };
    }
    if (def.baseFace && expr) {
      pose = { ...pose, gaze: expr.gaze, split: expr.split, eyes: expr.eyes };
    }
    return pose;
  }
  /**
   * Decalage des yeux a l'instant `now` pour un etat donne, en unites de rayon de boule.
   *
   * Il est LU dans une table et interpole, jamais recalcule : `eyefit.ts` explique
   * pourquoi cette distinction est tout le correctif. Ici il ne reste qu'a l'interpoler
   * sur l'axe de la forme, avec exactement la courbe et la duree du morph de silhouette
   * — c'est la meme cause, donc ce doit etre le meme mouvement.
   *
   * On interroge la table sur les BORNES du morph (`shapePrev` et `shape`) et non sur le
   * profil que rend `shapeAtTime` : celui-la est un tableau neuf alloue a chaque image,
   * donc sans identite, et il n'existe dans aucune table.
   */
  decalageAtTime(now, state) {
    const surAxe = (debut, duree, a, b) => {
      if (a === b) return b;
      const k = (now - debut) / duree;
      if (k >= 1) return b;
      const t = easings.easeOutQuint(clamp(k));
      return { x: lerp(a.x, b.x, t), y: lerp(a.y, b.y, t) };
    };
    const parForme = (radii) => surAxe(
      this.exprAt,
      _BotEngine.SHAPE_MORPH,
      decalageDesYeux(radii, state, this.exprPrev?.id ?? null),
      decalageDesYeux(radii, state, this.expr?.id ?? null)
    );
    return surAxe(
      this.shapeAt,
      _BotEngine.SHAPE_MORPH,
      parForme(this.shapePrev),
      parForme(this.shape)
    );
  }
  get state() {
    return this.cur;
  }
  /**
   * Repart sur `id` SANS etat precedent, comme un moteur neuf pose sur cet etat.
   *
   * C'est ce que veut dire « rembobiner » pour ce moteur. `setState` seul ne peut pas le
   * faire : il garde l'etat quitte pour le fondre, ce qui est exactement son role en
   * lecture, et exactement ce qu'il ne faut pas quand on revient au debut d'une sequence.
   * Rejouer l'image 0 apres une passe complete melangeait le premier etat avec le DERNIER,
   * et l'export GIF s'ouvrait sur une boule sans yeux — la comete a un `eyeAlpha` nul.
   *
   * `sample` reste une fonction pure du temps : comme `setState`, ceci est un setter DATE,
   * appele par le pilote de la sequence, jamais pendant un echantillonnage.
   */
  reset(id, now) {
    this.cur = id;
    this.prev = null;
    this.departFige = null;
    this.tCur = now;
    this.tPrev = now;
    this.blinkAt = -10;
  }
  /**
   * Origine du fondu en cours : la pose figee s'il y en a une, sinon l'etat quitte evalue
   * a son propre temps ecoule — donc encore en train de s'animer, ce qui est voulu.
   */
  origine(now, shape, expr) {
    if (this.departFige) return this.departFige;
    if (!this.prev) return null;
    const prevDef = STATE_BY_ID.get(this.prev);
    return this.posed(prevDef, Math.max(0, now - this.tPrev), shape, expr);
  }
  /**
   * Pose composite a l'instant `now`, fondu en cours compris : exactement ce que `sample`
   * melange, avant la couche de vie au repos et de regard. Extraite pour que `setState`
   * puisse la figer.
   */
  poseComposee(now) {
    const def = STATE_BY_ID.get(this.cur);
    const shape = this.shapeAtTime(now);
    const expr = this.exprAtTime(now);
    const pose = this.posed(def, Math.max(0, now - this.tCur), shape, expr);
    const since = now - this.tCur;
    if (since >= def.morph) return pose;
    const origine = this.origine(now, shape, expr);
    if (!origine) return pose;
    return blendPose(origine, pose, easings.easeOutQuint(clamp(since / def.morph)));
  }
  /**
   * Changement d'etat, date.
   *
   * Le moteur ne garde qu'UNE case d'historique, donc un changement qui arrive pendant un
   * fondu remplacait l'origine du melange par la pose PLEINE de l'etat qu'on quittait, au
   * lieu de l'image partiellement melangee qui etait a l'ecran. Mesure sur
   * `idle -> wide -> idle` a 100 ms : 35,9 px de saut contre 8,0 px de mouvement normal.
   *
   * On fige donc la pose composite courante et on melange depuis elle. Continu par
   * construction, quel que soit le nombre de changements enchaines.
   *
   * Et SEULEMENT dans ce cas. Figer a chaque changement arreterait net l'animation de
   * l'etat qu'on quitte pendant tout le fondu — le « ! » d'`alert` se figerait en pleine
   * course — alors qu'il n'y a rien a corriger hors morph : l'etat quitte y est deja
   * exactement l'image affichee. La lecture d'un montage, dont les blocs durent au moins
   * le plus long fondu (`MIN_BLOCK`), ne fige donc jamais rien et rend au bit ce qu'elle
   * rendait.
   */
  setState(id, now) {
    if (id === this.cur) return;
    const morph = STATE_BY_ID.get(this.cur).morph;
    const enPleinFondu = this.prev !== null && now - this.tCur < morph;
    this.departFige = enPleinFondu ? this.poseComposee(now) : null;
    this.prev = this.cur;
    this.tPrev = this.tCur;
    this.cur = id;
    this.tCur = now;
    if (STATE_BY_ID.get(id)?.blinkIn) this.blinkAt = now;
  }
  sample(now) {
    const R = this.scale;
    const def = STATE_BY_ID.get(this.cur);
    const shape = this.shapeAtTime(now);
    const expr = this.exprAtTime(now);
    let pose = this.posed(def, Math.max(0, now - this.tCur), shape, expr);
    let decalage = this.decalageAtTime(now, this.cur);
    const since = now - this.tCur;
    const origine = since < def.morph ? this.origine(now, shape, expr) : null;
    if (origine) {
      const ratio = easings.easeOutQuint(clamp(since / def.morph));
      pose = blendPose(origine, pose, ratio);
      const quitte = this.prev;
      if (quitte) {
        const avant = this.decalageAtTime(now, quitte);
        decalage = {
          x: lerp(avant.x, decalage.x, ratio),
          y: lerp(avant.y, decalage.y, ratio)
        };
      }
    }
    const alive = pose.eyeAlpha > 0.01;
    const look = this.lookAtTime(now);
    const life = liveliness(now, { wander: alive ? look.wander : 0, blink: alive });
    const gaze = {
      // Les deux visees REMPLACENT celles de la pose au lieu de s'y ajouter (voir
      // `Look`), et le tour se retranche en chemin. La derive s'ajoute APRES le
      // melange, sinon la cible l'annulerait en meme temps que la pose — or elle
      // doit survivre a une tete tournee sans pointeur.
      yaw: lerp(pose.gaze.yaw, look.yaw, look.mix) + life.dYaw - look.spin,
      pitch: lerp(pose.gaze.pitch, look.pitch, look.mix) + life.dPitch,
      // le roulis, lui, ne suit rien : la tete du bot est penchee de -13deg dans
      // la video, et la faire rouler avec le curseur casse cette signature
      roll: pose.gaze.roll + life.dRoll
    };
    const forced = clamp((now - this.blinkAt) / 0.2);
    const forcedLid = forced < 1 ? Math.abs(forced * 2 - 1) : 1;
    const lid = Math.min(life.lid, forcedLid);
    const offX = pose.offX + life.driftX;
    const offY = pose.offY + life.driftY;
    const sil = {
      ...pose.sil,
      cx: pose.sil.cx + offX,
      cy: pose.sil.cy + offY,
      sy: pose.sil.sy * life.breath
    };
    const bodyPath = closedPath(toPoints(sil, R, this.pts));
    const bodyRadius = (x, y) => radiusAtAngle(pose.sil.radii, Math.atan2(y, x) - pose.sil.rot);
    const eyes = [];
    if (pose.eyeAlpha > 0.01) {
      const poses = eyePoses(gaze, R, pose.split);
      for (let i = 0; i < 2; i++) {
        const e = poses[i];
        if (e.depth <= 0.02) continue;
        const cfg = pose.eyes[i];
        const fit = bodyRadius(e.x, e.y);
        const phi = (cfg.tilt ?? 0) * Math.PI / 180;
        const cp = Math.cos(phi);
        const sp = Math.sin(phi);
        const ax = e.a * cp + e.c * sp;
        const ay = e.b * cp + e.d * sp;
        const cx2 = -e.a * sp + e.c * cp;
        const cy2 = -e.b * sp + e.d * cp;
        const k = blinkScale(Math.min(lid, cfg.open));
        eyes.push({
          d: capsulePath(cfg.w * R, cfg.h * R),
          matrix: `matrix(${r2(ax)},${r2(ay * k)},${r2(cx2)},${r2(cy2 * k)},${r2(e.x * fit + (offX + decalage.x) * R)},${r2(e.y * fit + (offY + decalage.y) * R)})`,
          alpha: pose.eyeAlpha * clamp(e.depth / 0.12)
        });
      }
    }
    const dots = pose.dots.filter((p) => p.opacity > 0.01 && p.r > 5e-4).map((p) => ({ ...p, x: (p.x + offX) * R, y: (p.y + offY) * R, r: p.r * R }));
    const nFit = pose.notif ? bodyRadius(pose.notif.x, pose.notif.y) : 1;
    const nx = pose.notif ? (pose.notif.x * nFit + offX) * R : 0;
    const ny = pose.notif ? (pose.notif.y * nFit + offY) * R : 0;
    const notif = pose.notif ? { x: nx, y: ny, r: pose.notif.r * R } : null;
    const notch = pose.notif ? { x: nx, y: ny, r: pose.notif.notch * R } : null;
    return {
      bodyPath,
      bodyAlpha: pose.bodyAlpha,
      eyes,
      dots,
      dotsBehind: pose.dotsBehind,
      // Les etats declarent des arcs en unites de rayon de boule ; le moteur
      // est le seul a connaitre l'echelle du viewBox, donc c'est lui qui trace.
      arcs: pose.arcs.filter((a) => a.opacity > 0.01).map((a) => arcRender(a.seed, a.t, R, a.id, a.opacity)),
      notif,
      notch
    };
  }
};
/** duree du morph quand on change la forme du corps */
__publicField(_BotEngine, "SHAPE_MORPH", 0.45);
/**
 * Duree de rattrapage du regard vers la cible. Plus court que `SHAPE_MORPH` :
 * un regard qui suit doit paraitre attentif, pas visqueux. Comme la cible est
 * reposee a chaque mouvement de souris, c'est cette duree qui donne au suivi
 * son inertie — le regard n'atteint jamais tout a fait un curseur qui bouge.
 */
__publicField(_BotEngine, "LOOK_MORPH", 0.24);
let BotEngine = _BotEngine;
export {
  BotEngine
};
