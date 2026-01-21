(function () {
  const front = document.getElementById('frontFile');
  const back = document.getElementById('backFile');
  const btn = document.getElementById('analyzeBtn');
  const status = document.getElementById('analyzeStatus');

  // Discogs search inputs
  const s_barcode = document.getElementById('barcode');
  const s_artist = document.getElementById('artist');
  const s_title = document.getElementById('title');
  const s_year = document.getElementById('year');

  // Manual inputs (may not exist on some pages)
  const m_barcode = document.getElementById('m_barcode');
  const m_artist = document.getElementById('m_artist');
  const m_title = document.getElementById('m_title');
  const m_year = document.getElementById('m_year');

  function setStatus(text) {
    status.textContent = text || '';
  }

  function setIfEmpty(el, value) {
    if (!el) return;
    const v = (value || '').toString().trim();
    if (!v) return;
    // Don't overwrite user edits
    if ((el.value || '').trim()) return;
    el.value = v;
  }

  function setAlways(el, value) {
    if (!el) return;
    const v = (value || '').toString().trim();
    if (!v) return;
    el.value = v;
  }

  async function analyze() {
    const fd = new FormData();
    if (front?.files?.[0]) fd.append('front', front.files[0]);
    if (back?.files?.[0]) fd.append('back', back.files[0]);
    if (!front?.files?.[0] && !back?.files?.[0]) {
      setStatus('No images selected.');
      return;
    }

    btn.disabled = true;
    setStatus('Analyzing…');

    try {
      const res = await fetch('/api/analyze', { method: 'POST', body: fd });
      const data = await res.json();
      const out = (data && data.data) ? data.data : (data || {});

      // Wire results into BOTH modes:
      // - Discogs search fields (barcode/artist/title/year)
      // - Manual add fields (barcode/artist/title/year)
      //
      // We prefer not to overwrite what the user already typed.
      setIfEmpty(s_barcode, out.barcode);
      setIfEmpty(s_artist, out.artist);
      setIfEmpty(s_title, out.title);
      setIfEmpty(s_year, out.year);

      setIfEmpty(m_barcode, out.barcode);
      setIfEmpty(m_artist, out.artist);
      setIfEmpty(m_title, out.title);
      setIfEmpty(m_year, out.year);

      // If we recognized a barcode, it's usually safe to overwrite the Discogs barcode field
      // because it tends to be the primary search input.
      if (out.barcode) setAlways(s_barcode, out.barcode);

      const bits = [];
      if (out.barcode) bits.push('barcode');
      if (out.artist) bits.push('artist');
      if (out.title) bits.push('title');
      if (out.year) bits.push('year');
      setStatus(bits.length ? `Detected: ${bits.join(', ')}` : 'No useful data detected.');
    } catch (e) {
      setStatus('Analyze failed. Check OCR service logs.');
      console.error(e);
    } finally {
      btn.disabled = false;
    }
  }

  btn?.addEventListener('click', analyze);
})();