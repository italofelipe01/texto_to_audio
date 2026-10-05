import time

import pytest

from conftest import make_tone, needs_ffmpeg

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from texto_to_audio.web.app import create_app  # noqa: E402


@pytest.fixture
def client(settings, fake_engine):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def wait_done(client, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.2)
    raise AssertionError("job não terminou a tempo")


def test_index_and_static(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Texto para Áudio" in page.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"


def test_capabilities_and_voices(client):
    caps = client.get("/api/capabilities").json()
    assert any(e["name"] == "fake" for e in caps["engines"])
    assert {p["name"] for p in caps["presets"]} >= {"audiobook", "podcast", "video"}
    assert caps["defaults"]["formats"] == ["mp3"]
    voices = client.get("/api/voices", params={"engine": "fake", "lang": "pt"}).json()["voices"]
    assert voices[0]["id"] == "fake-voice"


def test_validation_errors(client):
    assert client.post("/api/jobs", data={"options": "{}"}).status_code == 400
    assert client.post("/api/jobs", data={"options": "[1]", "text": "x"}).status_code == 400
    bad = client.post("/api/jobs", data={"text": "x"}, files={"file": ("a.exe", b"MZ")})
    assert bad.status_code == 415
    assert client.get("/api/jobs/../../etc").status_code == 404
    assert client.get("/api/jobs/zzzzzzzzzzzz").status_code == 404


@needs_ffmpeg
def test_job_lifecycle_with_upload_and_assets(client, tmp_path):
    music = make_tone(tmp_path / "m.wav", 0.5)
    response = client.post(
        "/api/jobs",
        data={
            "options": '{"engine": "fake", "formats": ["mp3"], "music": "/etc/passwd"}',
            "lexicon": "TTS = tê tê ésse",
        },
        files={
            "file": ("roteiro.md", b"# Roteiro\n\nO TTS funciona.", "text/markdown"),
            "music": ("trilha.wav", music.read_bytes(), "audio/wav"),
        },
    )
    assert response.status_code == 202, response.text
    job = wait_done(client, response.json()["id"])
    assert job["status"] == "done", job["error"]
    assert job["options"]["music"].endswith("music.wav")  # client paths are ignored
    assert job["options"]["lexicon"] == {"TTS": "tê tê ésse"}
    assert job["report"]["channels"] == 2

    audio = client.get(f"/api/jobs/{job['id']}/files/{job['files']['mp3']}")
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/mpeg"
    ranged = client.get(
        f"/api/jobs/{job['id']}/files/{job['files']['mp3']}", headers={"Range": "bytes=0-99"}
    )
    assert ranged.status_code == 206
    download = client.get(
        f"/api/jobs/{job['id']}/files/{job['files']['srt']}", params={"download": True}
    )
    assert "attachment" in download.headers["content-disposition"]
    assert client.get(f"/api/jobs/{job['id']}/files/job.json").status_code == 404
    archive = client.get(f"/api/jobs/{job['id']}/zip")
    assert archive.status_code == 200 and archive.content[:2] == b"PK"

    events = client.get(f"/api/jobs/{job['id']}/events")
    assert '"status": "done"' in events.text

    listing = client.get("/api/jobs").json()["jobs"]
    assert listing[0]["id"] == job["id"] and "options" not in listing[0]
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


@needs_ffmpeg
def test_preview_returns_mp3(client):
    response = client.post("/api/preview", json={"engine": "fake", "text": "Teste de amostra."})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["x-tta-engine"] == "fake"
    again = client.post("/api/preview", json={"engine": "fake", "text": "Teste de amostra."})
    assert again.content == response.content


def test_jobs_survive_restart(settings, fake_engine):
    from texto_to_audio.pipeline import Pipeline
    from texto_to_audio.web.jobs import JobManager

    manager = JobManager(settings, Pipeline(settings))
    job = manager.create(title="x", source_name=None, text="oi")
    job.status = "running"
    manager.save(job)
    manager.shutdown()
    reloaded = JobManager(settings, Pipeline(settings)).get(job.id)
    assert reloaded.status == "error"
    assert "reiniciado" in reloaded.error
