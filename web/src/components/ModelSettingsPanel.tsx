import { useEffect, useState } from 'react';
import type { ModelProviderId, PublicModelSettings } from '@backend-types/index';

import { getModelSettings, saveModelSettings } from '../api';
import './ModelSettingsPanel.css';

const PROVIDER_DEFAULTS: Record<ModelProviderId, { baseUrl: string; model: string }> = {
  'anthropic-compatible': {
    baseUrl: 'https://api.deepseek.com/anthropic',
    model: 'deepseek-v4-flash',
  },
  'openai-compatible': {
    baseUrl: 'https://api.openai.com/v1',
    model: '',
  },
};

export function ModelSettingsPanel() {
  const [settings, setSettings] = useState<PublicModelSettings | null>(null);
  const [provider, setProvider] = useState<ModelProviderId>('anthropic-compatible');
  const [baseUrl, setBaseUrl] = useState(PROVIDER_DEFAULTS['anthropic-compatible'].baseUrl);
  const [model, setModel] = useState(PROVIDER_DEFAULTS['anthropic-compatible'].model);
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void getModelSettings()
      .then((loaded) => {
        if (!active) return;
        setSettings(loaded);
        setProvider(loaded.provider);
        setBaseUrl(loaded.baseUrl);
        setModel(loaded.model);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Failed to load model settings');
      });
    return () => { active = false; };
  }, []);

  const changeProvider = (next: ModelProviderId) => {
    setProvider(next);
    setBaseUrl(PROVIDER_DEFAULTS[next].baseUrl);
    setModel(PROVIDER_DEFAULTS[next].model);
    setMessage(null);
  };

  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const saved = await saveModelSettings({
        provider,
        baseUrl: baseUrl.trim(),
        model: model.trim(),
        ...(apiKey.trim() ? { apiKey: apiKey.trim() } : {}),
      });
      setSettings(saved);
      setApiKey('');
      setMessage('Model settings saved locally.');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Failed to save model settings');
    } finally {
      setSaving(false);
    }
  };

  return (
    <details className="model-settings" open={settings?.hasApiKey === false}>
      <summary>
        <span>Model Settings</span>
        <span className={`settings-status ${settings?.hasApiKey ? 'configured' : ''}`}>
          {settings?.hasApiKey ? `${settings.provider} · configured` : 'configuration required'}
        </span>
      </summary>
      <form className="settings-form" onSubmit={handleSave}>
        <label>
          Provider protocol
          <select value={provider} onChange={(event) => changeProvider(event.target.value as ModelProviderId)}>
            <option value="anthropic-compatible">Anthropic-compatible (Claude Agent SDK)</option>
            <option value="openai-compatible">OpenAI-compatible (Chat Completions)</option>
          </select>
        </label>
        <label>
          Base URL
          <input
            type="url"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            required
          />
        </label>
        <label>
          Model ID
          <input
            type="text"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder="Enter the exact model ID from your provider"
            required
          />
        </label>
        <label>
          API Key
          <input
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={settings?.hasApiKey ? 'Leave blank to keep the saved key' : 'Required'}
            autoComplete="off"
          />
        </label>
        <div className="settings-actions">
          <button className="btn-primary" type="submit" disabled={saving}>
            {saving ? 'Saving...' : 'Save locally'}
          </button>
          <p>Stored only in this RepoMentor installation under <code>data/</code>.</p>
        </div>
        {message && <p className="settings-message">{message}</p>}
        {error && <p className="error-text">{error}</p>}
      </form>
    </details>
  );
}
