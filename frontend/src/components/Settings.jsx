
import { useEffect, useState } from "react";
import { supabase } from "../lib/supabase";
import "./Settings.css";

const DEFAULT_SETTINGS = {
  name: "SMART ERP Admin",
  email: "",
  notifications: true,
  autoOCR: true,
  autoSave: true,
};

export default function Settings({ onBack }) {
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  // Load logged-in user and settings from Supabase
  useEffect(() => {
    let mounted = true;

    async function loadSettings() {
      setLoading(true);
      setErrorMessage("");

      try {
        const {
          data: { user: currentUser },
          error: authError,
        } = await supabase.auth.getUser();

        if (authError) throw authError;

        if (!currentUser) {
          throw new Error(
            "Please log in with your Supabase account to access Settings."
          );
        }

        if (!mounted) return;

        setUser(currentUser);

        const defaults = {
          ...DEFAULT_SETTINGS,
          name:
            currentUser.user_metadata?.full_name ||
            currentUser.user_metadata?.name ||
            currentUser.email?.split("@")[0] ||
            DEFAULT_SETTINGS.name,
          email: currentUser.email || "",
        };

        const { data, error } = await supabase
          .from("user_settings")
          .select("*")
          .eq("user_id", currentUser.id)
          .maybeSingle();

        if (error) throw error;

        if (!mounted) return;

        if (data) {
          setSettings({
            name: data.name ?? defaults.name,
            email: data.email ?? defaults.email,
            notifications:
              data.notifications ?? defaults.notifications,
            autoOCR: data.auto_ocr ?? defaults.autoOCR,
            autoSave: data.auto_save ?? defaults.autoSave,
          });
        } else {
          setSettings(defaults);
        }
      } catch (error) {
        console.error("Settings loading error:", error);

        if (mounted) {
          setErrorMessage(
            error.message ||
              "Unable to load settings from Supabase."
          );
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

  // Update a setting in the form
  function handleChange(field, value) {
    setSettings((previous) => ({
      ...previous,
      [field]: value,
    }));

    setSaved(false);
    setErrorMessage("");
  }

  // Save settings to Supabase
  async function handleSave() {
    if (!user) {
      setErrorMessage("Please log in before saving settings.");
      return;
    }

    setSaving(true);
    setSaved(false);
    setErrorMessage("");

    try {
      const payload = {
        user_id: user.id,
        name: settings.name.trim(),
        email: settings.email.trim(),
        notifications: settings.notifications,
        auto_ocr: settings.autoOCR,
        auto_save: settings.autoSave,
        updated_at: new Date().toISOString(),
      };

      const { error } = await supabase
        .from("user_settings")
        .upsert(payload, {
          onConflict: "user_id",
        });

      if (error) throw error;

      setSaved(true);
    } catch (error) {
      console.error("Settings saving error:", error);

      setErrorMessage(
        error.message ||
          "Unable to save settings. Please try again."
      );
    } finally {
      setSaving(false);
    }
  }

  // Reset settings and save the defaults to Supabase
  async function handleReset() {
    const confirmed = window.confirm(
      "Are you sure you want to reset all settings?"
    );

    if (!confirmed || !user) return;

    const defaults = {
      ...DEFAULT_SETTINGS,
      name:
        user.user_metadata?.full_name ||
        user.user_metadata?.name ||
        user.email?.split("@")[0] ||
        DEFAULT_SETTINGS.name,
      email: user.email || "",
    };

    setSettings(defaults);
    setSaved(false);
    setErrorMessage("");
    setSaving(true);

    try {
      const { error } = await supabase
        .from("user_settings")
        .upsert(
          {
            user_id: user.id,
            name: defaults.name,
            email: defaults.email,
            notifications: defaults.notifications,
            auto_ocr: defaults.autoOCR,
            auto_save: defaults.autoSave,
            updated_at: new Date().toISOString(),
          },
          { onConflict: "user_id" }
        );

      if (error) throw error;

      setSaved(true);
    } catch (error) {
      console.error("Settings reset error:", error);

      setErrorMessage(
        error.message ||
          "Unable to reset settings. Please try again."
      );
    } finally {
      setSaving(false);
    }
  }

  // Loading screen
  if (loading) {
    return (
      <div className="settings-page">
        <div className="settings-container">
          <section className="settings-card">
            <h2>Loading Settings...</h2>
            <p>Connecting to your SMART ERP account.</p>
          </section>
        </div>
      </div>
    );
  }

  return (
    <div className="settings-page">
      <div className="settings-container">

        {/* HEADER */}
        <header className="settings-header">
          <button
            type="button"
            className="settings-back-btn"
            onClick={onBack}
          >
            ← Back to Dashboard
          </button>

          <div className="settings-title">
            <h1>Settings</h1>
            <p>
              Manage your SMART ERP system preferences.
            </p>
          </div>
        </header>

        {/* SUCCESS MESSAGE */}
        {saved && (
          <div className="settings-success" role="status">
            <span>✓</span>
            <span>Settings saved successfully to Supabase.</span>
          </div>
        )}

        {/* ERROR MESSAGE */}
        {errorMessage && (
          <div className="settings-error" role="alert">
            <strong>Unable to complete operation</strong>
            <p>{errorMessage}</p>
          </div>
        )}

        {/* PROFILE */}
        <section className="settings-card">
          <div className="settings-card-header">
            <div className="settings-icon">👤</div>

            <div>
              <h2>Profile</h2>
              <p>
                Manage your SMART ERP account information.
              </p>
            </div>
          </div>

          <div className="settings-form">
            <div className="settings-field">
              <label htmlFor="settings-name">Name</label>

              <input
                id="settings-name"
                type="text"
                value={settings.name}
                onChange={(event) =>
                  handleChange("name", event.target.value)
                }
                placeholder="Enter your name"
                maxLength={100}
                disabled={saving || !user}
              />
            </div>

            <div className="settings-field">
              <label htmlFor="settings-email">Email</label>

              <input
                id="settings-email"
                type="email"
                value={settings.email}
                onChange={(event) =>
                  handleChange("email", event.target.value)
                }
                placeholder="Enter your email"
                maxLength={255}
                disabled={saving || !user}
              />

              <small>
                This changes the email displayed in Settings,
                not your Supabase login email.
              </small>
            </div>
          </div>
        </section>

        {/* NOTIFICATIONS */}
        <section className="settings-card">
          <div className="settings-card-header">
            <div className="settings-icon">🔔</div>

            <div>
              <h2>Notifications</h2>
              <p>
                Control SMART ERP notification preferences.
              </p>
            </div>
          </div>

          <div className="settings-option">
            <div className="settings-option-text">
              <h3>Enable Notifications</h3>
              <p>
                Receive notifications about certificate
                processing, OCR and system activity.
              </p>
            </div>

            <label className="toggle">
              <input
                type="checkbox"
                checked={settings.notifications}
                onChange={(event) =>
                  handleChange(
                    "notifications",
                    event.target.checked
                  )
                }
                disabled={saving || !user}
              />

              <span className="toggle-slider" />
            </label>
          </div>
        </section>

        {/* CERTIFICATE SETTINGS */}
        <section className="settings-card">
          <div className="settings-card-header">
            <div className="settings-icon">📄</div>

            <div>
              <h2>Certificate Settings</h2>
              <p>
                Configure certificate processing options.
              </p>
            </div>
          </div>

          {/* AUTOMATIC OCR */}
          <div className="settings-option">
            <div className="settings-option-text">
              <h3>Automatic OCR</h3>
              <p>
                Automatically read certificate text after
                upload or camera scanning.
              </p>
            </div>

            <label className="toggle">
              <input
                type="checkbox"
                checked={settings.autoOCR}
                onChange={(event) =>
                  handleChange("autoOCR", event.target.checked)
                }
                disabled={saving || !user}
              />

              <span className="toggle-slider" />
            </label>
          </div>

          {/* AUTOMATIC SAVE */}
          <div className="settings-option">
            <div className="settings-option-text">
              <h3>Automatic Save</h3>
              <p>
                Automatically save extracted certificate
                information after OCR processing.
              </p>
            </div>

            <label className="toggle">
              <input
                type="checkbox"
                checked={settings.autoSave}
                onChange={(event) =>
                  handleChange("autoSave", event.target.checked)
                }
                disabled={saving || !user}
              />

              <span className="toggle-slider" />
            </label>
          </div>
        </section>

        {/* SYSTEM INFORMATION */}
        <section className="settings-card">
          <div className="settings-card-header">
            <div className="settings-icon">⚙️</div>

            <div>
              <h2>System Information</h2>
              <p>
                SMART ERP Certificate Management System
              </p>
            </div>
          </div>

          <div className="system-information">
            <div className="system-row">
              <span>Application</span>
              <strong>SMART ERP</strong>
            </div>

            <div className="system-row">
              <span>Version</span>
              <strong>1.0.0</strong>
            </div>

            <div className="system-row">
              <span>Authentication</span>
              <strong className="status-active">
                ● Supabase Auth
              </strong>
            </div>

            <div className="system-row">
              <span>Settings Storage</span>
              <strong className="status-active">
                ● Supabase Database
              </strong>
            </div>
          </div>
        </section>

        {/* ACTION BUTTONS */}
        <div className="settings-actions">
          <button
            type="button"
            className="reset-btn"
            onClick={handleReset}
            disabled={saving || !user}
          >
            Reset Settings
          </button>

          <button
            type="button"
            className="save-btn"
            onClick={handleSave}
            disabled={saving || !user}
          >
            {saving ? "Saving..." : "✓ Save Settings"}
          </button>
        </div>
      </div>
    </div>
  );
}
