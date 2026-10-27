
import { useCallback, useEffect, useState } from "react";
import {
  LayoutDashboard,
  FilePlus2,
  Files,
  Users,
  Search,
  ChartColumn,
  Bot,
  Settings,
  LogOut,
  Box,
  GraduationCap,
  TreeDeciduous,
  Clock3,
  FileCheck,
  Plus,
  ArrowUpRight,
  RefreshCw,
  FileText,
  ShieldCheck,
  Menu,
} from "lucide-react";

import { supabase } from "../lib/supabase";
import { canAccessPage, ROLE_LABELS, normalizeRole } from "../auth/roles";
import AppGlobalHeader from "./AppGlobalHeader";
import "./Dashboard.css";


const menuItems = [
  { label: "Dashboard", icon: LayoutDashboard, page: "dashboard" },
  { label: "Upload Certificate", icon: FilePlus2, page: "certificateType" },
  { label: "Certificate Database", icon: Files, page: "extractedDataMenu" },
  { label: "My Certificates", icon: Files, page: "myCertificates" },
  { label: "Students", icon: Users, page: "students" },
  { label: "Search", icon: Search, page: "search" },
  { label: "Reports", icon: ChartColumn, page: "reports" },
  { label: "AI Assistant", icon: Bot, page: "aiAssistant" },
  { label: "User Management", icon: ShieldCheck, page: "userManagement" },
  { label: "Settings", icon: Settings, page: "settings" },
];

const formatDate = (value) => {
  if (!value) return "—";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return String(value);
  }

  return date.toLocaleDateString("en-GB");
};

const statusClass = (status) => {
  const value = String(status || "pending").toLowerCase();

  if (value.includes("verif") || value.includes("approved")) {
    return "verified";
  }

  if (value.includes("extract") || value.includes("process")) {
    return "extracted";
  }

  if (value.includes("reject") || value.includes("fail")) {
    return "rejected";
  }

  return "pending";
};

export default function Dashboard({
  user,
  userRole,
  onNavigate = () => {},
  onUpload = () => {},
  onLogout = () => {},
  onGlobalSearch = () => {},
  logoutLoading = false,
}) {
  const [certificates, setCertificates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState("");

  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const userId = user?.id;

  // Load certificate records from Supabase
  const loadCertificates = useCallback(async () => {
    setLoading(true);
    setErrorMessage("");

    try {
      if (!supabase || !userId) {
        throw new Error(
          "Supabase credentials missing. Check your frontend/.env file."
        );
      }

      let query = supabase
        .from("certificates")
        .select("*")
      if (userRole === "student") query = query.eq("student_user_id", userId);
      const { data, error } = await query.order("created_at", { ascending: false }).limit(500);

      if (error) throw error;

      setCertificates(data || []);
    } catch (error) {
      console.error("Supabase certificate loading error:", error);

      setErrorMessage(
        error.message || "Unable to load certificate records."
      );
    } finally {
      setLoading(false);
    }
  }, [userId, userRole]);

  useEffect(() => {
    // Initial dashboard load starts the Supabase request and its loading state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadCertificates();
  }, [loadCertificates]);

  const displayName =
    user?.user_metadata?.full_name ||
    user?.user_metadata?.name ||
    user?.email?.split("@")[0] ||
    "Student";

  const trustedRole = normalizeRole(userRole);
  const roleName = ROLE_LABELS[trustedRole] || "Unassigned";
  const visibleMenuItems = menuItems.filter(({ page }) => canAccessPage(trustedRole, page));

  const today = new Date();

  const todayKey = [
    today.getFullYear(),
    String(today.getMonth() + 1).padStart(2, "0"),
    String(today.getDate()).padStart(2, "0"),
  ].join("-");

  const todayUploads = certificates.filter((certificate) => {
    const value = certificate.created_at || certificate.date_extracted;

    if (!value) return false;

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) return false;

    const key = [
      date.getFullYear(),
      String(date.getMonth() + 1).padStart(2, "0"),
      String(date.getDate()).padStart(2, "0"),
    ].join("-");

    return key === todayKey;
  }).length;

  const uniqueStudents = new Set(
    certificates
      .map((certificate) => certificate.student_name?.trim())
      .filter(Boolean)
  ).size;

  const pendingCount = certificates.filter((certificate) => {
    const status = String(certificate.status || "").toLowerCase();

    return [
      "pending",
      "processing",
      "under review",
      "pending verification",
    ].includes(status);
  }).length;

  const stats = [
    {
      title: "Total Students",
      value: uniqueStudents,
      icon: GraduationCap,
      tone: "blue",
    },
    {
      title: "Total Certificates",
      value: certificates.length,
      icon: TreeDeciduous,
      tone: "green",
    },
    {
      title: "Pending Verification",
      value: pendingCount,
      icon: Clock3,
      tone: "orange",
    },
    {
      title: "Today's Uploads",
      value: todayUploads,
      icon: FileCheck,
      tone: "purple",
    },
  ];

  const navigateTo = (page) => {
    setMobileMenuOpen(false);

    if (page === "dashboard") return;

    if (page === "certificateType") {
      onUpload();
      return;
    }

    onNavigate(page);
  };

  return (
    <div className="erp-app">
      {mobileMenuOpen && (
        <button
          className="erp-mobile-overlay"
          type="button"
          aria-label="Close sidebar"
          onClick={() => setMobileMenuOpen(false)}
        />
      )}

      <aside
        className={`erp-sidebar ${mobileMenuOpen ? "is-open" : ""} ${sidebarCollapsed ? "is-collapsed" : ""}`}
      >
        <div className="erp-brand">
          <div className="erp-brand-mark">
            <Box size={25} />
          </div>
          <div className="erp-brand-copy"><span>SMART ERP</span><small>Certificate Data Extraction &amp; Management System</small></div>
        </div>

        <div className="erp-profile">
          <div className="erp-avatar">
            {displayName.slice(0, 2).toUpperCase()}
          </div>

          <div className="erp-profile-info">
            <strong>{displayName}</strong>
            <span>{roleName}</span>
          </div>
        </div>

        <nav className="erp-navigation">
          {visibleMenuItems.map(({ label, icon: Icon, page }) => (
            <button
              type="button"
              key={page}
              className={`erp-nav-item ${
                page === "dashboard" ? "active" : ""
              }`}
              onClick={() => navigateTo(page)}
            >
              <Icon size={19} />
              <span>{label}</span>
            </button>
          ))}
        </nav>

        <button
          type="button"
          className="erp-nav-item erp-logout"
          onClick={onLogout}
          disabled={logoutLoading}
        >
          <LogOut size={19} />
          <span>{logoutLoading ? "Signing out..." : "Logout"}</span>
        </button>
      </aside>

      <main className="erp-main">
        <header className="erp-topbar">
          <button type="button" className="erp-mobile-menu" aria-label={mobileMenuOpen ? "Close sidebar" : "Toggle sidebar"} onClick={() => {
            if (window.matchMedia("(max-width: 760px)").matches) setMobileMenuOpen((open) => !open);
            else setSidebarCollapsed((collapsed) => !collapsed);
          }}>
            <Menu size={22} />
          </button>

          <AppGlobalHeader user={user} userRole={userRole} onGlobalSearch={(query) => {
            setMobileMenuOpen(false);
            onGlobalSearch(query);
          }} onNavigate={navigateTo} onLogout={onLogout} logoutLoading={logoutLoading} />
        </header>

        <div className="erp-content">
          <div className="erp-page-heading">
            <div>
              <h1>Dashboard</h1>
              <p>
                Overview of your certificate data and system activity
              </p>
            </div>

            <button
              type="button"
              className="erp-refresh-button"
              onClick={loadCertificates}
              disabled={loading}
            >
              <RefreshCw
                size={16}
                className={loading ? "erp-spin" : ""}
              />
              Refresh
            </button>
          </div>

          {errorMessage && (
            <div className="erp-error-banner">
              <span>Supabase error: {errorMessage}</span>
              <button type="button" onClick={loadCertificates}>
                Retry
              </button>
            </div>
          )}

          <section className="erp-stats-grid">
            {stats.map(({ title, value, icon: Icon, tone }) => (
              <article className="erp-stat-card" key={title}>
                <div className={`erp-stat-icon ${tone}`}>
                  <Icon size={23} />
                </div>

                <div className="erp-stat-details">
                  <span>{title}</span>
                  <strong>
                    {loading ? "—" : value.toLocaleString()}
                  </strong>
                  <small>Live dashboard records</small>
                </div>
              </article>
            ))}
          </section>

          <section className="erp-dashboard-grid">
            <article className="erp-panel erp-recent-panel">
              <div className="erp-panel-heading">
                <h2>Recent Uploads</h2>

                <button
                  type="button"
                  onClick={() => navigateTo("myCertificates")}
                >
                  View All <ArrowUpRight size={15} />
                </button>
              </div>

              <div className="erp-table-wrap">
                <table className="erp-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Student Name</th>
                      <th>Certificate Type</th>
                      <th>Date</th>
                      <th>Status</th>
                    </tr>
                  </thead>

                  <tbody>
                    {loading ? (
                      <tr>
                        <td colSpan={5} className="erp-empty-state">
                          Loading Supabase records...
                        </td>
                      </tr>
                    ) : certificates.length === 0 ? (
                      <tr>
                        <td colSpan={5} className="erp-empty-state">
                          No certificate records found.
                        </td>
                      </tr>
                    ) : (
                      certificates.slice(0, 7).map((certificate, index) => (
                        <tr
                          key={
                            certificate.id ??
                            `${certificate.student_name}-${index}`
                          }
                        >
                          <td>{index + 1}</td>

                          <td className="erp-student-cell">
                            {certificate.student_name || "—"}
                          </td>

                          <td>
                            {certificate.certificate_type || "—"}
                          </td>

                          <td>
                            {formatDate(
                              certificate.date_extracted ||
                                certificate.created_at
                            )}
                          </td>

                          <td>
                            <span
                              className={`erp-status ${statusClass(
                                certificate.status
                              )}`}
                            >
                              {certificate.status || "Pending"}
                            </span>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </article>

            <div className="erp-right-column">
              <article className="erp-panel erp-quick-panel">
                <div className="erp-panel-heading">
                  <h2>Quick Actions</h2>
                </div>

                <div className="erp-quick-actions">
                  <button
                    type="button"
                    className="erp-action-primary"
                    onClick={onUpload}
                  >
                    <Plus size={19} />
                    Add Certificate
                  </button>

                  <button
                    type="button"
                    onClick={() => navigateTo("myCertificates")}
                  >
                    <Files size={17} />
                    View All Certificates
                  </button>

                  <button
                    type="button"
                    onClick={() => navigateTo("search")}
                  >
                    <Search size={17} />
                    Search Student
                  </button>

                  <button
                    type="button"
                    onClick={() => navigateTo("reports")}
                  >
                    <ChartColumn size={17} />
                    Generate Report
                  </button>
                </div>
              </article>

              <article className="erp-panel erp-documents-panel">
                <div className="erp-panel-heading">
                  <h2>Document Types Supported</h2>
                </div>

                <div className="erp-document-grid">
                  {[
                    ["10th/12th Marksheet", "blue", FileText],
                    ["TC Certificate", "red", Files],
                    ["Community Certificate", "purple", ShieldCheck],
                    ["Income Certificate", "green", FileCheck],
                    ["Aadhaar", "purple", Users],
                    ["Birth Certificate", "blue", GraduationCap],
                    ["Diploma", "purple", FileCheck],
                  ].map(([label, tone, Icon]) => (
                    <button
                      type="button"
                      className="erp-document-type"
                      key={label}
                      onClick={onUpload}
                    >
                      <span className={`erp-document-icon ${tone}`}>
                        <Icon size={21} />
                      </span>
                      <span>{label}</span>
                    </button>
                  ))}
                </div>
              </article>
            </div>
          </section>
        </div>

        <footer className="erp-footer">
          <GraduationCap size={21} />
          <div><strong>SMART ERP</strong><span>Certificate Data Extraction &amp; Management System</span><small>© 2026 SMART ERP. All rights reserved. &nbsp; Scan • Extract • Manage • Secure</small></div>
        </footer>
      </main>

    </div>
  );
}
