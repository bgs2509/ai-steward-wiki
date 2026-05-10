from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.classifier import (
    ClassifierSchemaError,
    FakeClaudeRunner,
    PromptCache,
    classify,
)
from ai_steward_wiki.storage.audit.engine import Base
from ai_steward_wiki.storage.audit.models import PromptVersion


def _write_prompt(tmp_path: Path, semver: str = "1.0.0", body: str = "system") -> Path:
    p = tmp_path / "classifier.md"
    p.write_text(f"---\nsemver: {semver}\n---\n{body}\n", encoding="utf-8")
    return p


@pytest.fixture
async def audit_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


async def test_classify_happy(tmp_path: Path) -> None:
    prompt = _write_prompt(tmp_path)
    runner = FakeClaudeRunner(
        responses=[
            {
                "intent": "reminder",
                "confidence": 0.91,
                "distilled_payload": {"when": "tomorrow"},
            }
        ]
    )
    res = await classify(
        "remind me tomorrow",
        correlation_id="c1",
        backend=runner,
        prompt_path=prompt,
        cache=PromptCache(),
    )
    assert res.intent.value == "reminder"
    assert res.confidence == 0.91
    assert res.backend == "fake"
    assert res.prompt_semver == "1.0.0"
    assert len(res.prompt_sha256) == 64
    assert res.latency_ms >= 0


async def test_classify_schema_violation(tmp_path: Path) -> None:
    prompt = _write_prompt(tmp_path)
    runner = FakeClaudeRunner(responses=[{"intent": "bogus", "confidence": 0.5}])
    with pytest.raises(ClassifierSchemaError):
        await classify(
            "x",
            correlation_id="c2",
            backend=runner,
            prompt_path=prompt,
            cache=PromptCache(),
        )


async def test_classify_audit_idempotent(tmp_path: Path, audit_session) -> None:
    prompt = _write_prompt(tmp_path)
    payload = {
        "intent": "wiki_query",
        "confidence": 0.7,
        "distilled_payload": {},
    }
    runner = FakeClaudeRunner(responses=[payload, dict(payload)])
    cache = PromptCache()
    await classify(
        "q1",
        correlation_id="c1",
        backend=runner,
        prompt_path=prompt,
        audit_session=audit_session,
        cache=cache,
    )
    await classify(
        "q2",
        correlation_id="c2",
        backend=runner,
        prompt_path=prompt,
        audit_session=audit_session,
        cache=cache,
    )
    await audit_session.commit()
    rows = (await audit_session.execute(PromptVersion.__table__.select())).all()
    assert len(rows) == 1, "prompt_versions must be deduplicated by (name, semver, sha256)"


async def test_prompt_cache_requires_semver(tmp_path: Path) -> None:
    bad = tmp_path / "p.md"
    bad.write_text("no frontmatter here", encoding="utf-8")
    cache = PromptCache()
    with pytest.raises(ClassifierSchemaError):
        cache.get(bad)


async def test_prompt_cache_hits(tmp_path: Path) -> None:
    prompt = _write_prompt(tmp_path)
    cache = PromptCache()
    a = cache.get(prompt)
    # mutate file; cached value must persist
    prompt.write_text("---\nsemver: 9.9.9\n---\nchanged\n", encoding="utf-8")
    b = cache.get(prompt)
    assert a is b
    cache.reload()
    c = cache.get(prompt)
    assert c.semver == "9.9.9"
