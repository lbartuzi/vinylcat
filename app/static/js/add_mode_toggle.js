
(function () {
  function setMode(isManual) {
    const discogs = document.getElementById("discogsSection");
    const manual = document.getElementById("manualSection");
    const hintD = document.getElementById("modeHintDiscogs");
    const hintM = document.getElementById("modeHintManual");
    if (!discogs || !manual) return;

    if (isManual) {
      discogs.classList.add("d-none");
      manual.classList.remove("d-none");
      hintD && hintD.classList.add("d-none");
      hintM && hintM.classList.remove("d-none");
    } else {
      discogs.classList.remove("d-none");
      manual.classList.add("d-none");
      hintD && hintD.classList.remove("d-none");
      hintM && hintM.classList.add("d-none");
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    const sw = document.getElementById("manualModeSwitch");
    if (!sw) return;

    // Template inject: if discogs token not present, default to manual.
    const tokenPresent = (window.__DISCOGS_TOKEN_PRESENT === 1);

    sw.checked = !tokenPresent; // default manual if no token
    setMode(sw.checked);

    sw.addEventListener("change", function () {
      setMode(sw.checked);
    });
  });
})();
