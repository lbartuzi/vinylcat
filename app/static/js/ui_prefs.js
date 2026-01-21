(function () {
  const html = document.documentElement;

  const keyCovers = 'vinylcat_show_covers';
  const keyContrast = 'vinylcat_contrast';

  const toggleCovers = document.getElementById('toggle-covers');
  const toggleContrast = document.getElementById('toggle-contrast');

  function loadCovers(enable) {
    document.querySelectorAll('img.cover-img').forEach((img) => {
      const url = img.getAttribute('data-src');
      if (!url) return;
      if (enable) {
        if (!img.getAttribute('src')) img.setAttribute('src', url);
      } else {
        img.removeAttribute('src');
      }
    });
  }

  function setContrast(enable) {
    html.classList.toggle('contrast-boost', enable);
  }

  function setCovers(enable) {
    html.classList.toggle('show-covers', enable);
    loadCovers(enable);
  }

  const savedCovers = localStorage.getItem(keyCovers);
  const savedContrast = localStorage.getItem(keyContrast);

  const coversEnabled = savedCovers === '1'; // default OFF
  const contrastEnabled = savedContrast === '1'; // default OFF

  if (toggleCovers) toggleCovers.checked = coversEnabled;
  if (toggleContrast) toggleContrast.checked = contrastEnabled;

  setCovers(coversEnabled);
  setContrast(contrastEnabled);

  if (toggleCovers) {
    toggleCovers.addEventListener('change', () => {
      const on = toggleCovers.checked;
      localStorage.setItem(keyCovers, on ? '1' : '0');
      setCovers(on);
    });
  }

  if (toggleContrast) {
    toggleContrast.addEventListener('change', () => {
      const on = toggleContrast.checked;
      localStorage.setItem(keyContrast, on ? '1' : '0');
      setContrast(on);
    });
  }
})();