// Show server timestamps in the browser's local time zone.
function localizeTimes(root) {
  root.querySelectorAll("time[data-ts]").forEach((el) => {
    const d = new Date(el.dataset.ts);
    if (!isNaN(d)) {
      el.textContent = d.toLocaleString();
      el.dateTime = el.dataset.ts;
    }
  });
}
document.addEventListener("DOMContentLoaded", () => localizeTimes(document));
document.addEventListener("htmx:afterSwap", (e) => localizeTimes(e.target));
