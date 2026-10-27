import { useEffect, useState } from "react";
import {
  AlertCircle,
  Bell,
  Check,
  Database,
  FileText,
  RefreshCw,
  Settings as SettingsIcon,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import { supabase } from "../lib/supabase";
import AppGlobalHeader from "./AppGlobalHeader";
import "./Settings.css";

const API_BASE_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, "");
const APP_VERSION = import.meta.env.VITE_APP_VERSION || "";

const DEFAULT_SETTINGS = {
  name: "",
  email: "",
  notifications: true,
  autoOCR: true,
  autoSave: true,
};

function profileDefaults(currentUser) {
  return {
    ...DEFAULT_SETTINGS,
    name: currentUser?.user_metadata?.full_name
      || currentUser?.user_metadata?.name
      || currentUser?.email?.split("@")[0]
      || "SMART ERP User",
    email: currentUser?.email || "",
  };
}

function systemStatusLabel(status) {
  if (status === "verified") return "Connected";
  if (status === "unavailable") return "Unavailable";
  if (status === "not-configured") return "Not configured";
  if (status === "not-checked") return "Not checked";
  return "Checking…";
}

function SwitchRow({ id, title, description, checked, disabled, onChange }) {
  return (
    <div className="settings-option">
      <div className="settings-option-text">
        <label htmlFor={id}>{title}</label>
        <p id={`${id}-description`}>{description}</p>
      </div>
      <label className="settings-switch">
        <input
          id={id}
          type="checkbox"
          role="switch"
          aria-describedby={`${id}-description`}
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span aria-hidden="true" />
      </label>
    </div>
  );
}

export default function Settings({
  onBack,
  onNavigate,
  onGlobalSearch,
  onLogout,
  logoutLoading = false,
  userRole = "admin",
}) {
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [systemStatus, setSystemStatus] = useState({
    authentication: supabase ? "checking" : "not-configured",
    storage: supabase ? "checking" : "not-configured",
    version: APP_VERSION || "checking",
  });

  useEffect(() => {
    let mounted = true;
    let authenticationVerified = false;
    let storageVerified = false;

    async function loadSettings() {
      if (!supabase) {
        setErrorMessage("Supabase is not configured. Add the frontend Supabase URL and anon key, then restart the app.");
        setLoading(false);
        return;
      }
      try {
        const {
          data: { user: currentUser },
          error: authError,
        } = await supabase.auth.getUser();
        if (authError) throw authError;
        if (!currentUser) throw new Error("Please sign in with your Supabase account to view Settings.");
        authenticationVerified = true;
        if (!mounted) return;
        setUser(currentUser);
        setSystemStatus((previous) => ({ ...previous, authentication: "verified" }));

        const defaults = profileDefaults(currentUser);
        const { data, error } = await supabase
          .from("user_settings")
          .select("*")
          .eq("user_id", currentUser.id)
          .maybeSingle();
        if (error) throw error;
        storageVerified = true;
        if (!mounted) return;

        setSettings(data ? {
          name: data.name ?? defaults.name,
          email: data.email ?? defaults.email,
          notifications: data.notifications ?? defaults.notifications,
          autoOCR: data.auto_ocr ?? defaults.autoOCR,
          autoSave: data.auto_save ?? defaults.autoSave,
        } : defaults);
        setSystemStatus((previous) => ({ ...previous, storage: "verified" }));
      } catch (error) {
        console.error("Settings loading error:", error);
        if (mounted) {
          setErrorMessage(error?.message || "Unable to load settings.");
          setSystemStatus((previous) => ({
            ...previous,
            authentication: authenticationVerified ? "verified" : "unavailable",
            storage: storageVerified ? "verified" : authenticationVerified ? "unavailable" : "not-checked",
          }));
        }
      } finally {
        if (mounted) setLoading(false);
      }
    }

    loadSettings();
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (APP_VERSION) return undefined;
    let mounted = true;
    fetch(`${API_BASE_URL}/openapi.json`)
      .then((response) => {
        if (!response.ok) throw new Error("Version endpoint unavailable");
        return response.json();
      })
      .then((data) => {
        if (mounted) setSystemStatus((previous) => ({ ...previous, version: data?.info?.version || "Unavailable" }));
      })
      .catch(() => {
        if (mounted) setSystemStatus((previous) => ({ ...previous, version: "Unavailable" }));
      });
    return () => {
      mounted = false;
    };
  }, []);

  function handleChange(field, value) {
    setSettings((previous) => ({ ...previous, [field]: value }));
    setSaved(false);
    setErrorMessage("");
  }

  async function persistSettings(nextSettings) {
    if (!user || !supabase) {
      setErrorMessage("Please sign in before saving Settings.");
      return false;
    }
    const normalized = {
      ...nextSettings,
      name: nextSettings.name.trim(),
      email: nextSettings.email.trim(),
    };
    if (!normalized.name) {
      setErrorMessage("Enter a name for your SMART ERP profile.");
      return false;
    }
    if (normalized.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalized.email)) {
      setErrorMessage("Enter a valid display email address.");
      return false;
    }

    setSaving(true);
    setSaved(false);
    setErrorMessage("");
    try {
      const { error } = await supabase.from("user_settings").upsert({
        user_id: user.id,
        name: normalized.name,
        email: normalized.email,
        notifications: normalized.notifications,
        auto_ocr: normalized.autoOCR,
        auto_save: normalized.autoSave,
        updated_at: new Date().toISOString(),
      }, { onConflict: "user_id" });
      if (error) throw error;
      setSettings(normalized);
      setSystemStatus((previous) => ({ ...previous, storage: "verified" }));
      setSaved(true);
      return true;
    } catch (error) {
      console.error("Settings save error:", error);
      setErrorMessage(error?.message || "Unable to save settings. Check your connection and try again.");
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function handleSave(event) {
    event.preventDefault();
    if (saving) return;
    await persistSettings(settings);
  }

  async function handleReset() {
    if (saving || !user) return;
    const confirmed = window.confirm(
      "Restore the notification and certificate preferences to their defaults and save them to your account? Your profile, login, certificates, and database records will not be deleted."
    );
    if (!confirmed) return;
    await persistSettings({
      ...settings,
      notifications: DEFAULT_SETTINGS.notifications,
      autoOCR: DEFAULT_SETTINGS.autoOCR,
      autoSave: DEFAULT_SETTINGS.autoSave,
    });
  }

  const controlsDisabled = loading || saving || !user;

  return (
    <main className="settings-page">
      <header className="erp-topbar settings-appbar is-navy">
        <AppGlobalHeader
          user={user}
          userRole={userRole}
          onGlobalSearch={onGlobalSearch}
          onNavigate={onNavigate}
          onLogout={onLogout}
          logoutLoading={logoutLoading}
          showBrand
          showSearch={false}
          variant="navy"
        />
      </header>

      <div className="settings-container">
        <nav className="settings-breadcrumb" aria-label="Breadcrumb">
          <button type="button" onClick={onBack}>Dashboard</button>
          <span>/</span>
          <strong>Settings</strong>
        </nav>

        <header className="settings-page-heading">
          <button type="button" className="settings-back-btn" onClick={onBack}>
            <span aria-hidden="true">←</span> Back to Dashboard
          </button>
          <div className="settings-title">
            <span className="settings-title-icon"><SettingsIcon size={22} /></span>
            <div>
              <h1>Settings &amp; Preferences</h1>
              <p>Manage your account, notification preferences, and certificate processing options.</p>
            </div>
          </div>
        </header>

        {saved && <div className="settings-success" role="status"><Check size={18} /><span>Settings saved to your account.</span></div>}
        {errorMessage && <div className="settings-error" role="alert"><AlertCircle size={18} /><div><strong>Unable to complete operation</strong><p>{errorMessage}</p></div></div>}

        {loading ? (
          <section className="settings-card settings-loading" aria-live="polite">
            <RefreshCw size={18} className="settings-spin" />
            <div><strong>Loading your Settings</strong><span>Checking your account and saved preferences…</span></div>
          </section>
        ) : (
          <form className="settings-form" onSubmit={handleSave}>
            <section className="settings-card">
              <div className="settings-card-header">
                <span className="settings-icon"><UserRound size={20} /></span>
                <div><h2>Profile Information</h2><p>Manage the display information shown in SMART ERP.</p></div>
              </div>
              <div className="settings-profile-fields">
                <div className="settings-field">
                  <label htmlFor="settings-name">Name</label>
                  <input id="settings-name" type="text" required maxLength={100} value={settings.name} onChange={(event) => handleChange("name", event.target.value)} placeholder="Enter your name" disabled={controlsDisabled} autoComplete="name" />
                </div>
                <div className="settings-field">
                  <label htmlFor="settings-email">Display email</label>
                  <input id="settings-email" type="email" maxLength={255} value={settings.email} onChange={(event) => handleChange("email", event.target.value)} placeholder="name@example.com" disabled={controlsDisabled} autoComplete="email" />
                  <small>This changes the email displayed in Settings, not your Supabase login email.</small>
                </div>
              </div>
            </section>

            <section className="settings-card">
              <div className="settings-card-header">
                <span className="settings-icon"><Bell size={20} /></span>
                <div><h2>Notifications</h2><p>Control your SMART ERP notification preference.</p></div>
              </div>
              <SwitchRow id="settings-notifications" title="Enable Notifications" description="Receive notifications about certificate processing, OCR, and system activity." checked={settings.notifications} disabled={controlsDisabled} onChange={(value) => handleChange("notifications", value)} />
              <p className="settings-limitation">This preference is saved to your account. The application does not currently generate system notifications.</p>
            </section>

            <section className="settings-card">
              <div className="settings-card-header">
                <span className="settings-icon"><FileText size={20} /></span>
                <div><h2>Certificate Processing Settings</h2><p>Configure your preferred certificate processing options.</p></div>
              </div>
              <SwitchRow id="settings-auto-ocr" title="Automatic OCR" description="Automatically read certificate text after upload or camera scanning." checked={settings.autoOCR} disabled={controlsDisabled} onChange={(value) => handleChange("autoOCR", value)} />
              <SwitchRow id="settings-auto-save" title="Automatic Save" description="Automatically save extracted certificate information after OCR processing." checked={settings.autoSave} disabled={controlsDisabled} onChange={(value) => handleChange("autoSave", value)} />
              <p className="settings-limitation">These preferences are stored but are not yet connected to the upload workflow. OCR currently runs after upload, and saving still requires the review screen action.</p>
            </section>

            <section className="settings-card">
              <div className="settings-card-header">
                <span className="settings-icon"><Database size={20} /></span>
                <div><h2>System Information</h2><p>SMART ERP Certificate Data Extraction and Management System.</p></div>
              </div>
              <div className="settings-information">
                <div className="settings-information-row"><span>Application</span><strong>SMART ERP</strong></div>
                <div className="settings-information-row"><span>Version</span><strong>{systemStatus.version === "checking" ? "Checking…" : systemStatus.version}</strong></div>
                <div className="settings-information-row"><span>Authentication</span><strong className={systemStatus.authentication === "verified" ? "is-verified" : ""}><ShieldCheck size={14} />{systemStatusLabel(systemStatus.authentication)}</strong></div>
                <div className="settings-information-row"><span>Settings Storage</span><strong className={systemStatus.storage === "verified" ? "is-verified" : ""}><Database size={14} />{systemStatusLabel(systemStatus.storage)}</strong></div>
              </div>
            </section>

            <div className="settings-actions">
              <p>Reset restores saved preferences to their defaults; it does not delete your profile or certificates.</p>
              <div>
                <button type="button" className="settings-reset-btn" onClick={handleReset} disabled={controlsDisabled}>Reset Settings</button>
                <button type="submit" className="settings-save-btn" disabled={controlsDisabled}>{saving ? "Saving…" : "Save Settings"}</button>
              </div>
            </div>
          </form>
        )}
      </div>
    </main>
  );
}
