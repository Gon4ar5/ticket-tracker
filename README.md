# Tiny Ticket Tracker

Лёгкий трекер задач для практики workflow «тикет → спецификация → AI-агент → код → CI → ревью».

## MVP

- Доска с колонками `Backlog`, `In Progress`, `Review`, `Done`.
- Перетаскивание тикетов между колонками с сохранением нового статуса через API.
- Создание и редактирование тикетов с приоритетом, описанием и критериями приёмки.
- Ссылки из тикета на спецификацию, ветку и pull request.
- REST API для чтения и изменения тикетов; схема доступна в `/docs`.
- SQLite для локального хранения данных.

## Запуск через Docker Compose

```bash
docker compose up --build
```

Откройте [http://localhost:8000](http://localhost:8000). Данные сохраняются в Docker volume `ticket_data`.
Чтобы сменить статус на доске, перетащите значок `⠿` в карточке в нужную колонку. Статус сохранится автоматически; также можно поменять его в списке на карточке.

## Локальный запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install .
uvicorn app.main:app --reload
```

База по умолчанию создаётся в `data/tickets.db`. Путь можно изменить переменной `TICKET_DB_PATH`.

## API

- `GET /api/tickets` — список тикетов; можно передать `status`.
- `POST /api/tickets` — создать тикет.
- `GET /api/tickets/{id}` — получить тикет.
- `PATCH /api/tickets/{id}` — обновить поля тикета.

## Codex Desktop ticket workflow

The tracker web app runs in Docker. The host worker in `worker/runner.py` talks to the
tracker over its local API and uses the signed-in Codex CLI/app-server plus GitHub CLI.
Neither the Codex login nor GitHub credentials are copied into the tracker container.
The tracker port is bound to `127.0.0.1` only.

1. Copy `worker/worker.example.toml` to `~/.config/tiny-ticket-tracker/worker.toml` and
   replace the example project paths and base branches with real local Git checkouts.
   The worker only offers projects from this allowlist and never accepts paths or shell
   commands from the browser.
2. Start the tracker with `docker compose up -d --build`.
3. Make sure Codex Desktop and the `codex` CLI are installed and signed in to the same
   local Codex profile. Check `gh auth status` for GitHub CLI authentication.
4. Start the worker from the repository directory:

   ```bash
   python3 worker/runner.py --config ~/.config/tiny-ticket-tracker/worker.toml
   ```

   Stop it with `Ctrl-C`. `--once` claims at most one queued run and exits. Worker logs
   stay in the terminal.
5. Open a ready ticket with a description, acceptance criteria and approved
   specification reference. Choose a live project and click **Взять в работу**.

The worker resolves the configured base branch, creates a unique branch and Git worktree,
then creates a persistent Codex app-server thread for that worktree. It opens the thread
in Codex Desktop so questions and replies happen in that chat. If requirements are clear,
the agent starts implementation right away. The worker monitors later turns in the same
thread, runs a separate read-only review, creates a draft PR, and allows one CI-fix cycle.
It never merges or deploys. The ticket stores the chat ID/link, branch, worktree, events,
report and PR URL; notifications remain available in the tracker after closing the page.
Browser notifications are optional and require enabling them while the tracker tab is open.

The Codex URI handler opens a thread directly in the desktop app. Threads created through
the app-server may not appear in the desktop sidebar; the ticket keeps a direct link to
reopen the chat. This behavior depends on the current Codex Desktop URI handler and
app-server versions; see [the Codex issue documenting the current behavior](https://github.com/openai/codex/issues/30895),
and check the linked chat before relying on a run.

`TICKET_AGENT_TOKEN` is the shared local worker-to-tracker credential. The example uses
`local-dev-only-change-me`; change it in the worker config and in the shell environment
used to start Docker Compose if the tracker will be reachable by anyone else on the
computer. The app is intentionally local-only and has no user authentication.

### Worker config format

See [`worker/worker.example.toml`](worker/worker.example.toml). Project IDs in ticket
forms always come from the worker's TOML allowlist; HTTP requests cannot supply a path
or shell command. References to specification files are read only if the resolved file
stays inside the selected repository; otherwise the reference path is passed to Codex
without reading the external file.

Пример создания:

```bash
curl -X POST http://localhost:8000/api/tickets \
  -H 'Content-Type: application/json' \
  -d '{"title":"Добавить поиск тикетов","description":"Поиск по ключу и названию","acceptance_criteria":"Фильтр находит тикет по части названия","priority":"High"}'
```

## Ограничения первой версии

Это локальный учебный MVP без пользователей, авторизации, комментариев и истории изменений. Не публикуйте его в открытый интернет без добавления аутентификации и защиты форм.
