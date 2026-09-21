import json
import struct
import tempfile
import unittest
from pathlib import Path
from qwen_probe import inspect_checkpoint

class Probe(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'model_index.json').write_text(json.dumps({'_class_name':'QwenImage21Pipeline'}))
        for name in ('LICENSE','processor/tokenizer.json','processor/tokenizer_config.json','processor/preprocessor_config.json',
                     'scheduler/scheduler_config.json','text_encoder/config.json','transformer/config.json','vae/config.json'):
            p=self.root/name;p.parent.mkdir(exist_ok=True);p.write_text('{}')
        header=json.dumps({'x':{'dtype':'F32','shape':[1],'data_offsets':[0,4]}}).encode()
        for folder in ('text_encoder','transformer','vae'):
            (self.root/folder/'model.safetensors').write_bytes(struct.pack('<Q',len(header))+header+b'\0'*4)
    def tearDown(self):self.temp.cleanup()
    def test_complete_structure(self):self.assertTrue(inspect_checkpoint(self.root)['ok'])
    def test_lfs_pointer_is_not_a_download(self):
        (self.root/'vae/model.safetensors').write_text('version https://git-lfs.github.com/spec/v1\noid sha256:123\nsize 99')
        self.assertFalse(inspect_checkpoint(self.root)['ok'])
    def test_missing_index_shard(self):
        (self.root/'transformer/model.safetensors.index.json').write_text(json.dumps({'weight_map':{'x':'missing.safetensors'}}))
        self.assertFalse(inspect_checkpoint(self.root)['ok'])
    def test_truncated_tensor_data(self):
        p=self.root/'vae/model.safetensors';p.write_bytes(p.read_bytes()[:-1])
        self.assertFalse(inspect_checkpoint(self.root)['ok'])

if __name__=='__main__':unittest.main()
