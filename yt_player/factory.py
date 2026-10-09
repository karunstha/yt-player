import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from yt_player.core.config import DATABASE_PATH, DEFAULT_BLUETOOTH_DEVICE_ID
from yt_player.core.errors import ServiceError
from yt_player.events.api import register_events_routes
from yt_player.events.service import EventService
from yt_player.player.backend import FfplayBackend
from yt_player.player.api import register_player_routes
from yt_player.player.controller import PlaybackController
from yt_player.player.legacy_api import register_legacy_routes
from yt_player.player.queue_api import register_queue_routes
from yt_player.player.queue_service import QueueService
from yt_player.player.service import PlaybackService
from yt_player.playlists.api import register_playlist_routes
from yt_player.playlists.repository import PlaylistRepository
from yt_player.playlists.service import PlaylistService
from yt_player.sources.api import register_search_routes
from yt_player.sources.youtube import YouTubeService


def create_app(
    *,
    repository: PlaylistRepository | None = None,
    youtube_service: YouTubeService | None = None,
    playback_backend: FfplayBackend | None = None,
    connect_bluetooth_on_startup: bool = True,
) -> FastAPI:
    playlist_repository = repository or PlaylistRepository(DATABASE_PATH)
    event_service = EventService()
    queue_service = QueueService()
    playlist_service = PlaylistService(playlist_repository)
    playback_service = PlaybackService(playback_backend)
    controller = PlaybackController(
        youtube_service=youtube_service or YouTubeService(),
        playback_service=playback_service,
        queue_service=queue_service,
        playlist_service=playlist_service,
        event_service=event_service,
    )

    sync_task: asyncio.Task | None = None

    async def sync_loop() -> None:
        while True:
            await asyncio.sleep(3)
            await controller.reconcile_playback()
            state = await controller.get_state()
            if state.state == "playing":
                await event_service.broadcast("playback.sync", state.model_dump(mode="json"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal sync_task
        if (
            connect_bluetooth_on_startup
            and DEFAULT_BLUETOOTH_DEVICE_ID
            and DEFAULT_BLUETOOTH_DEVICE_ID.strip()
            and hasattr(playback_service.backend, "try_connect_bluetooth")
        ):
            try:
                await asyncio.to_thread(
                    playback_service.backend.try_connect_bluetooth,
                    DEFAULT_BLUETOOTH_DEVICE_ID.strip(),
                )
            except Exception as exc:
                print(f"Startup bluetooth connect failed: {exc}")
        sync_task = asyncio.create_task(sync_loop())
        yield
        if sync_task is not None:
            sync_task.cancel()
            try:
                await sync_task
            except asyncio.CancelledError:
                pass
        await controller.shutdown()
        playlist_repository.close()

    app = FastAPI(title="Media Playback Service", lifespan=lifespan)
    app.state.controller = controller
    app.state.playlists = playlist_service
    app.state.events = event_service

    register_error_handlers(app)
    register_events_routes(app, controller=controller, event_service=event_service)
    register_search_routes(app, controller=controller)
    register_player_routes(app, controller=controller)
    register_queue_routes(app, controller=controller, queue_service=queue_service)
    register_playlist_routes(
        app,
        controller=controller,
        playlist_service=playlist_service,
        event_service=event_service,
    )
    register_legacy_routes(app, controller=controller)

    return app


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def service_error_handler(_request, exc: ServiceError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Request validation failed.",
                        "details": exc.errors(),
                    }
                }
            ),
        )
