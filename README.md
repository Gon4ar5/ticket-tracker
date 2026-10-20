# Tiny Ticket Tracker

Небольшой трекер задач для практики workflow «тикет → спецификация → AI-агент → код → CI → ревью».

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

## Local Codex ticket runner

The tracker UI/API runs in Docker. Codex runs on the host through the small worker in
`worker/runner.py`; it uses the locally installed Codex CLI login and never puts an API
key in the tracker container. The tracker port is bound to `127.0.0.1` only.

1. Copy `worker/worker.example.toml` to `~/.config/tiny-ticket-tracker/worker.toml` and
   edit its example project paths (`~/projects/vacancy-search` and `~/projects/ticket-tracker`)
   and base branches to match this computer. Only those Git checkouts can be
   selected in the ticket form. The starter config lists `ticket-tracker` and
   `vacancy-search`; the current `ticket-tracker` directory has no `.git`, so the worker
   skips it until the user places a Git checkout there. The worker does not initialize
   repositories or touch a checkout's current branch/working tree.
2. Start the tracker with `docker compose up -d --build`.
3. Make sure `codex --version` works and Codex is already signed in locally. Start the
   worker from this project directory:

   ```bash
   python3 worker/runner.py --config ~/.config/tiny-ticket-tracker/worker.toml
   ```

   Stop it with `Ctrl-C`. `--once` claims at most one queued run and exits, useful for
   debugging. Worker logs stay in the terminal.
4. Open a ticket with a description, acceptance criteria and approved specification
   reference. Choose one of the live worker projects and click **Взять в работу**.

Each run resolves its configured base branch to an explicit commit, creates a unique
`agent/<ticket>-<run>-<slug>` branch and worktree under
`~/.local/share/tiny-ticket-tracker/worktrees`, then runs `codex exec --json` with
`--sandbox workspace-write`. The original working copy is not used as the agent's
workspace. On success, the ticket moves to `Review`; on failure it stays `In Progress`.
The tracker keeps events, report, worktree path and an unread notification. No PR, merge
or deployment is performed. Browser notifications are optional and require enabling
them on a ticket page while the tracker tab is open.

A `Waiting for user` run stays in history with its notification, but it is a finished
attempt rather than an active worker. After resolving the question and updating the
ticket, the user can start a new run; only `Queued` and `Running` runs block a new launch.

A `Waiting for user` run stays in history with its notification, but it is a finished
attempt rather than an active worker. After resolving the question and updating the
ticket, the user can start a new run; only `Queued` and `Running` runs block a new launch.

A `Waiting for user` run stays in history with its notification, but it is a finished
attempt rather than an active worker. After resolving the question and updating the
ticket, the user can start a new run; only `Queued` and `Running` runs block a new launch.

A `Waiting for user` run stays in history with its notification, but it is a finished
attempt rather than an active worker. After resolving the question and updating the
ticket, the user can start a new run; only `Queued` and `Running` runs block a new launch.

A `Waiting for user` run stays in history with its notification, but it is a finished
attempt rather than an active worker. After resolving the question and updating the
ticket, the user can start a new run; only `Queued` and `Running` runs block a new launch.

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
