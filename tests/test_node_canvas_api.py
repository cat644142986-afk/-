from __future__ import annotations
import copy
import io
import json
import unittest
from unittest import mock
from PIL import Image
from tests.test_asset_api import AssetApiTests, png_bytes, server
from tests.test_node_scene_contract import scene


class NodeCanvasApiTests(unittest.TestCase):
    setUp = AssetApiTests.setUp
    tearDown = AssetApiTests.tearDown

    def test_workflow_validation_is_read_only_and_rejects_runtime_urls_secrets_and_invalid_graphs(self):
        value = scene()
        valid = self.client.post('/api/node-workflows/validate', json={'scene': value})
        self.assertEqual(valid.status_code, 200, valid.text)
        self.assertEqual(valid.json()['scene']['nodes'][0]['id'], 'source')
        self.assertEqual(self.ledger.list_jobs(), [])
        for field, text in [('src', 'http://external.example/image.png'), ('api_key', 'not-a-key')]:
            invalid = copy.deepcopy(value); invalid['nodes'][0]['data'][field] = text
            response = self.client.post('/api/node-workflows/validate', json={'scene': invalid})
            self.assertEqual(response.status_code, 400)
        invalid = copy.deepcopy(value); invalid['edges'][0]['target'] = 'missing'
        self.assertEqual(self.client.post('/api/node-workflows/validate', json={'scene': invalid}).status_code, 400)

    def source_canvas(self):
        asset = self.store.import_bytes(png_bytes(), 'source.png')
        value = scene(asset['id']); value['edges'] = []
        value['nodes'][0]['data']['draft'] = {'prompt': '只把 14.9 改成 10，其余不变', 'model': 'tt-image-2'}
        canvas = self.ledger.create_spatial_canvas(name='节点', client_request_id='create-node-api', scene=value)
        return asset, canvas

    def test_local_editor_exact_canvas_source_without_legacy_collection_and_png_alpha(self):
        asset, canvas = self.source_canvas()
        result = io.BytesIO(); Image.new('RGBA', (16, 8), (255, 30, 40, 77)).save(result, 'PNG')
        payload = {'version_id': canvas['current_version_id'], 'element_id': 'source', 'source_asset_id': asset['id'], 'client_request_id': 'apply-node-api', 'operation': 'crop'}
        first = self.client.post(f"/api/spatial-canvases/{canvas['id']}/local-derivatives", data=payload, files={'file': ('result.png', result.getvalue(), 'image/png')})
        self.assertEqual(first.status_code, 200, first.text)
        created = first.json()['asset']; self.assertEqual(created['lineage_parent_id'], asset['id'])
        content = self.client.get(f"/api/assets/{created['id']}/content")
        with Image.open(io.BytesIO(content.content)) as image:
            self.assertEqual(image.size, (16, 8)); self.assertEqual(image.getpixel((0, 0)), (255, 30, 40, 77))
        replay = self.client.post(f"/api/spatial-canvases/{canvas['id']}/local-derivatives", data=payload, files={'file': ('result.png', result.getvalue(), 'image/png')})
        self.assertEqual(replay.json()['asset']['id'], created['id']); self.assertEqual(self.ledger.list_jobs(), [])

    def test_node_governor_is_read_only_and_stale_input_rejected(self):
        asset, canvas = self.source_canvas(); media = canvas['scene']['nodes'][0]['data']['media']
        text = canvas['scene']['nodes'][0]['data']['draft']['prompt']
        signature = {'targetId': 'source', 'text': text, 'references': [{**media, 'sourceNodeId': 'source'}], 'taskKind': 'reference-generate', 'model': 'tt-image-2', 'ratio': '1:1', 'resolution': '2k', 'quality': 'auto', 'count': 1}
        payload = {'mode': 'single', 'command_id': 'command:existing-generate-single', 'user_request': text, 'objective': '视觉创作',
            'platform': 'general', 'output_kind': 'canvas-visual-design', 'intent_locks': {}, 'model': 'tt-image-2', 'prompt_version': 'prompt_v1', 'generation_strategy': 'single_pass',
            'output_spec': {'ratio': '1:1', 'resolution': '2k', 'format': 'PNG'}, 'output_ratio': '1:1', 'output_resolution': '2k',
            'source_asset_ids': [asset['id']], 'spatial_canvas_id': canvas['id'], 'spatial_source_element_id': 'source',
            'spatial_action': 'generate-image', 'node_target_id': 'source', 'node_input_signature': json.dumps(signature),
            'ui_context': {'input_surface': 'canvas-reference', 'contract_version': 'canvas-reference-input-v1'}}
        response = self.client.post('/api/knowledge/compile', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['execution_context']['user_intent']['user_request'], text)
        self.assertEqual(self.ledger.list_jobs(), [])
        changed = copy.deepcopy(canvas['scene']); changed['nodes'][0]['data']['draft']['prompt'] = '新消息'
        self.ledger.save_spatial_canvas_scene(canvas['id'], expected_revision=1, client_request_id='change-node-api', scene=changed)
        stale = self.client.post('/api/knowledge/compile', json=payload)
        self.assertEqual(stale.status_code, 400, stale.text); self.assertEqual(self.ledger.list_jobs(), [])

    def test_png_output_preserves_provider_pixels_and_legacy_jpeg_is_unchanged(self):
        image = Image.new('RGBA', (16, 8), (12, 34, 56, 77))
        png = server._stage_output(image, self.output_dir, 'node.png', 'result_main', format='PNG')
        with Image.open(png['temp_path']) as output: self.assertEqual(output.getpixel((0, 0)), (12, 34, 56, 77))
        self.assertEqual(png['mime'], 'image/png')
        legacy = server._stage_output(image, self.output_dir, 'legacy.jpg', 'result_main')
        with Image.open(legacy['temp_path']) as output: self.assertEqual(output.format, 'JPEG')
        self.assertEqual(legacy['mime'], 'image/jpeg')


if __name__ == '__main__': unittest.main()
