import { normalizePrintWebhookEndpointPath } from '../utils';

export const getModuleValidationErrors = (module) => {
  const errors = {};
  const config = module?.config || {};

  if (!String(module?.name || '').trim()) {
    errors.name = 'Module name is required.';
  }

  if (module?.type === 'email') {
    const email = String(config.email_user || '').trim();
    const password = String(config.email_password || '').trim();
    const provider = String(config.email_service || 'Gmail');
    const host = String(config.email_host || '').trim();
    const port = Number(config.email_port ?? 993);

    if (!email) {
      errors.email_user = 'Email address is required.';
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      errors.email_user = 'Enter a valid email address.';
    }

    if (!password) {
      errors.email_password = 'App password is required.';
    }

    if (provider === 'Custom') {
      if (!host) {
        errors.email_host = 'IMAP host is required for custom provider.';
      }
      if (!Number.isFinite(port) || port < 1 || port > 65535) {
        errors.email_port = 'Enter a valid IMAP port (1-65535).';
      }
    }
  }

  if (module?.type === 'webhook') {
    const url = String(config.url || '').trim();
    const method = String(config.method || 'GET').toUpperCase();
    const body = String(config.body || '').trim();
    const authType = String(config.auth_type || 'none').toLowerCase();
    const authUsername = String(config.auth_username || '').trim();
    const authPassword = String(config.auth_password || '');

    if (!url) {
      errors.url = 'URL is required.';
    } else {
      try {
        const parsed = new URL(url);
        if (!['http:', 'https:'].includes(parsed.protocol)) {
          errors.url = 'URL must start with http:// or https://';
        }
      } catch {
        errors.url = 'Enter a valid URL.';
      }
    }

    if (method === 'POST' && body) {
      try {
        JSON.parse(body);
      } catch {
        errors.body = 'Body must be valid JSON.';
      }
    }

    if (!['none', 'basic', 'digest'].includes(authType)) {
      errors.auth_type = 'Choose a valid auth type.';
    }

    if (['basic', 'digest'].includes(authType)) {
      if (!authUsername) {
        errors.auth_username = 'Username is required for this auth type.';
      }
      if (!authPassword) {
        errors.auth_password = 'Password is required for this auth type.';
      }
    }
  }

  if (module?.type === 'slack') {
    const botToken = String(config.bot_token || '').trim();
    const appToken = String(config.app_token || '').trim();

    if (!botToken) {
      errors.bot_token = 'Bot token is required.';
    } else if (!botToken.startsWith('xoxb-')) {
      errors.bot_token = 'Bot tokens start with xoxb-.';
    }

    if (!appToken) {
      errors.app_token = 'App-level token is required.';
    } else if (!appToken.startsWith('xapp-')) {
      errors.app_token = 'App-level tokens start with xapp-.';
    }

    const allowedIds = String(config.allowed_user_ids || '').trim();
    if (allowedIds && !/^[A-Za-z0-9,\s]+$/.test(allowedIds)) {
      errors.allowed_user_ids = 'Use comma-separated Slack member IDs (e.g. U0123ABC).';
    }
  }

  if (module?.type === 'newsstand') {
    const sources = String(config.sources || '').trim();
    if (!sources) {
      errors.sources = 'Add at least one source (recipe name, .recipe path, or feed URL).';
    }
  }

  if (module?.type === 'print_webhook') {
    const endpointPath = String(config.endpoint_path || '').trim();
    const enabledTypes = [config.accept_text, config.accept_images, config.accept_json].some(Boolean);

    if (endpointPath && !normalizePrintWebhookEndpointPath(endpointPath)) {
      errors.endpoint_path = 'Use letters, numbers, or hyphens in the endpoint path.';
    }

    if (!enabledTypes) {
      errors.accept_text = 'Enable at least one accepted payload type.';
    }
  }

  return errors;
};

export const getModuleValidationIssueCount = (module) => Object.keys(getModuleValidationErrors(module)).length;
