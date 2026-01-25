(() => {
  const btn = document.getElementById("btn-scan-barcode");
  if (!btn) return;

  const overlay = document.getElementById("barcode-scanner-overlay");
  const closeBtn = document.getElementById("btn-scan-close");
  const video = document.getElementById("barcode-video");

  const form = document.getElementById("searchForm");          // existing Discogs search form
  const barcodeInput = document.getElementById("barcode");     // existing barcode field

  if (!overlay || !closeBtn || !video || !form || !barcodeInput) {
    console.warn("Barcode scanner: missing required elements.");
    return;
  }

  // ---- state ---------------------------------------------------------------
  let mode = null; // "native" | "quagga" | null
  let stream = null;              // MediaStream for native mode
  let scanning = false;           // loop control for native mode
  let rafId = null;               // requestAnimationFrame id
  let quaggaDetectedHandler = null;
  let opening = false;            // prevent double-open race
  let quaggaTarget = null;        // DOM container quagga injects into

  // ---- helpers -------------------------------------------------------------
  function showOverlay() {
    overlay.classList.remove("d-none");
    overlay.setAttribute("aria-hidden", "false");
  }

  function hideOverlay() {
    overlay.classList.add("d-none");
    overlay.setAttribute("aria-hidden", "true");
  }

  function cleanupQuaggaTarget() {
    // Quagga injects canvases/video elements into the target; clean them up to avoid stacking.
    if (quaggaTarget) {
      quaggaTarget.querySelectorAll("video, canvas, img").forEach(el => {
        try { el.remove(); } catch (_) {}
      });
    }
  }

  function stopNative() {
    scanning = false;

    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }

    if (stream) {
      try { stream.getTracks().forEach(t => t.stop()); } catch (_) {}
      stream = null;
    }

    try { video.pause(); } catch (_) {}
    video.srcObject = null;
  }

  function stopQuagga() {
    if (!window.Quagga) return;

    try {
      if (quaggaDetectedHandler) {
        window.Quagga.offDetected(quaggaDetectedHandler);
        quaggaDetectedHandler = null;
      }
    } catch (_) {}

    try { window.Quagga.stop(); } catch (_) {}

    cleanupQuaggaTarget();
  }

  function stopAll() {
    opening = false;

    if (mode === "native") stopNative();
    if (mode === "quagga") stopQuagga();

    // Also stop both to be extra safe (prevents “camera still in use” on some browsers)
    stopNative();
    stopQuagga();

    mode = null;
  }

  function commitResult(code) {
    const digits = String(code).replace(/\D/g, "");
    if (!digits) return;

    // Stop scanner, close overlay, fill field — NO auto-submit
    stopAll();
    hideOverlay();
    barcodeInput.value = digits;
  }

  // ---- native BarcodeDetector mode ----------------------------------------
  async function startCameraForNative() {
    stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: "environment" },
        width: { ideal: 1280 },
        height: { ideal: 720 }
      },
      audio: false
    });
    video.srcObject = stream;
    await video.play();
  }

  async function scanWithBarcodeDetectorLoop() {
    if (!("BarcodeDetector" in window)) return false;

    let formats = ["ean_13", "ean_8", "upc_a", "upc_e"];
    try {
      if (BarcodeDetector.getSupportedFormats) {
        const supported = await BarcodeDetector.getSupportedFormats();
        formats = formats.filter(f => supported.includes(f));
        if (!formats.length) return false;
      }
    } catch (_) {}

    const detector = new BarcodeDetector({ formats });

    scanning = true;
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d", { willReadFrequently: true });

    const loop = async () => {
      if (!scanning) return;

      try {
        const w = video.videoWidth;
        const h = video.videoHeight;
        if (w && h) {
          canvas.width = w;
          canvas.height = h;
          ctx.drawImage(video, 0, 0, w, h);

          const barcodes = await detector.detect(canvas);
          if (barcodes && barcodes.length) {
            const val = barcodes[0].rawValue || "";
            if (val) return commitResult(val);
          }
        }
      } catch (_) {
        // ignore and keep looping
      }

      rafId = requestAnimationFrame(loop);
    };

    loop();
    return true;
  }

  // ---- Quagga fallback mode -----------------------------------------------
  async function startQuagga() {
    if (!window.Quagga) {
      throw new Error("Quagga fallback not available (missing /static/vendor/quagga2.min.js)");
    }

    // Choose a stable target container inside overlay where Quagga can inject elements
    quaggaTarget = overlay.querySelector(".scanner-card");
    if (!quaggaTarget) throw new Error("Scanner overlay target not found.");

    cleanupQuaggaTarget();

    await new Promise((resolve, reject) => {
      window.Quagga.init({
        inputStream: {
          type: "LiveStream",
          target: quaggaTarget,
          constraints: { facingMode: "environment" }
        },
        decoder: {
          readers: ["ean_reader", "ean_8_reader", "upc_reader", "upc_e_reader"]
        },
        locate: true
      }, (err) => (err ? reject(err) : resolve()));
    });

    window.Quagga.start();

    quaggaDetectedHandler = (data) => {
      const code = data?.codeResult?.code;
      if (code) commitResult(code);
    };
    window.Quagga.onDetected(quaggaDetectedHandler);
  }

  // ---- open/close controls -------------------------------------------------
  async function openScanner() {
    if (opening) return; // prevent rapid double taps
    opening = true;

    // Always hard-stop before starting (fixes unreliable reopen)
    stopAll();

    showOverlay();

    try {
      // Prefer native detector first
      mode = "native";
      await startCameraForNative();
      const ok = await scanWithBarcodeDetectorLoop();
      if (ok) {
        opening = false;
        return;
      }

      // Native not supported: fall back to Quagga
      stopNative();
      mode = "quagga";
      await startQuagga();

      opening = false;
    } catch (e) {
      console.error("Barcode scanner failed:", e);
      stopAll();
      hideOverlay();
      opening = false;
      alert("Camera/scanning failed. Please allow camera permission and try again.");
    }
  }

  function closeScanner() {
    stopAll();
    hideOverlay();
  }

  // ---- events --------------------------------------------------------------
  btn.addEventListener("click", openScanner);
  closeBtn.addEventListener("click", closeScanner);

  // Close if overlay background is clicked (optional UX)
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeScanner();
  });

  // Stop camera when tab is hidden (prevents “camera busy” on return)
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) closeScanner();
  });

  // Safety: stop on page unload/navigation
  window.addEventListener("pagehide", closeScanner);
})();
