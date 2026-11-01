import {
  createSpatialImageAiDraft, spatialImageAiPreviewPayload, applySpatialImageAiPreview,
  spatialImageAiCommandPayload, SPATIAL_CANVAS_REFERENCE_SURFACE,
  spatialImageAiResultAssetIds,
} from '../../../src/js/spatial-native-image-ai.js';
import { mediaIdentity } from './node-scene-codec.js';
import { createImageNode, nextPrototypeId, worldPosition } from './canvas-model.js';

export function taskProjection(execution, taskId) {
  return execution?.tasks?.[taskId] || (execution?.task_id === taskId ? execution : null);
}

const TERMINAL_STATUSES = new Set(['completed', 'partial', 'failed', 'canceled', 'interrupted']);
export function activeTaskIds(nodes) {
  return [...new Set(nodes.flatMap(node => {
    const execution = node.data.execution || {};
    return [execution, ...Object.values(execution.tasks || {})]
      .filter(task => task.task_id && !TERMINAL_STATUSES.has(task.status)).map(task => task.task_id);
  }))];
}

// Result reads are asynchronous. Only merge the Task projection/additions;
// never overwrite typing, geometry, imports or deletions made while awaiting.
export function mergeTaskProjection(job, beforeNodes, projected, liveNodes, liveEdges, { allowMissingSource = false } = {}) {
  const params = job.parameters || job.snapshot?.parameters || {};
  const targetId = params.node_target_id || params.spatial_source_element_id;
  if (!allowMissingSource && targetId && !liveNodes.some(node => node.id === targetId)) return { nodes: liveNodes, edges: liveEdges };
  const beforeIds = new Set(beforeNodes.map(node => node.id));
  const projectedById = new Map(projected.nodes.map(node => [node.id, node]));
  const existingAssets = new Set(liveNodes.map(node => node.data.media?.asset_id).filter(Boolean));
  const additions = projected.nodes.filter(node => !beforeIds.has(node.id) && !existingAssets.has(node.data.media?.asset_id));
  const nodes = [...liveNodes.map(node => {
    const projectedNode = projectedById.get(node.id);
    const projection = taskProjection(projectedNode?.data.execution, job.id);
    if (!projection) return node;
    const execution = bindTaskProjection(node.data.execution, job);
    const applied_result_ids = [...new Set([...(taskProjection(execution, job.id)?.applied_result_ids || []), ...(projection.applied_result_ids || [])])];
    return { ...node, data: { ...node.data, execution: { ...execution,
      ...(execution.task_id === job.id ? { applied_result_ids } : {}),
      tasks: { ...execution.tasks, [job.id]: { ...execution.tasks[job.id], applied_result_ids } },
    } } };
  }), ...additions];
  const nodeIds = new Set(nodes.map(node => node.id)), addedIds = new Set(additions.map(node => node.id));
  const edges = [...liveEdges, ...projected.edges.filter(edge => addedIds.has(edge.target)
    && nodeIds.has(edge.source) && nodeIds.has(edge.target) && !liveEdges.some(item => item.id === edge.id))];
  return { nodes, edges };
}

// These are references/projections of PA Tasks, not a second execution store.
// Several runs can originate at one node; completing an older run must neither
// replace the latest run's status nor lose that run's output on restart.
export function bindTaskProjection(execution = {}, job, { activate = false } = {}) {
  const { tasks: existingTasks = {}, ...latest } = execution;
  const tasks = { ...existingTasks };
  if (latest.task_id && !tasks[latest.task_id]) tasks[latest.task_id] = latest;
  const prior = tasks[job.id] || {};
  const projected = { ...prior, task_id: job.id, status: job.status,
    started_at: job.created_at || prior.started_at || '', updated_at: job.updated_at || '', error: job.error || '' };
  tasks[job.id] = projected;
  return { ...(activate || !latest.task_id || latest.task_id === job.id ? projected : latest), tasks };
}

export function freeBranchPosition(source, nodes, width, height) {
  const origin = source ? worldPosition(source, nodes) : { x: 0, y: 0 };
  const position = { x: origin.x + (source?.style.width || 316) + 100, y: origin.y };
  const boxes = nodes.filter(node => node.type !== 'group').map(node => ({ ...worldPosition(node, nodes),
    width: node.measured?.width || node.style.width, height: node.measured?.height || node.style.height,
  })).sort((a, b) => a.y - b.y);
  for (const box of boxes) {
    if (position.x < box.x + box.width + 48 && position.x + width + 48 > box.x && position.y < box.y + box.height + 64 && position.y + height + 64 > box.y) position.y = box.y + box.height + 64;
  }
  return position;
}

// Input UI and engine adapters only. PA's Governor, Command, admission, Task,
// Provider and receipts remain authoritative. No request is sent on restore.
export function createNodeExecutionAdapter({ api, canvasAdapter }) {
  async function preview(input) {
    if (!input.text?.trim() || input.text.length > 1200) throw new Error('请填写不超过 1200 字的当前创作要求');
    if (input.count !== 1) throw new Error('当前已验证的执行预算为 1 次 / 1 张');
    if (input.taskKind === 'image-to-video') {
      if (input.references.length !== 1 || input.references[0].kind !== 'image') throw new Error('视频预览需要一张精确图像作为首帧；视频素材可先抽帧');
      if (![3, 5, 8, 10].includes(input.duration_seconds) || !['1:1', '16:9', '9:16', '4:3', '3:4'].includes(input.ratio)) throw new Error('离线预览参数不兼容；草稿保留');
      // This is the existing free-local video contract, not an image model's
      // evidence promoted into real video generation.
      return { input, bundle: null, localOnly: true, draft: { requestId: '' },
        videoParameters: { contract_version: 'image-to-video-v1', provider: 'offline-preview-v1', prompt: input.text,
          output_ratio: input.ratio, duration_seconds: input.duration_seconds, motion_intensity: 3,
          first_frame_asset_id: input.references[0].asset_id, provider_call_confirmed: false, automatic_paid_retry: false } };
    }
    if (input.taskKind !== 'reference-generate' || input.references.length !== 1) throw new Error('当前正式合同只准入单参考图生成；这个输入不会被偷偷改成单产品图');
    const source = input.references[0];
    if (!source.sourceNodeId) throw new Error('参考素材已离开当前画布，请重新加入');
    const record = canvasAdapter.get();
    if (!record?.current_version_id) throw new Error('请先完成画布保存');
    const draft = createSpatialImageAiDraft('generate-image', {
      canvasId: record.id, sourceElementId: source.sourceNodeId, sourceAssetId: source.asset_id,
      sourceResultId: source.result_id || '', productProfileVersionId: source.product_profile_version_id || '',
      userRequest: input.text, model: input.model, outputRatio: input.ratio, outputResolution: input.resolution,
      inputSurface: SPATIAL_CANVAS_REFERENCE_SURFACE,
    });
    const payload = spatialImageAiPreviewPayload(draft);
    // Keep the existing compiler and execution path, but do not describe a
    // designer's reference task as a white-background ecommerce product job.
    const brief = { objective: '根据当前参考和用户要求完成视觉创作；只修改明确要求的内容', platform: 'general', output_kind: 'canvas-visual-design', intent_locks: {}, output_spec: { ratio: input.ratio, resolution: input.resolution, format: 'PNG' } };
    const bundle = await api.compileKnowledge({ ...payload, ...brief, output_ratio: input.ratio, output_resolution: input.resolution, node_target_id: input.targetId, node_input_signature: input.signature });
    return { input, bundle, brief, draft: applySpatialImageAiPreview(draft, bundle) };
  }

  async function submit(review, { confirmed = false } = {}) {
    if (!confirmed) throw new Error('未确认，未创建 Task，也未调用 Provider');
    if (review.localOnly) {
      review.draft.requestId ||= `node-video:${crypto.randomUUID()}`;
      const response = await api.executeCommand('command:image-to-video', { client_request_id: review.draft.requestId, max_attempts: 1,
        requested_concurrency: 1, source_asset_ids: [review.input.references[0].asset_id], spatial_canvas_id: canvasAdapter.get().id,
        spatial_source_element_id: review.input.references[0].sourceNodeId,
        parameters: { ...review.videoParameters, node_target_id: review.input.targetId, node_input_signature: review.input.signature } });
      const job = response.job || response;
      if (!job.id) throw new Error('视频 Task 响应不完整；请在日志查询，不重复执行');
      return job;
    }
    const submission = spatialImageAiCommandPayload(review.draft);
    review.draft = submission.draft; // Preserve request identity if transport fails.
    submission.payload.parameters.node_target_id = review.input.targetId;
    submission.payload.parameters.node_input_signature = review.input.signature;
    submission.payload.parameters.brief = { ...submission.payload.parameters.brief, ...review.brief };
    submission.payload.parameters.intent_locks = review.brief.intent_locks;
    const response = await api.executeCommand(submission.commandId, submission.payload);
    const job = response.job || response;
    if (!job?.id) throw new Error('任务响应不完整，请从日志查询；不要重复付费提交');
    return job;
  }

  async function project(job, nodes, edges, { restoreApplied = false } = {}) {
    const params = job.parameters || job.snapshot?.parameters || {};
    const targetId = params.node_target_id || params.spatial_source_element_id;
    const target = nodes.find((node) => node.id === targetId);
    const current = nodes.map((node) => node.id === targetId ? { ...node, data: { ...node.data,
      execution: bindTaskProjection(node.data.execution, job),
    } } : node);
    if (!['completed', 'partial'].includes(job.status)) return { nodes: current, edges };
    const ids = job.command_id === 'command:image-to-video' || job.snapshot?.command_id === 'command:image-to-video'
      ? [...new Set((job.items || []).flatMap((item) => item.result_asset_ids || []))] : spatialImageAiResultAssetIds(job);
    const recorded = taskProjection(target?.data.execution, job.id);
    const existing = new Set([...current.filter((node) => node.data?.media?.task_id === job.id).map((node) => node.data.media.asset_id), ...(restoreApplied ? [] : recorded?.applied_result_ids || [])]);
    const additions = [], connections = [];
    for (const assetId of ids.filter((id) => !existing.has(id))) {
      const response = await api.getAsset(assetId); const asset = response.asset || response;
      // Cover/cutout assets are retained in PA. The main result is one branch,
      // not duplicate nodes for each representation of the same generation.
      if (asset.role !== 'result_main' && asset.role !== 'result_video') continue;
      const media = mediaIdentity({ ...asset, task_id: job.id, result_id: asset.id });
      const node = createImageNode({ id: nextPrototypeId('result'), name: asset.name || '生成结果', media,
        src: await api.getAssetContentUrl(asset.id), naturalWidth: asset.width, naturalHeight: asset.height,
      });
      node.position = freeBranchPosition(target, [...current, ...additions], node.style.width, node.style.height);
      node.type = asset.kind === 'video' ? 'video' : 'image'; node.selected = false;
      additions.push(node);
      if (target) connections.push({ id: nextPrototypeId('lineage'), source: target.id, target: node.id, sourceHandle: 'reference-out', targetHandle: 'reference-in', type: 'reference', kind: 'flow' });
    }
    const applied_result_ids = [...new Set([...existing, ...additions.map((item) => item.data.media.asset_id)])];
    return { nodes: [...current.map((node) => {
      if (node.id !== targetId) return node;
      const execution = node.data.execution;
      return { ...node, data: { ...node.data, execution: { ...execution,
        ...(execution.task_id === job.id ? { applied_result_ids } : {}),
        tasks: { ...execution.tasks, [job.id]: { ...execution.tasks[job.id], applied_result_ids } },
      } } };
    }), ...additions], edges: [...edges, ...connections] };
  }
  return { preview, submit, project };
}
