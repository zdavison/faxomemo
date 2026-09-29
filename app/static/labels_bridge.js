/**
 * Adds a "Print on faxomemo" button to the vendored phomymo designer.
 *
 * phomymo prints over Web Bluetooth or WebUSB, but faxomemo already holds the
 * only connection the M220 accepts. So this bridge reuses phomymo's PNG export
 * instead: it captures the rendered canvas and POSTs it to faxomemo, which
 * rasterises and prints through its own driver.
 *
 * Nothing in vendor/phomymo is modified, so scripts/fetch_label_designer.sh can
 * pull upstream changes without conflicts. The one coupling is the export path:
 * phomymo's #export-png-btn must render the label through canvas.toBlob().
 */
(() => {
  const PRINT_ENDPOINT = '/api/labels/print';
  const RENDER_TIMEOUT_MS = 8000;
  // phomymo draws its label canvas at 8 px/mm, the M220's own 203 dpi.
  const PX_PER_MM = 8;
  // phomymo's "Export as PNG" renders the label at 4x for download quality.
  const EXPORT_SCALE = 4;

  let pendingCapture = null;

  const originalToBlob = HTMLCanvasElement.prototype.toBlob;
  HTMLCanvasElement.prototype.toBlob = function (callback, ...rest) {
    if (!pendingCapture) return originalToBlob.call(this, callback, ...rest);
    // Swallowing phomymo's callback stops its download anchor from firing.
    const capture = pendingCapture;
    pendingCapture = null;
    return originalToBlob.call(this, capture, ...rest);
  };

  function renderLabel() {
    const exportButton = document.getElementById('export-png-btn');
    if (!exportButton) {
      return Promise.reject(new Error('phomymo export button is missing'));
    }

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pendingCapture = null;
        reject(new Error('Nothing to print'));
      }, RENDER_TIMEOUT_MS);

      pendingCapture = (blob) => {
        clearTimeout(timer);
        if (blob) resolve(blob);
        else reject(new Error('Could not render the label'));
      };

      exportButton.click();
    });
  }

  /**
   * The designed label width in millimetres. Pixel width cannot stand in for it:
   * phomymo exports at a multiple of its canvas scale, so faxomemo would print
   * the label several times too wide.
   */
  function labelWidthMm(pngWidthPx) {
    const displayed = document.getElementById('print-size')?.textContent ?? '';
    const match = displayed.match(/([\d.]+)\s*(?:x|mm)/i);
    if (match) {
      const width = parseFloat(match[1]);
      if (width > 0) return width;
    }
    // phomymo's size display is gone or reworded: fall back to the export scale.
    return pngWidthPx / (PX_PER_MM * EXPORT_SCALE);
  }

  /**
   * Whether the selected size is continuous tape rather than die-cut labels.
   * phomymo hides its own continuous checkbox for M-series printers, so the
   * faxomemo checkbox below carries the choice and this seeds its default.
   */
  function sizeLooksContinuous() {
    const select = document.getElementById('label-size');
    if (!select) return false;
    if (select.value.endsWith(' cont')) return true;
    if (select.value === 'custom') {
      return Boolean(document.getElementById('custom-continuous')?.checked);
    }
    return false;
  }

  async function send(blob, widthMm, continuous) {
    const response = await fetch(PRINT_ENDPOINT, {
      method: 'POST',
      headers: {
        'Content-Type': 'image/png',
        'X-Label-Width-Mm': String(widthMm),
        'X-Label-Continuous': continuous ? 'true' : 'false',
      },
      body: blob,
      credentials: 'same-origin',
    });

    if (response.ok) return;

    if (response.status === 401) {
      throw new Error('Open faxomemo settings and log in first');
    }
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `faxomemo returned ${response.status}`);
  }

  function toast(message, isError) {
    const el = document.createElement('div');
    el.textContent = message;
    el.className =
      'fixed bottom-6 left-1/2 -translate-x-1/2 z-[100] px-4 py-2 rounded-lg text-sm text-white shadow-lg';
    el.style.backgroundColor = isError ? '#b91c1c' : '#111827';
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  async function pngWidth(blob) {
    const header = new DataView(await blob.slice(0, 24).arrayBuffer());
    return header.getUint32(16);
  }

  function dismissCompatibilityWarning() {
    // Over plain http phomymo blocks on "HTTPS required for printing". That
    // warning is about Web Bluetooth, which the faxomemo button does not use.
    const overlay = document.getElementById('compatibility-warning');
    if (overlay) overlay.remove();
    return Boolean(overlay);
  }

  function watchForCompatibilityWarning() {
    if (dismissCompatibilityWarning()) return;
    const observer = new MutationObserver(() => {
      if (dismissCompatibilityWarning()) observer.disconnect();
    });
    observer.observe(document.body, { childList: true });
    setTimeout(() => observer.disconnect(), 15000);
  }

  function buildContinuousToggle() {
    const wrapper = document.createElement('label');
    wrapper.className = 'ml-2 flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer';
    wrapper.title = 'Continuous tape has no gaps. Die-cut labels do, and the printer indexes to them.';

    const input = document.createElement('input');
    input.id = 'faxomemo-continuous';
    input.type = 'checkbox';
    input.className = 'w-3.5 h-3.5 rounded border-gray-300';
    input.checked = sizeLooksContinuous();

    wrapper.appendChild(input);
    wrapper.appendChild(document.createTextNode('Continuous'));

    // Changing the size re-seeds the box, so a manual override lasts until then.
    const reseed = () => {
      input.checked = sizeLooksContinuous();
    };
    document.getElementById('label-size')?.addEventListener('change', reseed);
    document.getElementById('custom-continuous')?.addEventListener('change', reseed);

    return { wrapper, input };
  }

  function build() {
    const printButton = document.getElementById('print-btn');
    if (!printButton || document.getElementById('faxomemo-print-btn')) return;

    const { wrapper: continuousWrapper, input: continuousInput } = buildContinuousToggle();

    const button = document.createElement('button');
    button.id = 'faxomemo-print-btn';
    button.type = 'button';
    button.textContent = 'Print on faxomemo';
    button.title = 'Send this label to the faxomemo printer';
    button.className =
      'ml-2 px-3 sm:px-5 py-1.5 sm:py-2 bg-emerald-700 hover:bg-emerald-800 text-white text-xs sm:text-sm font-medium rounded-lg transition-colors';

    button.addEventListener('click', async () => {
      const label = button.textContent;
      button.disabled = true;
      button.textContent = 'Printing...';
      try {
        const blob = await renderLabel();
        await send(blob, labelWidthMm(await pngWidth(blob)), continuousInput.checked);
        toast('Sent to faxomemo');
      } catch (error) {
        toast(error.message, true);
      } finally {
        button.disabled = false;
        button.textContent = label;
      }
    });

    printButton.parentElement.insertAdjacentElement('afterend', button);
    button.insertAdjacentElement('afterend', continuousWrapper);
    watchForCompatibilityWarning();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();
