import React, { useState, useEffect, useRef } from 'react';
import { SunIcon, MoonIcon, MonitorIcon } from '@phosphor-icons/react';
import { formatTimeForDisplay } from '../utils';
import { INK_GRADIENTS } from '../constants';
import PrimaryButton from './PrimaryButton';
import WiFiIcon from '../assets/WiFiIcon';
import WiFiOffIcon from '../assets/WiFiOffIcon';
import LocationSearch from './widgets/LocationSearch';
import { adminAuthFetch } from '../lib/adminAuthFetch';


const GeneralSettings = ({
  settings,
  saveGlobalSettings,
  wifiStatus,
  onConfigImported,
}) => {
  const inputClass = 'w-full p-3 text-base border-2 border-gray-300 rounded-lg focus:outline-none box-border';
  const labelClass = 'block mb-2 text-sm text-black font-bold';

  // Use shared ink gradients
  const inkGradients = INK_GRADIENTS;

  // System time state
  const [currentTime, setCurrentTime] = useState(null);
  const [manualDate, setManualDate] = useState('');
  const [manualTime, setManualTime] = useState('');
  const [timeStatus, setTimeStatus] = useState({ type: '', message: '' });
  const [useAutoTime, setUseAutoTime] = useState(false);

  // Device Password state
  const [devicePasswordStatus, setDevicePasswordStatus] = useState(null);
  const [devicePasswordMessage, setDevicePasswordMessage] = useState({ type: '', message: '' });
  const [showDevicePasswordChange, setShowDevicePasswordChange] = useState(false);
  const [currentDevicePassword, setCurrentDevicePassword] = useState('');
  const [newDevicePassword, setNewDevicePassword] = useState('');
  const [confirmDevicePassword, setConfirmDevicePassword] = useState('');
  const [changingDevicePassword, setChangingDevicePassword] = useState(false);
  const devicePasswordSource = devicePasswordStatus?.source;
  const devicePasswordBadgeMuted = !['managed_file', 'managed_fallback'].includes(devicePasswordSource || '');
  const devicePasswordUnavailableMessage =
    devicePasswordSource === 'managed_fallback'
      ? 'Password changes will be available after managed device credential storage is provisioned on this unit.'
      : 'Device Password changes are only available on managed faxomemo builds.';

  const [configTransferMessage, setConfigTransferMessage] = useState({ type: '', message: '', warnings: [] });
  const [exportingConfig, setExportingConfig] = useState(false);
  const [importingConfig, setImportingConfig] = useState(false);
  const configFileInputRef = useRef(null);

  // Fetch current system time on mount and periodically
  useEffect(() => {
    const fetchTime = async () => {
      try {
        const response = await fetch('/api/system/time');
        const data = await response.json();
        if (data.datetime) {
          setCurrentTime(data);
          // Pre-fill manual inputs with current time if they're empty
          setManualDate((prev) => prev || data.date);
          setManualTime((prev) => prev || data.time.substring(0, 5)); // HH:MM format for input
        }
      } catch (err) {
        console.error('Error fetching system time:', err);
      }
    };

    fetchTime();
    const interval = setInterval(fetchTime, 30000); // Update every 30 seconds
    return () => clearInterval(interval);
  }, []); // Only run on mount

  // Load saved time sync mode preference
  useEffect(() => {
    // Only initialize if settings are actually loaded (not empty initial state)
    // Check if settings has meaningful data (like timezone or city_name)
    if (settings && (settings.timezone || settings.city_name || settings.time_sync_mode !== undefined)) {
      const syncMode = settings.time_sync_mode || 'manual';
      console.log('[TimeSync] Loading preference:', { syncMode, time_sync_mode: settings.time_sync_mode });
      setUseAutoTime(syncMode === 'automatic');
    }
  }, [settings]);

  useEffect(() => {
    const fetchDevicePasswordStatus = async () => {
      try {
        const response = await fetch('/api/system/device-password/status');
        const data = await response.json();
        setDevicePasswordStatus(data);
      } catch (err) {
        console.error('Error fetching Device Password status:', err);
      }
    };

    fetchDevicePasswordStatus();
  }, []);

  const getApiError = (data, fallback) => {
    return data?.detail || data?.error || data?.message || fallback;
  };

  const clearStatusLater = (setter) => {
    setTimeout(() => setter({ type: '', message: '' }), 5000);
  };

  const clearConfigTransferMessage = () => {
    setConfigTransferMessage({ type: '', message: '', warnings: [] });
  };

  const parseContentDispositionFilename = (headerValue) => {
    if (!headerValue) return '';
    const utf8Match = headerValue.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match?.[1]) {
      try {
        return decodeURIComponent(utf8Match[1]);
      } catch {
        return utf8Match[1];
      }
    }

    const filenameMatch = headerValue.match(/filename=\"?([^\";]+)\"?/i);
    return filenameMatch?.[1] || '';
  };

  const handleExportConfig = async () => {
    setExportingConfig(true);
      setConfigTransferMessage({ type: '', message: '', warnings: [] });

    try {
      const response = await adminAuthFetch('/api/settings/export');
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(getApiError(data, 'Failed to export configuration'));
      }

      const blob = await response.blob();
      const filename =
        parseContentDispositionFilename(response.headers.get('Content-Disposition')) ||
        'pc1-config.json';
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);

      setConfigTransferMessage({ type: 'success', message: 'Configuration exported.', warnings: [] });
    } catch (err) {
      setConfigTransferMessage({ type: 'error', message: err.message || 'Failed to export configuration', warnings: [] });
    } finally {
      setExportingConfig(false);
    }
  };

  const handleImportConfig = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';

    if (!file) {
      return;
    }

    if (!window.confirm(`Import configuration from "${file.name}"? This will replace current settings, channels, and modules.`)) {
      return;
    }

    setImportingConfig(true);
    setConfigTransferMessage({ type: '', message: '', warnings: [] });

    try {
      const fileText = await file.text();

      const response = await adminAuthFetch('/api/settings/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: fileText,
      });
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(getApiError(data, 'Failed to import configuration'));
      }

      onConfigImported?.(data.config, data.message || 'Configuration imported successfully!');

      const warnings = Array.isArray(data.validation_warnings) ? data.validation_warnings : [];
      const warningSuffix = warnings.length > 0
        ? ' Imported with validation warnings.'
        : '';
      setConfigTransferMessage({
        type: 'success',
        message: `${data.message || 'Configuration imported.'}${warningSuffix}`,
        warnings,
      });
    } catch (err) {
      setConfigTransferMessage({ type: 'error', message: err.message || 'Failed to import configuration', warnings: [] });
    } finally {
      setImportingConfig(false);
    }
  };

  const handleDevicePasswordChange = async () => {
    if (!currentDevicePassword) {
      setDevicePasswordMessage({ type: 'error', message: 'Enter your current Device Password' });
      clearStatusLater(setDevicePasswordMessage);
      return;
    }
    if (!newDevicePassword || newDevicePassword.length < 8) {
      setDevicePasswordMessage({ type: 'error', message: 'New Device Password must be at least 8 characters' });
      clearStatusLater(setDevicePasswordMessage);
      return;
    }
    if (newDevicePassword !== confirmDevicePassword) {
      setDevicePasswordMessage({ type: 'error', message: 'New passwords do not match' });
      clearStatusLater(setDevicePasswordMessage);
      return;
    }

    setChangingDevicePassword(true);
    setDevicePasswordMessage({ type: '', message: '' });

    try {
      const response = await adminAuthFetch('/api/system/device-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          current_password: currentDevicePassword,
          new_password: newDevicePassword,
        }),
      });
      const data = await response.json();

      if (data.success) {
        setDevicePasswordMessage({ type: 'success', message: data.message });
        setCurrentDevicePassword('');
        setNewDevicePassword('');
        setConfirmDevicePassword('');
        setShowDevicePasswordChange(false);

        if (data.reauth_required) {
          setTimeout(() => {
            window.dispatchEvent(
              new CustomEvent('pc1-auth-required', {
                detail: {
                  message: 'Device Password changed. Enter the new Device Password to continue.',
                },
              }),
            );
          }, 1200);
        } else {
          clearStatusLater(setDevicePasswordMessage);
        }
      } else {
        setDevicePasswordMessage({
          type: 'error',
          message: getApiError(data, 'Failed to change Device Password'),
        });
        clearStatusLater(setDevicePasswordMessage);
      }
    } catch (err) {
      setDevicePasswordMessage({ type: 'error', message: `Error changing Device Password: ${err.message}` });
      clearStatusLater(setDevicePasswordMessage);
    } finally {
      setChangingDevicePassword(false);
    }
  };

  // Auto sync time
  const syncTimeAutomatically = async () => {
    if (!wifiStatus?.connected) {
      setTimeStatus({ type: 'error', message: 'Internet connection required for automatic time sync' });
      setTimeout(() => setTimeStatus({ type: '', message: '' }), 5000);
      return;
    }

    setTimeStatus({ type: '', message: '' });
    try {
      const response = await adminAuthFetch('/api/system/time/sync', {
        method: 'POST',
      });

      const data = await response.json();

      if (data.success) {
        setTimeStatus({ type: 'success', message: data.message || 'Time synchronized successfully' });
        // Refresh current time
        const timeResponse = await fetch('/api/system/time');
        const timeData = await timeResponse.json();
        if (timeData.datetime) {
          setCurrentTime(timeData);
          setManualDate(timeData.date);
          setManualTime(timeData.time.substring(0, 5));
        }
      } else {
        setTimeStatus({ type: 'error', message: getApiError(data, 'Failed to sync time') });
      }
    } catch (err) {
      setTimeStatus({ type: 'error', message: 'Error syncing time: ' + err.message });
    }

    setTimeout(() => setTimeStatus({ type: '', message: '' }), 5000);
  };

  // Set time manually
  const setTimeManually = async () => {
    // Validate inputs
    if (!manualDate || !manualTime) {
      setTimeStatus({ type: 'error', message: 'Please enter both date and time' });
      setTimeout(() => setTimeStatus({ type: '', message: '' }), 5000);
      return;
    }

    // Normalize and validate date format (YYYY-MM-DD)
    const normalizedDate = manualDate.trim();
    const dateRegex = /^\d{4}-\d{2}-\d{2}$/;
    if (!dateRegex.test(normalizedDate)) {
      setTimeStatus({ type: 'error', message: 'Invalid date format. Please use YYYY-MM-DD format.' });
      setTimeout(() => setTimeStatus({ type: '', message: '' }), 5000);
      return;
    }

    // Normalize and validate time format
    // HTML5 time input should return HH:MM format
    let normalizedTime = String(manualTime || '').trim();

    // Check if it's empty
    if (!normalizedTime || normalizedTime.length === 0) {
      setTimeStatus({ type: 'error', message: 'Please enter a valid time' });
      setTimeout(() => setTimeStatus({ type: '', message: '' }), 5000);
      return;
    }

    // HTML5 time input returns HH:MM format (e.g., "14:30")
    // Accept both HH:MM and HH:MM:SS formats
    const timeRegex = /^(\d{1,2}):(\d{2})(:(\d{2}))?$/;
    const timeMatch = normalizedTime.match(timeRegex);

    if (!timeMatch) {
      console.error('Time validation failed:', {
        normalizedTime,
        original: manualTime,
        type: typeof manualTime,
        length: normalizedTime.length,
        charCodes: normalizedTime.split('').map((c) => `${c}(${c.charCodeAt(0)})`),
      });
      setTimeStatus({ type: 'error', message: `Invalid time format: "${normalizedTime}". Please use HH:MM format (e.g., 14:30).` });
      setTimeout(() => setTimeStatus({ type: '', message: '' }), 5000);
      return;
    }

    // Extract and normalize time components
    const hours = timeMatch[1].padStart(2, '0');
    const minutes = timeMatch[2];
    const seconds = timeMatch[4] || '00';

    // Validate ranges
    const hourNum = parseInt(hours, 10);
    const minNum = parseInt(minutes, 10);
    const secNum = parseInt(seconds, 10);

    if (isNaN(hourNum) || hourNum < 0 || hourNum > 23) {
      setTimeStatus({ type: 'error', message: `Invalid hours: ${hours}. Must be between 00 and 23.` });
      setTimeout(() => setTimeStatus({ type: '', message: '' }), 5000);
      return;
    }

    if (isNaN(minNum) || minNum < 0 || minNum > 59) {
      setTimeStatus({ type: 'error', message: `Invalid minutes: ${minutes}. Must be between 00 and 59.` });
      setTimeout(() => setTimeStatus({ type: '', message: '' }), 5000);
      return;
    }

    if (isNaN(secNum) || secNum < 0 || secNum > 59) {
      setTimeStatus({ type: 'error', message: `Invalid seconds: ${seconds}. Must be between 00 and 59.` });
      setTimeout(() => setTimeStatus({ type: '', message: '' }), 5000);
      return;
    }

    // Format as HH:MM:SS
    const normalizedTimeStr = `${hours}:${minutes}:${seconds}`;

    setTimeStatus({ type: '', message: '' }); // Clear previous status

    try {
      console.log('Setting time:', { date: normalizedDate, time: normalizedTimeStr });
      const response = await adminAuthFetch('/api/system/time', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: normalizedDate, time: normalizedTimeStr }),
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();

      if (data.success) {
        setTimeStatus({ type: 'success', message: data.message || 'System time set successfully' });
        setUseAutoTime(false); // Ensure we're in manual mode
        // Refresh current time after a short delay to verify it was set correctly
        setTimeout(async () => {
          try {
            const timeResponse = await fetch('/api/system/time');
            const timeData = await timeResponse.json();
            if (timeData.datetime) {
              setCurrentTime(timeData);
              // Update manual inputs to match what was actually set
              setManualDate(timeData.date);
              setManualTime(timeData.time.substring(0, 5));
            }
          } catch (err) {
            console.error('Error refreshing time:', err);
          }
        }, 1000); // Increased delay to ensure time is set before checking
      } else {
        setTimeStatus({
          type: 'error',
          message: getApiError(data, 'Failed to set system time. The application may need sudo privileges.'),
        });
      }
    } catch (err) {
      console.error('Error setting system time:', err);
      setTimeStatus({
        type: 'error',
        message: `Error setting system time: ${err.message}. Make sure the backend is running and has proper permissions.`,
      });
    }

    setTimeout(() => setTimeStatus({ type: '', message: '' }), 5000);
  };

  return (
    <div className='space-y-4'>
      {/* Location Settings */}
      <div className='rounded-xl p-[4px] shadow-lg' style={{ background: inkGradients[2] }}>
        <div className='bg-bg-card rounded-lg p-4 flex flex-col'>
          <h3 className='font-bold text-black  text-lg tracking-tight mb-3'>Location</h3>

          {/* Universal Location Search Component */}
          <div className='mb-4'>
            <label className='block mb-2 text-sm text-black font-bold'>Set Location</label>
            <LocationSearch 
              value={settings} 
              onChange={(newLoc) => saveGlobalSettings(newLoc)} 
            />
          </div>
        </div>
      </div>

      {/* Time Settings */}
      <div className='rounded-xl p-[4px] shadow-lg' style={{ background: inkGradients[1] }}>
        <div className='bg-bg-card rounded-lg p-4 flex flex-col'>
          <h3 className='font-bold text-black  text-lg tracking-tight mb-3'>Time Settings</h3>

          {/* Time Format */}
          <div className='mb-4'>
            <label className='block mb-2 text-sm text-black font-bold'>Time Format</label>
            <select
              value={settings.time_format || '12h'}
              onChange={(e) => saveGlobalSettings({ time_format: e.target.value })}
              className={inputClass}>
              <option value='12h'>12-hour (3:45 PM)</option>
              <option value='24h'>24-hour (15:45)</option>
            </select>
            <p className='text-xs text-gray-600 mt-1 '>Choose how times are displayed across all modules</p>
          </div>

          {/* Time Synchronization Mode */}
          <div className='mb-4'>
            <label className='block mb-3 text-sm text-black font-bold'>Set Time</label>

            {/* Mode Selection - Tabs */}
            <div className='flex gap-0 mb-0'>
              <label
                className={`relative flex flex-col items-center px-4 py-2 border-t-2 border-l-2 border-r-2 cursor-pointer transition-all rounded-tl-lg ${
                  !useAutoTime
                    ? 'border-black border-b-0 bg-white z-10'
                    : 'border-gray-300 border-b-2 border-b-black bg-white hover:border-black z-0'
                }`}>
                <input
                  type='radio'
                  name='timeMode'
                  checked={!useAutoTime}
                  onChange={async () => {
                    console.log('[TimeSync] Switching to manual mode');
                    setUseAutoTime(false);
                    saveGlobalSettings({ time_sync_mode: 'manual' });
                    // When switching to manual, disable NTP sync to prevent override
                    try {
                      // Disable NTP sync when switching to manual mode
                      await adminAuthFetch('/api/system/time/sync/disable', { method: 'POST' }).catch(() => {
                        // Ignore errors - the backend will handle NTP disable when setting time
                      });
                    } catch {
                      // Ignore errors
                    }
                    // When switching to manual, ensure inputs are populated
                    if (currentTime) {
                      if (!manualDate && currentTime.date) {
                        setManualDate(currentTime.date);
                      }
                      if (!manualTime && currentTime.time) {
                        setManualTime(currentTime.time.substring(0, 5));
                      }
                    }
                  }}
                  className='sr-only'
                />
                <span className={`text-sm font-medium  ${!useAutoTime ? 'text-black font-bold' : 'text-gray-600'}`}>Manual</span>
              </label>

              <label
                className={`relative flex flex-col items-center px-4 py-2 border-t-2 border-l-2 border-r-2 cursor-pointer transition-all rounded-tr-lg ${
                  useAutoTime
                    ? 'border-black border-b-0 bg-white z-10'
                    : 'border-gray-300 border-b-2 border-b-black bg-white hover:border-black z-0'
                }`}>
                <input
                  type='radio'
                  name='timeMode'
                  checked={useAutoTime}
                  onChange={() => {
                    console.log('[TimeSync] Switching to automatic mode');
                    setUseAutoTime(true);
                    saveGlobalSettings({ time_sync_mode: 'automatic' });
                    if (wifiStatus?.connected) {
                      syncTimeAutomatically();
                    }
                  }}
                  className='sr-only'
                />
                <div className='flex items-baseline justify-center gap-1.5'>
                  <WiFiIcon
                    className={`w-3 h-3 flex-shrink-0 ${useAutoTime ? 'text-black' : 'text-gray-600'}`}
                    style={{ transform: 'translateY(0.125rem)' }}
                  />
                  <span className={`text-sm font-medium  ${useAutoTime ? 'text-black font-bold' : 'text-gray-600'}`}>Automatic</span>
                </div>
              </label>
            </div>

            {/* Automatic Mode Actions */}
            {useAutoTime && (
              <div className='p-4 border-2 border-black rounded-b-lg -mt-[2px]'>
                {wifiStatus?.connected ? (
                  <div>
                    <p className='text-sm font-bold text-black mb-2 '>Time Set Automatically</p>
                    {currentTime && (
                      <div className='p-3 bg-gray-50 border-2 border-gray-300 rounded-lg'>
                        <div className='text-xs text-gray-600 mb-1 uppercase font-bold'>Current Time</div>
                        <div className='flex items-center gap-2'>
                          <div className='text-lg font-bold text-black'>
                            {currentTime.date} {formatTimeForDisplay(currentTime.time, settings.time_format || '12h')}
                          </div>
                          <WiFiIcon className='w-3.5 h-3.5' style={{ color: 'var(--color-text-muted)' }} />
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div>
                    <p className='text-sm font-bold text-black mb-2 '>Not Syncing</p>
                    <p className='text-sm text-yellow-700 mb-3 '>
                      Internet connection required for automatic time synchronization. Connect to WiFi or use manual mode.
                    </p>
                    {currentTime && (
                      <div className='p-3 bg-gray-50 border-2 border-gray-300 rounded-lg'>
                        <div className='text-xs text-gray-600 mb-1 uppercase font-bold'>Current Time</div>
                        <div className='flex items-center gap-2'>
                          <div className='text-lg font-bold text-black'>
                            {currentTime.date} {formatTimeForDisplay(currentTime.time, settings.time_format || '12h')}
                          </div>
                          <WiFiOffIcon className='w-3.5 h-3.5' style={{ color: 'var(--color-error)' }} />
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Manual Mode Inputs */}
            {!useAutoTime && (
              <div className='p-4 border-2 border-black rounded-b-lg -mt-[2px]'>
                <div className='grid grid-cols-1 min-[340px]:grid-cols-2 gap-3 mb-3'>
                  <div>
                    <label className='block mb-2 text-sm text-black  font-bold'>Date</label>
                    <input
                      type='date'
                      value={manualDate}
                      onChange={(e) => setManualDate(e.target.value)}
                      className='w-full py-3 px-2 text-base bg-white border-2 border-gray-300 rounded-lg text-black focus:border-black focus:outline-none box-border '
                    />
                  </div>
                  <div>
                    <label className='block mb-2 text-sm text-black  font-bold'>Time</label>
                    <input
                      type='time'
                      value={manualTime || ''}
                      onChange={(e) => {
                        const value = e.target.value;
                        setManualTime(value);
                        console.log('Time input changed:', value, 'Type:', typeof value);
                      }}
                      className='w-full py-3 px-2 text-base bg-white border-2 border-gray-300 rounded-lg text-black focus:border-black focus:outline-none box-border '
                      step='1'
                      required
                    />
                  </div>
                </div>
                <PrimaryButton onClick={setTimeManually} className='w-full'>
                  Set System Time
                </PrimaryButton>
                <p className='text-xs text-gray-600 mt-2 text-center '>Use this when offline or to set a specific time</p>
              </div>
            )}

            {/* Status Messages */}
            {timeStatus.message && (
              <div
                className={`mt-4 p-3 rounded-lg text-sm  border-2 ${
                  timeStatus.type === 'success' ? 'bg-gray-100 text-black border-black' : 'bg-white text-black border-black border-dashed'
                }`}>
                {timeStatus.type === 'error' && <span className='font-bold mr-2'>ERROR:</span>}
                {timeStatus.message}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Appearance */}
      <div className='rounded-xl p-[4px] shadow-lg' style={{ background: inkGradients[4] }}>
        <div className='bg-bg-card rounded-lg p-4 flex flex-col'>
          <h3 className='font-bold text-black  text-lg tracking-tight mb-3'>Appearance</h3>
          <div className='flex flex-wrap items-center justify-between gap-4'>
            <p className='flex-1 min-w-40 text-sm text-gray-600 '>
              System follows the appearance setting of the device you're browsing from.
            </p>
            <div className='inline-flex rounded-full border-2 border-black bg-white p-1 shadow-sm'>
              {[
                { value: 'light', Icon: SunIcon },
                { value: 'dark', Icon: MoonIcon },
                { value: 'system', Icon: MonitorIcon },
              ].map((choice) => {
                const value = choice.value;
                const ThemeIcon = choice.Icon;
                const selected = (settings?.theme || 'light') === value;
                return (
                  <button
                    key={value}
                    type='button'
                    className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-bold uppercase tracking-wide transition ${
                      selected
                        ? 'active-inverted'
                        : 'bg-transparent text-black hover:bg-gray-100'
                    }`}
                    aria-pressed={selected}
                    onClick={() => {
                      if (selected) return;
                      saveGlobalSettings({ theme: value });
                    }}
                  >
                    <ThemeIcon className='w-3.5 h-3.5' weight='bold' />
                    {value}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {/* Printer Settings */}
      <div className='rounded-xl p-[4px] shadow-lg' style={{ background: inkGradients[3] }}>
        <div className='bg-bg-card rounded-lg p-4 flex flex-col'>
          <h3 className='font-bold text-black  text-lg tracking-tight mb-3'>Printer Settings</h3>
          <div>
            <label className={labelClass}>Maximum Print Lines</label>
            <input
              type='number'
              min='0'
              max='1000'
              value={settings.max_print_lines ?? 200}
              onChange={(e) => saveGlobalSettings({ max_print_lines: parseInt(e.target.value) || 0 })}
              className={inputClass}
            />
            <p className='text-xs text-gray-600 mt-1 '>
              Maximum lines per print job to prevent endless prints. Set to 0 for no limit (default: 200)
            </p>
          </div>
        </div>
      </div>

      {/* Device Password */}
      <div className='rounded-xl p-[4px] shadow-lg' style={{ background: inkGradients[5] || inkGradients[4] }}>
        <div className='bg-bg-card rounded-lg p-4 flex flex-col'>
          <h3 className='font-bold text-black  text-lg tracking-tight mb-3'>Device Password</h3>
          <p className='text-sm text-gray-600 mb-4 '>
            The Device Password is shared across settings login, setup WiFi, printed setup instructions, and SSH access.
          </p>

          <div className='space-y-4'>
            <div className='p-4 border-2 border-gray-300 rounded-lg'>
              <div className='flex items-center justify-between mb-2 gap-3'>
                <span className='text-sm text-black font-bold'>Password Storage</span>
                <span
                  className={`px-3 py-1 rounded-full text-xs font-medium border-2 ${
                    devicePasswordBadgeMuted ? 'bg-white text-gray-500 border-gray-300' : 'bg-white text-black border-black'
                  }`}>
                  {devicePasswordStatus?.status_label || 'Loading...'}
                </span>
              </div>
              <p className='text-xs text-gray-600 mt-1 '>
                {devicePasswordStatus?.message || 'Loading Device Password status...'}
              </p>
            </div>

            {devicePasswordStatus?.can_change ? (
              <>
                <button
                  type='button'
                  onClick={() => setShowDevicePasswordChange(!showDevicePasswordChange)}
                  className='w-full py-2.5 px-4 bg-transparent border-2 border-gray-400 text-black rounded-lg  font-bold hover:border-black hover:bg-white transition-all cursor-pointer'>
                  {showDevicePasswordChange ? 'Cancel' : 'Change Device Password'}
                </button>

                {showDevicePasswordChange && (
                  <div className='p-4 border-2 border-gray-300 rounded-lg'>
                    <h4 className='text-sm text-black mb-3 font-bold'>Change Device Password</h4>
                    <div className='space-y-3'>
                      <div>
                        <label className='block mb-2 text-sm text-black  font-bold'>Current Device Password</label>
                        <input
                          type='password'
                          value={currentDevicePassword}
                          onChange={(e) => setCurrentDevicePassword(e.target.value)}
                          placeholder='Enter current password'
                          className={inputClass}
                          minLength={8}
                        />
                      </div>
                      <div>
                        <label className='block mb-2 text-sm text-black  font-bold'>New Device Password</label>
                        <input
                          type='password'
                          value={newDevicePassword}
                          onChange={(e) => setNewDevicePassword(e.target.value)}
                          placeholder='Minimum 8 characters'
                          className={inputClass}
                          minLength={8}
                        />
                      </div>
                      <div>
                        <label className='block mb-2 text-sm text-black  font-bold'>Confirm New Password</label>
                        <input
                          type='password'
                          value={confirmDevicePassword}
                          onChange={(e) => setConfirmDevicePassword(e.target.value)}
                          placeholder='Re-enter new password'
                          className={inputClass}
                          minLength={8}
                        />
                      </div>
                      <PrimaryButton
                        onClick={handleDevicePasswordChange}
                        disabled={
                          changingDevicePassword ||
                          !currentDevicePassword ||
                          !newDevicePassword ||
                          !confirmDevicePassword
                        }
                        loading={changingDevicePassword}
                        className='w-full'>
                        Change Device Password
                      </PrimaryButton>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className='p-4 border-2 border-gray-300 rounded-lg'>
                <p className='text-sm text-gray-600 '>
                  {devicePasswordUnavailableMessage}
                </p>
              </div>
            )}

            {devicePasswordMessage.message && (
              <div
                className={`p-3 rounded-lg text-sm  border-2 ${
                  devicePasswordMessage.type === 'success'
                    ? 'bg-gray-100 text-black border-black'
                    : 'bg-white text-black border-black border-dashed'
                }`}>
                {devicePasswordMessage.type === 'error' && <span className='font-bold mr-2'>ERROR:</span>}
                {devicePasswordMessage.message}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Config Backup */}
      <div className='rounded-xl p-[4px] shadow-lg' style={{ background: inkGradients[4] }}>
        <div className='bg-bg-card rounded-lg p-4 flex flex-col'>
          <h3 className='font-bold text-black text-lg tracking-tight mb-3'>Config Backup</h3>
          <p className='text-sm text-gray-600 mb-4'>
            Export your full faxomemo configuration to a JSON file, or import a previously exported config to restore settings, channels, and modules.
          </p>

          <input
            ref={configFileInputRef}
            type='file'
            accept='application/json,.json'
            onChange={handleImportConfig}
            className='hidden'
          />

          <div className='flex flex-col sm:flex-row gap-3'>
            <PrimaryButton
              onClick={handleExportConfig}
              disabled={exportingConfig || importingConfig}
              loading={exportingConfig}
              className='flex-1'>
              Export Config
            </PrimaryButton>
            <PrimaryButton
              onClick={() => configFileInputRef.current?.click()}
              disabled={exportingConfig || importingConfig}
              loading={importingConfig}
              className='flex-1'>
              Import Config
            </PrimaryButton>
          </div>

          <p className='text-xs text-gray-600 mt-2'>
            Imports replace the current configuration immediately. Use exported JSON files from faxomemo for the safest restore path.
          </p>

          {configTransferMessage.message && (
            <div
              className={`mt-4 p-3 rounded-lg text-sm border-2 ${
                configTransferMessage.type === 'success'
                  ? 'bg-gray-100 text-black border-black'
                  : 'bg-white text-black border-black border-dashed'
              }`}>
              <div className='flex items-start justify-between gap-3'>
                <div className='flex-1'>
                  {configTransferMessage.type === 'error' && <span className='font-bold mr-2'>ERROR:</span>}
                  <span>{configTransferMessage.message}</span>
                </div>
                <button
                  type='button'
                  onClick={clearConfigTransferMessage}
                  className='shrink-0 rounded border border-gray-400 bg-white px-2 py-1 text-xs font-bold text-gray-700 hover:border-black hover:text-black transition-colors cursor-pointer'>
                  X
                </button>
              </div>
              {configTransferMessage.warnings?.length > 0 && (
                <div className='mt-3 space-y-2'>
                  {configTransferMessage.warnings.map((warning, index) => (
                    <div
                      key={`${warning}-${index}`}
                      className='rounded-lg border border-gray-300 bg-white px-3 py-2 text-xs text-gray-700'>
                      {warning}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

    </div>
  );
};

export default GeneralSettings;
