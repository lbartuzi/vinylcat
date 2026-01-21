(function () {
  function q(id) { return document.getElementById(id); }

  function addRow(title, dur) {
    const tbody = q("trackTableBody");
    if (!tbody) return;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input class="form-control form-control-sm" type="text" placeholder="Track title" value="${(title||"").replace(/"/g, '&quot;')}"></td>
      <td><input class="form-control form-control-sm" type="text" placeholder="mm:ss" value="${(dur||"").replace(/"/g, '&quot;')}"></td>
      <td class="text-end"><button class="btn btn-outline-danger btn-sm" type="button" data-action="remove-track">&times;</button></td>
    `;
    tbody.appendChild(tr);
  }

  function removeRow(btn) {
    const tr = btn.closest("tr");
    if (tr) tr.remove();
  }

  function buildTracklistText() {
    const tbody = q("trackTableBody");
    if (!tbody) return "";
    const lines = [];
    tbody.querySelectorAll("tr").forEach(tr => {
      const inputs = tr.querySelectorAll("input");
      if (!inputs || inputs.length < 2) return;
      const title = (inputs[0].value || "").trim();
      const dur = (inputs[1].value || "").trim();
      if (!title) return;
      if (dur) lines.push(`${title} - ${dur}`);
      else lines.push(title);
    });
    return lines.join("\n");
  }

  document.addEventListener("DOMContentLoaded", function () {
    const btn = q("addTrackRowBtn");
    const tbody = q("trackTableBody");
    const form = q("manualForm");
    const hidden = q("tracklist_text");

    if (btn) {
      btn.addEventListener("click", function () {
        addRow("", "");
      });
    }

    if (tbody) {
      tbody.addEventListener("click", function (e) {
        const t = e.target;
        if (t && t.getAttribute && t.getAttribute("data-action") === "remove-track") {
          // Keep at least one row for convenience
          const rows = tbody.querySelectorAll("tr");
          if (rows.length <= 1) {
            // clear inputs instead of removing last row
            const inputs = rows[0].querySelectorAll("input");
            if (inputs && inputs.length >= 2) { inputs[0].value = ""; inputs[1].value = ""; }
            return;
          }
          removeRow(t);
        }
      });
    }

    if (form) {
      form.addEventListener("submit", function () {
        if (hidden) hidden.value = buildTracklistText();
      });
    }
  });
})();
