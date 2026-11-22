"""Local HTTP routes for dubbing projects, AI voices and ADR recordings."""

import asyncio
import logging
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from f5_tts.studio.adr_audio import MEDIA_INPUTS, TAKE_LIMIT
from f5_tts.studio.dubbing_review import ExportRequest, VoiceRequest


class TakeChoice(BaseModel):
    take_id: UUID | None = None


def register_dubbing_routes(app, projects):
    def project(project_id):
        if project_id not in projects:
            raise HTTPException(404, "Projeto de dublagem não encontrado.")
        return projects[project_id]

    def invoke(operation, *args):
        try:
            return operation(*args)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except Exception as error:
            logging.getLogger(__name__).exception("Dubbing operation failed")
            raise HTTPException(500, "A operação de dublagem falhou. Confira o log do Studio.") from error

    @app.post("/api/dubbing/{project_id}/segments/{segment_id}/regenerate")
    async def regenerate(project_id: UUID, segment_id: UUID, request: VoiceRequest):
        current = project(project_id)
        audio, duration = await asyncio.to_thread(invoke, current.regenerate, segment_id, request)
        return {"audio_url": current.url(audio), "duration": duration}

    @app.post("/api/dubbing/{project_id}/export")
    async def export(project_id: UUID, request: ExportRequest):
        current = project(project_id)
        video, captions, _ = await asyncio.to_thread(invoke, current.export, request)
        return {"video_url": current.url(video), "captions_url": current.url(captions)}

    @app.get("/api/dubbing/{project_id}/files/{filename}")
    async def media(project_id: UUID, filename: str):
        path = project(project_id)._files.get(filename)
        if path is None or not path.is_file():
            raise HTTPException(404, "Arquivo de dublagem não encontrado.")
        return FileResponse(path, stat_result=path.stat())

    @app.post("/api/dubbing/{project_id}/segments/{segment_id}/takes")
    async def upload_take(project_id: UUID, segment_id: UUID, request: Request, capture_offset: int = 0):
        current = project(project_id)
        if segment_id not in current.rows:
            raise HTTPException(404, "Esta fala não pertence ao projeto.")
        media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if media_type not in MEDIA_INPUTS or not -1000 <= capture_offset <= 1000:
            raise HTTPException(422, "Formato ou sincronização da gravação inválidos.")
        with tempfile.TemporaryDirectory(prefix="recording-", dir=current.directory) as folder:
            source = Path(folder) / "recording"
            size = 0
            with source.open("wb") as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > TAKE_LIMIT:
                        raise HTTPException(413, "A gravação excede 20 MB.")
                    output.write(chunk)
            if size == 0:
                raise HTTPException(422, "A gravação está vazia.")
            take = await asyncio.to_thread(invoke, current.store_take, segment_id, source, media_type, capture_offset)
        return {"id": str(take.id), "audio_url": current.url(take.path), "duration": take.duration}

    @app.patch("/api/dubbing/{project_id}/segments/{segment_id}/takes")
    async def choose_take(project_id: UUID, segment_id: UUID, request: TakeChoice):
        current = project(project_id)
        await asyncio.to_thread(invoke, current.choose_take, segment_id, request.take_id)
        return {"take_id": request.take_id}

    @app.delete("/api/dubbing/{project_id}/segments/{segment_id}/takes/{take_id}")
    async def delete_take(project_id: UUID, segment_id: UUID, take_id: UUID):
        current = project(project_id)
        await asyncio.to_thread(invoke, current.forget_take, segment_id, take_id)
        return {"deleted": True}
