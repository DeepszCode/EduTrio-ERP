
import { useState } from "react";
import { supabase } from "../lib/supabase";
import { ROLE_LABELS, ROLES } from "../auth/roles";

export default function LoginPage({ onBack, onLogin, authError = "" }) {
  const [activeTab, setActiveTab] = useState("login");
  const [selectedRole, setSelectedRole] = useState(ROLES.STUDENT);

  return (
    <main className="login-page">
      {/* LEFT BLUE PANEL */}
      <section className="login-info-panel">
        <button
          type="button"
          className="login-back-button"
          onClick={onBack}
        >
          ← Back
        </button>

        <div className="login-info-content">
          <div className="login-info-text">
            {activeTab === "login" ? (
              <>
                <h1>Login to Your Account</h1>
                <p>
                  Access your certificates, manage
                  <br />
                  records and more.
                </p>
              </>
            ) : (
              <>
                <h1>Join SMART ERP</h1>
                <p>
                  Create your account to get started
                  <br />
                  and manage your certificates.
                </p>
              </>
            )}
          </div>

          <div className="security-illustration">
            <SecurityIllustration />
          </div>
        </div>

        <div className="login-circle circle-one" />
        <div className="login-circle circle-two" />
        <div className="login-glow" />
      </section>

      {/* RIGHT FORM PANEL */}
      <section className="login-form-panel">
        <div className="login-container">
          <div className="auth-tabs">
            <button
              type="button"
              className={
                activeTab === "login"
                  ? "auth-tab active"
                  : "auth-tab"
              }
              onClick={() => setActiveTab("login")}
            >
              Login
            </button>

            <button
              type="button"
              className={
                activeTab === "register"
                  ? "auth-tab active"
                  : "auth-tab"
              }
              onClick={() => setActiveTab("register")}
            >
              Create Account
            </button>
          </div>

          {activeTab === "login" ? (
            <LoginForm
              onCreateAccount={() => setActiveTab("register")}
              onLogin={onLogin}
              selectedRole={selectedRole}
              onRoleChange={setSelectedRole}
              authError={authError}
            />
          ) : (
            <CreateAccount
              onSwitchToLogin={() => setActiveTab("login")}
              onAuthenticated={onLogin}
              selectedRole={selectedRole}
              onRoleChange={setSelectedRole}
            />
          )}
        </div>
      </section>

      {/* FOOTER */}
      <footer className="login-footer">
        <svg
          viewBox="0 0 48 40"
          className="login-footer-logo"
          aria-hidden="true"
        >
          <path
            d="M24 2 L44 11 L24 20 L4 11 Z"
            fill="#FFFFFF"
          />
          <path
            d="M7 18 V30 Q17 29 23 37 V23 Q15 17 7 18 Z"
            fill="none"
            stroke="#FFFFFF"
            strokeWidth="3"
          />
          <path
            d="M41 18 V30 Q31 29 25 37 V23 Q33 17 41 18 Z"
            fill="none"
            stroke="#FFFFFF"
            strokeWidth="3"
          />
        </svg>

        <span>
          SMART ERP CERTIFICATE DATA EXTRACTION
          AND MANAGEMENT SYSTEM
        </span>
      </footer>
    </main>
  );
}

export function PasswordResetPage({ onComplete, onBack, invitation = false }) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    setMessage("");
    if (password.length < 8) return setError("Password must contain at least 8 characters.");
    if (password !== confirm) return setError("Passwords do not match.");
    if (!supabase) return setError("Supabase configuration is missing.");
    setLoading(true);
    try {
      const { error: updateError } = await supabase.auth.updateUser({ password });
      if (updateError) throw updateError;
      setMessage("Password updated. Verifying your account...");
      const { data: { session }, error: sessionError } = await supabase.auth.getSession();
      if (sessionError || !session) throw sessionError || new Error("Your recovery session expired. Request a new reset link.");
      const accepted = await onComplete(session);
      if (accepted === false) throw new Error("Your password was updated, but your assigned role could not be verified. Sign in again or contact an administrator.");
    } catch (err) {
      setError(err?.message || "Unable to update your password.");
    } finally {
      setLoading(false);
    }
  };

  return <main className="login-page password-reset-page">
    <section className="login-info-panel"><div className="login-info-content"><div className="login-info-text"><h1>{invitation ? "Accept Your Invitation" : "Secure Your Account"}</h1><p>{invitation ? "Set your password to finish creating your SMART ERP student account." : "Choose a new password to continue using SMART ERP."}</p></div><div className="security-illustration"><SecurityIllustration /></div></div></section>
    <section className="login-form-panel"><div className="login-container"><div className="login-card"><div className="login-heading"><h2>{invitation ? "Complete Student Registration" : "Reset Password"}</h2><p>Choose a new password for your account</p></div>
      <form onSubmit={submit}>
        <div className="form-group"><label htmlFor="reset-password">New password</label><div className="input-wrapper"><input id="reset-password" type="password" autoComplete="new-password" minLength={8} required value={password} onChange={(event) => setPassword(event.target.value)} placeholder="At least 8 characters" /></div></div>
        <div className="form-group"><label htmlFor="reset-confirm">Confirm password</label><div className="input-wrapper"><input id="reset-confirm" type="password" autoComplete="new-password" minLength={8} required value={confirm} onChange={(event) => setConfirm(event.target.value)} placeholder="Enter the password again" /></div></div>
        {error && <p className="register-message" role="alert">{error}</p>}{message && <p className="register-message" role="status">{message}</p>}
        <button type="submit" className="login-submit" disabled={loading}><span>{loading ? "Updating..." : invitation ? "Set Password & Accept Invitation" : "Save New Password"}</span></button>
      </form><div className="create-account-text"><button type="button" onClick={onBack}>Back to Login</button></div>
    </div></div></section>
  </main>;
}

/* LOGIN FORM */
function RoleSelector({ selectedRole, onChange, registration = false }) {
  return (
    <fieldset className="role-selector">
      <legend>{registration ? "Account role" : "Sign in as"}</legend>
      <div className="role-options">
        {Object.entries(ROLE_LABELS).map(([role, label]) => {
          const disabled = registration && role === ROLES.ADMIN;
          return <button type="button" key={role} className={`role-option${selectedRole === role ? " selected" : ""}`} aria-pressed={selectedRole === role} disabled={disabled} onClick={() => onChange(role)}>
            <span>{label}</span>{disabled && <small>Invite only</small>}
          </button>;
        })}
      </div>
      {registration && <p className="role-help">Staff registration is limited to email addresses approved by an Admin. Students must use their Staff invitation email. Admin accounts cannot be created here.</p>}
    </fieldset>
  );
}

function LoginForm({ onCreateAccount, onLogin, selectedRole, onRoleChange, authError }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError("");
    setNotice("");

    if (!supabase) {
      setError("Supabase configuration is missing. Check your frontend .env file.");
      return;
    }

    const cleanEmail = email.trim();

    if (!cleanEmail || !cleanEmail.includes("@")) {
      setError("Please enter a valid email address.");
      return;
    }

    if (!password) {
      setError("Please enter your password.");
      return;
    }

    setLoading(true);

    try {
      const { data, error: authError } =
        await supabase.auth.signInWithPassword({
          email: cleanEmail,
          password,
        });

      if (authError) {
        setError(authError.message);
        return;
      }

      if (data.session && onLogin) {
        await onLogin(data.session, selectedRole);
      }
    } catch (err) {
      setError(err?.message || "Unable to log in. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleGoogleLogin = async () => {
    setError("");
    setNotice("");

    if (!supabase) {
      setError("Supabase configuration is missing. Check your frontend .env file.");
      return;
    }

    setLoading(true);
    try {
      window.sessionStorage.setItem("smart-erp-login-role", selectedRole);
      const { error: oauthError } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: {
          redirectTo: window.location.origin,
        },
      });

      if (oauthError) {
        window.sessionStorage.removeItem("smart-erp-login-role");
        setError(oauthError.message);
        setLoading(false);
      }
      // Supabase redirects the browser to Google when OAuth starts successfully.
    } catch (err) {
      setError(err?.message || "Unable to sign in with Google. Please try again.");
      setLoading(false);
    }
  };

  const handleForgotPassword = async () => {
    setError("");
    setNotice("");

    if (!supabase) {
      setError("Supabase configuration is missing.");
      return;
    }

    const cleanEmail = email.trim();

    if (!cleanEmail || !cleanEmail.includes("@")) {
      setError("Enter your registered email address first.");
      return;
    }

    setLoading(true);

    try {
      const { error: resetError } =
        await supabase.auth.resetPasswordForEmail(cleanEmail, {
          redirectTo: window.location.origin,
        });

      if (resetError) {
        setError(resetError.message);
      } else {
        setNotice(
          "If an account exists for this email, a password reset link has been requested. Check your inbox and spam folder."
        );
      }
    } catch (err) {
      setError(err?.message || "Unable to request password reset.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-card">
      <div className="login-heading">
        <h2>Welcome Back!</h2>
        <p>Login to your SMART ERP account</p>
      </div>

      <RoleSelector selectedRole={selectedRole} onChange={onRoleChange} />
      {authError && <p className="register-message" role="alert">{authError}</p>}

      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label htmlFor="login-email">
            <span className="label-icon"><UserIcon /></span>
            Email Address
          </label>

          <div className="input-wrapper">
            <span className="input-icon"><UserIcon /></span>
            <input
              id="login-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="Enter your email address"
              autoComplete="username"
              required
            />
          </div>
        </div>

        <div className="form-group">
          <label htmlFor="login-password">
            <span className="label-icon"><LockIcon /></span>
            Password
          </label>

          <div className="input-wrapper">
            <span className="input-icon"><LockIcon /></span>
            <input
              id="login-password"
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Enter your password"
              autoComplete="current-password"
              required
            />

            <button
              type="button"
              className="password-toggle"
              onClick={() => setShowPassword((value) => !value)}
              aria-label={showPassword ? "Hide password" : "Show password"}
            >
              {showPassword ? <EyeOffIcon /> : <EyeIcon />}
            </button>
          </div>
        </div>

        {error && (
          <p className="register-message" role="alert">{error}</p>
        )}

        {notice && (
          <p className="register-message" role="status">{notice}</p>
        )}

        <div className="login-options">
          <span />
          <button
            type="button"
            className="forgot-button"
            onClick={handleForgotPassword}
            disabled={loading}
          >
            Forgot password?
          </button>
        </div>

        <button
          type="submit"
          className="login-submit"
          disabled={loading}
        >
          <span>{loading ? "Please wait..." : "Login"}</span>
          <span className="submit-arrow">→</span>
        </button>
      </form>

      <button
        type="button"
        className="google-login-button"
        onClick={handleGoogleLogin}
        disabled={loading}
        style={{
          width: "100%",
          minHeight: "48px",
          marginTop: "16px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: "12px",
          background: "#ffffff",
          color: "#202124",
          border: "1px solid #dadce0",
          borderRadius: "8px",
          fontSize: "15px",
          fontWeight: 600,
          cursor: loading ? "not-allowed" : "pointer",
          opacity: loading ? 0.7 : 1,
        }}
      >
        <GoogleIcon />
        <span>{loading ? "Connecting..." : "Continue with Google"}</span>
      </button>

      <div className="create-account-text">
        <span>Don't have an account?</span>
        <button type="button" onClick={onCreateAccount}>
          Create New Account
        </button>
      </div>
    </div>
  );
}

/* CREATE ACCOUNT */
function CreateAccount({ onSwitchToLogin, onAuthenticated, selectedRole, onRoleChange }) {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  const handleRegister = async (event) => {
    event.preventDefault();
    setError("");
    setMessage("");

    if (selectedRole !== ROLES.STAFF) {
      setError("Student accounts must be created from a valid Staff invitation. Admin accounts cannot be registered here.");
      return;
    }

    if (!supabase) {
      setError("Supabase configuration is missing. Check your frontend .env file.");
      return;
    }

    if (!fullName.trim() || !email.trim() || !password || !confirmPassword) {
      setError("Please fill in all fields.");
      return;
    }

    if (!email.trim().includes("@")) {
      setError("Please enter a valid email address.");
      return;
    }

    if (password.length < 8) {
      setError("Password must contain at least 8 characters.");
      return;
    }

    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setLoading(true);

    try {
      const { data, error: signUpError } =
        await supabase.auth.signUp({
          email: email.trim(),
          password,
          options: {
            data: { full_name: fullName.trim() },
            emailRedirectTo: `${window.location.origin}/?flow=staff-register`,
          },
        });

      if (signUpError) {
        const detail = signUpError.message || "Unable to create account.";
        setError(/database error saving new user|registration requires/i.test(detail)
          ? "This email is not approved for Staff registration. Ask an Admin to approve it first."
          : detail);
        return;
      }

      if (data.session) {
        const activated = await onAuthenticated?.(data.session, ROLES.STAFF);
        if (activated === false) {
          setError("The Staff account was created, but access could not be activated. Confirm the email and Staff approval, then sign in again.");
          return;
        }
        setMessage("Staff account created and verified.");
      } else {
        setMessage(
          "Staff account created. Confirm your email, then return to log in as Staff. Staff access activates after email verification."
        );
      }
    } catch (err) {
      setError(err?.message || "Unable to create account. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-card register-card">
      <div className="login-heading">
        <h2>Create New Account</h2>
        <p>Fill in your details to create an account</p>
      </div>

      <RoleSelector selectedRole={selectedRole} onChange={onRoleChange} registration />

      {selectedRole === ROLES.STUDENT ? (
        <div className="register-message" role="status">
          Student registration is invitation-only. Open the invitation email sent by your Staff member and use its secure link to set your password and activate your account.
        </div>
      ) : <form onSubmit={handleRegister}>
        <div className="form-group">
          <label htmlFor="register-name">
            <span className="label-icon"><UserIcon /></span>
            Full Name
          </label>
          <div className="input-wrapper">
            <span className="input-icon"><UserIcon /></span>
            <input
              id="register-name"
              type="text"
              value={fullName}
              onChange={(event) => setFullName(event.target.value)}
              placeholder="Enter your full name"
              autoComplete="name"
              required
            />
          </div>
        </div>

        <div className="form-group">
          <label htmlFor="register-email">
            <span className="label-icon"><MailIcon /></span>
            Email Address
          </label>
          <div className="input-wrapper">
            <span className="input-icon"><MailIcon /></span>
            <input
              id="register-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="Enter your email"
              autoComplete="email"
              required
            />
          </div>
        </div>

        <div className="form-group">
          <label htmlFor="register-password">
            <span className="label-icon"><LockIcon /></span>
            Password
          </label>
          <div className="input-wrapper">
            <span className="input-icon"><LockIcon /></span>
            <input
              id="register-password"
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Create a password"
              autoComplete="new-password"
              minLength={8}
              required
            />
            <button
              type="button"
              className="password-toggle"
              onClick={() => setShowPassword((value) => !value)}
              aria-label={showPassword ? "Hide password" : "Show password"}
            >
              {showPassword ? <EyeOffIcon /> : <EyeIcon />}
            </button>
          </div>
        </div>

        <div className="form-group">
          <label htmlFor="confirm-password">
            <span className="label-icon"><LockIcon /></span>
            Confirm Password
          </label>
          <div className="input-wrapper">
            <span className="input-icon"><LockIcon /></span>
            <input
              id="confirm-password"
              type={showConfirmPassword ? "text" : "password"}
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              placeholder="Re-enter your password"
              autoComplete="new-password"
              minLength={8}
              required
            />
            <button
              type="button"
              className="password-toggle"
              onClick={() => setShowConfirmPassword((value) => !value)}
              aria-label={
                showConfirmPassword ? "Hide password" : "Show password"
              }
            >
              {showConfirmPassword ? <EyeOffIcon /> : <EyeIcon />}
            </button>
          </div>
        </div>

        {error && (
          <p className="register-message" role="alert">{error}</p>
        )}

        {message && (
          <p className="register-message" role="status">{message}</p>
        )}

        <button
          type="submit"
          className="login-submit"
          disabled={loading}
        >
          <span>{loading ? "Creating account..." : "Register as Staff"}</span>
          <span className="submit-arrow">→</span>
        </button>
      </form>}

      <div className="create-account-text">
        <span>Already have an account?</span>
        <button type="button" onClick={onSwitchToLogin}>Login</button>
      </div>
    </div>
  );
}

/* SECURITY ILLUSTRATION */
function SecurityIllustration() {
  return (
    <svg viewBox="0 0 560 430" className="security-svg" aria-hidden="true">
      <defs>
        <linearGradient id="screenGradient" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#356CF2" />
          <stop offset="100%" stopColor="#4A2FE4" />
        </linearGradient>
        <linearGradient id="documentGradient" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#3D82F5" />
          <stop offset="100%" stopColor="#5A43E9" />
        </linearGradient>
      </defs>

      <path
        d="M110 100 C135 40 220 20 285 55 C350 90 405 50 455 105 C510 165 470 245 500 295 C530 350 455 385 375 365 C300 345 260 390 190 370 C115 350 75 285 90 220 C102 170 82 145 110 100Z"
        fill="#102F78"
        opacity="0.55"
      />

      <g transform="translate(80 125)">
        <path d="M0 15 Q0 0 15 0 H67 L98 30 V105 Q98 120 83 120 H15 Q0 120 0 105Z" fill="url(#documentGradient)" />
        <path d="M67 0 V30 H98" fill="#8DB5FF" />
        <rect x="20" y="52" width="54" height="6" rx="3" fill="#DCE8FF" />
        <rect x="20" y="68" width="45" height="6" rx="3" fill="#DCE8FF" opacity="0.75" />
        <rect x="20" y="84" width="50" height="6" rx="3" fill="#DCE8FF" opacity="0.75" />
      </g>

      <g transform="translate(255 65)">
        <rect width="92" height="115" rx="12" fill="url(#documentGradient)" />
        <path d="M18 25 H67 M18 40 H67 M18 55 H58" stroke="#E7EEFF" strokeWidth="6" strokeLinecap="round" />
        <circle cx="46" cy="82" r="17" fill="#FFFFFF" />
        <path d="M46 69 L50 78 L60 80 L53 87 L55 97 L46 92 L37 97 L39 87 L32 80 L42 78Z" fill="#4D5EEA" />
      </g>

      <g transform="translate(390 145)">
        <rect width="92" height="120" rx="13" fill="#593FE5" />
        <path d="M22 25 H70 M22 42 H70 M22 59 H59" stroke="#E9EDFF" strokeWidth="6" strokeLinecap="round" />
        <circle cx="47" cy="90" r="17" fill="#FFFFFF" opacity="0.9" />
        <path d="M39 90 L45 96 L57 82" fill="none" stroke="#4E49E9" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
      </g>

      <g transform="translate(115 205)">
        <path d="M30 0 H310 Q325 0 328 17 L345 190 H0 L17 17 Q19 0 30 0Z" fill="#D6E1FF" />
        <path d="M32 18 H304 L318 165 H18Z" fill="url(#screenGradient)" />
        <circle cx="168" cy="77" r="36" fill="#FFFFFF" />
        <circle cx="168" cy="68" r="13" fill="#5270E8" />
        <path d="M145 105 Q168 82 191 105 V116 H145Z" fill="#5270E8" />
        <rect x="112" y="133" width="112" height="8" rx="4" fill="#FFFFFF" opacity="0.8" />
        <rect x="132" y="148" width="72" height="7" rx="3" fill="#FFFFFF" opacity="0.55" />
        <path d="M0 190 H345 L382 210 Q386 218 373 222 H-28 Q-40 218 -35 210Z" fill="#AFC5F7" />
        <rect x="140" y="202" width="68" height="7" rx="3" fill="#6D87C9" />
      </g>

      <g transform="translate(325 275)">
        <path d="M0 15 L78 0 L156 15 V86 Q156 147 78 177 Q0 147 0 86Z" fill="#334DDB" />
        <path d="M14 25 L78 13 L142 25 V84 Q142 132 78 157 Q14 132 14 84Z" fill="#4E6DF0" />
        <rect x="48" y="77" width="60" height="52" rx="9" fill="#FFFFFF" />
        <path d="M62 77 V60 Q62 37 78 37 Q94 37 94 60 V77" fill="none" stroke="#FFFFFF" strokeWidth="12" strokeLinecap="round" />
        <circle cx="78" cy="101" r="7" fill="#3E5EE7" />
        <rect x="75" y="101" width="6" height="15" rx="3" fill="#3E5EE7" />
      </g>

      <g transform="translate(55 310)">
        <path d="M20 58 Q50 15 80 58 L73 115 H27Z" fill="#4D5DE5" />
        <path d="M45 60 C20 40 20 12 50 27 C55 42 51 52 45 60Z" fill="#4E86E9" />
        <path d="M55 57 C75 35 100 39 89 62 C77 73 65 68 55 57Z" fill="#5B91F2" />
        <path d="M50 58 C44 37 57 18 69 31 C70 48 61 56 50 58Z" fill="#386BD8" />
      </g>
    </svg>
  );
}

/* ICONS */
function UserIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true">
      <circle cx="12" cy="8" r="4" stroke="currentColor" strokeWidth="2" />
      <path d="M4 21 C4.5 16.8 7.2 14.5 12 14.5 C16.8 14.5 19.5 16.8 20 21" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true">
      <rect x="4" y="10" width="16" height="11" rx="2" stroke="currentColor" strokeWidth="2" />
      <path d="M8 10 V7 C8 4.8 9.8 3 12 3 C14.2 3 16 4.8 16 7 V10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <circle cx="12" cy="15" r="1.5" fill="currentColor" />
    </svg>
  );
}

function EyeIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true">
      <path d="M2 12 C4.5 7 8 5 12 5 C16 5 19.5 7 22 12 C19.5 17 16 19 12 19 C8 19 4.5 17 2 12Z" stroke="currentColor" strokeWidth="2" />
      <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true">
      <path d="M3 3L21 21" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M10.6 5.2 C11.05 5.07 11.51 5 12 5 C16 5 19.5 7 22 12 C21.2 13.6 20.2 15 19 16.1" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M6.1 6.1 C4.4 7.2 3 9 2 12 C4.5 17 8 19 12 19 C13.7 19 15.3 18.6 16.7 17.8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M9.5 9.5 C8.2 10.8 8.2 13 9.5 14.5 C10.8 15.8 13 15.8 14.5 14.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function GoogleIcon() {
  return (
    <svg viewBox="0 0 48 48" width="20" height="20" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5Z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.74 7.18l7.65 5.93c4.46-4.12 7.13-10.2 7.13-17.58Z" />
      <path fill="#FBBC05" d="M10.53 28.59A14.4 14.4 0 0 1 9.75 24c0-1.59.27-3.13.76-4.59l-7.98-6.19A23.9 23.9 0 0 0 0 24c0 3.87.93 7.52 2.56 10.78l7.97-6.19Z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.9-5.87l-7.65-5.93c-2.13 1.43-4.86 2.28-8.25 2.28-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48Z" />
    </svg>
  );
}

function MailIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true">
      <rect x="3" y="5" width="18" height="14" rx="2" stroke="currentColor" strokeWidth="2" />
      <path d="M4 7L12 13L20 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
