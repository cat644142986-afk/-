export const LOCAL_EDIT_MASK_SCHEMA_VERSION = 1;
export const LOCAL_EDIT_DEFAULT_BRUSH_RADIUS = 24;
export const LOCAL_EDIT_MAX_BRUSH_RADIUS = 2048;
export const LOCAL_EDIT_MAX_FEATHER_RADIUS = 256;

function integer(value, label, minimum = 0, maximum = 32768) {
  const number = Number(value);
  if (!Number.isInteger(number) || number < minimum || number > maximum) {
    throw new Error(`${label} 超出允许范围`);
  }
  return number;
}

function sourceSize(layer) {
  const width = integer(layer?.source?.original_pixel_width, '原图宽度', 1);
  const height = integer(layer?.source?.original_pixel_height, '原图高度', 1);
  return { width, height };
}

export function sourcePointFromScene(layer, point) {
  const rotation = Number(layer?.transform?.rotation_degrees || 0);
  if (!Number.isFinite(rotation) || Math.abs(rotation) > 0.0001) {
    throw new Error('精确选区要求图层旋转为 0°');
  }
  const scaleX = Number(layer?.transform?.scale_x);
  const scaleY = Number(layer?.transform?.scale_y);
  if (!(scaleX > 0) || !(scaleY > 0)) throw new Error('图层缩放状态无效');
  const { width, height } = sourceSize(layer);
  const x = Math.max(0, Math.min(width, (Number(point?.x) - Number(layer.transform.x)) / scaleX));
  const y = Math.max(0, Math.min(height, (Number(point?.y) - Number(layer.transform.y)) / scaleY));
  if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error('无法换算选区坐标');
  return { x, y };
}

export function roiFromSceneDrag(layer, start, end) {
  const first = sourcePointFromScene(layer, start);
  const second = sourcePointFromScene(layer, end);
  const { width: sourceWidth, height: sourceHeight } = sourceSize(layer);
  const x = Math.max(0, Math.min(sourceWidth - 1, Math.floor(Math.min(first.x, second.x))));
  const y = Math.max(0, Math.min(sourceHeight - 1, Math.floor(Math.min(first.y, second.y))));
  const right = Math.max(x + 1, Math.min(sourceWidth, Math.ceil(Math.max(first.x, second.x))));
  const bottom = Math.max(y + 1, Math.min(sourceHeight, Math.ceil(Math.max(first.y, second.y))));
  return { x, y, width: right - x, height: bottom - y };
}

export function normalizeSourceRoi(layer, value) {
  const { width: sourceWidth, height: sourceHeight } = sourceSize(layer);
  const rect = {
    x: integer(value?.x, '选区 X'),
    y: integer(value?.y, '选区 Y'),
    width: integer(value?.width, '选区宽度', 1),
    height: integer(value?.height, '选区高度', 1),
  };
  if (rect.x + rect.width > sourceWidth || rect.y + rect.height > sourceHeight) {
    throw new Error('选区不能超出原图像素范围');
  }
  return rect;
}

export function sceneRectFromSourceRoi(layer, value) {
  const rect = normalizeSourceRoi(layer, value);
  const rotation = Number(layer?.transform?.rotation_degrees || 0);
  if (!Number.isFinite(rotation) || Math.abs(rotation) > 0.0001) {
    throw new Error('精确选区要求图层旋转为 0°');
  }
  const scaleX = Number(layer?.transform?.scale_x);
  const scaleY = Number(layer?.transform?.scale_y);
  if (!(scaleX > 0) || !(scaleY > 0)) throw new Error('图层缩放状态无效');
  return {
    x: Number(layer.transform.x) + rect.x * scaleX,
    y: Number(layer.transform.y) + rect.y * scaleY,
    width: rect.width * scaleX,
    height: rect.height * scaleY,
  };
}

export function createMaskDefinition(layer, base = 'empty') {
  const size = sourceSize(layer);
  return {
    schema_version: LOCAL_EDIT_MASK_SCHEMA_VERSION,
    coordinate_space: 'source-pixel',
    width: size.width,
    height: size.height,
    base: base === 'full' ? 'full' : 'empty',
    strokes: [],
    feather_radius: 0,
  };
}

export function cloneMaskDefinition(definition) {
  return JSON.parse(JSON.stringify(definition));
}

export function setMaskBase(definition, base) {
  const next = cloneMaskDefinition(definition);
  next.base = base === 'full' ? 'full' : 'empty';
  next.strokes = [];
  return next;
}

export function invertMaskDefinition(definition) {
  const next = cloneMaskDefinition(definition);
  next.base = next.base === 'full' ? 'empty' : 'full';
  next.strokes = next.strokes.map((stroke) => ({
    ...stroke,
    mode: stroke.mode === 'include' ? 'exclude' : 'include',
  }));
  return next;
}

export function setMaskFeather(definition, featherRadius) {
  const next = cloneMaskDefinition(definition);
  next.feather_radius = integer(
    featherRadius,
    '边缘柔化',
    0,
    LOCAL_EDIT_MAX_FEATHER_RADIUS,
  );
  return next;
}

export function appendMaskStroke(definition, mode, brushRadius, points, roi) {
  const next = cloneMaskDefinition(definition);
  const radius = integer(brushRadius, '画笔半径', 1, LOCAL_EDIT_MAX_BRUSH_RADIUS);
  const normalizedRoi = {
    x: integer(roi?.x, '选区 X'),
    y: integer(roi?.y, '选区 Y'),
    width: integer(roi?.width, '选区宽度', 1),
    height: integer(roi?.height, '选区高度', 1),
  };
  const normalized = Array.from(points || []).map((point) => ({
    x: integer(Math.round(Number(point?.x)), '笔触 X', 0, next.width - 1),
    y: integer(Math.round(Number(point?.y)), '笔触 Y', 0, next.height - 1),
  })).filter((point, index, all) => (
    point.x >= normalizedRoi.x
    && point.y >= normalizedRoi.y
    && point.x < normalizedRoi.x + normalizedRoi.width
    && point.y < normalizedRoi.y + normalizedRoi.height
    && (index === 0 || point.x !== all[index - 1].x || point.y !== all[index - 1].y)
  ));
  if (!normalized.length) throw new Error('笔触必须落在当前选区内');
  if (next.strokes.length >= 200) throw new Error('当前蒙版笔触过多，请先保存或重置');
  next.strokes.push({
    mode: mode === 'exclude' ? 'exclude' : 'include',
    radius,
    points: normalized,
  });
  return next;
}

export function maskHasWritablePixels(definition) {
  if (definition?.base === 'full') return true;
  return Array.from(definition?.strokes || []).some((stroke) => stroke.mode === 'include');
}

function candidateResult(value) {
  const id = String(value?.asset_id || value?.id || '').trim();
  const role = String(value?.role || '').trim();
  const width = Number(value?.width);
  const height = Number(value?.height);
  if (!id || !role.startsWith('result_') || !Number.isInteger(width) || !Number.isInteger(height)) {
    return null;
  }
  return {
    ...value,
    id,
    asset_id: id,
    role,
    width,
    height,
    name: String(value?.name || `结果 ${id.slice(-8)}`),
  };
}

export function expectedLocalEditCandidateSize(contract, layer) {
  if (contract?.mode === 'outpaint') {
    return {
      width: integer(
        contract?.outpaint?.output_width ?? contract?.outpaint?.output_size?.width,
        '扩图输出宽度',
        1,
      ),
      height: integer(
        contract?.outpaint?.output_height ?? contract?.outpaint?.output_size?.height,
        '扩图输出高度',
        1,
      ),
    };
  }
  const fallback = sourceSize(layer);
  return {
    width: integer(contract?.source_size?.width ?? fallback.width, '候选宽度', 1),
    height: integer(contract?.source_size?.height ?? fallback.height, '候选高度', 1),
  };
}

export function compatibleLocalEditCandidates(currentResults, recentResults, contract, layer) {
  const expected = expectedLocalEditCandidateSize(contract, layer);
  const deduplicated = new Map();
  [
    ...Object.values(currentResults || {}).flatMap((items) => Array.from(items || [])),
    ...Array.from(recentResults || []),
  ].forEach((value) => {
    const candidate = candidateResult(value);
    if (
      candidate
      && candidate.width === expected.width
      && candidate.height === expected.height
      && !deduplicated.has(candidate.id)
    ) deduplicated.set(candidate.id, candidate);
  });
  return [...deduplicated.values()];
}

export function defaultOutpaintConfig(layer) {
  const size = sourceSize(layer);
  const width = Math.min(32768, Math.max(size.width + 2, Math.ceil(size.width * 1.5)));
  const height = Math.min(32768, Math.max(size.height + 2, Math.ceil(size.height * 1.5)));
  return {
    output_width: width,
    output_height: height,
    source_x: Math.floor((width - size.width) / 2),
    source_y: Math.floor((height - size.height) / 2),
    transition_width: 0,
  };
}

export function normalizeOutpaintConfig(layer, value) {
  const size = sourceSize(layer);
  const config = {
    output_width: integer(value?.output_width, '扩图输出宽度', 1),
    output_height: integer(value?.output_height, '扩图输出高度', 1),
    source_x: integer(value?.source_x, '原图 X'),
    source_y: integer(value?.source_y, '原图 Y'),
    transition_width: integer(
      value?.transition_width,
      '过渡带宽度',
      0,
      Math.floor(Math.min(size.width, size.height) / 2),
    ),
  };
  if (
    config.output_width < size.width
    || config.output_height < size.height
    || config.source_x + size.width > config.output_width
    || config.source_y + size.height > config.output_height
  ) throw new Error('原图必须完整放在扩图输出范围内');
  if (
    config.output_width === size.width
    && config.output_height === size.height
    && config.source_x === 0
    && config.source_y === 0
  ) throw new Error('扩图输出必须包含新增区域');
  return config;
}

function sameRectValue(left, right) {
  return ['x', 'y', 'width', 'height']
    .every((key) => Number(left?.[key]) === Number(right?.[key]));
}

export function buildPaidOutpaintContract({
  operationId,
  canvasVersionId,
  layer,
  sourceSha256 = '',
  sourcePixelSha256 = '',
  roi,
  outpaint,
  confirmed = false,
}) {
  const normalized = normalizeOutpaintConfig(layer, outpaint);
  if (!confirmed) throw new Error('请先确认本次扩图规格与调用次数');
  const expectedRect = {
    x: 0,
    y: 0,
    width: normalized.output_width,
    height: normalized.output_height,
  };
  if (
    !roi?.id
    || roi.coordinate_space !== 'output-pixel'
    || !sameRectValue(roi.rect, expectedRect)
  ) throw new Error('扩图写入范围尚未冻结');
  return {
    schema_version: 1,
    operation_id: String(operationId),
    mode: 'outpaint',
    source_canvas_version_id: String(canvasVersionId),
    source_layer_id: String(layer.id),
    source_sha256: String(sourceSha256).toUpperCase(),
    source_pixel_sha256: String(sourcePixelSha256).toUpperCase(),
    source_size: sourceSize(layer),
    roi: {
      id: String(roi.id),
      coordinate_space: 'output-pixel',
      rect: expectedRect,
    },
    mask: null,
    strict_pixel_protection: true,
    outpaint: normalized,
    cost: {
      mode: 'paid',
      confirmed_call_count: 1,
      user_confirmation_required: true,
      user_confirmed: true,
      automatic_paid_retry: false,
    },
  };
}

export function buildFreeLocalEditContract({
  operationId,
  canvasVersionId,
  layer,
  sourceSha256 = '',
  sourcePixelSha256 = '',
  roi,
  mask,
}) {
  if (!mask?.version?.id || !mask?.version?.pixel_sha256) {
    throw new Error('请先保存蒙版版本');
  }
  return {
    schema_version: 1,
    operation_id: String(operationId),
    mode: 'inpaint',
    source_canvas_version_id: String(canvasVersionId),
    source_layer_id: String(layer.id),
    source_sha256: String(sourceSha256).toUpperCase(),
    source_pixel_sha256: String(sourcePixelSha256).toUpperCase(),
    source_size: sourceSize(layer),
    roi: {
      id: String(roi.id),
      coordinate_space: 'source-pixel',
      rect: { ...roi.rect },
    },
    mask: {
      id: String(mask.version.id),
      roi_id: String(roi.id),
      width: Number(mask.version.definition.width),
      height: Number(mask.version.definition.height),
      sha256: String(mask.version.pixel_sha256).toUpperCase(),
    },
    strict_pixel_protection: true,
    cost: {
      mode: 'free',
      confirmed_call_count: 0,
      user_confirmation_required: false,
      user_confirmed: false,
      automatic_paid_retry: false,
    },
  };
}
