
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Search as SearchIcon,
  ArrowLeft,
  FileText,
  Users,
  RefreshCw,
  SearchX,
  AlertCircle,
  ShieldCheck,
  Clock3,
  GraduationCap,
} from "lucide-react";

import "./Dashboard.css";
import "./Search.css";

function parseOcrResult(value) {
  if (!value) return {};

  if (typeof value === "object") return value;

  if (typeof value === "string") {
    try {
      return JSON.parse(value);
    } catch {
      return { extracted_text: value };
    }
  }

  return {};
}

function normalizeKey(value) {
  return String(value).toLowerCase().replace(/[\s-]+/g, "_");
}

function getOcrFields(ocrResult) {
  const source = parseOcrResult(ocrResult);

  const fields =
    source.fields ||
    source.extracted_fields ||
    source.extracted_data ||
    source.data ||
    source;

  if (Array.isArray(fields)) {
    return fields.reduce((result, item) => {
      if (!item || typeof item !== "object") return result;

      const key = item.key || item.name || item.label;
      const value = item.value ?? item.text;

      if (key && value != null) {
        result[normalizeKey(key)] = String(value);
      }

      return result;
    }, {});
  }

  if (!fields || typeof fields !== "object") return {};

  return Object.entries(fields).reduce((result, [key, value]) => {
    if (value == null) return result;

    if (typeof value !== "object") {
      result[normalizeKey(key)] = String(value);
    } else if (!Array.isArray(value)) {
      const nestedValue = value.value ?? value.text;

      if (nestedValue != null) {
        result[normalizeKey(key)] = String(nestedValue);
      }
    }

    return result;
  }, {});
}

function readValue(object, keys) {
  if (!object || typeof object !== "object") return "";

  for (const key of keys) {
    const value = object[key];

    if (value !== null && value !== undefined && String(value).trim()) {
      return String(value).trim();
    }
  }

  return "";
}

function findField(fields, keys) {
  for (const key of keys) {
    const value = fields[normalizeKey(key)];

    if (value !== undefined && String(value).trim()) {
      return String(value).trim();
    }
  }

  return "";
}

function formatDate(value) {
  if (!value) return "—";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) return String(value);

  return date.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function normalizeStatus(value) {
  const status = String(value || "Extracted").toLowerCase();

  if (status.includes("verified")) return "Verified";

  if (status.includes("pending") || status.includes("review")) {
    return "Pending";
  }

  return "Extracted";
}

function mapCertificate(record, index) {
  const ocrResult = parseOcrResult(
    record.ocr_result ??
      record.extracted_data ??
      record.extracted_fields ??
      record.ocr_data ??
      record.extracted_text
  );

  const fields = getOcrFields(ocrResult);

  const studentName =
    readValue(record, [
      "student_name",
      "candidate_name",
      "holder_name",
      "full_name",
      "name",
    ]) ||
    findField(fields, [
      "student_name",
      "candidate_name",
      "holder_name",
      "full_name",
      "name",
    ]) ||
    "Unknown Student";

  const registerNumber =
    readValue(record, [
      "register_number",
      "register_no",
      "registration_number",
      "registration_no",
      "roll_number",
      "roll_no",
      "admission_number",
    ]) ||
    findField(fields, [
      "register_number",
      "register_no",
      "registration_number",
      "registration_no",
      "roll_number",
      "roll_no",
      "admission_number",
    ]) ||
    "—";

  const certificateType =
    readValue(record, [
      "certificate_type",
      "document_type",
      "doc_type",
      "certificate_name",
      "document_name",
      "type",
    ]) ||
    readValue(ocrResult, [
      "certificate_type",
      "document_type",
      "doc_type",
    ]) ||
    "Certificate";

  const dateValue =
    record.created_at ||
    record.date_extracted ||
    record.uploaded_at ||
    record.issue_date;

  const searchableContent = [
    studentName,
    registerNumber,
    certificateType,
    record.status,
    JSON.stringify(ocrResult),
    JSON.stringify(fields),
  ]
    .join(" ")
    .toLowerCase();

  return {
    id: record.id ?? `${studentName}-${index}`,
    studentName,
    registerNumber,
    certificateType: certificateType.replace(/[_-]+/g, " "),
    date: formatDate(dateValue),
    status: normalizeStatus(record.status),
    searchableContent,
  };
}

export default function Search({ onBack, supabase, user }) {
  const [searchText, setSearchText] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  const loadCertificates = useCallback(async () => {
    setLoading(true);
    setErrorMessage("");

    try {
      if (!supabase) {
        throw new Error(
          "Supabase client is missing. Check how Search is connected in your app."
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
        throw new Error("Please log in to search your certificates.");
      }

      // Select all existing columns. This avoids errors caused by
      // optional column names that may differ in your Supabase table.
      const { data, error } = await supabase
        .from("certificates")
        .select("*")
        .order("created_at", { ascending: false });

      if (error) throw error;

      // RLS should be enabled on the certificates table.
      // This additional filter also limits records to the current user
      // when the table has a user_id column.
      const userRecords = (data || []).filter((record) => {
        if (
          Object.prototype.hasOwnProperty.call(record, "user_id") &&
          record.user_id != null
        ) {
          return record.user_id === currentUser.id;
        }

        return true;
      });

      setRecords(userRecords.map(mapCertificate));
    } catch (error) {
      console.error("Unable to load certificates:", error);
      setRecords([]);
      setErrorMessage(
        error.message || "Unable to load certificates. Please try again."
      );
    } finally {
      setLoading(false);
    }
  }, [supabase, user]);

  useEffect(() => {
    loadCertificates();
  }, [loadCertificates, refreshKey]);

  const filteredRecords = useMemo(() => {
    const query = submittedQuery.trim().toLowerCase();

    if (!query) return [];

    return records.filter((record) =>
      record.searchableContent.includes(query)
    );
  }, [records, submittedQuery]);

  const studentCount = useMemo(() => {
    const names = new Set(
      records
        .map((record) => record.studentName.trim().toLowerCase())
        .filter((name) => name && name !== "unknown student")
    );

    return names.size;
  }, [records]);

  const verifiedCount = records.filter(
    (record) => record.status === "Verified"
  ).length;

  const pendingCount = records.filter(
    (record) => record.status === "Pending"
  ).length;

  function handleSearch(event) {
    event.preventDefault();
    setSubmittedQuery(searchText.trim());
  }

  function handleRefresh() {
    setRefreshKey((previous) => previous + 1);
  }

  function clearSearch() {
    setSearchText("");
    setSubmittedQuery("");
  }

  return (
    <div className="dashboard-page search-page">
      <header className="search-topbar">
        <div className="search-brand">
          <div className="search-brand-icon">
            <GraduationCap size={24} />
          </div>

          <div>
            <strong>SMART ERP</strong>
            <span>Certificate Data Extraction &amp; Management System</span>
          </div>
        </div>

        <div className="search-topbar-actions">
          <button
            type="button"
            className="search-topbar-button"
            onClick={onBack}
          >
            <ArrowLeft size={16} />
            Back to Dashboard
          </button>

          <button
            type="button"
            className="search-topbar-button search-topbar-refresh"
            onClick={handleRefresh}
            disabled={loading}
          >
            <RefreshCw
              size={16}
              className={loading ? "search-spinning" : ""}
            />
            Refresh
          </button>
        </div>
      </header>

      <main className="search-main">
        <section className="search-content">
          <div className="search-page-heading">
            <span className="search-eyebrow">
              CERTIFICATE MANAGEMENT
            </span>

            <h1>Search Certificates</h1>

            <p>
              Find, search and review saved certificates and extracted
              student information.
            </p>

            <span className="search-header-badge">
              <span className="search-live-dot" />
              {loading ? "Loading records" : "Certificate records"}
            </span>
          </div>

          <section className="search-stats">
            <article className="search-stat-card">
              <div className="search-stat-icon search-stat-blue">
                <FileText size={22} />
              </div>

              <div>
                <p>Total Certificates</p>
                <h2>{loading ? "…" : records.length}</h2>
              </div>
            </article>

            <article className="search-stat-card">
              <div className="search-stat-icon search-stat-green">
                <ShieldCheck size={22} />
              </div>

              <div>
                <p>Verified</p>
                <h2>{loading ? "…" : verifiedCount}</h2>
              </div>
            </article>

            <article className="search-stat-card">
              <div className="search-stat-icon search-stat-orange">
                <Clock3 size={22} />
              </div>

              <div>
                <p>Pending Review</p>
                <h2>{loading ? "…" : pendingCount}</h2>
              </div>
            </article>
          </section>

          {errorMessage && (
            <div className="search-error-message" role="alert">
              <AlertCircle size={21} />

              <div className="search-error-content">
                <strong>Unable to load certificate records</strong>
                <p>{errorMessage}</p>
              </div>

              <button type="button" onClick={handleRefresh}>
                Try again
              </button>
            </div>
          )}

          <section className="search-results-panel">
            <div className="search-panel-header">
              <div>
                <h2>Certificate Records</h2>
                <p>
                  Search saved certificates by student name, certificate
                  type, register number or extracted OCR details.
                </p>
              </div>

              <span className="search-record-count">
                {loading ? "Loading..." : `${records.length} records`}
              </span>
            </div>

            <form className="search-form" onSubmit={handleSearch}>
              <div className="search-input-wrapper">
                <SearchIcon size={19} />

                <input
                  type="search"
                  value={searchText}
                  onChange={(event) => setSearchText(event.target.value)}
                  placeholder="Search student name, certificate or register number..."
                  aria-label="Search certificate records"
                />

                {searchText && (
                  <button
                    type="button"
                    className="search-clear-button"
                    onClick={clearSearch}
                    aria-label="Clear search"
                  >
                    ×
                  </button>
                )}
              </div>

              <button type="submit" className="search-submit-button">
                <SearchIcon size={17} />
                Search
              </button>
            </form>

            {submittedQuery && (
              <div className="search-query-row">
                <span>
                  Results for <strong>"{submittedQuery}"</strong>
                </span>

                <button type="button" onClick={clearSearch}>
                  Clear search
                </button>
              </div>
            )}

            <div className="search-table-wrapper">
              <table className="search-results-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Student Name</th>
                    <th>Certificate Type</th>
                    <th>Register Number</th>
                    <th>Date</th>
                    <th>Status</th>
                  </tr>
                </thead>

                <tbody>
                  {loading ? (
                    <tr>
                      <td colSpan={6} className="search-empty-cell">
                        <RefreshCw
                          size={22}
                          className="search-spinning"
                        />
                        <span>Loading certificate records...</span>
                      </td>
                    </tr>
                  ) : errorMessage ? (
                    <tr>
                      <td colSpan={6} className="search-empty-cell">
                        Certificate records could not be loaded.
                      </td>
                    </tr>
                  ) : !submittedQuery ? (
                    <tr>
                      <td colSpan={6} className="search-empty-cell">
                        <div className="search-empty-icon">
                          <SearchIcon size={25} />
                        </div>
                        <strong>Search your certificates</strong>
                        <span>
                          Enter a keyword above and click Search to view
                          matching records.
                        </span>
                      </td>
                    </tr>
                  ) : filteredRecords.length > 0 ? (
                    filteredRecords.map((record, index) => (
                      <tr key={record.id}>
                        <td>{index + 1}</td>

                        <td>
                          <div className="search-student-cell">
                            <span className="search-student-avatar">
                              {record.studentName
                                .charAt(0)
                                .toUpperCase()}
                            </span>
                            <strong>{record.studentName}</strong>
                          </div>
                        </td>

                        <td>
                          <span className="search-certificate-type">
                            <FileText size={15} />
                            {record.certificateType}
                          </span>
                        </td>

                        <td>{record.registerNumber}</td>
                        <td>{record.date}</td>

                        <td>
                          <span
                            className={`search-status search-status-${record.status
                              .toLowerCase()
                              .replace(/\s+/g, "-")}`}
                          >
                            {record.status}
                          </span>
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={6} className="search-empty-cell">
                        <div className="search-empty-icon">
                          <SearchX size={26} />
                        </div>
                        <strong>No matching certificates found</strong>
                        <span>
                          Try a different name, register number or
                          certificate type.
                        </span>
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="search-table-footer">
              <span>
                {loading
                  ? "Loading records..."
                  : submittedQuery
                    ? `Showing ${filteredRecords.length} matching records`
                    : `${records.length} certificates available to search`}
              </span>

              <span>SMART ERP · Certificate Management</span>
            </div>
          </section>

          {!loading && !errorMessage && !submittedQuery && (
            <section className="search-tips-section">
              <div className="search-tips-heading">
                <h2>What can you search for?</h2>
                <p>
                  Search across saved certificate information and extracted
                  OCR fields.
                </p>
              </div>

              <div className="search-tips-grid">
                <article className="search-tip-card">
                  <div className="search-tip-icon search-tip-blue">
                    <Users size={21} />
                  </div>

                  <div>
                    <h3>Student Name</h3>
                    <p>Find certificates using the student's name.</p>
                  </div>
                </article>

                <article className="search-tip-card">
                  <div className="search-tip-icon search-tip-green">
                    <FileText size={21} />
                  </div>

                  <div>
                    <h3>Certificate Type</h3>
                    <p>
                      Search marksheets, transfer certificates and other
                      document types.
                    </p>
                  </div>
                </article>

                <article className="search-tip-card">
                  <div className="search-tip-icon search-tip-purple">
                    <SearchIcon size={21} />
                  </div>

                  <div>
                    <h3>Extracted Details</h3>
                    <p>
                      Search register numbers and information stored in
                      OCR results.
                    </p>
                  </div>
                </article>
              </div>
            </section>
          )}
        </section>
      </main>

      <footer className="search-footer">
        <div className="search-footer-brand">
          <GraduationCap size={17} />
          <strong>SMART ERP</strong>
          <span className="search-footer-divider">|</span>
          <span>Certificate Data Extraction &amp; Management System</span>
        </div>

        <span>Scan · Extract · Manage · Secure</span>
      </footer>
    </div>
  );
}
