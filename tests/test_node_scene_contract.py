from __future__ import annotations
import copy
import json
import tempfile
import unittest
from pathlib import Path
from python.atelier_ledger import AtelierLedger
from python.spatial_canvas_contract import normalize_spatial_scene, spatial_scene_references
from python.node_scene_contract import validate_node_execution_input


def scene(asset_id=None):
    media = {"asset_id": asset_id, "kind": "image", "role": "workspace_source", "width": 4096, "height": 2048} if asset_id else None
    return {"schema_version": 2, "kind": "pa-node-canvas", "nodes": [
        {"id": "source", "type": "image", "position": {"x": 10, "y": 20}, "style": {"width": 460, "height": 230}, "data": {"name": "source.png", **({"media": media} if media else {})}},
        {"id": "prompt", "type": "prompt", "position": {"x": -300, "y": 40}, "style": {"width": 280, "height": 200}, "data": {"text": "仅把 14.9 改为 10，其余不变", "draft": {"prompt": "第二轮", "references": [media] if media else []}}},
    ], "edges": [{"id": "input", "source": "prompt", "target": "source", "kind": "input"}], "viewport": {"x": 30, "y": -20, "zoom": .8}}


class NodeSceneTests(unittest.TestCase):
    def test_manual_reference_keeps_exact_duplicate_element_and_never_reverts_to_original(self):
        raw = scene('ast_exact'); raw['edges'] = []
        duplicate = copy.deepcopy(raw['nodes'][0]); duplicate['id'] = 'copy'
        raw['nodes'].append(duplicate)
        ref = {**duplicate['data']['media'], 'source_element_id': 'copy'}
        raw['nodes'][0]['data']['draft'] = {'prompt': '只改数字', 'model': 'tt-image-2', 'references': [ref], 'useCurrentMedia': False}
        semantic = {'targetId': 'source', 'text': '只改数字', 'references': [{**ref, 'sourceNodeId': 'copy'}], 'taskKind': 'reference-generate', 'model': 'tt-image-2', 'ratio': '1:1', 'resolution': '2k', 'quality': 'auto', 'count': 1}
        params = {'node_target_id': 'source', 'node_input_signature': json.dumps(semantic), 'model': 'tt-image-2', 'output_ratio': '1:1', 'output_resolution': '2k', 'brief': {'user_request': '只改数字'}}
        self.assertEqual(validate_node_execution_input(raw, params, source_element_id='copy', source_asset_id='ast_exact'), semantic)
        raw['nodes'] = [node for node in raw['nodes'] if node['id'] != 'copy']
        with self.assertRaises(ValueError): validate_node_execution_input(raw, params, source_element_id='source', source_asset_id='ast_exact')

    def test_optional_template_metadata_is_validated_and_durable_in_the_same_ledger(self):
        raw = scene()
        raw['metadata'] = {'templates': [{'id': 'template_exact', 'name': '只改数字', 'category': '商业', 'text': '保留 Logo\n不改构图'}]}
        with tempfile.TemporaryDirectory() as root:
            ledger = AtelierLedger(Path(root) / 'atelier.sqlite3')
            doc = ledger.create_spatial_canvas(name='模板', client_request_id='template-create', scene=raw)
            self.assertEqual(ledger.get_spatial_canvas(doc['id'])['scene']['metadata'], raw['metadata'])
        changed = copy.deepcopy(raw); changed['metadata']['templates'][0]['text'] = ''
        with self.assertRaises(ValueError): normalize_spatial_scene(changed)
        changed = copy.deepcopy(raw); changed['metadata']['token'] = 'not-allowed'
        with self.assertRaises(ValueError): normalize_spatial_scene(changed)

    def test_all_task_projections_are_referenced_without_rewriting_old_single_projection(self):
        raw = scene()
        raw['nodes'][0]['data']['execution'] = {'task_id': 'job_second', 'tasks': {
            'job_first': {'task_id': 'job_first', 'status': 'completed'},
            'job_second': {'task_id': 'job_second', 'status': 'queued'},
        }}
        value = normalize_spatial_scene(raw)
        self.assertEqual({item['ref_id'] for item in spatial_scene_references(value)}, {'job_first', 'job_second'})
        raw['nodes'][0]['data']['execution']['tasks']['job_first']['task_id'] = 'job_wrong'
        with self.assertRaises(ValueError): normalize_spatial_scene(raw)

    def test_backend_re_resolves_exact_node_draft_and_ignores_geometry(self):
        raw = scene('ast_exact')
        raw['nodes'][0]['data']['draft'] = {'prompt': '只改数字', 'model': 'tt-image-2'}
        raw['edges'] = []
        semantic = {'targetId': 'source', 'text': '只改数字', 'references': [{**raw['nodes'][0]['data']['media'], 'sourceNodeId': 'source'}],
            'taskKind': 'reference-generate', 'model': 'tt-image-2', 'ratio': '1:1', 'resolution': '2k', 'quality': 'auto', 'count': 1}
        import json
        params = {'node_target_id': 'source', 'node_input_signature': json.dumps(semantic), 'model': 'tt-image-2',
            'output_ratio': '1:1', 'output_resolution': '2k', 'brief': {'user_request': '只改数字'}}
        self.assertEqual(validate_node_execution_input(raw, params, source_element_id='source', source_asset_id='ast_exact'), semantic)
        raw['nodes'][0]['position']['x'] += 999
        self.assertEqual(validate_node_execution_input(raw, params, source_element_id='source', source_asset_id='ast_exact'), semantic)
        for key, value in [('prompt', '新的要求'), ('model', 'banana-2'), ('ratio', '16:9')]:
            changed = copy.deepcopy(raw); changed['nodes'][0]['data']['draft'][key] = value
            with self.assertRaises(ValueError): validate_node_execution_input(changed, params, source_element_id='source', source_asset_id='ast_exact')
        with self.assertRaises(ValueError): validate_node_execution_input(raw, params, source_element_id='source', source_asset_id='ast_original')

    def test_same_ledger_durable_readback_revision_refs_and_schema(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "atelier.sqlite3"
            ledger = AtelierLedger(path)
            asset = ledger.register_workspace_asset(sha256="a" * 64, storage_path=str(Path(root) / "source.png"), mime="image/png", size_bytes=128, width=4096, height=2048, name="source.png")
            value = scene(asset["id"])
            doc = ledger.create_spatial_canvas(name="节点设计", client_request_id="node-create-1", scene=value)
            self.assertEqual(doc["scene"], normalize_spatial_scene(value))
            self.assertEqual(doc["thumbnail"]["scene_kind"], "pa-node-canvas")
            value["nodes"][1]["data"]["draft"]["prompt"] = "保留草稿"
            saved = ledger.save_spatial_canvas_scene(doc["id"], expected_revision=1, client_request_id="node-save-1", scene=value)
            self.assertEqual(saved["current_revision"], 2)
            reopened = AtelierLedger(path).get_spatial_canvas(doc["id"])
            self.assertEqual(reopened["scene"], saved["scene"])
            self.assertEqual(len(spatial_scene_references(value)), 2)
            self.assertLess(len(str(saved["scene"])), 3000)

    def test_embedded_images_urls_credentials_and_runtime_callbacks_rejected(self):
        for field, value in [("src", "data:image/png;base64,AAAA"), ("token", "secret"), ("url", "http://127.0.0.1:8765/a"), ("onDelete", "callback")]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                raw = scene()
                raw["nodes"][0]["data"]["draft"] = {field: value}
                normalize_spatial_scene(raw)

    def test_invalid_graph_and_parent_validation(self):
        variants = []
        raw = scene(); raw["edges"].append({"id": "back", "source": "source", "target": "prompt"}); variants.append(raw)
        raw = scene(); raw["edges"][0]["target"] = "missing"; variants.append(raw)
        raw = scene(); raw["nodes"][0]["parentId"] = "prompt"; variants.append(raw)
        raw = scene(); raw["nodes"][0]["style"]["width"] = float("inf"); variants.append(raw)
        raw = scene(); raw["nodes"].append(copy.deepcopy(raw["nodes"][0])); variants.append(raw)
        for raw in variants:
            with self.assertRaises(ValueError): normalize_spatial_scene(raw)

    def test_v1_documents_are_not_migrated_or_rewritten(self):
        from tests.test_spatial_canvas_ledger import spatial_scene
        old = spatial_scene()
        self.assertEqual(normalize_spatial_scene(old)["schema_version"], 1)
        self.assertNotIn("nodes", old)


if __name__ == "__main__": unittest.main()
