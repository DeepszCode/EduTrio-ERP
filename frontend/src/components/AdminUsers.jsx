import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, ChevronDown, ChevronUp, MailCheck, RefreshCw, ShieldCheck, UserPlus, Users } from "lucide-react";
import { supabase } from "../lib/supabase";
import { normalizeRole, ROLE_LABELS } from "../auth/roles";
import AppGlobalHeader from "./AppGlobalHeader";
import "./AdminUsers.css";

const API_BASE_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, "");

export default function AdminUsers({ user, userRole, onBack, onNavigate, onGlobalSearch, onLogout, logoutLoading = false }) {
  const [users, setUsers] = useState([]);
  const [approvals, setApprovals] = useState([]);
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [usersExpanded, setUsersExpanded] = useState(true);
  const [approvalsExpanded, setApprovalsExpanded] = useState(true);

  const request = useCallback(async (path, options = {}) => {
    if (!supabase) throw new Error("Supabase is not configured. Check the frontend environment.");
    let { data: { session }, error: sessionError } = await supabase.auth.getSession();
    if (sessionError || !session?.access_token) {
      const refreshed = await supabase.auth.refreshSession();
      session = refreshed.data?.session;
      if (refreshed.error || !session?.access_token) throw new Error("Your Admin session has expired. Sign in again.");
    }

    const send = async (accessToken) => {
      try {
        return await fetch(`${API_BASE_URL}${path}`, {
          ...options,
          headers: { "Content-Type": "application/json", ...(options.headers || {}), Authorization: `Bearer ${accessToken}` },
        });
      } catch (requestError) {
        if (requestError instanceof TypeError || /failed to fetch/i.test(requestError?.message || "")) {
          throw new Error(`Could not reach the configured SMART ERP backend (${API_BASE_URL}). Confirm FastAPI is running and that this address is reachable from your browser.`, { cause: requestError });
        }
        throw requestError;
      }
    };
    let response = await send(session.access_token);
    if (response.status === 401) {
      const refreshed = await supabase.auth.refreshSession();
      if (refreshed.error || !refreshed.data?.session?.access_token) {
        await supabase.auth.signOut();
        throw new Error("Your Admin session has expired. Sign in again.");
      }
      response = await send(refreshed.data.session.access_token);
    }
    const body = await response.json().catch(() => ({}));
    if (response.status === 401) {
      await supabase.auth.signOut();
      throw new Error("Your Admin session is no longer valid. Sign in again.");
    }
    if (!response.ok) throw new Error(body.detail || `Request failed (${response.status}).`);
    return body;
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadFailed(false);
    setError("");
    try {
      const [userData, approvalData] = await Promise.all([
        request("/api/admin/users"),
        request("/api/admin/staff-approvals"),
      ]);
      setUsers(userData.users || []);
      setApprovals(approvalData.approvals || []);
    } catch (loadError) {
      setLoadFailed(true);
      setError(
        loadError.message || "Could not load administration data."
      );
    } finally {
      setLoading(false);
    }
  }, [request]);

  useEffect(() => {
    if (normalizeRole(userRole) === "admin") {
      // Initial load sets the pending state while requesting remote admin data.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      load();
    }
  }, [load, userRole]);

  const changeRole = async (targetUserId, role) => {
    if (!window.confirm(`Change this user's role to ${role}?${role === "admin" ? " Admin assignment is restricted to the three approved accounts." : ""}`)) return;
    setBusy(targetUserId);
    setError("");
    setNotice("");
    try {
      await request(`/api/admin/users/${encodeURIComponent(targetUserId)}/role`, {
        method: "PATCH", body: JSON.stringify({ role }),
      });
      setNotice("User role updated.");
      await load();
    } catch (changeError) {
      setError(changeError.message || "Could not update this role.");
    } finally {
      setBusy("");
    }
  };

  const approveStaff = async (event) => {
    event.preventDefault();
    const normalizedEmail = email.trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail)) {
      setError("Enter a valid Staff email address.");
      return;
    }
    if (approvals.some((approval) => approval.enabled && approval.email?.toLowerCase() === normalizedEmail)) {
      setError("");
      setNotice("This Staff email is already approved.");
      return;
    }
    setBusy("approval");
    setError("");
    setNotice("");
    try {
      await request("/api/admin/staff-approvals", { method: "POST", body: JSON.stringify({ email: normalizedEmail }) });
      setEmail("");
      setNotice("Staff email approved for registration.");
      await load();
    } catch (approvalError) {
      setError(approvalError.message || "Could not approve this Staff email.");
    } finally {
      setBusy("");
    }
  };

  const revokeStaff = async (approvalEmail) => {
    if (!window.confirm(`Disable Staff registration approval for ${approvalEmail}?`)) return;
    setBusy(approvalEmail);
    setError("");
    setNotice("");
    try {
      await request(`/api/admin/staff-approvals/${encodeURIComponent(approvalEmail)}`, { method: "DELETE" });
      setNotice("Staff registration approval disabled.");
      await load();
    } catch (revokeError) {
      setError(revokeError.message || "Could not disable this Staff approval.");
    } finally {
      setBusy("");
    }
  };

  if (normalizeRole(userRole) !== "admin") {
    return <main className="admin-users-page"><section className="admin-users-card"><h1>Admin access required</h1><p>Your account does not have permission to manage user roles.</p><button type="button" onClick={onBack}>Back to Dashboard</button></section></main>;
  }

  return (
    <main className="admin-users-page">
      <header className="erp-topbar admin-users-appbar is-navy">
        <AppGlobalHeader user={user} userRole={userRole} onGlobalSearch={onGlobalSearch} onNavigate={onNavigate} onLogout={onLogout} logoutLoading={logoutLoading} showBrand showSearch={false} variant="navy" />
      </header>
      <div className="admin-users-wrap">
        <nav className="admin-users-breadcrumb" aria-label="Breadcrumb"><button type="button" onClick={onBack}>Dashboard</button><span>/</span><strong>User Management</strong></nav>
        <header className="admin-users-header">
          <button type="button" className="admin-users-back" onClick={onBack}><ArrowLeft size={16} /> Dashboard</button>
          <div className="admin-users-title"><span><ShieldCheck size={24} /></span><div><h1>User Management</h1><p>Manage user roles, staff registration approvals, and system access.</p></div></div>
          <button type="button" className="admin-users-refresh" onClick={load} disabled={loading}><RefreshCw size={16} /> Refresh</button>
        </header>

        <section className="admin-users-overview" aria-label="User management overview">
          <article className="admin-overview-card users-overview-card">
            <span className="admin-overview-icon"><Users size={19} /></span>
            <div><span>Assigned users</span><strong>{loading || loadFailed ? "—" : users.length}</strong><small>Accounts with an assigned SMART ERP role</small></div>
          </article>
          <article className="admin-overview-card approvals-overview-card">
            <span className="admin-overview-icon"><MailCheck size={19} /></span>
            <div><span>Active Staff approvals</span><strong>{loading || loadFailed ? "—" : approvals.filter((entry) => entry.enabled).length}</strong><small>Emails approved for Staff registration</small></div>
          </article>
          <article className="admin-overview-card roles-overview-card">
            <span className="admin-overview-icon"><ShieldCheck size={19} /></span>
            <div><span>Access roles</span><strong>{Object.keys(ROLE_LABELS).length}</strong><small>{Object.values(ROLE_LABELS).join(", ")}</small></div>
          </article>
        </section>

        {error && <div className="admin-users-message error" role="alert">{error}</div>}
        {notice && <div className="admin-users-message success" role="status">{notice}</div>}

        <section className="admin-users-card">
          <div className="admin-users-section-heading"><div><Users size={18} /><div><h2>Assigned users</h2><p>Admin assignment is restricted to the three approved accounts.</p></div></div><div className="admin-section-actions"><strong>{loading || loadFailed ? "—" : users.length}</strong><button type="button" className="admin-section-toggle" aria-label={usersExpanded ? "Collapse assigned users" : "Expand assigned users"} aria-expanded={usersExpanded} onClick={() => setUsersExpanded((expanded) => !expanded)}>{usersExpanded ? <ChevronUp size={17} /> : <ChevronDown size={17} />}</button></div></div>
          {usersExpanded && <div className="admin-users-table-wrap"><table className="admin-users-table"><thead><tr><th>User ID</th><th>Role</th><th>Created</th><th>Change role</th></tr></thead><tbody>
            {loading ? <tr><td colSpan="4" className="admin-users-empty">Loading assigned users…</td></tr> : loadFailed ? <tr><td colSpan="4" className="admin-users-empty">Could not load assigned users. Refresh to try again.</td></tr> : users.length ? users.map((entry) => <tr key={entry.user_id}><td><code>{entry.user_id}</code>{entry.user_id === user?.id && <small className="admin-you-label">You</small>}</td><td><span className={`admin-role-pill role-${entry.role}`}>{entry.role}</span></td><td>{entry.created_at ? new Date(entry.created_at).toLocaleDateString() : "—"}</td><td><select aria-label={`Change role for ${entry.user_id}`} value={entry.role} disabled={busy === entry.user_id || entry.user_id === user?.id} onChange={(event) => changeRole(entry.user_id, event.target.value)}><option value="admin">Admin</option><option value="staff">Staff</option><option value="student">Student</option></select></td></tr>) : <tr><td colSpan="4" className="admin-users-empty">No assigned users were found.</td></tr>}
          </tbody></table></div>}
        </section>

        <section className="admin-users-card">
          <div className="admin-users-section-heading"><div><UserPlus size={18} /><div><h2>Staff registration approvals</h2><p>Only approved email addresses can register as Staff.</p></div></div><div className="admin-section-actions"><strong>{loading || loadFailed ? "—" : approvals.filter((entry) => entry.enabled).length + " active"}</strong><button type="button" className="admin-section-toggle" aria-label={approvalsExpanded ? "Collapse Staff approvals" : "Expand Staff approvals"} aria-expanded={approvalsExpanded} onClick={() => setApprovalsExpanded((expanded) => !expanded)}>{approvalsExpanded ? <ChevronUp size={17} /> : <ChevronDown size={17} />}</button></div></div>
          {approvalsExpanded && <>
            <form className="admin-approval-form" onSubmit={approveStaff}><label htmlFor="staff-approval-email">Staff email address</label><div><input id="staff-approval-email" type="email" required maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)} placeholder="staff@example.com" /><button type="submit" disabled={loading || loadFailed || busy === "approval"}>{busy === "approval" ? "Saving…" : "Approve email"}</button></div></form>
            <div className="admin-approval-list">{loading ? <p>Loading approved email addresses…</p> : loadFailed ? <p>Could not load approved emails. Refresh to try again.</p> : approvals.length ? approvals.map((approval) => <div className="admin-approval-row" key={approval.email}><span>{approval.email}</span><span className={approval.enabled ? "approval-enabled" : "approval-disabled"}>{approval.enabled ? "Enabled" : "Disabled"}</span>{approval.enabled && <button type="button" disabled={busy === approval.email} onClick={() => revokeStaff(approval.email)}>{busy === approval.email ? "…" : "Disable"}</button>}</div>) : <p>No Staff emails are approved yet.</p>}</div>
          </>}
        </section>
      </div>
    </main>
  );
}
