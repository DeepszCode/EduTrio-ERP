import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Users,
  ArrowLeft,
  FileText,
  RefreshCw,
  Download,
  Clock,
  CheckCircle,
  AlertCircle,
  PieChart,
  Bell,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  LogOut,
  Search,
  FileBadge,
  X,
} from "lucide-react";
import "./Reports.css";
import { canAccessPage } from "../auth/roles";

const TABLE_NAME = "certificates";

function readFields(record) {
  const ocr = record?.ocr_result;
  let fields = ocr?.fields || ocr?.extracted_fields || ocr?.data || {};

  if (Array.isArray(fields)) {
    return Object.fromEntries(
      fields
        .filter((item) => item && typeof item === "object")
        .map((item) => [
          String(item.key || item.name || item.label || "").toLowerCase().replace(/[\s-]+/g, "_"),
          item.value ?? item.text ?? "",
        ])
    );
  }

  return fields && typeof fields === "object" ? fields : {};
}

function studentName(record) {
  const fields = readFields(record);
  return String(
    record?.student_name ||
    fields.student_name ||
    fields.candidate_name ||
    fields.holder_name ||
    fields.name ||
    "Unknown Student"
  );
}

function certificateType(record) {
  return String(
    record?.certificate_type ||
    record?.ocr_result?.certificate_type ||
    record?.ocr_result?.document_type ||
    record?.ocr_result?.doc_type ||
    record?.certificate_name ||
    "Certificate"
  );
}

function recordStatus(status) {
  const value = String(status || "Extracted").toLowerCase();
  if (value.includes("verif")) return "Verified";
  if (value.includes("pending") || value.includes("review")) return "Pending";
  return "Extracted";
}

function displayDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleDateString("en-GB");
}

function csvEscape(value) {
  return `"${String(value ?? "").replace(/"/g, '""')}"`;
}

function DonutChart({ items, total, title }) {
  const colors = ["#3478f6", "#16bd91", "#a855f7", "#f59e0b", "#ec4899", "#06b6d4"];
  let accumulated = 0;

  const segments = items
    .filter((item) => item.count > 0)
    .map((item) => {
      const start = accumulated;
      accumulated += (item.count / Math.max(total, 1)) * 100;
      return `${item.color || colors[items.indexOf(item) % colors.length]} ${start}% ${accumulated}%`;
    });

  const background = total
    ? `conic-gradient(${segments.join(", ")})`
    : "#e9eff8";

  return (
    <div
      className="report-donut"
      style={{ background }}
      role="img"
      aria-label={`${title}: ${total} records`}
    >
      <div className="report-donut-hole">
        <strong>{total}</strong>
        <span>Total</span>
      </div>
    </div>
  );
}

export default function Reports({ onBack, onNavigate, onGlobalSearch, onLogout, logoutLoading = false, supabase, user, userRole = "student" }) {
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState("");
  const [exportMessage, setExportMessage] = useState("");
  const [page, setPage] = useState(1);
  const [lastRefreshed, setLastRefreshed] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const pageSize = 8;

  const loadReports = useCallback(async () => {
    setLoading(true);
    setErrorMessage("");

    try {
      if (!supabase) {
        throw new Error("Supabase is not configured. Check your frontend .env file.");
      }

      let activeUser = user;

      if (!activeUser?.id) {
        const { data, error } = await supabase.auth.getUser();
        if (error) throw error;
        activeUser = data?.user;
      }

      if (!activeUser?.id) {
        setRecords([]);
        throw new Error("Please log in to view your reports.");
      }

      let query = supabase
        .from(TABLE_NAME)
        .select("id,user_id,student_name,certificate_type,certificate_name,date_extracted,status,created_at,ocr_result");
      if (userRole === "student") query = query.eq("student_user_id", activeUser.id);
      const { data, error } = await query.order("created_at", { ascending: false });

      if (error) throw error;
      setRecords(data || []);
      setPage(1);
      setLastRefreshed(new Date());
    } catch (error) {
      console.error("Unable to load reports:", error);
      setRecords([]);
      setErrorMessage(error?.message || "Unable to load certificate reports.");
    } finally {
      setLoading(false);
    }
  }, [supabase, user, userRole]);

  useEffect(() => {
    // This effect starts the initial remote data load and its loading state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadReports();
  }, [loadReports]);

  const uniqueStudents = useMemo(() => {
    const names = new Set();
    records.forEach((record) => {
      const name = studentName(record).trim().toLowerCase();
      if (name && name !== "unknown student") names.add(name);
    });
    return names.size;
  }, [records]);

  const statusItems = useMemo(() => {
    const count = (status) =>
      records.filter((record) => recordStatus(record.status) === status).length;

    return [
      { label: "Extracted", count: count("Extracted"), color: "#16b981" },
      { label: "Verified", count: count("Verified"), color: "#3478f6" },
      { label: "Pending", count: count("Pending"), color: "#f59e0b" },
    ];
  }, [records]);

  const typeItems = useMemo(() => {
    const counts = new Map();
    records.forEach((record) => {
      const type = certificateType(record);
      counts.set(type, (counts.get(type) || 0) + 1);
    });

    const colors = ["#3478f6", "#16bd91", "#a855f7", "#f59e0b", "#ec4899", "#06b6d4"];

    return Array.from(counts, ([label, count], index) => ({
      label,
      count,
      color: colors[index % colors.length],
    }));
  }, [records]);

  const totalPages = Math.max(1, Math.ceil(records.length / pageSize));
  const visibleRecords = records.slice((page - 1) * pageSize, page * pageSize);

  const generateCsv = () => {
    if (!records.length) {
      setExportMessage("No records are available to export.");
      return;
    }

    const headers = [
      "Student Name",
      "Certificate Type",
      "Certificate Name",
      "Date",
      "Status",
    ];

    const rows = records.map((record) => [
      studentName(record),
      certificateType(record),
      record.certificate_name || "",
      displayDate(record.date_extracted || record.created_at),
      recordStatus(record.status),
    ]);

    const csv = [headers, ...rows]
      .map((row) => row.map(csvEscape).join(","))
      .join("\r\n");

    const blob = new Blob(["\uFEFF" + csv], {
      type: "text/csv;charset=utf-8;",
    });

    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "SMART-ERP-Certificate-Report.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);

    setExportMessage("Certificate report downloaded successfully.");
  };

  const userLabel =
    user?.user_metadata?.full_name ||
    user?.user_metadata?.name ||
    user?.email?.split("@")[0] ||
    "User";

  return (
    <div className="reports-app">
      <div className="reports-workspace">
        <header className="reports-appbar">
          <button className="reports-brand" type="button" onClick={onBack} aria-label="SMART ERP dashboard">
            <span className="reports-brand-mark"><FileBadge size={21} /></span>
            <span><strong>SMART ERP</strong><small>Certificate Data Management</small></span>
          </button>

          <form className="reports-search" onSubmit={(event) => { event.preventDefault(); onGlobalSearch?.(searchQuery.trim()); }}>
            <Search size={16} />
            <input value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="Search students, certificates, or reports..." aria-label="Search students and certificates" />
            {searchQuery && <button type="button" aria-label="Clear search" onClick={() => setSearchQuery("")}><X size={14} /></button>}
          </form>

          <div className="reports-profile">
            <div className="reports-menu-anchor">
              <button type="button" className="reports-notification" aria-label="Notifications" aria-expanded={notificationsOpen} onClick={() => { setNotificationsOpen((open) => !open); setProfileOpen(false); }}>
                <Bell size={18} /><span />
              </button>
              {notificationsOpen && <div className="reports-popover" role="status"><strong>Notifications</strong><p>No notifications are available.</p></div>}
            </div>
            <div className="reports-menu-anchor">
              <button type="button" className="reports-profile-trigger" aria-haspopup="menu" aria-expanded={profileOpen} onClick={() => { setProfileOpen((open) => !open); setNotificationsOpen(false); }}>
                <span className="reports-avatar">{userLabel.slice(0, 2).toUpperCase()}</span>
                <span className="reports-profile-name">{userLabel}</span>
                <ChevronDown size={14} />
              </button>
              {profileOpen && <div className="reports-popover reports-profile-popover" role="menu">
                {canAccessPage(userRole, "settings") && <button type="button" role="menuitem" onClick={() => { setProfileOpen(false); onNavigate?.("settings"); }}>Settings</button>}
                <button type="button" role="menuitem" disabled={logoutLoading} onClick={onLogout}><LogOut size={15} />{logoutLoading ? "Signing out..." : "Sign out"}</button>
              </div>}
            </div>
          </div>
        </header>

        <main className="reports-main">
          <nav className="reports-breadcrumb" aria-label="Breadcrumb"><button type="button" onClick={onBack}>Dashboard</button><span>/</span><strong>Reports &amp; Analytics</strong></nav>
          <div className="reports-heading-row">
            <div>
              <button type="button" className="reports-back" onClick={onBack}><ArrowLeft size={15} /> Back to Dashboard</button>
              <span className="reports-eyebrow">ANALYTICS &amp; INSIGHTS</span>
              <h1>Reports &amp; Analytics</h1>
              <p>Monitor certificate records, student statistics, and system performance.</p>
            </div>

            <div className="reports-refresh-area">
              <button
                className="reports-primary-button"
                onClick={loadReports}
                disabled={loading}
              >
                <RefreshCw size={16} className={loading ? "reports-spin" : ""} />
                {loading ? "Refreshing..." : "Refresh"}
              </button>
              <span className="reports-live-indicator">
                <i />
                {loading ? "Updating records..." : lastRefreshed ? `Updated ${lastRefreshed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "Live Supabase records"}
              </span>
            </div>
          </div>

          {errorMessage && (
            <div className="reports-alert" role="alert">
              <AlertCircle size={19} />
              <span>{errorMessage}</span>
              <button onClick={loadReports}>Try again</button>
            </div>
          )}

          {exportMessage && (
            <div className="reports-export-notice" role="status">
              <CheckCircle size={17} />
              {exportMessage}
              <button onClick={() => setExportMessage("")} aria-label="Dismiss">
                ×
              </button>
            </div>
          )}

          <section className="reports-stat-grid">
            <article className="reports-stat-card students-card">
              <div className="reports-stat-icon"><Users size={24} /></div>
              <div className="reports-stat-copy">
                <span>Total Students</span>
                <strong>{loading ? "…" : uniqueStudents}</strong>
                <small>Unique student names in records</small>
              </div>
            </article>

            <article className="reports-stat-card certificates-card">
              <div className="reports-stat-icon"><FileText size={24} /></div>
              <div className="reports-stat-copy">
                <span>Total Certificates</span>
                <strong>{loading ? "…" : records.length}</strong>
                <small>Saved certificate records</small>
              </div>
            </article>

            <article className="reports-stat-card pending-card">
              <div className="reports-stat-icon"><Clock size={24} /></div>
              <div className="reports-stat-copy">
                <span>Pending Review</span>
                <strong>{loading ? "…" : statusItems[2].count}</strong>
                <small>Awaiting review</small>
              </div>
            </article>
          </section>

          <section className="reports-chart-grid">
            <article className="reports-panel reports-chart-panel">
              <div className="reports-panel-heading">
                <div className="reports-panel-icon"><PieChart size={20} /></div>
                <div>
                  <h2>Certificate Distribution</h2>
                  <p>Breakdown of certificates by type.</p>
                </div>
              </div>

              {loading ? (
                <div className="reports-chart-loading">Loading chart data...</div>
              ) : (
                <div className="reports-chart-body">
                  <DonutChart
                    items={typeItems}
                    total={records.length}
                    title="Certificate distribution"
                  />
                  <div className="reports-legend">
                    {typeItems.length ? typeItems.map((item) => (
                      <div className="reports-legend-row" key={item.label}>
                        <span className="reports-legend-name">
                          <i style={{ background: item.color }} />
                          <span>{item.label}</span>
                        </span>
                        <strong>{item.count}</strong>
                        <span className="reports-legend-percent">
                          {records.length ? ((item.count / records.length) * 100).toFixed(1) : "0.0"}%
                        </span>
                      </div>
                    )) : <p className="reports-empty-legend">No certificate data yet.</p>}
                  </div>
                </div>
              )}
            </article>

            <article className="reports-panel reports-chart-panel">
              <div className="reports-panel-heading">
                <div className="reports-panel-icon"><PieChart size={20} /></div>
                <div>
                  <h2>Student Certificate Overview</h2>
                  <p>Certificate status distribution.</p>
                </div>
                {!loading && records.length > 0 && statusItems[2].count === 0 && (
                  <span className="reports-all-extracted">
                    <CheckCircle size={15} /> All records reviewed
                  </span>
                )}
              </div>

              {loading ? (
                <div className="reports-chart-loading">Loading chart data...</div>
              ) : (
                <div className="reports-chart-body">
                  <DonutChart
                    items={statusItems}
                    total={records.length}
                    title="Certificate status overview"
                  />
                  <div className="reports-legend reports-status-legend">
                    {statusItems.map((item) => (
                      <div className="reports-legend-row" key={item.label}>
                        <span className="reports-legend-name">
                          <i style={{ background: item.color }} />
                          <span>{item.label}</span>
                        </span>
                        <strong>{item.count}</strong>
                        <span className="reports-legend-percent">
                          {records.length ? ((item.count / records.length) * 100).toFixed(1) : "0.0"}%
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </article>
          </section>

          <section className="reports-panel reports-records-panel">
            <div className="reports-records-heading">
              <div className="reports-panel-heading">
                <div className="reports-panel-icon"><FileText size={20} /></div>
                <div>
                  <h2>Recent Certificate Records</h2>
                  <p>Latest certificate records from your system.</p>
                </div>
              </div>

              <button
                className="reports-primary-button"
                onClick={generateCsv}
                disabled={loading || records.length === 0}
              >
                <Download size={16} />
                Generate CSV
              </button>
            </div>

            <div className="reports-table-scroll">
              <table className="reports-table">
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
                    <tr><td colSpan={5} className="reports-table-message">Loading records from Supabase...</td></tr>
                  ) : visibleRecords.length ? (
                    visibleRecords.map((record, index) => {
                      const status = recordStatus(record.status);
                      return (
                        <tr key={record.id ?? `${page}-${index}`}>
                          <td>{(page - 1) * pageSize + index + 1}</td>
                          <td><strong>{studentName(record)}</strong></td>
                          <td>{certificateType(record)}</td>
                          <td>{displayDate(record.date_extracted || record.created_at)}</td>
                          <td><span className={`reports-status-pill reports-pill-${status.toLowerCase()}`}>{status}</span></td>
                        </tr>
                      );
                    })
                  ) : (
                    <tr><td colSpan={5} className="reports-table-message">No certificate records found for this account.</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            <footer className="reports-table-footer">
              <span><FileText size={15} /> {loading ? "Loading records..." : `${records.length} records available`}</span>
              <div className="reports-pagination">
                <span>
                  Showing {records.length ? (page - 1) * pageSize + 1 : 0}–{Math.min(page * pageSize, records.length)} of {records.length}
                </span>
                <button
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                  disabled={page <= 1}
                  aria-label="Previous page"
                >
                  <ChevronLeft size={16} />
                </button>
                <button className="reports-current-page" aria-current="page">{page}</button>
                <button
                  onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                  disabled={page >= totalPages}
                  aria-label="Next page"
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </footer>
          </section>

        </main>
      </div>
    </div>
  );
}
