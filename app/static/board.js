(() => {
  const board = document.querySelector("#ticket-board");
  const toast = document.querySelector("#board-toast");
  if (!board || !toast) return;

  let draggedCard = null;
  let toastTimer;

  const announce = (message, isError = false) => {
    toast.textContent = message;
    toast.classList.toggle("is-error", isError);
    toast.classList.add("is-visible");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 2600);
  };

  const refreshColumn = (stack) => {
    const column = stack.closest(".board-column");
    const count = stack.querySelectorAll("[data-ticket-card]").length;
    column.querySelector("[data-count]").textContent = count;

    let emptyState = stack.querySelector("[data-empty]");
    if (count === 0 && !emptyState) {
      emptyState = document.createElement("div");
      emptyState.className = "column-empty";
      emptyState.dataset.empty = "";
      emptyState.textContent = "Перетащите тикет сюда";
      stack.append(emptyState);
    } else if (count > 0 && emptyState) {
      emptyState.remove();
    }
  };

  board.addEventListener("dragstart", (event) => {
    const grip = event.target.closest(".ticket-grip");
    if (!grip) return;
    draggedCard = grip.closest("[data-ticket-card]");
    if (!draggedCard) return;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", draggedCard.dataset.ticketId);
    requestAnimationFrame(() => draggedCard?.classList.add("is-dragging"));
  });

  board.addEventListener("dragend", () => {
    draggedCard?.classList.remove("is-dragging");
    draggedCard = null;
    board.querySelectorAll(".is-drop-target").forEach((column) => column.classList.remove("is-drop-target"));
  });

  board.querySelectorAll("[data-status]").forEach((stack) => {
    const column = stack.closest(".board-column");
    column.addEventListener("dragover", (event) => {
      if (!draggedCard) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      column.classList.add("is-drop-target");
    });
    column.addEventListener("dragleave", (event) => {
      if (!column.contains(event.relatedTarget)) column.classList.remove("is-drop-target");
    });
    column.addEventListener("drop", async (event) => {
      event.preventDefault();
      column.classList.remove("is-drop-target");
      if (!draggedCard) return;

      const oldStack = draggedCard.parentElement;
      if (oldStack === stack) return;

      const card = draggedCard;
      const ticketId = card.dataset.ticketId;
      const newStatus = stack.dataset.status;
      card.classList.add("is-saving");
      try {
        const response = await fetch(`/api/tickets/${ticketId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ status: newStatus }),
        });
        if (!response.ok) throw new Error("Не удалось сохранить новый статус");

        const statusSelect = card.querySelector(".quick-status select");
        if (statusSelect) statusSelect.value = newStatus;
        stack.querySelector("[data-empty]")?.remove();
        stack.append(card);
        refreshColumn(oldStack);
        refreshColumn(stack);
        announce(`Тикет ${card.querySelector(".ticket-key").textContent} перемещён: ${newStatus}`);
      } catch (error) {
        announce(error.message || "Не удалось переместить тикет. Попробуйте ещё раз.", true);
      } finally {
        card.classList.remove("is-saving", "is-dragging");
        draggedCard = null;
      }
    });
  });
})();
