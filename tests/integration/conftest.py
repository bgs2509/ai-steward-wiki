# FILE: tests/integration/conftest.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Shared fixtures for tests/integration/* — spin DefaultPipeline
#            against real Claude CLI classifier with fake runner/output/voice/
#            photo collaborators and tmp-path-backed SQLite stores.
#   SCOPE: FakeAiogramBot (re-export of FakeSender), audit/sessions session
#          makers via Alembic upgrade head into tmp_path, IdempotencyService +
#          ConfirmationService factories, fake VoiceHandler, real PhotoIngestor,
#          _RealClassifierAdapter (real Claude CLI), composed DefaultPipeline.
#   DEPENDS: pytest, sqlalchemy.ext.asyncio, alembic.command, pypdf (PDF gen),
#            ai_steward_wiki.* runtime modules, tests/unit/tg/conftest.FakeSender
#   LINKS: M-INTEGRATION-E2E (chunk 23), aisw-vb9
#   ROLE: TEST
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   FakeAiogramBot - in-memory TgSender stub (send_message/edit_message_text/send_document)
#   CLASSIFIER_PROMPT - path to the prompts/classifier.md prompt file
#   REPO_ROOT - repository root path used by Alembic config + prompt lookup
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - chunk 23 M-INTEGRATION-E2E: initial conftest with
#                FakeAiogramBot, tmp Alembic-backed audit/sessions makers, real
#                Claude CLI classifier adapter, composed DefaultPipeline fixture.
# END_CHANGE_SUMMARY

from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.classifier import ClaudeCliBackend, PromptCache, classify
from ai_steward_wiki.classifier.schema import ClassifierResult
from ai_steward_wiki.inbox.idempotency import IdempotencyService
from ai_steward_wiki.tg.confirm import ConfirmationService
from ai_steward_wiki.tg.photo import PhotoIngestor
from ai_steward_wiki.tg.pipeline import DefaultPipeline, WikiRunOutcome
from ai_steward_wiki.tg.voice import Transcript, VoiceHandler

REPO_ROOT = Path(__file__).resolve().parents[2]
CLASSIFIER_PROMPT = REPO_ROOT / "prompts" / "classifier.md"


@dataclass
class _FakeMessage:
    message_id: int


class FakeAiogramBot:
    """In-memory recorder satisfying ai_steward_wiki.tg.bot.TgSender Protocol.

    Mirrors tests/unit/tg/conftest.FakeSender but kept local to avoid pulling
    tests.unit as a package on PYTHONPATH.
    """

    def __init__(self) -> None:
        self._next_id = 1000
        self.sends: list[dict] = []
        self.edits: list[dict] = []
        self.documents: list[dict] = []

    async def send_message(self, chat_id, text, *, parse_mode="HTML", reply_markup=None):
        self._next_id += 1
        self.sends.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
                "message_id": self._next_id,
            }
        )
        return _FakeMessage(message_id=self._next_id)

    async def edit_message_text(
        self, chat_id, message_id, text, *, parse_mode="HTML", reply_markup=None
    ):
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )

    async def send_document(self, chat_id, *, path, caption=None):
        self._next_id += 1
        self.documents.append(
            {"chat_id": chat_id, "path": str(path), "caption": caption, "message_id": self._next_id}
        )
        return _FakeMessage(message_id=self._next_id)


# Module-scoped cache — real classifier reuses cache across scenarios to amortise CLI cost.
_PROMPT_CACHE = PromptCache()


def _alembic_upgrade(branch: str, db_path: Path, env_var: str, monkeypatch) -> None:
    """Run Alembic upgrade head for the given branch against tmp sqlite DB."""
    monkeypatch.setenv(env_var, f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / branch / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / branch))
    command.upgrade(cfg, "head")


@pytest.fixture
async def audit_sm(tmp_path: Path, monkeypatch) -> AsyncIterator[async_sessionmaker]:
    db_path = tmp_path / "audit.db"
    _alembic_upgrade("audit", db_path, "AISW_AUDIT_DB_URL_SYNC", monkeypatch)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture
async def sessions_sm(tmp_path: Path, monkeypatch) -> AsyncIterator[async_sessionmaker]:
    db_path = tmp_path / "sessions.db"
    _alembic_upgrade("sessions", db_path, "AISW_SESSIONS_DB_URL_SYNC", monkeypatch)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture
def inbox_root(tmp_path: Path) -> Path:
    root = tmp_path / "inbox"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def fake_bot() -> FakeAiogramBot:
    return FakeAiogramBot()


@dataclass
class _StubTranscriber:
    canned_text: str = "напомни мне завтра в 9 утра позвонить маме"
    lang: str = "ru"

    async def transcribe(self, audio_bytes: bytes, *, hint_lang: str | None = None) -> Transcript:
        return Transcript(
            text=self.canned_text,
            lang=self.lang,
            duration_s=1.0,
            model="stub-transcriber",
            rtf=0.1,
        )


@pytest.fixture
def fake_voice(inbox_root: Path) -> VoiceHandler:
    return VoiceHandler(_StubTranscriber(), inbox_root=inbox_root)


@pytest.fixture
def fake_photo(inbox_root: Path) -> PhotoIngestor:
    return PhotoIngestor(inbox_root=inbox_root)


@pytest.fixture
def fake_runner():
    runner = MagicMock(name="WikiRunner")
    runner.run = AsyncMock(
        return_value=WikiRunOutcome(run_id="r-test", text="ответ", latency_ms=10)
    )
    return runner


@pytest.fixture
def fake_output():
    output = MagicMock(name="OutputDelivery")
    output.deliver = AsyncMock(return_value=None)
    return output


class _RealClassifierAdapter:
    """Adapter wrapping ClaudeCliBackend.classify behind the pipeline Classifier Protocol."""

    def __init__(self) -> None:
        cfg_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
        self._backend = ClaudeCliBackend(claude_config_dir=cfg_dir, timeout_s=60.0)

    async def classify(self, text: str, *, correlation_id: str) -> ClassifierResult:
        return await classify(
            text,
            correlation_id=correlation_id,
            backend=self._backend,
            prompt_path=CLASSIFIER_PROMPT,
            cache=_PROMPT_CACHE,
        )


@pytest.fixture
def real_classifier() -> _RealClassifierAdapter:
    return _RealClassifierAdapter()


@pytest.fixture
def idempotency(audit_sm) -> IdempotencyService:
    return IdempotencyService(audit_sm)


@pytest.fixture
def confirmation(fake_bot: FakeAiogramBot, sessions_sm) -> ConfirmationService:
    return ConfirmationService(fake_bot, sessions_sm)


@pytest.fixture
def pipeline(
    fake_bot: FakeAiogramBot,
    idempotency: IdempotencyService,
    confirmation: ConfirmationService,
    fake_voice: VoiceHandler,
    fake_photo: PhotoIngestor,
    real_classifier: _RealClassifierAdapter,
    fake_runner,
    fake_output,
) -> DefaultPipeline:
    return DefaultPipeline(
        sender=fake_bot,
        idempotency=idempotency,
        confirmation=confirmation,
        voice=fake_voice,
        photo=fake_photo,
        classifier=real_classifier,
        runner=fake_runner,
        output=fake_output,
    )


__all__ = [
    "CLASSIFIER_PROMPT",
    "REPO_ROOT",
    "FakeAiogramBot",
]


def pytest_collection_modifyitems(config, items):
    """Skip every test in this folder unless we are on a host that can actually
    invoke the Claude CLI subprocess: needs RUN_INTEGRATION=1, the `claude`
    binary on PATH, and NOT running inside a parent Claude Code session
    (CLAUDECODE=1 — recursive invocation breaks subscription auth)."""
    skip_no_gate = pytest.mark.skip(reason="set RUN_INTEGRATION=1 to enable integration suite")
    skip_no_claude = pytest.mark.skip(reason="`claude` binary not on PATH")
    skip_recursive = pytest.mark.skip(
        reason="recursive claude invocation (CLAUDECODE=1) — run outside Claude Code"
    )
    gate = os.environ.get("RUN_INTEGRATION") == "1"
    has_claude = shutil.which("claude") is not None
    is_recursive = os.environ.get("CLAUDECODE") == "1"
    for item in items:
        if str(item.fspath).startswith(str(Path(__file__).parent)):
            if not gate:
                item.add_marker(skip_no_gate)
            elif not has_claude:
                item.add_marker(skip_no_claude)
            elif is_recursive:
                item.add_marker(skip_recursive)
