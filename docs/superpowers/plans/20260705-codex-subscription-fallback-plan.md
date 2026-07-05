# Codex Subscription Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Автоматически переключать исчерпавший подписочный лимит Claude на Codex CLI через подписку ChatGPT.

**Architecture:** Один процессный `FailoverPolicy` управляет состояниями `claude`, `codex` и `probe`. Режимные адаптеры сохраняют текущие контракты классификатора, WIKI runner, генератора схемы и cron consumer.

**Tech Stack:** Python 3.11, `asyncio`, `dataclasses`, Pydantic v2, structlog, pytest, Claude CLI, Codex CLI `0.142.5`.

---

# 1. Контекст

Claude остаётся основным провайдером всех текущих LLM-вызовов.
Подтверждённый подписочный лимит должен переводить общий circuit в состояние `codex`.
Безопасная операция повторяется через подписочный Codex, сохраняя текущий потребительский контракт.
Частично изменившая данные операция не повторяется автоматически.

# 2. Содержание

1. Базовый circuit и типизированные ошибки Claude
2. Codex CLI adapter, настройки и readiness
3. Structured fallback для Stage-0
4. Text fallback для schema generation и cron
5. Agent fallback и replay safety для WIKI
6. Runtime wiring, ошибки и observability
7. Deployment, runbooks и итоговая верификация

# 3. Краткая версия плана

### Этап 1: Базовый circuit и типизированные ошибки Claude

1. **Проблема.** Сейчас разные Claude-вызовы теряют причину подписочного ограничения. Общего состояния провайдера нет.
2. **Действие.** Реализовать `src/ai_steward_wiki/llm/failover.py` и распознавание лимита в `src/ai_steward_wiki/claude_cli/common.py`.
3. **Результат.** Появятся атомарные состояния `claude`, `codex`, `probe`, cooldown и единственный probe.
4. **Зависимости.** Этап использует утверждённые discovery, design, ADR-035 и GRACE-контракты.
5. **Риски.** Ошибка классификации может переключить провайдера без настоящего лимита. Закрытый набор признаков исключает эвристику по stderr.
6. **Без этого.** Каждый call site реализует собственный circuit и получает несовместимую семантику.

### Этап 2: Codex CLI adapter, настройки и readiness

1. **Проблема.** Codex CLI пока не имеет изолированного runtime-контракта внутри сервиса.
2. **Действие.** Реализовать `src/ai_steward_wiki/llm/codex.py` и параметры в `src/ai_steward_wiki/settings.py`.
3. **Результат.** Structured, text и agent режимы используют подписку ChatGPT без API-ключей.
4. **Зависимости.** Требуется circuit из этапа 1 и установленный Codex CLI версии `0.142.5`.
5. **Риски.** Пользовательская конфигурация Codex может расширить права. Явные flags и ограниченное окружение закрывают риск.
6. **Без этого.** Fallback не сможет безопасно запускать Codex и проверять его readiness.

### Этап 3: Structured fallback для Stage-0

1. **Проблема.** Классификатор и time parser полностью останавливаются после лимита Claude Haiku.
2. **Действие.** Добавить failover wrapper в `src/ai_steward_wiki/classifier/backend.py`.
3. **Результат.** Haiku-задачи безопасно переходят на `gpt-5.4-mini` с reasoning `low`.
4. **Зависимости.** Нужны этапы 1 и 2, существующий `ClassifierBackend` и Pydantic-валидация потребителей.
5. **Риски.** Разные JSON-формы могут нарушить схему. Codex возвращает объект, проверяемый текущей моделью потребителя.
6. **Без этого.** Бот не сможет маршрутизировать сообщения при исчерпании лимита Claude.

### Этап 4: Text fallback для schema generation и cron

1. **Проблема.** Генерация WIKI-схем и cron-команды вызывают Claude напрямую.
2. **Действие.** Встроить policy в `wiki/schema_gen.py` и `scheduler/consumer.py` до внешних side effects.
3. **Результат.** Sonnet-задачи переходят на `gpt-5.5` с reasoning `medium` до записи или доставки.
4. **Зависимости.** Требуются этапы 1 и 2. Этап 3 не блокирует эту работу.
5. **Риски.** Повтор после доставки создаст дубликат. Policy вызывается до `repair_managed_zone` и Telegram delivery.
6. **Без этого.** Два прямых Claude-пути останутся едиными точками отказа.

### Этап 5: Agent fallback и replay safety для WIKI

1. **Проблема.** WIKI runner может успеть изменить файлы или отправить streaming-ответ до лимита.
2. **Действие.** Удерживать один WIKI lock, собирать evidence и нормализовать Codex JSONL в `StreamEvent`.
3. **Результат.** Безопасные WIKI-запуски продолжатся через Codex. Опасный replay будет запрещён.
4. **Зависимости.** Нужны этапы 1 и 2, существующие lock, transcript и streaming-контракты.
5. **Риски.** Неизвестное действие может скрывать мутацию. Любое неизвестное evidence считается небезопасным.
6. **Без этого.** Agent fallback сможет продублировать WIKI-запись или Telegram-ответ.

### Этап 6: Runtime wiring, ошибки и observability

1. **Проблема.** Отдельные адаптеры не дадут общего состояния без композиции в runtime.
2. **Действие.** Создать один policy и adapter в `src/ai_steward_wiki/__main__.py` и внедрить их во все пути.
3. **Результат.** Все LLM-вызовы разделят circuit, readiness, counters и безопасные structured logs.
4. **Зависимости.** Требуются этапы 1–5 и текущие runtime adapters.
5. **Риски.** Ошибка Codex readiness может остановить сервис. Неуспех отключает только fallback.
6. **Без этого.** Каждый модуль получит отдельное состояние и нарушит NFR-3.

### Этап 7: Deployment, runbooks и итоговая верификация

1. **Проблема.** На production-хосте Codex не подготовлен для неинтерактивного запуска сервисом.
2. **Действие.** Обновить systemd, runbooks, GRACE-артефакты и выполнить полный quality gate.
3. **Результат.** Оператор получает воспроизводимую установку, login, smoke, восстановление и диагностику.
4. **Зависимости.** Требуются все предыдущие этапы и операторский доступ к подпискам.
5. **Риски.** Реальный smoke расходует квоту. Он выполняется вручную только перед deployment.
6. **Без этого.** Код останется непроверенным в production-окружении и fallback будет неготов.

# 4. Полная версия плана

## Этап 1: Базовый circuit и типизированные ошибки Claude

**Соответствие GRACE:** `Phase-G.1`, `M-LLM-FAILOVER`, `V-M-LLM-FAILOVER`.

**Файлы:**

- Modify: `src/ai_steward_wiki/llm/failover.py`
- Modify: `src/ai_steward_wiki/llm/__init__.py`
- Modify: `src/ai_steward_wiki/claude_cli/common.py`
- Create: `tests/unit/llm/test_failover.py`
- Modify: `tests/unit/claude_cli/test_common.py`

### Task 1.1: Зафиксировать error taxonomy и replay evidence

- [ ] **Шаг 1: Написать падающие тесты типов**

```python
from ai_steward_wiki.llm.failover import (
    AttemptEvidence,
    EvidenceKind,
    ProviderLimitError,
    ProviderState,
)


def test_provider_state_uses_approved_names() -> None:
    assert [state.value for state in ProviderState] == ["claude", "codex", "probe"]


def test_only_read_only_evidence_is_replay_safe() -> None:
    assert AttemptEvidence(EvidenceKind.READ_ONLY).replay_safe is True
    assert AttemptEvidence(EvidenceKind.MUTATION).replay_safe is False
    assert AttemptEvidence(EvidenceKind.DELIVERED).replay_safe is False
    assert AttemptEvidence(EvidenceKind.UNKNOWN).replay_safe is False


def test_limit_error_carries_reset_and_evidence(reset_at) -> None:
    error = ProviderLimitError(
        provider="claude",
        reset_at=reset_at,
        evidence=AttemptEvidence(EvidenceKind.READ_ONLY),
    )
    assert error.reset_at == reset_at
    assert error.evidence.replay_safe is True
```

- [ ] **Шаг 2: Подтвердить красный тест**

Run: `uv run pytest tests/unit/llm/test_failover.py -q`

Expected: FAIL с ошибкой импорта новых типов.

- [ ] **Шаг 3: Реализовать минимальные типы**

```python
class ProviderState(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"
    PROBE = "probe"


class EvidenceKind(StrEnum):
    READ_ONLY = "read_only"
    MUTATION = "mutation"
    DELIVERED = "delivered"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AttemptEvidence:
    kind: EvidenceKind
    reason: str | None = None

    @property
    def replay_safe(self) -> bool:
        return self.kind is EvidenceKind.READ_ONLY


@dataclass(eq=False)
class ProviderLimitError(RuntimeError):
    provider: str
    reset_at: datetime | None
    evidence: AttemptEvidence


class ReplayBlockedError(RuntimeError):
    pass


@dataclass(eq=False)
class ProvidersUnavailableError(RuntimeError):
    primary_error: Exception
    fallback_error: Exception
```

- [ ] **Шаг 4: Запустить типовые тесты**

Run: `uv run pytest tests/unit/llm/test_failover.py -q`

Expected: PASS для taxonomy и evidence.

### Task 1.2: Распознавать только структурированный Claude subscription limit

- [ ] **Шаг 1: Добавить тесты закрытого классификатора**

```python
def test_parse_limit_accepts_structured_429() -> None:
    payload = {
        "is_error": True,
        "api_error_status": 429,
        "result": "subscription limit reached; resets at 2026-07-05T18:00:00Z",
    }
    result = parse_claude_subscription_limit(payload)
    assert result is not None
    assert result.reset_at is not None
    assert result.reset_at.isoformat() == "2026-07-05T18:00:00+00:00"


@pytest.mark.parametrize(
    "payload",
    [
        {"is_error": False, "api_error_status": 429},
        {"is_error": True, "api_error_status": 500},
        {"is_error": True, "result": "429 in arbitrary text"},
    ],
)
def test_parse_limit_rejects_unconfirmed_shapes(payload: dict[str, object]) -> None:
    assert parse_claude_subscription_limit(payload) is None
```

- [ ] **Шаг 2: Подтвердить красный тест**

Run: `uv run pytest tests/unit/claude_cli/test_common.py -q`

Expected: FAIL, функция отсутствует.

- [ ] **Шаг 3: Реализовать parser без stderr-эвристики**

```python
@dataclass(frozen=True, slots=True)
class ClaudeSubscriptionLimit:
    reset_at: datetime | None


_RESET_AT_RE = re.compile(
    r"resets? at (?P<value>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))",
    re.IGNORECASE,
)


def parse_claude_subscription_limit(
    payload: Mapping[str, object],
) -> ClaudeSubscriptionLimit | None:
    if payload.get("is_error") is not True or payload.get("api_error_status") != 429:
        return None
    result = payload.get("result")
    if not isinstance(result, str):
        return ClaudeSubscriptionLimit(reset_at=None)
    match = _RESET_AT_RE.search(result)
    if match is None:
        return ClaudeSubscriptionLimit(reset_at=None)
    reset_at = datetime.fromisoformat(match.group("value").replace("Z", "+00:00"))
    return ClaudeSubscriptionLimit(reset_at=reset_at)
```

Функция возвращает `None` только для не-лимита.
Подтверждённый лимит всегда возвращает typed result с optional reset-time.

- [ ] **Шаг 4: Проверить parser**

Run: `uv run pytest tests/unit/claude_cli/test_common.py -q`

Expected: PASS, включая существующие тесты `common.py`.

### Task 1.3: Реализовать атомарный circuit и single-flight probe

- [ ] **Шаг 1: Добавить async-тесты переходов и concurrency**

```python
async def test_limit_moves_claude_to_codex_and_runs_safe_fallback(clock) -> None:
    policy = FailoverPolicy(cooldown_s=900.0, clock=clock)

    async def claude() -> str:
        raise ProviderLimitError(
            provider="claude",
            reset_at=None,
            evidence=AttemptEvidence(EvidenceKind.READ_ONLY),
        )

    async def codex() -> str:
        return "fallback"

    assert await policy.execute(
        run_kind="structured",
        correlation_id="c1",
        claude=claude,
        codex=codex,
    ) == "fallback"
    assert policy.state is ProviderState.CODEX


async def test_concurrent_requests_start_one_probe(clock, async_barrier) -> None:
    policy = FailoverPolicy(cooldown_s=1.0, clock=clock)
    await move_policy_to_codex(policy)
    clock.advance(1.0)
    results = await asyncio.gather(
        *[run_request(policy, async_barrier) for _ in range(10)]
    )
    assert results.count("probe") == 1
    assert results.count("codex") == 9
```

Добавить отдельные тесты для reset-time, blocked replay, dual failure и probe recovery.

- [ ] **Шаг 2: Подтвердить красный test suite**

Run: `uv run pytest tests/unit/llm/test_failover.py -q`

Expected: FAIL, `FailoverPolicy` ещё отсутствует.

- [ ] **Шаг 3: Реализовать policy с короткой критической секцией**

```python
T = TypeVar("T")
Attempt = Callable[[], Awaitable[T]]


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    provider: ProviderState
    is_probe: bool = False


class FailoverPolicy:
    def __init__(
        self,
        *,
        cooldown_s: float,
        clock: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(UTC),
        on_event: Callable[[FailoverEvent], None] | None = None,
    ) -> None:
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._utcnow = utcnow
        self._on_event = on_event
        self._lock = asyncio.Lock()
        self._state = ProviderState.CLAUDE
        self._probe_after = 0.0
        self._last_limit_error: ProviderLimitError | None = None

    @property
    def state(self) -> ProviderState:
        return self._state

    async def execute(
        self,
        *,
        run_kind: str,
        correlation_id: str,
        claude: Attempt[T],
        codex: Attempt[T],
    ) -> T:
        selection = await self._select()
        if selection.provider is ProviderState.CODEX:
            return await self._run_codex(codex)
        try:
            result = await claude()
        except ProviderLimitError as primary_error:
            await self._move_to_codex(primary_error.reset_at)
            if not primary_error.evidence.replay_safe:
                raise ReplayBlockedError(primary_error.evidence.reason or "unsafe replay")
            return await self._run_codex(codex)
        except Exception:
            if selection.is_probe:
                await self._move_to_codex(None)
                return await self._run_codex(codex)
            raise
        if selection.is_probe:
            await self._recover_claude()
        return result

    async def _run_codex(self, codex: Attempt[T]) -> T:
        try:
            return await codex()
        except Exception as fallback_error:
            primary_error = self._last_limit_error or RuntimeError("claude unavailable")
            raise ProvidersUnavailableError(primary_error, fallback_error) from fallback_error
```

`_select`, `_move_to_codex` и `_recover_claude` меняют состояние только под `asyncio.Lock`.
Вызовы CLI никогда не выполняются внутри lock.
`_move_to_codex` сохраняет последний typed limit для последующих dual-provider ошибок.
Reset-time преобразуется в monotonic delay через injected UTC clock.
Без reset-time используется configured cooldown.

- [ ] **Шаг 4: Проверить circuit и бюджет выбора**

Run: `uv run pytest tests/unit/llm/test_failover.py -q`

Expected: PASS для всех переходов и p95 ниже 100 ms.

- [ ] **Шаг 5: Обновить MODULE_MAP и публичные exports**

Экспортировать утверждённые типы через `src/ai_steward_wiki/llm/__init__.py`.
Поднять версии изменённых MODULE_CONTRACT headers.

- [ ] **Шаг 6: Выполнить локальный gate этапа**

Run: `uv run pytest tests/unit/llm/test_failover.py tests/unit/claude_cli/test_common.py -q`

Expected: PASS.

- [ ] **Шаг 7: Зафиксировать этап**

```bash
git add src/ai_steward_wiki/llm src/ai_steward_wiki/claude_cli/common.py tests/unit/llm tests/unit/claude_cli/test_common.py
git commit -m "feat(M-LLM-FAILOVER): add provider circuit"
```

## Этап 2: Codex CLI adapter, настройки и readiness

**Соответствие GRACE:** `Phase-G.2`, `M-LLM-CODEX`, `V-M-LLM-CODEX`.

**Файлы:**

- Modify: `src/ai_steward_wiki/llm/codex.py`
- Modify: `src/ai_steward_wiki/settings.py`
- Create: `tests/unit/llm/test_codex.py`
- Create: `tests/integration/llm/test_fake_codex_cli.py`
- Modify: `tests/unit/test_settings.py`

### Task 2.1: Добавить валидируемую конфигурацию без API-key режима

- [ ] **Шаг 1: Написать тесты defaults и overrides**

```python
def test_codex_subscription_defaults() -> None:
    settings = Settings()
    assert settings.llm_codex_enabled is True
    assert settings.llm_failover_cooldown_s == 900.0
    assert settings.codex_cli_version == "0.142.5"
    assert settings.codex_light_model == "gpt-5.4-mini"
    assert settings.codex_light_reasoning == "low"
    assert settings.codex_complex_model == "gpt-5.5"
    assert settings.codex_complex_reasoning == "medium"


def test_codex_home_is_configurable(monkeypatch) -> None:
    monkeypatch.setenv("AISW_CODEX_HOME", "/tmp/codex-home")
    assert Settings().codex_home == Path("/tmp/codex-home")
```

- [ ] **Шаг 2: Подтвердить красный тест**

Run: `uv run pytest tests/unit/test_settings.py -q`

Expected: FAIL на отсутствующих fields.

- [ ] **Шаг 3: Добавить settings**

```python
ReasoningEffort = Literal["low", "medium", "high"]


class Settings(BaseSettings):
    llm_codex_enabled: bool = True
    llm_failover_cooldown_s: float = 900.0
    codex_cli_binary: str = "codex"
    codex_cli_version: str = "0.142.5"
    codex_home: Path = Path("/var/lib/ai-steward-wiki/codex")
    codex_light_model: str = "gpt-5.4-mini"
    codex_light_reasoning: ReasoningEffort = "low"
    codex_complex_model: str = "gpt-5.5"
    codex_complex_reasoning: ReasoningEffort = "medium"
```

Не добавлять OpenAI API key в settings, документацию или subprocess environment.

- [ ] **Шаг 4: Проверить settings**

Run: `uv run pytest tests/unit/test_settings.py -q`

Expected: PASS.

### Task 2.2: Реализовать capability profiles и subprocess contract

- [ ] **Шаг 1: Написать argv и environment tests**

```python
def test_structured_argv_is_explicit(adapter, request) -> None:
    argv = adapter.build_structured_argv(request)
    assert "--ephemeral" in argv
    assert "--ignore-user-config" in argv
    assert "--ignore-rules" in argv
    assert "--strict-config" in argv
    assert "--skip-git-repo-check" in argv
    assert pair(argv, "--model") == "gpt-5.4-mini"
    assert pair(argv, "--sandbox") == "read-only"
    assert 'model_reasoning_effort="low"' in argv


def test_environment_contains_no_api_key(adapter) -> None:
    env = adapter.build_env()
    assert set(env) == {"CODEX_HOME", "PATH", "LANG"}
    assert "OPENAI_API_KEY" not in env
```

- [ ] **Шаг 2: Подтвердить красный тест**

Run: `uv run pytest tests/unit/llm/test_codex.py -q`

Expected: FAIL на отсутствующем adapter.

- [ ] **Шаг 3: Добавить request types и adapter surface**

```python
class CodexRunKind(StrEnum):
    STRUCTURED = "structured"
    TEXT = "text"
    AGENT_READ = "agent_read"
    AGENT_WRITE = "agent_write"
    WEB = "web"


@dataclass(frozen=True, slots=True)
class CodexRequest:
    prompt: str
    model: str
    reasoning: ReasoningEffort
    run_kind: CodexRunKind
    correlation_id: str
    timeout_s: float
    cwd: Path
    writable_wiki: Path | None = None
    readable_paths: tuple[Path, ...] = ()
    image_paths: tuple[Path, ...] = ()
    output_schema: Mapping[str, object] | None = None


class CodexAdapter(Protocol):
    async def run_structured(self, request: CodexRequest) -> dict[str, Any]: ...
    async def run_text(self, request: CodexRequest) -> str: ...
    async def run_agent(self, request: CodexRequest) -> list[StreamEvent]: ...
    async def check_readiness(self) -> CodexReadiness: ...
```

Общий builder добавляет `approval_policy="never"` и `project_doc_max_bytes=0`.
Structured mode добавляет `--output-schema <runtime-schema-path>`.
Agent mode добавляет `--json`.
Web mode добавляет global flag `--search` до subcommand `exec`.
Write mode использует selected WIKI через `--cd` без `--add-dir`.
Отклонение одобрено пользователем 2026-07-05 для выполнения NFR-5.

- [ ] **Шаг 4: Реализовать restricted process execution**

```python
def build_env(self) -> dict[str, str]:
    return {
        "CODEX_HOME": str(self._codex_home),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }


async def _spawn(self, argv: Sequence[str], *, stdin: bytes, timeout_s: float) -> ProcessResult:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=self.build_env(),
        cwd=str(self._neutral_cwd),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout_s)
    except TimeoutError:
        proc.terminate()
        await proc.wait()
        raise CodexUnavailableError("codex timeout")
    return ProcessResult(proc.returncode or 0, stdout, stderr)
```

Prompt передаётся только через stdin.
Логи содержат длины, длительность, provider, model и outcome.

- [ ] **Шаг 5: Проверить unit contract**

Run: `uv run pytest tests/unit/llm/test_codex.py -q`

Expected: PASS для argv, env, timeout, cancellation, JSON и JSONL.

### Task 2.3: Реализовать readiness без модельного вызова

- [ ] **Шаг 1: Написать readiness tests**

```python
async def test_readiness_uses_only_non_model_commands(fake_spawner, adapter) -> None:
    result = await adapter.check_readiness()
    assert result.ready is True
    assert fake_spawner.argvs == [
        [adapter.binary, "--version"],
        [adapter.binary, "login", "status"],
        [adapter.binary, "exec", "--help"],
    ]


async def test_version_mismatch_disables_fallback(fake_spawner, adapter) -> None:
    fake_spawner.version = "codex-cli 0.142.4"
    result = await adapter.check_readiness()
    assert result.ready is False
    assert result.reason == "version_mismatch"
```

- [ ] **Шаг 2: Реализовать `CodexReadiness`**

```python
@dataclass(frozen=True, slots=True)
class CodexReadiness:
    ready: bool
    reason: str | None
    binary: str | None
    version: str | None
```

Проверить binary, точную версию, directory readability, login status и наличие non-interactive flags.

- [ ] **Шаг 3: Создать fake CLI integration matrix**

Fake executable должен записывать argv и environment в временные файлы.
Он возвращает controlled structured JSON, text и Codex JSONL.
Containment test проверяет отказ записи вне selected WIKI.

```python
FAKE_CODEX = """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
trace_path = Path(sys.argv[0]).with_suffix(".trace")
with trace_path.open("a", encoding="utf-8") as trace:
    trace.write(json.dumps({"argv": args, "env": sorted(os.environ)}) + "\\n")
if args == ["--version"]:
    print("codex-cli 0.142.5")
elif args == ["login", "status"]:
    print("Logged in using ChatGPT")
elif args == ["exec", "--help"]:
    print("--ephemeral --ignore-user-config --ignore-rules --strict-config --json")
elif "--json" in args:
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
else:
    print(json.dumps({"intent": "WIKI_QUERY", "confidence": 1.0}))
"""


def make_fake_codex(tmp_path: Path) -> Path:
    binary = tmp_path / "codex"
    binary.write_text(FAKE_CODEX, encoding="utf-8")
    binary.chmod(0o755)
    return binary
```

- [ ] **Шаг 4: Запустить adapter gates**

Run: `uv run pytest tests/unit/llm/test_codex.py tests/unit/test_settings.py -q`

Expected: PASS.

Run: `RUN_INTEGRATION=1 uv run pytest tests/integration/llm/test_fake_codex_cli.py -q`

Expected: PASS без реальной подписки.

- [ ] **Шаг 5: Обновить MODULE_MAP и зафиксировать этап**

```bash
git add src/ai_steward_wiki/llm/codex.py src/ai_steward_wiki/settings.py tests/unit/llm/test_codex.py tests/integration/llm/test_fake_codex_cli.py tests/unit/test_settings.py
git commit -m "feat(M-LLM-CODEX): add Codex CLI adapter"
```

## Этап 3: Structured fallback для Stage-0

**Соответствие GRACE:** часть `Phase-G.3`, `M-CLASSIFIER-STAGE0`.

**Файлы:**

- Modify: `src/ai_steward_wiki/classifier/backend.py`
- Modify: `tests/unit/classifier/test_cli_envelope.py`
- Create: `tests/unit/classifier/test_failover_backend.py`

### Task 3.1: Поднять typed limit из Claude envelope

- [ ] **Шаг 1: Добавить failing tests для error envelope**

```python
async def test_nonzero_structured_429_raises_provider_limit(tmp_path) -> None:
    spawner = StubSpawner(
        rc=1,
        stdout=json.dumps(
            {
                "is_error": True,
                "api_error_status": 429,
                "result": "subscription limit reached",
            }
        ).encode(),
        stderr=b"",
    )
    backend = ClaudeCliBackend(claude_config_dir=tmp_path, spawner=spawner)
    with pytest.raises(ProviderLimitError):
        await backend.call(text="x", prompt_path=prompt_path, correlation_id="c1")


async def test_non_limit_nonzero_remains_classifier_error(tmp_path) -> None:
    spawner = StubSpawner(rc=1, stdout=b"{}", stderr=b"boom")
    backend = ClaudeCliBackend(claude_config_dir=tmp_path, spawner=spawner)
    with pytest.raises(ClassifierError):
        await backend.call(text="x", prompt_path=prompt_path, correlation_id="c1")
```

- [ ] **Шаг 2: Изменить порядок parsing**

Сначала декодировать JSON-object из stdout.
Затем вызвать `parse_claude_subscription_limit`.
После этого сохранить существующую обработку rc и успешного envelope.

```python
envelope = _decode_cli_envelope(stdout)
limit = parse_claude_subscription_limit(envelope)
if limit is not None:
    raise ProviderLimitError(
        provider="claude",
        reset_at=limit.reset_at,
        evidence=AttemptEvidence(EvidenceKind.READ_ONLY),
    )
if rc != 0:
    raise ClassifierError(
        f"claude CLI exited with rc={rc}; stderr={truncate_stderr(stderr)}"
    )
return _unwrap_cli_envelope(envelope)
```

- [ ] **Шаг 3: Проверить Claude compatibility**

Run: `uv run pytest tests/unit/classifier/test_cli_envelope.py -q`

Expected: PASS.

### Task 3.2: Добавить `FailoverClassifierBackend`

- [ ] **Шаг 1: Написать contract tests**

```python
async def test_healthy_claude_never_calls_codex() -> None:
    backend = build_failover_backend(claude_result={"intent": "WIKI_QUERY"})
    result = await backend.call(text="query", prompt_path=PROMPT, correlation_id="c1")
    assert result["intent"] == "WIKI_QUERY"
    assert backend.codex.calls == []


async def test_limit_maps_to_light_codex_profile() -> None:
    backend = build_failover_backend(claude_error=typed_limit())
    await backend.call(text="query", prompt_path=PROMPT, correlation_id="c1")
    request = backend.codex.calls[0]
    assert request.model == "gpt-5.4-mini"
    assert request.reasoning == "low"
    assert request.run_kind is CodexRunKind.STRUCTURED
```

- [ ] **Шаг 2: Реализовать wrapper с прежним Protocol**

```python
@dataclass
class FailoverClassifierBackend:
    primary: ClassifierBackend
    codex: CodexCliAdapter
    policy: FailoverPolicy
    model: str = "claude-haiku-4-5"
    name: str = "claude_cli"

    async def call(
        self,
        *,
        text: str,
        prompt_path: Path,
        correlation_id: str,
    ) -> dict[str, Any]:
        prompt = prompt_path.read_text(encoding="utf-8")
        return await self.policy.execute(
            run_kind="structured",
            correlation_id=correlation_id,
            claude=lambda: self.primary.call(
                text=text,
                prompt_path=prompt_path,
                correlation_id=correlation_id,
            ),
            codex=lambda: self.codex.run_structured(
                CodexRequest(
                    prompt=f"{prompt}\n\nUSER_INPUT:\n{text}",
                    model=self.codex.light_model,
                    reasoning=self.codex.light_reasoning,
                    run_kind=CodexRunKind.STRUCTURED,
                    correlation_id=correlation_id,
                    timeout_s=self.codex.structured_timeout_s,
                    cwd=self.codex.neutral_cwd,
                    output_schema={"type": "object"},
                )
            ),
        )
```

Wrapper сохраняет `name` и `model` primary backend.
Фактический provider фиксируется только в LLM telemetry.

- [ ] **Шаг 3: Проверить classifier и time parser regressions**

Run: `uv run pytest tests/unit/classifier tests/unit/test_runtime_wiring.py -q`

Expected: PASS.

- [ ] **Шаг 4: Обновить MODULE_MAP и зафиксировать этап**

```bash
git add src/ai_steward_wiki/classifier/backend.py tests/unit/classifier/test_cli_envelope.py tests/unit/classifier/test_failover_backend.py
git commit -m "feat(M-CLASSIFIER-STAGE0): add structured Codex fallback"
```

## Этап 4: Text fallback для schema generation и cron

**Соответствие GRACE:** оставшаяся часть `Phase-G.3`, `M-WIKI-MIGRATION`, `M-SCHEDULER-CONSUMER`.

**Файлы:**

- Modify: `src/ai_steward_wiki/wiki/schema_gen.py`
- Modify: `tests/unit/wiki/test_schema_gen.py`
- Modify: `src/ai_steward_wiki/scheduler/consumer.py`
- Modify: `tests/unit/scheduler/test_consumer.py`

### Task 4.1: Сделать schema generation replay-safe до записи

- [ ] **Шаг 1: Написать fallback и dual-failure tests**

```python
async def test_schema_limit_uses_codex_before_apply(tmp_path) -> None:
    generator = build_failover_schema_generator(claude_error=typed_limit())
    managed = await generator.generate(
        wiki_name="Health-WIKI",
        first_content="source",
        correlation_id="c1",
    )
    assert validate_schema(managed)
    assert generator.codex.calls[0].model == "gpt-5.5"
    assert generator.codex.calls[0].reasoning == "medium"


async def test_dual_failure_keeps_default_schema(tmp_path) -> None:
    applied = await apply_generated_schema(
        claude_md=tmp_path / "CLAUDE.md",
        generator=dual_failure_generator(),
        wiki_name="Health-WIKI",
        first_content="source",
        correlation_id="c1",
    )
    assert applied is False
```

- [ ] **Шаг 2: Перевести Claude generator на structured envelope**

Добавить `-p`, `--output-format json` и пустой tool surface.
Распознать typed 429 до общего `SchemaGenError`.
Вернуть только строку `result` из успешного envelope.

```python
def _argv(self) -> list[str]:
    return [
        resolve_binary(self.binary),
        "-p",
        "--model",
        self.model,
        "--output-format",
        "json",
        "--max-turns",
        "1",
        *system_prompt_argv(self.prompt_path),
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--tools",
        "",
        "--permission-mode",
        "dontAsk",
    ]


def _unwrap_schema_result(stdout: bytes) -> str:
    envelope = json.loads(stdout.decode("utf-8"))
    limit = parse_claude_subscription_limit(envelope)
    if limit is not None:
        raise ProviderLimitError(
            provider="claude",
            reset_at=limit.reset_at,
            evidence=AttemptEvidence(EvidenceKind.READ_ONLY),
        )
    result = envelope.get("result")
    if envelope.get("subtype") != "success" or not isinstance(result, str):
        raise SchemaGenError("claude schema result is invalid")
    return result
```

- [ ] **Шаг 3: Добавить `FailoverSchemaGenerator`**

```python
@dataclass
class FailoverSchemaGenerator:
    primary: SchemaGenerator
    codex: CodexCliAdapter
    policy: FailoverPolicy

    async def generate(self, *, wiki_name: str, first_content: str, correlation_id: str) -> str:
        return await self.policy.execute(
            run_kind="text",
            correlation_id=correlation_id,
            claude=lambda: self.primary.generate(
                wiki_name=wiki_name,
                first_content=first_content,
                correlation_id=correlation_id,
            ),
            codex=lambda: self.codex.run_text(
                build_schema_codex_request(wiki_name, first_content, correlation_id)
            ),
        )
```

`apply_generated_schema` остаётся единственным местом записи.

- [ ] **Шаг 4: Проверить schema suite**

Run: `uv run pytest tests/unit/wiki/test_schema_gen.py -q`

Expected: PASS.

- [ ] **Шаг 5: Зафиксировать schema slice**

```bash
git add src/ai_steward_wiki/wiki/schema_gen.py tests/unit/wiki/test_schema_gen.py
git commit -m "feat(M-WIKI-MIGRATION): add schema text fallback"
```

### Task 4.2: Выполнять cron fallback до Telegram delivery

- [ ] **Шаг 1: Написать tests Claude envelope и fallback**

```python
async def test_cron_limit_falls_back_before_delivery(session_factory, fake_prompt_file) -> None:
    consumer = build_consumer(
        claude_result=structured_limit_result(),
        codex_text="done by codex",
        session_factory=session_factory,
        prompt_path=fake_prompt_file,
    )
    await consumer._execute_one(_msg(1))
    assert consumer.bot.sent == [(CHAT_ID, "done by codex")]
    assert consumer.codex.calls[0].model == "gpt-5.5"


async def test_cron_dual_failure_sends_one_message(session_factory, fake_prompt_file) -> None:
    consumer = build_dual_failure_consumer(session_factory, fake_prompt_file)
    await consumer._execute_one(_msg(1))
    assert len(consumer.bot.sent) == 1
    assert "оба сервиса" in consumer.bot.sent[0][1]
```

- [ ] **Шаг 2: Нормализовать Claude text result**

В `_build_argv` добавить `-p`, `--output-format json` и `--tools ""`.
Сохранить literal `--` перед пользовательской командой.
Декодировать успешный `result` и typed 429 до delivery.

```python
def _decode_claude_text(stdout: bytes) -> str:
    envelope = json.loads(stdout.decode("utf-8"))
    limit = parse_claude_subscription_limit(envelope)
    if limit is not None:
        raise ProviderLimitError(
            provider="claude",
            reset_at=limit.reset_at,
            evidence=AttemptEvidence(EvidenceKind.READ_ONLY),
        )
    result = envelope.get("result")
    if envelope.get("subtype") != "success" or not isinstance(result, str):
        raise RuntimeError("claude cron result is invalid")
    return result.strip() or _EMPTY_OUTPUT_RU
```

- [ ] **Шаг 3: Внедрить policy и adapter в `CronConsumer`**

```python
class CronConsumer:
    def __init__(
        self,
        *,
        failover_policy: FailoverPolicy | None = None,
        codex_adapter: CodexCliAdapter | None = None,
        **existing: object,
    ) -> None:
        self._failover_policy = failover_policy
        self._codex_adapter = codex_adapter

    async def _execute_text(self, msg: CronUserQueueMsg) -> str:
        if self._failover_policy is None or self._codex_adapter is None:
            return await self._execute_claude_text(msg)
        return await self._failover_policy.execute(
            run_kind="text",
            correlation_id=msg.correlation_id,
            claude=lambda: self._execute_claude_text(msg),
            codex=lambda: self._codex_adapter.run_text(build_cron_codex_request(msg)),
        )
```

`_deliver` вызывается один раз после получения final text.

- [ ] **Шаг 4: Проверить cron suite**

Run: `uv run pytest tests/unit/scheduler/test_consumer.py -q`

Expected: PASS, включая timeout, injection guard и delivery failure.

- [ ] **Шаг 5: Обновить MODULE_MAP и зафиксировать cron slice**

```bash
git add src/ai_steward_wiki/scheduler/consumer.py tests/unit/scheduler/test_consumer.py
git commit -m "feat(M-SCHEDULER-CONSUMER): add cron text fallback"
```

## Этап 5: Agent fallback и replay safety для WIKI

**Соответствие GRACE:** `Phase-G.4`, `M-WIKI-RUNNER`, agent-часть `M-LLM-CODEX`.

**Файлы:**

- Modify: `src/ai_steward_wiki/llm/codex.py`
- Modify: `tests/unit/llm/test_codex.py`
- Modify: `src/ai_steward_wiki/wiki/runner.py`
- Modify: `src/ai_steward_wiki/wiki/streaming.py`
- Create: `tests/unit/wiki/test_runner_failover.py`
- Modify: `tests/unit/wiki/test_runner.py`
- Modify: `tests/unit/wiki/test_streaming.py`
- Modify: `tests/unit/wiki/test_runner_on_event.py`

### Task 5.1: Нормализовать Codex JSONL в существующий `StreamEvent`

- [ ] **Шаг 1: Написать event mapping tests**

```python
@pytest.mark.parametrize(
    ("raw_type", "expected_type", "evidence"),
    [
        ("agent_message", "assistant_chunk", EvidenceKind.READ_ONLY),
        ("command_execution", "tool_use", EvidenceKind.UNKNOWN),
        ("file_change", "tool_use", EvidenceKind.MUTATION),
        ("web_search", "tool_use", EvidenceKind.READ_ONLY),
    ],
)
def test_normalize_codex_item(raw_type, expected_type, evidence) -> None:
    event, observed = normalize_codex_event(codex_item(raw_type))
    assert event.type == expected_type
    assert observed.kind is evidence
```

Добавить tests для `turn.completed`, `turn.failed` и malformed JSONL.

- [ ] **Шаг 2: Реализовать закрытый mapping**

```python
def normalize_codex_event(raw: Mapping[str, Any]) -> tuple[StreamEvent, AttemptEvidence]:
    item = raw.get("item")
    if raw.get("type") == "item.completed" and isinstance(item, Mapping):
        item_type = item.get("type")
        if item_type == "agent_message":
            return StreamEvent(type="assistant_chunk", payload={"text": item.get("text", "")}), READ_ONLY
        if item_type == "file_change":
            return StreamEvent(type="tool_use", payload=dict(item)), MUTATION
        if item_type == "web_search":
            return StreamEvent(type="tool_use", payload=dict(item)), READ_ONLY
        if item_type == "command_execution":
            evidence = classify_command_evidence(item.get("command"))
            return StreamEvent(type="tool_use", payload=dict(item)), evidence
        return StreamEvent(type="unknown", payload=dict(raw)), UNKNOWN
    if raw.get("type") == "turn.completed":
        return StreamEvent(type="final", payload=dict(raw)), READ_ONLY
    if raw.get("type") in {"turn.failed", "error"}:
        raise CodexOutputError("codex agent turn failed")
    return StreamEvent(type="unknown", payload=dict(raw)), UNKNOWN
```

Command allowlist включает только доказанно read-only команды.
Остальные команды дают `EvidenceKind.UNKNOWN` или `MUTATION`.

- [ ] **Шаг 3: Проверить Codex agent normalization**

Run: `uv run pytest tests/unit/llm/test_codex.py -q`

Expected: PASS.

### Task 5.2: Собирать Claude evidence и typed limit из stream

- [ ] **Шаг 1: Написать evidence tests**

```python
def test_write_tool_marks_mutation() -> None:
    evidence = evidence_from_claude_event(tool_event("Write"))
    assert evidence.kind is EvidenceKind.MUTATION


def test_unknown_tool_fails_closed() -> None:
    evidence = evidence_from_claude_event(tool_event("CustomTool"))
    assert evidence.kind is EvidenceKind.UNKNOWN


async def test_successful_on_event_marks_delivery() -> None:
    tracker = AttemptEvidenceTracker()
    await tracker.notify(assistant_event("partial"), successful_callback)
    assert tracker.evidence.kind is EvidenceKind.DELIVERED
```

- [ ] **Шаг 2: Реализовать monotonic evidence tracker**

```python
_EVIDENCE_PRIORITY = {
    EvidenceKind.READ_ONLY: 0,
    EvidenceKind.UNKNOWN: 1,
    EvidenceKind.DELIVERED: 2,
    EvidenceKind.MUTATION: 3,
}


@dataclass
class AttemptEvidenceTracker:
    evidence: AttemptEvidence = field(
        default_factory=lambda: AttemptEvidence(EvidenceKind.READ_ONLY)
    )

    def observe(self, evidence: AttemptEvidence) -> None:
        if _EVIDENCE_PRIORITY[evidence.kind] > _EVIDENCE_PRIORITY[self.evidence.kind]:
            self.evidence = evidence
```

Успешный streaming callback даёт `DELIVERED`.
Исключение callback даёт `UNKNOWN`.
Write, Edit, MultiEdit и file-change дают `MUTATION`.

- [ ] **Шаг 3: Поднять typed 429 с accumulated evidence**

Финальный Claude event анализируется через `parse_claude_subscription_limit`.
При лимите runner поднимает `ProviderLimitError` с текущим evidence.
Обычный non-zero exit остаётся `WikiRunnerError`.

```python
def _raise_if_claude_limit(
    events: Sequence[StreamEvent],
    evidence: AttemptEvidence,
) -> None:
    for event in reversed(events):
        if event.type != "final":
            continue
        limit = parse_claude_subscription_limit(event.payload)
        if limit is not None:
            raise ProviderLimitError(
                provider="claude",
                reset_at=limit.reset_at,
                evidence=evidence,
            )
        return
```

- [ ] **Шаг 4: Проверить streaming compatibility**

Run: `uv run pytest tests/unit/wiki/test_streaming.py tests/unit/wiki/test_runner_on_event.py -q`

Expected: PASS.

### Task 5.3: Удерживать один WIKI lock вокруг двух безопасных попыток

- [ ] **Шаг 1: Написать end-to-end runner tests**

```python
async def test_safe_limit_runs_codex_under_same_lock(runner_fixture) -> None:
    result = await runner_fixture.run(claude_events=read_only_limit_events())
    assert result.events[-1].type == "final"
    assert runner_fixture.acquirer.calls == [("W", runner_fixture.wiki_path)]
    assert runner_fixture.codex.calls == 1


async def test_mutating_limit_blocks_codex(runner_fixture) -> None:
    with pytest.raises(ReplayBlockedError):
        await runner_fixture.run(claude_events=write_then_limit_events())
    assert runner_fixture.codex.calls == 0


async def test_streamed_limit_blocks_codex(runner_fixture) -> None:
    with pytest.raises(ReplayBlockedError):
        await runner_fixture.run(
            claude_events=assistant_then_limit_events(),
            on_event=successful_callback,
        )
    assert runner_fixture.codex.calls == 0
```

Добавить matrix для read-only, write, media, web, digest и cancellation.

- [ ] **Шаг 2: Расширить `_RunConfig` optional dependencies**

```python
@dataclass
class _RunConfig:
    model: str = "claude-sonnet-4-5"
    timeout_s: float = 300.0
    term_grace_s: float = 10.0
    claude_config_dir: Path = field(default_factory=default_claude_config_dir)
    allowed_tools: list[str] | None = None
    web_search: bool = False
    failover_policy: FailoverPolicy | None = None
    codex_adapter: CodexCliAdapter | None = None
```

`None` сохраняет прежнее поведение и существующие tests.

- [ ] **Шаг 3: Выделить provider attempts внутри существующего lock**

```python
async with acquirer.acquire(wiki_id, wiki_path):
    if config.failover_policy is None or config.codex_adapter is None:
        events, exit_code = await _run_claude_attempt(
            spawner=spawner,
            argv=claude_argv,
            env=claude_env,
            cwd=claude_cwd,
            stdin_data=stdin_data,
            timeout_s=config.timeout_s,
            term_grace_s=config.term_grace_s,
            on_event=on_event,
        )
    else:
        events, exit_code = await config.failover_policy.execute(
            run_kind=_run_kind(config),
            correlation_id=correlation_id,
            claude=lambda: _run_claude_attempt(
                spawner=spawner,
                argv=claude_argv,
                env=claude_env,
                cwd=claude_cwd,
                stdin_data=stdin_data,
                timeout_s=config.timeout_s,
                term_grace_s=config.term_grace_s,
                on_event=on_event,
            ),
            codex=lambda: _run_codex_attempt(
                adapter=config.codex_adapter,
                request=codex_request,
                on_event=on_event,
            ),
        )
    _persist_transcript(events, transcript_target)
    transcript_path = transcript_target
```

Codex read-only и web используют neutral cwd.
Codex write получает selected WIKI как единственный writable root.
Media и additional WIKIs передаются абсолютными read-only paths в prompt.

- [ ] **Шаг 4: Проверить WIKI suite**

Run: `uv run pytest tests/unit/wiki/test_runner.py tests/unit/wiki/test_runner_aggregate.py tests/unit/wiki/test_runner_on_event.py tests/unit/wiki/test_runner_failover.py tests/unit/wiki/test_streaming.py -q`

Expected: PASS.

- [ ] **Шаг 5: Обновить MODULE_MAP и зафиксировать этап**

```bash
git add src/ai_steward_wiki/llm/codex.py src/ai_steward_wiki/wiki/runner.py src/ai_steward_wiki/wiki/streaming.py tests/unit/llm/test_codex.py tests/unit/wiki
git commit -m "feat(M-WIKI-RUNNER): add safe agent failover"
```

## Этап 6: Runtime wiring, ошибки и observability

**Соответствие GRACE:** часть `Phase-G.5`, `M-RUNTIME-WIRING`, observability `M-LLM-FAILOVER`.

**Файлы:**

- Modify: `src/ai_steward_wiki/__main__.py`
- Modify: `src/ai_steward_wiki/logging_events.py`
- Modify: `src/ai_steward_wiki/tg/pipeline.py`
- Modify: `tests/unit/test_runtime_wiring.py`
- Modify: `tests/unit/test_logging_events_catalog.py`
- Create: `tests/unit/test_llm_runtime_wiring.py`
- Modify: `tests/unit/tg/test_pipeline.py`

### Task 6.1: Зафиксировать безопасный log catalog и counters

- [ ] **Шаг 1: Добавить catalog tests**

```python
def test_llm_event_catalog_is_stable() -> None:
    assert LLM_PROVIDER_SELECTED == "llm.provider.selected"
    assert LLM_FAILOVER_TRIGGERED == "llm.failover.triggered"
    assert LLM_CIRCUIT_CHANGED == "llm.circuit.changed"
    assert LLM_PROVIDER_FAILED == "llm.provider.failed"
    assert LLM_PROVIDER_RECOVERED == "llm.provider.recovered"
    assert LLM_REPLAY_BLOCKED == "llm.replay.blocked"
```

- [ ] **Шаг 2: Добавить constants и event payload**

```python
@dataclass(frozen=True, slots=True)
class FailoverEvent:
    event: str
    provider: str
    model: str | None
    run_kind: str
    correlation_id: str
    outcome: str
    latency_ms: int | None = None
    previous_state: str | None = None
    next_state: str | None = None
    reason: str | None = None
    evidence: str | None = None
```

Запрещённые fields: prompt, user content, auth data, session identifier.
Policy хранит counters primary success, fallback success, dual failure, blocked replay и recovery.

- [ ] **Шаг 3: Проверить logging tests**

Run: `uv run pytest tests/unit/test_logging_events_catalog.py tests/unit/llm/test_failover.py -q`

Expected: PASS.

### Task 6.2: Создать один runtime policy и adapter

- [ ] **Шаг 1: Написать composition tests**

```python
async def test_runtime_shares_one_policy_across_all_paths(settings) -> None:
    runtime = await build_llm_runtime(settings)
    assert runtime.classifier.policy is runtime.policy
    assert runtime.schema_generator.policy is runtime.policy
    assert runtime.cron_consumer.policy is runtime.policy
    assert all(config.failover_policy is runtime.policy for config in runtime.wiki_configs)


async def test_failed_codex_readiness_keeps_claude_startup(settings) -> None:
    runtime = await build_llm_runtime(settings, readiness=not_ready("login_missing"))
    assert runtime.codex_adapter is None
    assert runtime.classifier.name == "claude_cli"
```

- [ ] **Шаг 2: Добавить runtime factory**

```python
@dataclass(frozen=True, slots=True)
class LlmRuntime:
    policy: FailoverPolicy
    codex: CodexCliAdapter | None


async def _build_llm_runtime(settings: Settings) -> LlmRuntime:
    policy = FailoverPolicy(
        cooldown_s=settings.llm_failover_cooldown_s,
        on_event=_log_failover_event,
    )
    if not settings.llm_codex_enabled:
        return LlmRuntime(policy=policy, codex=None)
    adapter = CodexCliAdapter.from_settings(settings)
    readiness = await adapter.check_readiness()
    if not readiness.ready:
        logger.warning(
            LLM_PROVIDER_FAILED,
            provider="codex",
            reason=readiness.reason,
            outcome="fallback_disabled",
        )
        return LlmRuntime(policy=policy, codex=None)
    return LlmRuntime(policy=policy, codex=adapter)
```

Readiness выполняется после Settings и до wiring adapters.
Она не запускает модель и не блокирует Claude-backed startup.

- [ ] **Шаг 3: Внедрить shared objects во все пять `_RunConfig`**

Внедрить policy и adapter в classifier, router, librarian, digest, web и schema generator.
Передать их также в `CronConsumer`.
Anthropic API backend не оборачивать Claude subscription fallback.

```python
def _wiki_run_config(
    *,
    settings: Settings,
    llm_runtime: LlmRuntime,
    allowed_tools: list[str] | None = None,
    web_search: bool = False,
    timeout_s: float | None = None,
) -> _RunConfig:
    return _RunConfig(
        model=settings.wiki_runner_model,
        timeout_s=timeout_s or settings.wiki_runner_timeout_s,
        term_grace_s=settings.wiki_runner_term_grace_s,
        claude_config_dir=default_claude_config_dir(),
        allowed_tools=allowed_tools,
        web_search=web_search,
        failover_policy=llm_runtime.policy if llm_runtime.codex is not None else None,
        codex_adapter=llm_runtime.codex,
    )
```

Все пять текущих `_RunConfig(...)` заменяются вызовами `_wiki_run_config(...)`.

- [ ] **Шаг 4: Проверить runtime wiring**

Run: `uv run pytest tests/unit/test_runtime_wiring.py tests/unit/test_llm_runtime_wiring.py -q`

Expected: PASS.

### Task 6.3: Вернуть одну понятную ошибку при отказе обоих провайдеров

- [ ] **Шаг 1: Добавить Telegram boundary tests**

```python
async def test_dual_provider_failure_sends_one_recoverable_message(pipeline_fixture) -> None:
    pipeline_fixture.runner.error = ProvidersUnavailableError(
        RuntimeError("claude limit"),
        RuntimeError("codex limit"),
    )
    await pipeline_fixture.on_text(message("source remains in Telegram"))
    assert pipeline_fixture.sender.messages == [
        "Claude и Codex сейчас недоступны. Исходное сообщение сохранено — повторите позже."
    ]
```

- [ ] **Шаг 2: Добавить отдельный catch до общего provider error**

```python
LLM_PROVIDERS_UNAVAILABLE_RU = (
    "Claude и Codex сейчас недоступны. "
    "Исходное сообщение сохранено — повторите позже."
)

except ProvidersUnavailableError:
    await self._sender.send_message(chat_id, LLM_PROVIDERS_UNAVAILABLE_RU)
    return
```

Не создавать автоматический retry job.
Cron использует такой же смысл, но сохраняет существующий job status `failed`.

- [ ] **Шаг 3: Запустить runtime и pipeline regressions**

Run: `uv run pytest tests/unit/test_runtime_wiring.py tests/unit/tg tests/unit/classifier tests/unit/wiki tests/unit/scheduler/test_consumer.py -q`

Expected: PASS.

- [ ] **Шаг 4: Обновить MODULE_MAP и зафиксировать этап**

```bash
git add src/ai_steward_wiki/__main__.py src/ai_steward_wiki/logging_events.py src/ai_steward_wiki/tg/pipeline.py tests/unit/test_runtime_wiring.py tests/unit/test_logging_events_catalog.py tests/unit/test_llm_runtime_wiring.py tests/unit/tg
git commit -m "feat(M-RUNTIME-WIRING): wire shared provider fallback"
```

## Этап 7: Deployment, runbooks и итоговая верификация

**Соответствие GRACE:** остаток `Phase-G.5`, `DF-LLM-READINESS`, `NFR-14`.

**Файлы:**

- Modify: `deploy/systemd/aisw-bot.service`
- Modify: `docs/runbook/deploy.md`
- Modify: `docs/runbook/operations.md`
- Modify: `docs/knowledge-graph.xml`
- Modify: `docs/verification-plan.xml`
- Modify: `docs/development-plan.xml`
- Create later during Finish: `docs/reports/20260705-codex-subscription-fallback-report.md`

Файлы `.env*` не читаются и не меняются.
Настройки описываются в runbooks и systemd unit.

### Task 7.1: Подготовить production unit и операторскую установку

- [ ] **Шаг 1: Добавить dedicated subscription home в unit**

```ini
Environment=AISW_CODEX_HOME=/var/lib/ai-steward-wiki/codex
```

Не добавлять token или API key.
Service продолжает работать под текущим `User=bgs`.

- [ ] **Шаг 2: Добавить точные deployment commands**

```bash
sudo npm install --global @openai/codex@0.142.5
sudo install -d -o bgs -g bgs -m 0700 /var/lib/ai-steward-wiki/codex
sudo -u bgs env CODEX_HOME=/var/lib/ai-steward-wiki/codex codex login --device-auth
sudo -u bgs env CODEX_HOME=/var/lib/ai-steward-wiki/codex codex login status
codex --version
```

Ожидаемая версия: `codex-cli 0.142.5`.
Login выполняет оператор вне bot runtime.

- [ ] **Шаг 3: Проверить systemd syntax**

Run: `systemd-analyze verify deploy/systemd/aisw-bot.service`

Expected: exit 0.

### Task 7.2: Документировать smoke, incidents и recovery

- [ ] **Шаг 1: Добавить deployment smoke**

Smoke выполняет четыре безопасных проверки:

1. `gpt-5.4-mini` с reasoning `low` и JSON Schema.
2. `gpt-5.5` с reasoning `medium` и JSONL.
3. Read-only run не может создать файл.
4. Workspace-write меняет файл только внутри временной selected WIKI.

Smoke использует синтетический текст без пользовательских данных.

- [ ] **Шаг 2: Добавить operational diagnostics**

```bash
journalctl -u aisw-bot -o cat | grep -E 'llm\.(provider|failover|circuit|replay)'
sudo -u bgs env CODEX_HOME=/var/lib/ai-steward-wiki/codex codex login status
pgrep -a -P "$(systemctl show aisw-bot -p MainPID --value)"
```

Runbook описывает:

- `claude -> codex` при typed 429;
- `codex -> probe -> claude` после recovery;
- login expiration без остановки Claude;
- dual failure без автоматического replay;
- безопасный повтор пользователем.
- эксплуатацию только на доверенном private VPS.

- [ ] **Шаг 3: Проверить документацию на запрещённые secrets**

Run: `rg -n 'OPENAI_API_KEY|token=|session_id' deploy/systemd docs/runbook`

Expected: нет новых credential values.

- [ ] **Шаг 4: Зафиксировать deployment slice**

```bash
git add deploy/systemd/aisw-bot.service docs/runbook/deploy.md docs/runbook/operations.md
git commit -m "docs(runbook): add Codex subscription operations"
```

### Task 7.3: Синхронизировать GRACE и выполнить полный gate

- [ ] **Шаг 1: Обновить source/test semantic maps**

Обновить MODULE_CONTRACT версии и MODULE_MAP exports каждого изменённого модуля.
Не менять public contracts, не затронутые утверждённым design.

- [ ] **Шаг 2: Refresh derived artifacts**

Run: `grace-refresh`

Expected: `docs/knowledge-graph.xml` отражает новые exports и dependencies.

Run: `grace-refresh --verify`

Expected: `docs/verification-plan.xml` отражает новые tests и log markers.

- [ ] **Шаг 3: Выполнить feature tests**

Создать `tests/integration/llm/test_fake_provider_chain.py`.
Тест запускает fake Claude CLI и fake Codex CLI как отдельные executables.
Он доказывает typed 429, same-operation fallback и один final result.

```bash
uv run pytest tests/unit/llm tests/unit/claude_cli/test_common.py tests/unit/classifier tests/unit/wiki tests/unit/scheduler/test_consumer.py tests/unit/test_settings.py tests/unit/test_runtime_wiring.py tests/unit/test_llm_runtime_wiring.py -q
RUN_INTEGRATION=1 uv run pytest tests/integration/llm/test_fake_codex_cli.py tests/integration/llm/test_fake_provider_chain.py -q
```

Expected: PASS.

- [ ] **Шаг 4: Выполнить полный project gate**

```bash
make lint
uv run pytest -q
grace lint --path . --profile standard
```

Expected:

- Ruff PASS;
- Ruff format PASS;
- Mypy PASS;
- pytest PASS;
- GRACE: 0 errors и 0 warnings.

- [ ] **Шаг 5: Проверить FR/NFR coverage**

Coverage anchors:

- FR-1: единая provider chain создаётся на этапах 1 и 6.
- FR-2: structured Claude 429 распознаётся на этапах 1, 3 и 5.
- FR-3: generic failures не переключают provider на этапах 1, 3 и 5.
- FR-4: same-operation fallback проверяется на этапах 1, 3, 4 и 5.
- FR-5: `gpt-5.4-mini` structured mapping реализуется на этапе 3.
- FR-6: `gpt-5.5` medium mapping реализуется на этапах 4 и 5.
- FR-7: reset-time и пропуск Claude реализуются на этапе 1.
- FR-8: single-flight probe и recovery реализуются на этапе 1.
- FR-9: общий атомарный state реализуется на этапах 1 и 6.
- FR-10: replay guard реализуется на этапах 1, 4 и 5.
- FR-11: существующая Pydantic validation сохраняется на этапе 3.
- FR-12: JSONL, usage, failures и transcript нормализуются на этапе 5.
- FR-13: capability profiles проверяются на этапах 2 и 5.
- FR-14: timeout, cancellation, lock и partial-result regressions проверяются на этапах 4 и 5.
- FR-15: non-model readiness и model smoke реализуются на этапах 2 и 7.
- FR-16: Claude-only startup при неготовом Codex реализуется на этапе 6.
- FR-17: dual-provider Russian error реализуется на этапах 4 и 6.
- FR-18: provider, binary, model, reasoning и cooldown settings реализуются на этапе 2.
- NFR-1: selection p95 проверяется на этапе 1.
- NFR-2: circuit запрещает новые Claude processes до probe на этапе 1.
- NFR-3: atomic process-local state проверяется на этапах 1 и 6.
- NFR-4: один commit и одна delivery проверяются на этапах 4 и 5.
- NFR-5: least-privilege profiles реализуются на этапах 2 и 5.
- NFR-6: credential и content redaction проверяются на этапах 2, 6 и 7.
- NFR-7: private trusted VPS boundary документируется на этапе 7.
- NFR-8: Telegram, schema, WIKI, DB и job compatibility проверяются на этапах 3–7.
- NFR-9: structured fields реализуются на этапе 6.
- NFR-10: counters реализуются на этапе 6.
- NFR-11: pinned CLI и configurable models реализуются на этапах 2 и 7.
- NFR-12: план не добавляет Python dependency или DB migration.
- NFR-13: unit и fake-CLI integration matrix выполняется на этапах 1–7.
- NFR-14: полный quality gate и GRACE sync выполняются на этапе 7.

- [ ] **Шаг 6: Зафиксировать synchronized artifacts**

```bash
git add docs/knowledge-graph.xml docs/verification-plan.xml docs/development-plan.xml tests/integration/llm/test_fake_provider_chain.py
git commit -m "docs(M-LLM-FAILOVER): synchronize GRACE verification"
```

### Task 7.4: Finish workflow после успешной реализации

- [ ] Создать completion report через `_report`.
- [ ] Провести code review через `requesting-code-review`.
- [ ] Исправить только подтверждённые review findings.
- [ ] Повторить полный project gate после исправлений.
- [ ] Закрыть `aisw-8gw` через `bd close aisw-8gw`.
- [ ] Не выполнять `git push` без отдельного явного запроса пользователя.
