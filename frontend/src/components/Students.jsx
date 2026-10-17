
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Users,
  FileText,
  Search,
  ArrowLeft,
  GraduationCap,
  RefreshCw,
  CircleAlert,
  ShieldCheck,
} from "lucide-react";

import "./Students.css";

function parseOcrResult(value) {
  if (!value) return {};

  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return {};
    }
  }

  if (!value || typeof value !== "object") return {};

  const source =
    value.fields ||
    value.extracted_fields ||
    value.data ||
    value.extracted_data ||
    value;

  if (Array.isArray(source)) {
    return source.reduce((result, item) => {
      if (!item || typeof item !== "object") return result;

      const key = item.key || item.name || item.label;
      const value = item.value ?? item.text;

      if (key && value != null) {
        result[normalizeKey(key)] = String(value);
      }

      return result;
    }, {});
  }

  return Object.entries(source).reduce((result, [key, value]) => {
    if (value == null) return result;

    if (typeof value !== "object") {
      result[normalizeKey(key)] = String(value);
    } else {
      const nested = value.value ?? value.text;

      if (nested != null) {
        result[normalizeKey(key)] = String(nested);
      }
    }

    return result;
  }, {});
}

function normalizeKey(key) {
  return String(key)
    .trim()
    .toLowerCase()
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ");
}

function getField(fields, possibleKeys) {
  for (const key of possibleKeys) {
    const value = fields[normalizeKey(key)];

    if (value != null && String(value).trim()) {
      return String(value).trim();
    }
  }

  return "";
}

function getName(record) {
  const fields = parseOcrResult(
    record.ocr_result ??
    record.extracted_data ??
    record.extracted_fields
  );

  return (
    record.student_name ||
    record.holder_name ||
    record.candidate_name ||
    getField(fields, [
      "student_name",
      "student name",
      "candidate_name",
      "candidate name",
      "holder_name",
      "holder name",
      "name of student",
      "name",
    ]) ||
    "Unknown Student"
  );
}

function getRegisterNumber(record) {
  const fields = parseOcrResult(
    record.ocr_result ??
    record.extracted_data ??
    record.extracted_fields
  );

  return (
    record.register_number ||
    record.register_no ||
    record.registration_number ||
    record.roll_number ||
    record.admission_number ||
    getField(fields, [
      "register_number",
      "register number",
      "register_no",
      "register no",
      "registration_number",
      "registration number",
      "roll_number",
      "roll number",
      "roll_no",
      "admission_number",
      "admission number",
    ]) ||
    ""
  );
}

function mapStudents(records) {
  const students = new Map();

  records.forEach((record, index) => {
    const name = getName(record);
    const registerNumber = getRegisterNumber(record);

    // Prefer register number when available.
    // Without it, use the name to group certificates.
    const key = registerNumber
      ? `register:${registerNumber.toLowerCase().trim()}`
      : `name:${name.toLowerCase().trim()}`;

    if (!students.has(key)) {
      students.set(key, {
        id: key || `student-${index}`,
        name,
        registerNumber: registerNumber || "—",
        certificates: 0,
      });
    }

    const student = students.get(key);
    student.certificates += 1;

    if (
      student.name === "Unknown Student" &&
      name !== "Unknown Student"
    ) {
      student.name = name;
    }

    if (student.registerNumber === "—" && registerNumber) {
      student.registerNumber = registerNumber;
    }
  });

  return Array.from(students.values()).sort((a, b) =>
    a.name.localeCompare(b.name)
  );
}

export default function Students({ onBack, supabase, user }) {
  const [search, setSearch] = useState("");
  const [certificates, setCertificates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  const loadStudents = useCallback(async () => {
    setLoading(true);
    setErrorMessage("");

    try {
      if (!supabase) {
        throw new Error(
          "Supabase client is missing. Pass your configured Supabase client to the Students page."
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
        throw new Error("Please log in to view student records.");
      }

      // Select all existing columns. This avoids errors when optional
      // columns such as candidate_name or date_extracted do not exist.
      let query = supabase
        .from("certificates")
        .select("*")
        .order("created_at", { ascending: false });

      // Keep records scoped to the signed-in user.
      query = query.eq("user_id", currentUser.id);

      const { data, error } = await query;

      if (error) throw error;

      setCertificates(Array.isArray(data) ? data : []);
    } catch (error) {
      console.error("Unable to load student records:", error);
      setCertificates([]);
      setErrorMessage(
        error?.message || "Unable to load student records."
      );
    } finally {
      setLoading(false);
    }
  }, [supabase, user]);

  useEffect(() => {
    loadStudents();
  }, [loadStudents, refreshKey]);

  const studentRecords = useMemo(
    () => mapStudents(certificates),
    [certificates]
  );

  const filteredStudents = useMemo(() => {
    const term = search.trim().toLowerCase();

    if (!term) return studentRecords;

    return studentRecords.filter((student) =>
      student.name.toLowerCase().includes(term) ||
      student.registerNumber.toLowerCase().includes(term)
    );
  }, [studentRecords, search]);

  const refreshStudents = () => {
    setRefreshKey((value) => value + 1);
  };

  return (
    <div className="students-page">
      {/* Top header — matches My Certificates */}
      <header className="students-topbar">
        <div className="students-brand">
          <div className="students-brand-icon">
            <GraduationCap size={23} />
          </div>

          <div className="students-brand-copy">
            <strong>SMART ERP</strong>
            <span>Certificate Data Extraction &amp; Management System</span>
          </div>
        </div>

        <div className="students-topbar-actions">
          <button
            type="button"
            className="students-header-button"
            onClick={onBack}
          >
            <ArrowLeft size={15} />
            Back to Dashboard
          </button>

          <button
            type="button"
            className="students-header-button"
            onClick={refreshStudents}
            disabled={loading}
          >
            <RefreshCw
              size={15}
              className={loading ? "students-spinning" : ""}
            />
            Refresh
          </button>
        </div>
      </header>

      <main className="students-main">
        <div className="students-content">
          <section className="students-heading">
            <div>
              <span className="students-eyebrow">
                STUDENT MANAGEMENT
              </span>

              <h1>Students</h1>

              <p>
                Manage student records and certificate information.
              </p>
            </div>
          </section>

          {/* Statistics */}
          <section className="students-stats">
            <article className="students-stat-card">
              <div className="students-stat-icon students-blue">
                <Users size={22} />
              </div>

              <div className="students-stat-info">
                <p>Total Students</p>
                <h2>{loading ? "…" : studentRecords.length}</h2>
              </div>
            </article>

            <article className="students-stat-card">
              <div className="students-stat-icon students-green">
                <FileText size={22} />
              </div>

              <div className="students-stat-info">
                <p>Total Certificates</p>
                <h2>{loading ? "…" : certificates.length}</h2>
              </div>
            </article>

            <article className="students-stat-card">
              <div className="students-stat-icon students-purple">
                <GraduationCap size={23} />
              </div>

              <div className="students-stat-info">
                <p>Matching Students</p>
                <h2>{loading ? "…" : filteredStudents.length}</h2>
              </div>
            </article>
          </section>

          {/* Error message */}
          {errorMessage && (
            <div className="students-error-message" role="alert">
              <CircleAlert size={19} />

              <div className="students-error-copy">
                <strong>Unable to load student records</strong>
                <span>{errorMessage}</span>
              </div>

              <button
                type="button"
                onClick={refreshStudents}
                disabled={loading}
              >
                Try again
              </button>
            </div>
          )}

          {/* Records */}
          <section className="students-records-card">
            <div className="students-card-header">
              <div>
                <h2>Student Records</h2>
                <p>
                  View and search students in your saved certificates.
                </p>
              </div>

              <span className="students-count">
                {loading
                  ? "Loading..."
                  : `${filteredStudents.length} records`}
              </span>
            </div>

            <div className="students-toolbar">
              <label className="students-search-box">
                <Search size={18} />

                <input
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search student or register number..."
                  aria-label="Search student or register number"
                />
              </label>
            </div>

            <div className="students-table-wrapper">
              <table className="students-table">
                <thead>
                  <tr>
                    <th className="students-number-column">#</th>
                    <th>Student Name</th>
                    <th>Register Number</th>
                    <th>Certificates</th>
                  </tr>
                </thead>

                <tbody>
                  {loading ? (
                    <tr>
                      <td colSpan={4} className="students-empty">
                        <span className="students-loading-indicator" />
                        Loading student records...
                      </td>
                    </tr>
                  ) : errorMessage ? (
                    <tr>
                      <td colSpan={4} className="students-empty">
                        Student records could not be loaded.
                      </td>
                    </tr>
                  ) : filteredStudents.length > 0 ? (
                    filteredStudents.map((student, index) => (
                      <tr key={student.id}>
                        <td>{index + 1}</td>

                        <td>
                          <div className="students-name-cell">
                            <span className="students-avatar">
                              {student.name.charAt(0).toUpperCase()}
                            </span>

                            <strong>{student.name}</strong>
                          </div>
                        </td>

                        <td className="students-register-number">
                          {student.registerNumber}
                        </td>

                        <td>
                          <span className="students-certificate-count">
                            <FileText size={14} />
                            {student.certificates}
                          </span>
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={4} className="students-empty">
                        {studentRecords.length === 0
                          ? "No student records found. Save a certificate first."
                          : "No matching students. Try another search."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="students-table-footer">
              <span>
                Showing {loading ? "…" : filteredStudents.length} of{" "}
                {loading ? "…" : studentRecords.length} students
              </span>

              <span className="students-footer-caption">
                SMART ERP · Student Management
              </span>
            </div>
          </section>
        </div>
      </main>

      {/* Footer — matches My Certificates */}
      <footer className="students-footer">
        <div className="students-footer-brand">
          <GraduationCap size={16} />
          <strong>SMART ERP</strong>
          <span className="students-footer-divider">|</span>
          <span>Certificate Data Extraction &amp; Management System</span>
        </div>

        <div className="students-footer-links">
          <span>Scan</span>
          <i>·</i>
          <span>Extract</span>
          <i>·</i>
          <span>Manage</span>
          <i>·</i>
          <span>Secure</span>
        </div>
      </footer>
    </div>
  );
}
