import { useEffect, useRef, useState } from "react";
import { Bell, ChevronDown, GraduationCap, LogOut, Search, Settings, UserRound, X } from "lucide-react";
import { canAccessPage, normalizeRole, ROLE_LABELS } from "../auth/roles";
import "./Dashboard.css";

export default function AppGlobalHeader({
  user,
  userRole,
  onGlobalSearch,
  onNavigate,
  onLogout,
  logoutLoading = false,
  showBrand = false,
  showSearch = true,
  variant = "standard",
}) {
  const [query, setQuery] = useState("");
  const [profileOpen, setProfileOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [profileDialogOpen, setProfileDialogOpen] = useState(false);
  const profileRef = useRef(null);
  const notificationsRef = useRef(null);

  useEffect(() => {
    const handlePointerDown = (event) => {
      if (!profileRef.current?.contains(event.target)) setProfileOpen(false);
      if (!notificationsRef.current?.contains(event.target)) setNotificationsOpen(false);
    };
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        setProfileOpen(false);
        setNotificationsOpen(false);
        setProfileDialogOpen(false);
      }
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  const displayName = user?.user_metadata?.full_name
    || user?.user_metadata?.name
    || user?.email?.split("@")[0]
    || "User";
  const trustedRole = normalizeRole(userRole);
  const roleName = ROLE_LABELS[trustedRole] || "Unassigned";

  const submitSearch = (event) => {
    event.preventDefault();
    onGlobalSearch?.(query.trim());
  };

  return (
    <>
      {showBrand && <button type="button" className={`erp-global-brand ${variant === "navy" ? "is-navy" : ""}`} onClick={() => onNavigate?.("dashboard")} aria-label="SMART ERP dashboard">
        <span className="erp-global-brand-mark"><GraduationCap size={21} /></span>
        <span className="erp-global-brand-copy"><strong>SMART ERP</strong>{variant === "navy" && <small>Certificate Data Extraction &amp; Management System</small>}</span>
      </button>}
      {showSearch && <form className="erp-global-search" onSubmit={submitSearch}>
        <Search size={19} />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search certificates, students, or records..." aria-label="Search certificates and students" />
        {query && <button type="button" className="erp-clear-search" aria-label="Clear search" onClick={() => setQuery("")}><X size={15} /></button>}
      </form>}
      <div className="erp-topbar-right">
        <div className="erp-popover-anchor" ref={notificationsRef}>
          <button type="button" className="erp-icon-button" aria-label="Notifications" aria-expanded={notificationsOpen} onClick={() => { setNotificationsOpen((open) => !open); setProfileOpen(false); }}><Bell size={19} /></button>
          {notificationsOpen && <section className="erp-popover erp-notifications-popover" role="dialog" aria-label="Notifications"><strong>Notifications</strong><p>No notifications are available.</p></section>}
        </div>
        <div className="erp-popover-anchor" ref={profileRef}>
          <button type="button" className="erp-profile-trigger" aria-haspopup="menu" aria-expanded={profileOpen} onClick={() => { setProfileOpen((open) => !open); setNotificationsOpen(false); }}>
            <span className="erp-top-avatar">{displayName.slice(0, 2).toUpperCase()}</span>
            <span className="erp-top-user"><strong>{displayName}</strong><span>{roleName}</span></span><ChevronDown size={15} />
          </button>
          {profileOpen && <div className="erp-popover erp-profile-popover" role="menu">
            <button type="button" role="menuitem" onClick={() => { setProfileDialogOpen(true); setProfileOpen(false); }}><UserRound size={16} /> My Profile</button>
            {canAccessPage(trustedRole, "settings") && <button type="button" role="menuitem" onClick={() => { setProfileOpen(false); onNavigate?.("settings"); }}><Settings size={16} /> Settings</button>}
            <button type="button" role="menuitem" disabled={logoutLoading} onClick={onLogout}><LogOut size={16} /> {logoutLoading ? "Signing out..." : "Logout"}</button>
          </div>}
        </div>
      </div>
      {profileDialogOpen && <div className="erp-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setProfileDialogOpen(false); }}>
        <section className="erp-profile-dialog" role="dialog" aria-modal="true" aria-labelledby="erp-profile-title">
          <button className="erp-profile-dialog-close" type="button" aria-label="Close profile" onClick={() => setProfileDialogOpen(false)}><X size={19} /></button>
          <div className="erp-top-avatar">{displayName.slice(0, 2).toUpperCase()}</div>
          <h2 id="erp-profile-title">My Profile</h2>
          <dl><dt>Name</dt><dd>{displayName}</dd><dt>Email</dt><dd>{user?.email || "Not provided"}</dd><dt>Role</dt><dd>{roleName}</dd></dl>
        </section>
      </div>}
    </>
  );
}
