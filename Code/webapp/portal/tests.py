"""Web app tests (local mode, mock services, temporary data folder).

    cd Code/webapp && python manage.py test
"""
import shutil
import tempfile

from django.test import SimpleTestCase, override_settings

from callcenter import pipeline, samples
from callcenter.config import get_settings, reset_settings_cache
from callcenter.factory import get_blob_store


@override_settings(ALLOWED_HOSTS=["testserver"], AUTH_MODE="dev", CRM_API_KEYS=["test-key"])
class PortalTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import os

        cls.tmp = tempfile.mkdtemp()
        cls._env = os.environ.get("LOCAL_DATA_DIR")
        os.environ["LOCAL_DATA_DIR"] = cls.tmp
        reset_settings_cache()
        s = get_settings()
        cls.calls = samples.generate_calls(count=8, days=5, seed=11)
        for call_id, meta, wav in cls.calls:
            get_blob_store().put_json(s.container_recordings, f"{call_id}.json", meta)
            get_blob_store().put_bytes(s.container_recordings, f"{call_id}.wav", wav)
            pipeline.process_recording(f"{call_id}.wav")

    @classmethod
    def tearDownClass(cls):
        import os

        if cls._env is None:
            os.environ.pop("LOCAL_DATA_DIR", None)
        else:
            os.environ["LOCAL_DATA_DIR"] = cls._env
        reset_settings_cache()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def login(self, user_id):
        response = self.client.post("/auth/login", {"user": user_id})
        self.assertEqual(response.status_code, 302)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get("/calls/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response["Location"])

    def test_admin_pages_render(self):
        self.login("dev-admin")
        call_id = self.calls[0][0]
        for url in ["/", "/calls/", "/calls/?sentiment=negative&sort=qa", f"/calls/{call_id}/", "/agents/",
                    "/agents/A1001/", "/insights/", "/pipeline/", "/settings/"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_htmx_returns_partial(self):
        self.login("dev-admin")
        response = self.client.get("/calls/?q=CALL", HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<html")

    def test_audio_supports_range_requests(self):
        self.login("dev-admin")
        response = self.client.get(f"/calls/{self.calls[0][0]}/audio", HTTP_RANGE="bytes=0-99")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Length"], "100")
        self.assertTrue(response["Content-Range"].startswith("bytes 0-99/"))

    def test_agent_only_sees_own_calls(self):
        agent_id = self.calls[0][1]["agent_id"]
        self.login(f"dev-{agent_id}")
        own = [c for c, m, _ in self.calls if m["agent_id"] == agent_id]
        other = [c for c, m, _ in self.calls if m["agent_id"] != agent_id]
        self.assertEqual(self.client.get(f"/calls/{own[0]}/").status_code, 200)
        if other:
            self.assertEqual(self.client.get(f"/calls/{other[0]}/").status_code, 403)
        for url in ["/agents/", "/insights/", "/pipeline/", "/settings/"]:
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get("/agents/me/").status_code, 200)

    def test_settings_rejects_invalid_yaml(self):
        self.login("dev-admin")
        response = self.client.post("/settings/", {"file": "qa_rubric.yaml", "content": "criteria: []"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not saved")

    def test_crm_api_key(self):
        self.assertEqual(self.client.get("/api/calls").status_code, 401)
        response = self.client.get("/api/calls", HTTP_X_API_KEY="test-key")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], len(self.calls))
        detail = self.client.get(f"/api/calls/{self.calls[0][0]}", HTTP_X_API_KEY="test-key").json()
        self.assertIn("transcript", detail)
        self.assertNotIn("history", detail)

    def test_upload_processes_file(self):
        self.login("dev-supervisor")
        from django.core.files.uploadedfile import SimpleUploadedFile

        wav = samples.make_wav(3000)
        response = self.client.post("/pipeline/upload", {
            "audio": SimpleUploadedFile("test.wav", wav, content_type="audio/wav"),
            "call_id": "UPLOAD-TEST-1", "agent_id": "A1001", "agent_name": "Priya Sharma",
        })
        self.assertEqual(response.status_code, 302)
        from callcenter.factory import get_dispatcher

        get_dispatcher().pool.shutdown(wait=True)  # wait for background processing
        from callcenter.factory import get_call_store

        self.assertEqual(get_call_store().get("UPLOAD-TEST-1")["status"], "completed")


@override_settings(ALLOWED_HOSTS=["testserver"], AUTH_MODE="password", DEMO_PASSWORD="s3cret-demo")
class DemoPasswordTests(SimpleTestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_wrong_password_refused_then_locked(self):
        for _ in range(5):
            self.assertEqual(self.client.post("/auth/login", {"user": "dev-admin", "password": "nope"}).status_code, 403)
        response = self.client.post("/auth/login", {"user": "dev-admin", "password": "s3cret-demo"})
        self.assertEqual(response.status_code, 429)

    def test_right_password_signs_in(self):
        response = self.client.post("/auth/login", {"user": "dev-qa", "password": "s3cret-demo"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

    @override_settings(DEMO_PASSWORD="")
    def test_no_password_configured_refuses_sign_in(self):
        self.assertContains(self.client.get("/auth/login"), "DEMO_PASSWORD is empty")
        response = self.client.post("/auth/login", {"user": "dev-admin", "password": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 302)
