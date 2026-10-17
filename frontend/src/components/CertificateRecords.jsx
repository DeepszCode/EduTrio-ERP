
import { useEffect, useState } from "react";
import { supabase } from "./supabase";
import "./CertificateRecords.css";

export default function CertificateRecords() {
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function fetchRecords() {
    setLoading(true);
    setError("");

    const { data, error } = await supabase
      .from("certificates")
      .select("*")
      .order("created_at", { ascending: false });

    if (error) {
      setError(error.message);
    } else {
      setRecords(data || []);
    }

    setLoading(false);
  }

  useEffect(() => {
    fetchRecords();
  }, []);

  // Display every database field, including
  // additional OCR fields saved in extracted_data.
  const extraKeys = [
    ...new Set(
      records.flatMap((record) =>
        record.extracted_data &&
        typeof record.extracted_data === "object" &&
        !Array.isArray(record.extracted_data)
          ? Object.keys(record.extracted_data)
          : []
      )
    ),
  ];

  const columns = [
    { key: "id", label: "Record ID" },
    { key: "student_id", label: "Student ID" },
    { key: "certificate_type", label: "Certificate Type" },
    { key: "holder_name", label: "Name" },
    { key: "institution_name", label: "Institution Name" },
    { key: "certificate_number", label: "Certificate Number" },
    { key: "issue_date", label: "Issue Date" },
    { key: "uploaded_file_url", label: "Uploaded File" },
    { key: "extracted_text", label: "Extracted OCR Text" },
    { key: "created_at", label: "Created At" },
    ...extraKeys.map((key) => ({
      key: `extracted_data.${key}`,
      label: key.replace(/_/g, " "),
    })),
  ];

  function getValue(record, key) {
    if (key.startsWith("extracted_data.")) {
      const field = key.slice("extracted_data.".length);
      return record.extracted_data?.[field];
    }

    return record[key];
  }

  function formatValue(value) {
    if (value === null || value === undefined || value === "") {
      return "—";
    }

    if (typeof value === "object") {
      return JSON.stringify(value);
    }

    return String(value);
  }

  return (
    <main className="certificate-records-page">
      <div className="records-heading">
        <div>
          <h1>Certificate Records</h1>
          <p>All saved certificate details from the database</p>
        </div>

        <button onClick={fetchRecords} className="refresh-btn">
          Refresh Records
        </button>
      </div>

      {loading && <p>Loading certificate records...</p>}

      {error && (
        <p className="records-error">
          Failed to load records: {error}
        </p>
      )}

      {!loading && !error && (
        <>
          <div className="records-summary">
            Total Records: <strong>{records.length}</strong>
          </div>

          <div className="records-table-wrapper">
            <table className="records-table">
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th key={column.key}>{column.label}</th>
                  ))}
                  <th>Actions</th>
                </tr>
              </thead>

              <tbody>
                {records.length === 0 ? (
                  <tr>
                    <td
                      colSpan={columns.length + 1}
                      className="empty-records"
                    >
                      No certificate records found.
                    </td>
                  </tr>
                ) : (
                  records.map((record) => (
                    <tr key={record.id}>
                      {columns.map((column) => {
                        const value = getValue(record, column.key);

                        return (
                          <td key={column.key}>
                            {column.key === "uploaded_file_url" &&
                            value ? (
                              <a
                                href={value}
                                target="_blank"
                                rel="noreferrer"
                              >
                                View File
                              </a>
                            ) : (
                              formatValue(value)
                            )}
                          </td>
                        );
                      })}

                      <td>
                        <button
                          className="view-btn"
                          onClick={() =>
                            window.alert(
                              JSON.stringify(record, null, 2)
                            )
                          }
                        >
                          View Details
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </main>
  );
}
