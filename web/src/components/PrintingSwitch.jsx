import { useEffect, useState } from 'react';

/**
 * Global on/off switch for automatic printing.
 *
 * Off suppresses cron schedules, chat bridges and incoming webhooks. Manual
 * prints (this UI, the physical button) keep working, so the device stays
 * usable as a label printer while paused.
 */
export default function PrintingSwitch({ onError }) {
  const [enabled, setEnabled] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const response = await fetch('/api/printing');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const data = await response.json();
        if (!cancelled) setEnabled(Boolean(data.enabled));
      } catch (err) {
        console.error('Error fetching printing state:', err);
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = async () => {
    if (enabled === null || saving) return;

    const next = !enabled;
    setSaving(true);
    try {
      const response = await fetch('/api/printing', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next }),
      });
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
      const data = await response.json();
      setEnabled(Boolean(data.enabled));
    } catch (err) {
      console.error('Error updating printing state:', err);
      onError?.('Could not change the printing switch.');
    } finally {
      setSaving(false);
    }
  };

  if (enabled === null) return null;

  const paused = !enabled;

  return (
    <div
      className='flex items-center justify-between gap-4 border-2 px-4 py-3'
      style={{
        borderColor: paused ? 'var(--color-error)' : 'var(--color-border-main)',
        backgroundColor: paused ? 'var(--color-error-10)' : 'var(--color-bg-card)',
      }}>
      <div>
        <div
          className='text-sm font-bold tracking-wider'
          style={{ color: paused ? 'var(--color-error)' : 'var(--color-text-main)' }}>
          {paused ? 'PRINTING PAUSED' : 'PRINTING ON'}
        </div>
        <p className='text-xs leading-4 mt-0.5' style={{ color: 'var(--color-text-muted)' }}>
          {paused
            ? 'Schedules and triggers are being dropped. Labels and test prints still work.'
            : 'Schedules and triggers print as they arrive.'}
        </p>
      </div>
      <button
        type='button'
        role='switch'
        aria-checked={enabled}
        aria-label='Printing'
        disabled={saving}
        onClick={toggle}
        className='shrink-0 border-2 px-4 py-2 text-sm font-bold tracking-wider transition-all disabled:opacity-50'
        style={{
          borderColor: paused ? 'var(--color-error)' : 'var(--color-border-main)',
          backgroundColor: paused ? 'transparent' : 'var(--color-text-main)',
          color: paused ? 'var(--color-error)' : 'var(--color-text-inverse)',
        }}>
        {paused ? 'TURN ON' : 'TURN OFF'}
      </button>
    </div>
  );
}
