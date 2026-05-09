# Router-agent

**Тип:** entity
**Статус:** draft
**Источники:** [overview §8.3.1](../raw/20260507-ai-steward-wiki-only-overview.md)

## Суть

Claude-инстанс, запускаемый в `Inbox-WIKI/` с **router-промптом**. Выполняет: классификацию контента из `raw/`, выбор целевой WIKI, формирование уточняющих вопросов с inline-кнопками, перемещение файла в целевую WIKI после подтверждения, создание cron-задач.

## Отличие от Classifier

1. **Classifier** = первый из двух вызовов в общем pipeline (выбор WIKI/сессии, fast-path Haiku допустим).
2. **Router-agent** = специализированный Claude CLI режим внутри `Inbox-WIKI`, всегда heavy path. Делает не только классификацию, но и follow-up диалог + действия.

В overview эти роли частично сливаются (§8.3.1 Router-промпт vs §2.1 Классификатор). Развилка для уточнения — см. [Q-A-04](../questions/Q-A-04-classifier-engine.md).

## Связанные

1. [Inbox-WIKI](inbox-wiki.md)
2. [Classifier](classifier.md)
3. [Smart inbox routing](../concepts/smart-inbox-routing.md)
