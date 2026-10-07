"""HTTP surface for workflow V4.5 steps 05/C3 (routes and cost) and 06/C4 (dialogue takes and timing).

Session policy comes from the owning app.
"""
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from .dialogue_service import DialogueService
from .route_service import RouteService
from .timing_service import TimingService


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RouteSettingsIn(Strict):
    expected_revision: int
    main_characters: list[str]
    prices: dict


class DecideIn(Strict):
    shot_id: str
    expected_revision: int
    route: str
    video_model: str | None = None
    locked: bool


class LockManyIn(Strict):
    expected_plan_hash: str
    scene_key: str | None = None


class ExpectedHashIn(Strict):
    expected_hash: str


class C4In(ExpectedHashIn):
    impact_hash: str | None = None


class LanguageIn(Strict):
    expected_revision: int
    language: str


class LineIn(Strict):
    expected_revision: int
    spoken_text: str | None = None
    settings: dict


class GenerateIn(Strict):
    line_ids: list[str] | None = None
    client_request_id: str


class DecisionIn(Strict):
    decision: str


class ProposeIn(Strict):
    expected_hash: str | None = None


class ShotTimingIn(Strict):
    expected_hash: str
    length_frames: int
    pauses: dict[str, int]
    cues: list[dict]


class AuditionIn(Strict):
    expected_hash: str
    scene_key: str | None = None


def install(app, routes: RouteService, dialogue: DialogueService, timing: TimingService):
    @app.get('/projects/{project_id}/route-settings')
    def route_settings(project_id: str):
        return routes.settings(project_id)

    @app.post('/projects/{project_id}/route-settings')
    def save_route_settings(project_id: str, body: RouteSettingsIn):
        return routes.save_settings(project_id, **body.model_dump())

    @app.get('/episodes/{episode_id}/routes')
    def route_plan(episode_id: str):
        return routes.plan(episode_id)

    @app.post('/episodes/{episode_id}/routes/decide')
    def decide_route(episode_id: str, body: DecideIn):
        return routes.decide(episode_id, **body.model_dump())

    @app.post('/episodes/{episode_id}/routes/lock')
    def lock_routes(episode_id: str, body: LockManyIn):
        return routes.lock_many(episode_id, body.expected_plan_hash, body.scene_key)

    @app.post('/episodes/{episode_id}/c3/approve')
    def approve_c3(episode_id: str, body: ExpectedHashIn):
        return routes.approve_c3(episode_id, body.expected_hash)

    @app.get('/episodes/{episode_id}/dialogue')
    def dialogue_state(episode_id: str):
        return dialogue.state(episode_id)

    @app.post('/episodes/{episode_id}/dialogue/language')
    def dialogue_language(episode_id: str, body: LanguageIn):
        return dialogue.save_language(episode_id, body.expected_revision, body.language)

    @app.post('/episodes/{episode_id}/dialogue/lines/{line_id}')
    def save_line(episode_id: str, line_id: str, body: LineIn):
        return dialogue.save_line(episode_id, line_id, body.expected_revision, body.spoken_text, body.settings)

    @app.post('/episodes/{episode_id}/dialogue/generate')
    def generate_dialogue(episode_id: str, body: GenerateIn):
        return dialogue.enqueue(episode_id, body.line_ids, body.client_request_id)

    @app.post('/episodes/{episode_id}/dialogue/scenes/{scene_key}/approve')
    def approve_scene_takes(episode_id: str, scene_key: str):
        return dialogue.approve_scene(episode_id, scene_key)

    @app.post('/dialogue/takes/{take_id}/review')
    def review_take(take_id: str, body: DecisionIn):
        return dialogue.review_take(take_id, body.decision)

    @app.get('/dialogue/takes/{take_id}/file')
    def take_file(take_id: str):
        return FileResponse(dialogue.take_file(take_id), media_type='audio/wav', headers={'Cache-Control': 'no-store'})

    @app.get('/episodes/{episode_id}/timing')
    def timing_state(episode_id: str):
        return timing.state(episode_id)

    @app.post('/episodes/{episode_id}/timing/propose')
    def propose_timing(episode_id: str, body: ProposeIn):
        return timing.propose(episode_id, body.expected_hash)

    @app.post('/episodes/{episode_id}/timing/shots/{shot_id}')
    def save_shot_timing(episode_id: str, shot_id: str, body: ShotTimingIn):
        return timing.save_shot(episode_id, body.expected_hash, shot_id, body.length_frames, body.pauses, body.cues)

    @app.post('/episodes/{episode_id}/timing/audition')
    def build_audition(episode_id: str, body: AuditionIn):
        return timing.build_audition(episode_id, body.expected_hash, body.scene_key)

    @app.get('/timing/auditions/{audition_id}/file')
    def audition_file(audition_id: str):
        return FileResponse(timing.audition_file(audition_id), media_type='audio/wav', headers={'Cache-Control': 'no-store'})

    @app.post('/episodes/{episode_id}/c4/approve')
    def approve_c4(episode_id: str, body: C4In):
        return timing.approve_c4(episode_id, body.expected_hash, body.impact_hash)
