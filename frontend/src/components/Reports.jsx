
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  Users,
  FileText,
  Clock,
  Download,
  ChartNoAxesColumn,
  CheckCircle,
  FileSpreadsheet,
  RefreshCw,
  AlertCircle,
} from "lucide-react";

import "./Reports.css";

const TABLE_NAME = "certificates";

function getOcrFields(record) {
  const ocr = record?.ocr_result;

  if (!ocr || typeof ocr !== "object") return {};

  const fields =
    ocr.fields ||
    ocr.extracted_fields ||
    ocr.data ||
    {};

  if (Array.isArray(fields)) {
    return fields.reduce((result, item) => {
      if (!item || typeof item !== "object") return result;

      const key = item.key || item.name || item.label;
      const value = item.value ?? item.text;

      if (key && value != null) {
        result[String(key).toLowerCase().replace(/[\s-]+/g, "_")] =
          String(value);
      }

      return result;
    }, {});
  }

  if (typeof fields !== "object") return {};

  return Object.entries(fields).reduce((result, [key, value]) => {
    let finalValue = value;

    if (value && typeof value === "object") {
      finalValue = value.value ?? value.text ?? "";
    }

    if (
      finalValue !== null &&
      finalValue !== undefined &&
      typeof finalValue !== "object" &&
      String(finalValue).trim()
    ) {
      result[key.toLowerCase().replace(/[\s-]+/g, "_")] =
        String(finalValue);
    }

    return result;
  }, {});
}

function getStudentName(record) {
  const fields = getOcrFields(record);

  return (
    record?.student_name ||
    fields.student_name ||
    fields.candidate_name ||
    fields.holder_name ||
    fields.name ||
    "Unknown Student"
  );
}

function getCertificateType(record) {
  return (
    record?.certificate_type ||
    record?.ocr_result?.certificate_type ||
    record?.ocr_result?.document_type ||
    record?.ocr_result?.doc_type ||
    record?.certificate_name ||
    "Certificate"
  );
}

function getStatus(status) {
  const value = String(status || "Extracted").toLowerCase();

  if (value.includes("verified")) return "Verified";

  if (value.includes("pending") || value.includes("review")) {
    return "Pending";
  }

  return "Extracted";
}

function formatDate(value) {
  if (!value) return "—";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) return String(value);

  return date.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

function escapeCsv(value) {
  return `"${String(value ?? "").replace(/"/g, '""')}"`;
}

export default function Reports({ onBack, supabase, user }) {
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState("");
  const [reportGenerated, setReportGenerated] = useState(false);

  const loadReports = useCallback(async () => {
    setLoading(true);
    setErrorMessage("");
    setReportGenerated(false);

    try {
      if (!supabase) {
        throw new Error(
          "Supabase client is missing. Check your application setup."
        );
      }

      let currentUser = user;

      if (!currentUser?.id) {
        const {
          data: { user: authenticatedUser },
          error: authError,
        } = await supabase.auth.getUser();

        if (authError) throw authError;
        currentUser = authenticatedUser;
      }

      if (!currentUser?.id) {
        setRecords([]);
        throw new Error("Please log in to view your reports.");
      }

      const { data, error } = await supabase
        .from(TABLE_NAME)
        .select(
          "id, user_id, student_name, certificate_type, certificate_name, date_extracted, status, created_at, ocr_result"
        )
        .eq("user_id", currentUser.id)
        .order("created_at", { ascending: false });

      if (error) throw error;

      setRecords(data || []);
    } catch (error) {
      console.error("Reports: failed to load Supabase records:", error);
      setRecords([]);
      setErrorMessage(
        error.message || "Unable to load reports from Supabase."
      );
    } finally {
      setLoading(false);
    }
  }, [supabase, user?.id]);

  useEffect(() => {
    loadReports();
  }, [loadReports]);

  const totalStudents = useMemo(() => {
    const names = new Set();

    records.forEach((record) => {
      const name = getStudentName(record).trim().toLowerCase();

      if (name && name !== "unknown student") {
        names.add(name);
      }
    });

    return names.size;
  }, [records]);

  const verifiedCount = records.filter(
    (record) => getStatus(record.status) === "Verified"
  ).length;

  const pendingCount = records.filter(
    (record) => getStatus(record.status) === "Pending"
  ).length;

  const extractedCount = records.filter(
    (record) => getStatus(record.status) === "Extracted"
  ).length;

  const statusItems = [
    { label: "Extracted", count: extractedCount, color: "green" },
    { label: "Verified", count: verifiedCount, color: "blue" },
    { label: "Pending", count: pendingCount, color: "orange" },
  ];

  const generateReport = () => {
    if (!records.length) {
      setErrorMessage("There are no certificate records to export.");
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
      getStudentName(record),
      getCertificateType(record),
      record.certificate_name || "",
      formatDate(record.date_extracted || record.created_at),
      getStatus(record.status),
    ]);

    const csvContent = [headers, ...rows]
      .map((row) => row.map(escapeCsv).join(","))
      .join("\r\n");

    const blob = new Blob(["\uFEFF" + csvContent], {
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
    setReportGenerated(true);
  };

  return (
    <div className="reports-page">
      {/* FULL-WIDTH SMART ERP HEADER */}
      <header className="reports-top-header">
        <div className="reports-brand">
          <div className="reports-brand-logo">
            <FileText size={23} />
          </div>

          <div className="reports-brand-text">
            <h2>SMART ERP</h2>
            <span>Certificate Data Management</span>
          </div>
        </div>

        <div className="reports-header-label">
          REPORTS &amp; ANALYTICS
        </div>
      </header>

      <main className="reports-main">
        <div className="reports-content">
          <button
            type="button"
            onClick={onBack}
            className="reports-back-button"
          >
            <ArrowLeft size={17} />
            <span>Back to Dashboard</span>
          </button>

          <header className="reports-page-header">
            <div className="reports-page-title">
              <span className="reports-eyebrow">
                ANALYTICS &amp; INSIGHTS
              </span>

              <h1>Reports</h1>

              <p>
                View live certificate statistics and download your reports.
              </p>
            </div>

            <button
              type="button"
              onClick={loadReports}
              disabled={loading}
              className="reports-refresh-button"
            >
              <RefreshCw
                size={16}
                className={loading ? "reports-spinning" : ""}
              />
              {loading ? "Refreshing..." : "Refresh"}
            </button>
          </header>

          {errorMessage && (
            <div className="reports-error" role="alert">
              <AlertCircle size={19} />
              <span>{errorMessage}</span>

              <button type="button" onClick={loadReports}>
                Try again
              </button>
            </div>
          )}

          <section className="reports-stats">
            <article className="reports-stat-card">
              <div className="reports-stat-icon reports-blue">
                <Users size={23} />
              </div>

              <div className="reports-stat-info">
                <p>Total Students</p>
                <h2>{loading ? "…" : totalStudents}</h2>
                <span>Unique student names in records</span>
              </div>
            </article>

            <article className="reports-stat-card">
              <div className="reports-stat-icon reports-green">
                <FileText size={23} />
              </div>

              <div className="reports-stat-info">
                <p>Total Certificates</p>
                <h2>{loading ? "…" : records.length}</h2>
                <span>Saved certificate records</span>
              </div>
            </article>

            <article className="reports-stat-card">
              <div className="reports-stat-icon reports-orange">
                <Clock size={23} />
              </div>

              <div className="reports-stat-info">
                <p>Pending Review</p>
                <h2>{loading ? "…" : pendingCount}</h2>
                <span>Awaiting review</span>
              </div>
            </article>
          </section>

          <section className="reports-overview-card">
            <div className="reports-section-heading">
              <div className="reports-heading-icon">
                <ChartNoAxesColumn size={22} />
              </div>

              <div>
                <h2>Certificate Overview</h2>
                <p>Current certificate status distribution.</p>
              </div>
            </div>

            <div className="reports-status-list">
              {statusItems.map((item) => {
                const percentage = records.length
                  ? (item.count / records.length) * 100
                  : 0;

                return (
                  <div
                    className="reports-status-item"
                    key={item.label}
                  >
                    <div className="reports-status-label">
                      <span
                        className={`reports-dot reports-dot-${item.color}`}
                      />
                      <span>{item.label}</span>
                      <strong>{loading ? "…" : item.count}</strong>
                    </div>

                    <div className="reports-progress-track">
                      <div
                        className={`reports-progress reports-progress-${item.color}`}
                        style={{ width: `${percentage}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="reports-table-card">
            <div className="reports-table-header">
              <div>
                <h2>Certificate Report</h2>
                <p>
                  All saved certificate records included in your report.
                </p>
              </div>

              <button
                type="button"
                onClick={generateReport}
                className="reports-export-button"
                disabled={loading || records.length === 0}
              >
                <Download size={17} />
                Generate CSV
              </button>
            </div>

            {reportGenerated && (
              <div className="reports-success-message">
                <CheckCircle size={17} />
                Your certificate report has been downloaded.
              </div>
            )}

            <div className="reports-table-wrapper">
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
                    <tr>
                      <td
                        colSpan={5}
                        className="reports-table-message"
                      >
                        Loading certificate records from Supabase...
                      </td>
                    </tr>
                  ) : records.length > 0 ? (
                    records.map((record, index) => {
                      const status = getStatus(record.status);

                      return (
                        <tr
                          key={
                            record.id ??
                            `${index}-${getCertificateType(record)}`
                          }
                        >
                          <td>{index + 1}</td>

                          <td>
                            <strong>{getStudentName(record)}</strong>
                          </td>

                          <td>{getCertificateType(record)}</td>

                          <td>
                            {formatDate(
                              record.date_extracted || record.created_at
                            )}
                          </td>

                          <td>
                            <span
                              className={`reports-status-badge reports-badge-${status.toLowerCase()}`}
                            >
                              {status}
                            </span>
                          </td>
                        </tr>
                      );
                    })
                  ) : (
                    <tr>
                      <td
                        colSpan={5}
                        className="reports-table-message"
                      >
                        No certificate records found for this account.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            <footer className="reports-table-footer">
              <FileSpreadsheet size={16} />

              <span>
                {loading
                  ? "Loading records..."
                  : `${records.length} saved records available for export`}
              </span>

              <span className="reports-footer-brand">
                SMART ERP · Reports
              </span>
            </footer>
          </section>
        </div>
      </main>
    </div>
  );
}
