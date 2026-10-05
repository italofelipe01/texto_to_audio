"""FastAPI application: REST API + single page interface."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import shutil
import tempfile
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..config import Settings
from ..engines import engine_names, get_engine
from ..ffmpeg import FORMATS, VIDEO_SIZES
from ..pipeline import Pipeline, PipelineError
from ..presets import PRESETS, JobOptions
from ..text import SUPPORTED_EXTENSIONS, parse_lexicon
from .jobs import JOB_ID_RE, TERMINAL, JobManager

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ASSETS = {
    "music": AUDIO_EXTENSIONS,
    "intro": AUDIO_EXTENSIONS,
    "outro": AUDIO_EXTENSIONS,
    "cover": IMAGE_EXTENSIONS,
    "background": IMAGE_EXTENSIONS,
}
LANGUAGES = [
    ("pt-BR", "Português (Brasil)"),
    ("pt-PT", "Português (Portugal)"),
    ("en-US", "Inglês (EUA)"),
    ("es-ES", "Espanhol"),
    ("fr-FR", "Francês"),
]
mimetypes.add_type("audio/mp4", ".m4b")
mimetypes.add_type("audio/ogg", ".opus")
mimetypes.add_type("text/vtt", ".vtt")
mimetypes.add_type("application/x-subrip", ".srt")


class PreviewRequest(BaseModel):
    engine: str = "auto"
    voice: str | None = None
    lang: str = "pt-BR"
    rate: int = 0
    pitch: float = 0.0
    text: str | None = Field(default=None, max_length=400)


async def _save_upload(upload: UploadFile, dest: Path, max_bytes: int) -> Path:
    size = 0
    with dest.open("wb") as fh:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    413,
                    f"Arquivo '{upload.filename}' excede o limite de "
                    f"{max_bytes // (1024 * 1024)} MB.",
                )
            fh.write(chunk)
    return dest


def create_app(settings: Settings | None = None, pipeline: Pipeline | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    pipeline = pipeline or Pipeline(settings)
    manager = JobManager(settings, pipeline)
    preview_dir = Path(settings.cache_dir) / "previews"

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        manager.shutdown()

    app = FastAPI(
        title="Texto para Áudio",
        version=__version__,
        description="Vozes neurais gratuitas + pós-produção com FFmpeg.",
        lifespan=lifespan,
    )
    app.state.manager = manager
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def get_job_or_404(job_id: str):
        job = manager.get(job_id) if JOB_ID_RE.match(job_id) else None
        if not job:
            raise HTTPException(404, "Job não encontrado.")
        return job

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/capabilities")
    def capabilities():
        caps = pipeline.ff.capabilities()
        return {
            "version": __version__,
            "ffmpeg": caps,
            "engines": [get_engine(n, settings).info() for n in engine_names()],
            "formats": [
                {"key": k, "label": f.label, "available": caps["formats"].get(k, False)}
                for k, f in FORMATS.items()
            ],
            "presets": [p.to_dict() for p in PRESETS.values()],
            "video_sizes": {k: list(v) for k, v in VIDEO_SIZES.items()},
            "languages": [{"code": c, "label": label} for c, label in LANGUAGES],
            "extensions": list(SUPPORTED_EXTENSIONS),
            "limits": {"max_chars": settings.max_chars, "max_upload_mb": settings.max_upload_mb},
            "defaults": JobOptions().to_dict(),
        }

    @app.get("/api/voices")
    def voices(engine: str = "auto", lang: str = "pt"):
        names = engine_names() if engine == "auto" else [engine]
        result = []
        for name in names:
            try:
                eng = get_engine(name, settings)
            except Exception as exc:
                raise HTTPException(400, str(exc)) from exc
            if not eng.check()[0]:
                continue
            result += [v.to_dict() for v in eng.list_voices(None if lang == "all" else lang)]
        return {"voices": result}

    @app.post("/api/preview")
    def preview(req: PreviewRequest):
        text = (req.text or "").strip() or (
            "Olá! Esta é uma amostra da voz escolhida para o seu áudio."
            if req.lang.lower().startswith("pt")
            else "Hello! This is a sample of the selected voice."
        )
        options = JobOptions.from_dict(
            {
                "preset": "rascunho",
                "engine": req.engine,
                "voice": req.voice,
                "lang": req.lang,
                "rate": req.rate,
                "pitch": req.pitch,
                "speak_titles": False,
            }
        )
        key = pipeline.cache.key(
            "preview",
            options.engine,
            options.voice,
            options.lang,
            options.rate,
            options.pitch,
            text,
        )
        target = preview_dir / f"{key}.mp3"
        headers = {"Cache-Control": "no-store"}
        if not target.exists():
            preview_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=preview_dir) as tmp:
                try:
                    result = pipeline.run(options=options, output_dir=Path(tmp), text=text)
                except PipelineError as exc:
                    raise HTTPException(502, str(exc)) from exc
                shutil.move(str(result.path("mp3")), target)
                target.with_suffix(".json").write_text(
                    json.dumps(
                        {"voice": result.report["voice"], "engine": result.report["engine"]}
                    ),
                    encoding="utf-8",
                )
        meta_path = target.with_suffix(".json")
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            headers["X-TTA-Voice"] = meta.get("voice", "")
            headers["X-TTA-Engine"] = meta.get("engine", "")
        return FileResponse(target, media_type="audio/mpeg", headers=headers)

    @app.post("/api/jobs", status_code=202)
    async def create_job(
        options: str = Form("{}"),
        text: str | None = Form(None),
        lexicon: str | None = Form(None),
        file: UploadFile | None = File(None),
        music: UploadFile | None = File(None),
        intro: UploadFile | None = File(None),
        outro: UploadFile | None = File(None),
        cover: UploadFile | None = File(None),
        background: UploadFile | None = File(None),
    ):
        try:
            data = json.loads(options or "{}")
            if not isinstance(data, dict):
                raise ValueError
        except ValueError as exc:
            raise HTTPException(400, "Campo 'options' deve ser um objeto JSON.") from exc
        text = (text or "").strip() or None
        has_file = file is not None and bool(file.filename)
        if not text and not has_file:
            raise HTTPException(400, "Envie um texto ou um arquivo.")
        if text and len(text) > settings.max_chars:
            raise HTTPException(413, f"Texto excede {settings.max_chars} caracteres.")
        if has_file:
            ext = Path(file.filename).suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                raise HTTPException(
                    415, f"Formato '{ext}' não suportado. Use: {', '.join(SUPPORTED_EXTENSIONS)}"
                )

        max_bytes = settings.max_upload_mb * 1024 * 1024
        job = manager.create(
            title=data.get("title") or None,
            source_name=file.filename if has_file else None,
            text=text,
        )
        inputs = manager.inputs_dir(job.id)
        try:
            source = None
            if has_file:
                source = await _save_upload(file, inputs / f"documento{ext}", max_bytes)
            uploads = {
                "music": music,
                "intro": intro,
                "outro": outro,
                "cover": cover,
                "background": background,
            }
            for key, upload in uploads.items():
                data.pop(key, None)  # never trust paths coming from the client
                if upload is None or not upload.filename:
                    continue
                suffix = Path(upload.filename).suffix.lower()
                if suffix not in ASSETS[key]:
                    raise HTTPException(415, f"Arquivo inválido para '{key}': {upload.filename}")
                data[key] = str(await _save_upload(upload, inputs / f"{key}{suffix}", max_bytes))
            if lexicon:
                data["lexicon"] = parse_lexicon(lexicon)
            job_options = JobOptions.from_dict(data)
        except HTTPException:
            manager.delete(job.id)
            raise
        manager.submit(job, job_options, text=text, source=source)
        return job.to_dict()

    @app.get("/api/jobs")
    def list_jobs():
        return {"jobs": [job.summary() for job in manager.list()]}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return get_job_or_404(job_id).to_dict()

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request):
        job = get_job_or_404(job_id)

        async def stream():
            last_version = -1
            idle = 0.0
            while True:
                if await request.is_disconnected():
                    break
                current = manager.get(job.id)
                if current is None:
                    yield "event: deleted\ndata: {}\n\n"
                    break
                if current.version != last_version:
                    last_version = current.version
                    idle = 0.0
                    yield f"data: {json.dumps(current.to_dict(), ensure_ascii=False)}\n\n"
                    if current.status in TERMINAL:
                        break
                elif idle >= 15:
                    idle = 0.0
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.4)
                idle += 0.4

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        get_job_or_404(job_id)
        return manager.cancel(job_id).to_dict()

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str):
        get_job_or_404(job_id)
        manager.delete(job_id)
        return {"deleted": job_id}

    @app.get("/api/jobs/{job_id}/files/{name}")
    def job_file(job_id: str, name: str, download: bool = False):
        job = get_job_or_404(job_id)
        if name not in job.files.values():
            raise HTTPException(404, "Arquivo não encontrado.")
        path = manager.output_dir(job_id) / name
        if not path.is_file():
            raise HTTPException(404, "Arquivo não encontrado.")
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return FileResponse(
            path,
            media_type=media_type,
            filename=name if download else None,
            content_disposition_type="attachment" if download else "inline",
        )

    @app.get("/api/jobs/{job_id}/zip")
    def job_zip(job_id: str):
        job = get_job_or_404(job_id)
        if job.status != "done":
            raise HTTPException(409, "O job ainda não terminou.")
        out = manager.output_dir(job_id)
        base = (job.report or {}).get("basename", job_id)
        archive = manager.job_dir(job_id) / f"{base}.zip"
        if not archive.exists():
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
                for name in job.files.values():
                    if (out / name).is_file():
                        zf.write(out / name, arcname=name)
        return FileResponse(archive, media_type="application/zip", filename=archive.name)

    @app.exception_handler(PipelineError)
    async def pipeline_error_handler(_request: Request, exc: PipelineError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app
