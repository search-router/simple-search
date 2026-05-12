// Theme toggle, locale auto-submit, image lightbox, and minor UX polish.
(() => {
  const root = document.documentElement;
  const STORAGE_KEY = "search-service:theme";
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") {
    root.dataset.theme = stored;
  }

  const themeButton = document.querySelector("[data-theme-toggle]");
  if (themeButton) {
    themeButton.addEventListener("click", () => {
      const current = root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = current === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      localStorage.setItem(STORAGE_KEY, next);
    });
  }

  document.querySelectorAll("[data-auto-submit]").forEach((el) => {
    el.addEventListener("change", () => el.closest("form")?.submit());
  });

  // Lightbox: shared <dialog> with three slots.
  const dialog = document.getElementById("lightbox");
  if (!dialog || typeof dialog.showModal !== "function") {
    return;
  }

  const imgEl = dialog.querySelector("[data-lightbox-image]");
  const titleEl = dialog.querySelector("[data-lightbox-title]");
  const linkEl = dialog.querySelector("[data-lightbox-link]");
  const metaEl = dialog.querySelector("[data-lightbox-meta]");

  document.addEventListener("click", (event) => {
    const tile = event.target.closest("[data-lightbox]");
    if (!tile) return;
    event.preventDefault();
    if (imgEl) imgEl.src = tile.dataset.imageUrl || tile.querySelector("img")?.src || "";
    if (titleEl) titleEl.textContent = tile.dataset.title || "";
    if (linkEl) {
      linkEl.href = tile.dataset.pageUrl || "#";
      linkEl.textContent = tile.dataset.domain || tile.dataset.pageUrl || "";
    }
    if (metaEl) metaEl.textContent = tile.dataset.dimensions || "";
    dialog.showModal();
  });

  dialog.addEventListener("click", (event) => {
    const rect = dialog.getBoundingClientRect();
    const insideContent = event.target.closest("[data-lightbox-content]");
    if (!insideContent) {
      dialog.close();
    }
  });

  dialog.querySelector("[data-lightbox-close]")?.addEventListener("click", () => dialog.close());
})();
