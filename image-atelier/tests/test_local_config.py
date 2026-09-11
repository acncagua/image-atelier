import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from local_config import api_key


class LocalConfigTests(unittest.TestCase):
    def test_file_priority_and_environment_fallback(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'OPENAI_API_KEY': 'test-env'}):
            path = Path(folder) / 'config.local.json'
            self.assertEqual(api_key(path), 'test-env')
            path.write_text(json.dumps({'openai_api_key': ' test-file '}), encoding='utf-8-sig')
            self.assertEqual(api_key(path), 'test-file')
            path.write_text('{"openai_api_key":""}', encoding='utf-8')
            self.assertEqual(api_key(path), 'test-env')

    def test_invalid_config_does_not_disclose_content(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.local.json'
            for content in ('{ secret-test-value', '[]', '{"openai_api_key":123}'):
                path.write_text(content, encoding='utf-8')
                with self.assertRaises(ValueError) as error:
                    api_key(path)
                self.assertNotIn('secret-test-value', str(error.exception))
