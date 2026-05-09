# Q-A-06: Planner.json SSoT

**Tier:** A
**Источник:** [overview §9 п.6](../raw/20260507-ai-steward-wiki-only-overview.md), §8.4

## Формулировка

Читать из основного `ai-steward` (cross-service coupling) vs мигрировать planner внутрь каждой WIKI vs дублировать (drift-риск).

## Варианты

1. **A. Cross-service read.** Wiki-сервис на отдельной VPS читает `planner.json` основного `ai-steward` через сеть/общий volume. Минусы: coupling между двумя сервисами на разных машинах.
2. **B. Migrate planner внутрь WIKI.** Каждая `<Domain>-WIKI/planner.json`. Плюсы: полная автономность wiki-сервиса. Минусы: нужно перенести planner-сервис и его UI/CRUD.
3. **C. Дублировать с sync.** Опасно — drift гарантирован.

## Решение

- [x] **Вариант C в строгой форме: `planner.json` НЕ существует в сервисе вообще.** Только `jobs.db` как единственный SSoT. Юзер подтвердил 2026-05-08 («NO planner.json AT ALL. DELETE IT»). См. [D-005](../decisions/D-005-no-planner-json.md) (accepted).
- [x] оформлено как [D-005](../decisions/D-005-no-planner-json.md)

## Связанные

1. [Job-model](../entities/job-model.md) (digest_job читает planner.json)
