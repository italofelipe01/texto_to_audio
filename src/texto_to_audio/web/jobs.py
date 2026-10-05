"""Background job queue used by the web interface (persisted to disk)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..config import Settings
from ..pipeline import JobCancelled, Pipeline, PipelineError
from ..presets import JobOptions

logger = logging.getLogger(__name__)

TERMINAL = {"done", "error", "cancelled"}
JOB_ID_RE = re.compile(r"^[a-f0-9]{12}$")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    title: str | None = None
    status: str = "queued"
    progress: float = 0.0
    stage: str = "Na fila"
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    source_name: str | None = None
    text_preview: str | None = None
    options: dict = field(default_factory=dict)
    error: str | None = None
    files: dict[str, str] = field(default_factory=dict)
    sizes: dict[str, int] = field(default_factory=dict)
    report: dict | None = None
    version: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> dict:
        data = {k: v for k, v in self.to_dict().items() if k not in ("options", "report")}
        if self.report:
            data["duration"] = self.report.get("duration")
            data["voice"] = self.report.get("voice")
        return data


class JobManager:
    def __init__(self, settings: Settings, pipeline: Pipeline) -> None:
        self.settings = settings
        self.pipeline = pipeline
        self.root = Path(settings.jobs_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        self._last_save: dict[str, float] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=settings.max_jobs, thread_name_prefix="tta-job"
        )
        self.load()
        self.cleanup()

    # -- paths ---------------------------------------------------------------- #

    def job_dir(self, job_id: str) -> Path:
        if not JOB_ID_RE.match(job_id):
            raise KeyError(job_id)
        return self.root / job_id

    def inputs_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "entrada"

    def output_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "saida"

    # -- persistence ------------------------------------------------------------ #

    def save(self, job: Job, *, force: bool = True) -> None:
        if job.id not in self.jobs:  # deleted while running
            return
        now = time.monotonic()
        if not force and now - self._last_save.get(job.id, 0) < 1.0:
            return
        self._last_save[job.id] = now
        path = self.job_dir(job.id) / "job.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp{threading.get_ident()}")
        tmp.write_text(json.dumps(job.to_dict(), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def load(self) -> None:
        for path in self.root.glob("*/job.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                job = Job(**{k: v for k, v in data.items() if k in Job.__dataclass_fields__})
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            self.jobs[job.id] = job
            if job.status not in TERMINAL:
                job.status = "error"
                job.error = "Interrompido: o servidor foi reiniciado durante o processamento."
                job.stage = "Interrompido"
                self.save(job)

    def cleanup(self) -> int:
        """Remove finished jobs older than ``TTA_JOB_TTL_DAYS`` (0 disables)."""
        if self.settings.job_ttl_days <= 0:
            return 0
        limit = datetime.now(UTC) - timedelta(days=self.settings.job_ttl_days)
        removed = 0
        for job in list(self.jobs.values()):
            stamp = job.finished_at or job.created_at
            try:
                when = datetime.fromisoformat(stamp)
            except (TypeError, ValueError):
                continue
            if job.status in TERMINAL and when < limit:
                self.delete(job.id)
                removed += 1
        return removed

    # -- lifecycle ---------------------------------------------------------------- #

    def create(self, *, title: str | None, source_name: str | None, text: str | None) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], title=title, source_name=source_name)
        if text:
            job.text_preview = text.strip()[:280]
        self.inputs_dir(job.id).mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.jobs[job.id] = job
        return job

    def submit(
        self, job: Job, options: JobOptions, *, text: str | None, source: Path | None
    ) -> Job:
        job.options = options.to_dict()
        job.title = job.title or options.title
        self._cancel[job.id] = threading.Event()
        self._update(job)
        self._executor.submit(self._run, job, options, text, source)
        self.cleanup()
        return job

    def _update(self, job: Job, *, force: bool = True, **changes) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(job, key, value)
            job.version += 1
        self.save(job, force=force)

    def _run(self, job: Job, options: JobOptions, text: str | None, source: Path | None) -> None:
        cancel = self._cancel.get(job.id) or threading.Event()
        if cancel.is_set():
            self._update(job, status="cancelled", stage="Cancelado", finished_at=_now())
            return
        self._update(job, status="running", stage="Iniciando", started_at=_now())

        def progress(fraction: float, message: str) -> None:
            self._update(job, force=fraction >= 1, progress=round(fraction, 4), stage=message)

        try:
            result = self.pipeline.run(
                options=options,
                output_dir=self.output_dir(job.id),
                text=text,
                source=source,
                progress=progress,
                cancel=cancel,
            )
        except JobCancelled:
            self._update(job, status="cancelled", stage="Cancelado", finished_at=_now())
            return
        except PipelineError as exc:
            self._update(job, status="error", stage="Falhou", error=str(exc), finished_at=_now())
            return
        except Exception as exc:  # pragma: no cover - unexpected bugs
            logger.exception("Erro inesperado no job %s", job.id)
            self._update(
                job,
                status="error",
                stage="Falhou",
                error=f"Erro inesperado: {exc}",
                finished_at=_now(),
            )
            return
        sizes = {}
        for kind, name in result.files.items():
            with contextlib.suppress(OSError):
                sizes[kind] = (result.output_dir / name).stat().st_size
        self._update(
            job,
            status="done",
            progress=1.0,
            stage="Concluído",
            files=result.files,
            sizes=sizes,
            report=result.report,
            title=result.report.get("title") or job.title,
            finished_at=_now(),
        )

    # -- queries / actions ----------------------------------------------------------- #

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

    def cancel(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if job and job.status not in TERMINAL:
            self._cancel.setdefault(job_id, threading.Event()).set()
            if job.status == "queued":
                self._update(job, status="cancelled", stage="Cancelado", finished_at=_now())
        return job

    def delete(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        self.cancel(job_id)
        with self._lock:
            self.jobs.pop(job_id, None)
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)
        return True

    def shutdown(self) -> None:
        for event in self._cancel.values():
            event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
