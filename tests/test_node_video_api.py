from __future__ import annotations
import json
from tests.test_video_job_api import OfflineVideoJobApiTests
from tests.test_node_scene_contract import scene


class NodeVideoApiTests(OfflineVideoJobApiTests):
    def test_node_video_exact_input_offline_task_receipt_and_replay(self):
        with self.live_client() as client:
            source = self.direct_asset('frame.png', (55, 100, 190))
            value = scene(source['id']); value['edges'] = []
            text = '镜头缓慢推进，保持文字'
            value['nodes'][0]['data']['draft'] = {'prompt': text, 'mode': 'video'}
            canvas = self.ledger.create_spatial_canvas(name='节点视频', client_request_id='node-video-canvas', scene=value)
            media = canvas['scene']['nodes'][0]['data']['media']
            signature = {'targetId': 'source', 'text': text, 'references': [{**media, 'sourceNodeId': 'source'}],
                'taskKind': 'image-to-video', 'model': 'offline-preview-v1', 'ratio': '1:1', 'resolution': 'offline-fixture', 'quality': 'auto', 'count': 1, 'duration_seconds': 5}
            request = {'client_request_id': 'node-video-task', 'source_asset_ids': [source['id']], 'spatial_canvas_id': canvas['id'],
                'spatial_source_element_id': 'source', 'max_attempts': 1, 'parameters': {'contract_version': 'image-to-video-v1',
                'provider': 'offline-preview-v1', 'prompt': text, 'output_ratio': '1:1', 'duration_seconds': 5,
                'motion_intensity': 3, 'first_frame_asset_id': source['id'], 'automatic_paid_retry': False,
                'provider_call_confirmed': False, 'node_target_id': 'source', 'node_input_signature': json.dumps(signature)}}
            response = client.post('/api/commands/command:image-to-video/execute', json=request)
            self.assertEqual(response.status_code, 200, response.text)
            job_id = response.json()['job']['id']
            completed = self.wait_for_job(job_id)
            self.assertEqual(completed['status'], 'completed', completed)
            assets = completed['items'][0]['result_asset_ids']; self.assertTrue(assets)
            video = next(self.ledger.get_asset(asset_id) for asset_id in assets if self.ledger.get_asset(asset_id)['kind'] == 'video')
            self.assertEqual(video['parent_asset_id'], source['id'])
            replay = client.post('/api/commands/command:image-to-video/execute', json=request)
            self.assertEqual(replay.json()['job']['id'], job_id)
            self.assertEqual(len(self.ledger.list_jobs()), 1)
            self.assert_zero_network()
