(() => {
  const count = document.querySelector("#notification-count");
  const previousIds = new Set([...document.querySelectorAll("[data-notification-id]")].map((node) => node.dataset.notificationId));
  let hasNotificationBaseline = previousIds.size > 0;
  const safeText = (node, value) => { if (node) node.textContent = value || ""; };

  async function refreshNotifications() {
    try {
      const response = await fetch("/api/agent/notifications", { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      const notifications = await response.json();
      if (count) {
        count.textContent = notifications.length;
        count.hidden = notifications.length === 0;
      }
      for (const item of notifications) {
        const id = String(item.id);
        if (previousIds.has(id)) continue;
        previousIds.add(id);
        if (hasNotificationBaseline && "Notification" in window && Notification.permission === "granted") {
          new Notification(item.title, { body: item.message, tag: `ticket-run-${item.run_id}` });
        }
      }
      hasNotificationBaseline = true;
    } catch (_) { /* The tracker can still be used if background refresh is unavailable. */ }
  }

  document.querySelector("[data-enable-notifications]")?.addEventListener("click", async (event) => {
    if (!("Notification" in window)) {
      event.currentTarget.textContent = "Браузер не поддерживает уведомления";
      return;
    }
    const permission = await Notification.requestPermission();
    event.currentTarget.textContent = permission === "granted" ? "Уведомления включены" : "Разрешение не выдано";
  });

  document.querySelector("[data-confirm-launch]")?.addEventListener("submit", (event) => {
    const projectSelect = event.currentTarget.querySelector("select");
    const projectName = projectSelect?.selectedOptions[0]?.textContent || "выбранный проект";
    const ticketName = document.querySelector(".detail-heading h1")?.textContent || "тикет";
    if (!window.confirm(`Запустить Codex для ${ticketName} в проекте ${projectName}?`)) event.preventDefault();
  });

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-mark-read]");
    if (!button) return;
    button.disabled = true;
    try {
      const response = await fetch(`/api/agent/notifications/${button.dataset.markRead}/read`, { method: "POST" });
      if (!response.ok) throw new Error("Не удалось отметить уведомление");
      button.closest("[data-notification-id]")?.classList.add("is-read");
      button.remove();
      refreshNotifications();
    } catch (_) { button.disabled = false; }
  });

  async function refreshRuns() {
    const runs = document.querySelectorAll("[data-agent-run]");
    await Promise.all([...runs].map(async (card) => {
      try {
        const response = await fetch(`/api/agent/runs/${card.dataset.agentRun}`, { headers: { Accept: "application/json" } });
        if (!response.ok) return;
        const run = await response.json();
        const status = card.querySelector("[data-run-status]");
        if (status) {
          status.textContent = run.status;
          status.className = `run-status run-status-${run.status.toLowerCase().replaceAll(" ", "-")}`;
        }
        safeText(card.querySelector("[data-run-progress]"), run.progress || (run.status === "Queued" ? "Ожидает worker" : ""));
        const errorNode = card.querySelector("[data-run-error]");
        if (errorNode) { safeText(errorNode, run.error); errorNode.hidden = !run.error; }
        const report = card.querySelector("[data-run-report]");
        if (report) safeText(report, run.final_report);
        const details = card.querySelector("[data-run-report-details]");
        if (details) details.hidden = !run.final_report;
        const events = card.querySelector("[data-run-events]");
        if (events) {
          events.replaceChildren(...run.events.map((item) => {
            const li = document.createElement("li");
            const time = document.createElement("time");
            time.textContent = new Date(item.created_at).toLocaleTimeString();
            const strong = document.createElement("strong");
            strong.textContent = item.type;
            li.append(time, " ", strong, ` ${item.message}`);
            return li;
          }));
        }
      } catch (_) { /* Keep the last rendered run state while the API is unavailable. */ }
    }));
  }

  refreshNotifications();
  refreshRuns();
  window.setInterval(refreshNotifications, 10000);
  window.setInterval(refreshRuns, 5000);
})();
