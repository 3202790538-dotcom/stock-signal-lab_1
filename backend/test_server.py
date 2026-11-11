import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import server

class LocalServerTests(unittest.TestCase):
    def test_failed_provider_refresh_keeps_last_valid_dashboard(self):
        with TemporaryDirectory() as folder:
            p=Path(folder);previous='{"marketAsOf":"2026-11-06","sentinel":"valid"}'
            (p/'dashboard.json').write_text(previous)
            with patch.object(server,'DATA',p),patch.object(server.subprocess,'run',return_value=SimpleNamespace(returncode=1,stderr='provider unavailable',stdout='')):
                response=server.refresh()
                self.assertEqual(response.status_code,502)
                self.assertEqual((p/'dashboard.json').read_text(),previous)
                self.assertFalse(server.refresh_lock.locked())

    def test_foreign_origin_cannot_trigger_price_refresh(self):
        with TestClient(server.app) as client:
            response=client.post('/api/refresh',headers={'host':'127.0.0.1:18765','origin':'https://unrelated.example'})
            self.assertEqual(response.status_code,403)

    def test_current_local_origins_can_trigger_price_refresh(self):
        with TestClient(server.app) as client:
            for origin in ('http://127.0.0.1:5173','http://localhost:5173','http://127.0.0.1:18765','http://localhost:18765'):
                with self.subTest(origin=origin),patch.object(server.subprocess,'run',return_value=SimpleNamespace(returncode=0,stderr='',stdout='')) as run:
                    response=client.post('/api/refresh',headers={'host':'127.0.0.1:18765','origin':origin})
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(response.json(),{'ok':True})
                    run.assert_called_once()

    def test_previous_backend_origins_cannot_trigger_price_refresh(self):
        with TestClient(server.app) as client:
            for origin in ('http://127.0.0.1:8765','http://localhost:8765'):
                with self.subTest(origin=origin),patch.object(server.subprocess,'run') as run:
                    response=client.post('/api/refresh',headers={'host':'127.0.0.1:18765','origin':origin})
                    self.assertEqual(response.status_code,403)
                    run.assert_not_called()

    def test_rebound_hostname_cannot_read_private_account_data(self):
        with TestClient(server.app) as client:
            response=client.get('/api/dashboard',headers={'host':'attacker.example'})
            self.assertEqual(response.status_code,403)

if __name__=='__main__':unittest.main()
