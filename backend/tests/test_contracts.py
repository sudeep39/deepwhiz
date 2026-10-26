import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from finwhiz.api import create_app
from finwhiz.config import ROOT, AppError
from finwhiz.storage import Jobs, Storage, runtime_lock
from worker_fixture import fixture_worker, waiting_worker

BASELINE = ROOT / "specs/001-python-backend/baseline"


def test_frozen_http_contracts(settings):
    with TestClient(create_app(settings, disable_workers=True)) as client:
        for case in json.loads((BASELINE / "http.json").read_text()):
            request = case["request"]
            result = client.request(
                request["method"],
                request["path"],
                content=request["body"],
                headers={"Authorization": "Bearer baseline-password", **request["headers"]},
            )
            assert result.status_code == case["status"], case["name"]
            expected=case['body']
            assert result.json() == expected, case["name"]
            for key, value in case["headers"].items():
                assert result.headers.get(key) == value, (case["name"], key)


def test_upload_restart_provider_isolation_and_sources(settings):
    configured = settings.model_copy(
        update={
            "openai_api_key": "offline",
            "nebius_api_key": "offline",
            "pinecone_api_key": "offline",
        }
    )
    app = create_app(configured, disable_workers=True)
    headers = {"Authorization": "Bearer baseline-password", "X-AI-Provider": "nebius"}
    with TestClient(app) as client:
        result = client.post(
            "/api/uploads",
            headers=headers,
            data={"details": '[{"ticker":"ACME","reportDate":"2025-03-31","form":"10-Q"}]'},
            files={"file_0": ("filing.txt", b"Readable financial context. " * 20)},
        )
        assert result.status_code == 202
        job = result.json()["job"]
        assert job["aiConfig"]["provider"] == "nebius"
        assert set(job["files"][0]) == {"id", "name", "size"}
        source = client.get(f"/api/sources/{job['files'][0]['id']}", headers=headers)
        assert source.status_code == 200 and source.content == b"Readable financial context. " * 20
        app.state.jobs.update(
            job["id"],
            status="ready",
            documents=[
                dict(
                    id="doc",
                    ticker="ACME",
                    company="Acme",
                    reportDate="2025-03-31",
                    form="10-Q",
                    sourceUrl="/api/sources/doc",
                )
            ],
        )
        assert len(client.get("/api/state", headers=headers).json()["documents"]) == 1
        assert not client.get("/api/state", headers={**headers, "X-AI-Provider": "openai"}).json()[
            "documents"
        ]
        before = set(settings.finwhiz_data_dir.iterdir())
        assert (
            client.post(
                "/api/uploads", headers=headers, files={"file_0": ("malicious.exe", b"bad")}
            ).status_code
            == 400
        )
        assert set(settings.finwhiz_data_dir.iterdir()) == before
        app.state.jobs.update(job["id"], status="processing")
    with TestClient(create_app(configured, disable_workers=True)) as client:
        state = client.get("/api/state", headers=headers).json()
        assert state["jobs"][0]["status"] == "queued"


def test_queue_claims_are_atomic_and_retry_requires_failure(tmp_path):
    jobs = Jobs(Storage(tmp_path))
    jobs.add(dict(id="a", status="queued", files=[]))
    with ThreadPoolExecutor(max_workers=4) as executor:
        claimed = list(executor.map(lambda _: jobs.claim(), range(4)))
    assert sum(job is not None for job in claimed) == 1
    with pytest.raises(AppError):
        jobs.retry("a")
    jobs.update("a", status="failed")
    assert jobs.retry("a")["status"] == "queued"
    with runtime_lock(tmp_path):
        with pytest.raises(AppError):
            with runtime_lock(tmp_path):
                pass


def test_validation_auth_and_limits(settings):
    app = create_app(settings, disable_workers=True)
    headers = {"Authorization": "Bearer baseline-password"}
    with TestClient(app) as client:
        for body, error in [
            ({"question": "a", "tickers": []}, "Select at least one company."),
            (
                {"question": "a", "tickers": ["ACME"], "strategy": "invalid"},
                "Invalid retrieval strategy.",
            ),
            (
                {"question": "a", "tickers": ["ACME"], "periods": ["2025-02-30"]},
                "Invalid period selection.",
            ),
        ]:
            assert client.post("/api/chat", headers=headers, json=body).json() == {"error": error}
        assert client.post("/api/chat", headers=headers, content="x" * 32001).status_code == 413
        for _ in range(8):
            client.post("/api/chat", headers=headers, json={})
        assert client.post("/api/chat", headers=headers, json={}).status_code == 429
    with TestClient(
        create_app(
            settings.model_copy(update={"app_password": "", "node_env": "production"}),
            disable_workers=True,
        )
    ) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/state").status_code == 503


@pytest.mark.parametrize("provider_id", ["openai", "nebius"])
def test_real_spawned_worker_publishes_only_verified_ready_state(settings, provider_id):
    configured = settings.model_copy(
        update={
            "openai_api_key": "offline",
            "nebius_api_key": "offline",
            "pinecone_api_key": "offline",
        }
    )
    headers = {"Authorization": "Bearer baseline-password", "X-AI-Provider": provider_id}
    with TestClient(create_app(configured, worker_target=fixture_worker)) as client:
        result = client.post(
            "/api/uploads",
            headers=headers,
            data={"details": '[{"ticker":"ACME","reportDate":"2025-03-31","form":"10-Q"}]'},
            files={
                "file_0": (
                    "filing.txt",
                    b"Revenue was 125 million and operating cash flow was 100 million. " * 10,
                )
            },
        )
        assert result.status_code == 202
        state = None
        for _ in range(200):
            state = client.get("/api/state", headers=headers).json()
            if state["jobs"][0]["status"] in ("ready", "failed"):
                break
            assert state["documents"] == []
            time.sleep(0.1)
        assert state["jobs"][0]["status"] == "ready", state
        assert len(state["documents"]) == 1
        assert set(state["jobs"][0]["namespaces"]) == {"fixed", "semantic"}


def test_chat_backpressure(settings):
    from test_workflows import setup_job
    from finwhiz.ingestion import ingest
    from finwhiz.schemas import Answer

    store, jobs, job, provider = setup_job(settings)
    ingest(job, store, jobs, provider)
    entered, release = threading.Barrier(3), threading.Event()
    original = provider.structured

    def blocking(system, user, schema, model=None):
        if schema is Answer:
            entered.wait(timeout=10)
            release.wait(timeout=10)
        return original(system, user, schema, model)

    provider.structured = blocking
    headers = {"Authorization": "Bearer baseline-password"}
    with TestClient(
        create_app(settings, disable_workers=True, provider_factory=lambda *_: provider)
    ) as client:

        def chat():
            return client.post(
                "/api/chat",
                headers=headers,
                json=dict(question="What revenue was reported?", tickers=["ACME"]),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = [executor.submit(chat) for _ in range(2)]
            try:
                entered.wait(timeout=10)
                rejected = chat()
                assert rejected.status_code == 429
                assert "Two questions" in rejected.json()["error"]
            finally:
                release.set()
            assert all(future.result().status_code == 200 for future in pending)


def test_upload_boundaries_and_queue_backpressure(settings):
    configured = settings.model_copy(
        update={"openai_api_key": "offline", "pinecone_api_key": "offline"}
    )
    app = create_app(configured, disable_workers=True)
    headers = {"Authorization": "Bearer baseline-password"}
    with TestClient(app) as client:
        oversized = client.post(
            "/api/uploads",
            headers=headers,
            files={"file_0": ("large.txt", b"x" * (20 * 1024 * 1024 + 1))},
        )
        assert oversized.status_code == 400
        assert "20 MB" in oversized.json()["error"]
        too_many = client.post(
            "/api/uploads",
            headers=headers,
            files=[(f"file_{i}", ("small.txt", b"test")) for i in range(33)],
        )
        assert too_many.status_code == 400
        assert not app.state.jobs.all()
        for number in range(3):
            assert (
                client.post(
                    "/api/uploads", headers=headers, files={"file_0": ("small.txt", f"test {number}".encode())}
                ).status_code
                == 202
            )
        assert (
            client.post(
                "/api/uploads", headers=headers, files={"file_0": ("small.txt", b"new content")}
            ).status_code
            == 429
        )
        assert len(app.state.jobs.all()) == 3


@pytest.mark.parametrize("timeout", [False, True])
def test_worker_deadline_and_shutdown_preserve_recovery(settings, timeout):
    configured = settings.model_copy(
        update={"openai_api_key": "offline", "pinecone_api_key": "offline"}
    )
    app = create_app(
        configured,
        worker_target=waiting_worker,
        processing_timeout_seconds=0.5 if timeout else 1800,
    )
    headers = {"Authorization": "Bearer baseline-password"}
    with TestClient(app) as client:
        result = client.post(
            "/api/uploads", headers=headers, files={"file_0": ("small.txt", b"test")}
        )
        assert result.status_code == 202
        wanted = "failed" if timeout else "processing"
        for _ in range(100):
            state = client.get("/api/state", headers=headers).json()
            if state["jobs"][0]["status"] == wanted:
                break
            time.sleep(0.05)
        assert state["jobs"][0]["status"] == wanted
        assert state["documents"] == []
    if timeout:
        assert app.state.jobs.all()[0]["status"] == "failed"
    else:
        with TestClient(create_app(configured, disable_workers=True)) as client:
            assert client.get("/api/state", headers=headers).json()["jobs"][0]["status"] == "queued"
