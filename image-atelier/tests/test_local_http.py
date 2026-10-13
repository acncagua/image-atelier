import ssl
import unittest
from unittest.mock import patch
import comfy_backend
import strata_client
from local_http import LOCAL_HTTP_TLS_CONTEXT

class LocalHTTP(unittest.TestCase):
    def test_plain_local_clients_do_not_load_default_certificates_per_poll(self):
        with patch('ssl.create_default_context',side_effect=AssertionError('Unnecessary certificate loading')),patch('strata_client.strata_api_key',return_value=''):
            for _ in range(10):
                with comfy_backend.client(comfy_backend.DEFAULTS):pass
                with strata_client.client(strata_client.DEFAULTS):pass
        self.assertTrue(LOCAL_HTTP_TLS_CONTEXT.check_hostname)
        self.assertEqual(LOCAL_HTTP_TLS_CONTEXT.verify_mode,ssl.CERT_REQUIRED)

    def test_local_clients_still_reject_https_and_external_hosts(self):
        for url in ('https://127.0.0.1:8188','http://example.com:8188'):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):comfy_backend.client({**comfy_backend.DEFAULTS,'url':url})
                with self.assertRaises(ValueError):strata_client.client({**strata_client.DEFAULTS,'url':url})
