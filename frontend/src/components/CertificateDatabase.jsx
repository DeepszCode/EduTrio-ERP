
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  GraduationCap,
  Database,
  Bell,
  UserCircle,
  ChevronDown,
  Search,
  BookOpen,
  Landmark,
  User,
  Eye,
  Download,
  FileDown,
  FileSpreadsheet,
  FileText,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  X,
} from "lucide-react";
import "./CertificateDatabase.css";

/* -----------------------------------------
   Certificate category helpers
----------------------------------------- */

const getCategory = (certificateType = "") => {
  const type = String(certificateType).toLowerCase();

  if (
    type.includes("aadhaar") ||
    type.includes("aadhar") ||
    type.includes("identity") ||
    type.includes("identification")
  ) {
    return "Identity";
  }

  if (
    type.includes("community") ||
    type.includes("income") ||
    type.includes("birth") ||
    type.includes("nativity") ||
    type.includes("government")
  ) {
    return "Government";
  }

  return "Education";
};

const getCertificateType = (record) =>
  record?.certificate_type ||
  record?.certificateType ||
  record?.ocr_result?.doc_type ||
  record?.ocr_result?.document_type ||
  record?.certificate_name ||
  "Certificate";

const getStudentName = (record) => {
  const fields = record?.ocr_result?.fields || {};

  return (
    record?.student_name ||
    fields.student_name ||
    fields.candidate_name ||
    fields.studentName ||
    fields.candidateName ||
    fields.name ||
    "—"
  );
};

const formatDate = (date) => {
  if (!date) return "—";

  const parsed = new Date(date);
  if (Number.isNaN(parsed.getTime())) return String(date);

  return parsed.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
};

/* -----------------------------------------
   Flatten structured OCR fields.
   Intentionally excludes raw OCR text.
----------------------------------------- */

const flattenFields = (ocrResult) => {
  if (!ocrResult || typeof ocrResult !== "object") return {};

  const source =
    ocrResult.fields && typeof ocrResult.fields === "object"
      ? ocrResult.fields
      : ocrResult.extracted_data &&
          typeof ocrResult.extracted_data === "object"
        ? ocrResult.extracted_data
        : {};

  const flattened = {};

  const addValue = (key, value) => {
    if (
      value === null ||
      value === undefined ||
      typeof value === "object"
    ) {
      return;
    }

    const cleanKey = String(key)
      .replace(/[_-]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();

    if (!cleanKey) return;

    flattened[cleanKey] =
      String(value).trim() || "—";
  };

  Object.entries(source).forEach(([key, value]) => {
    if (typeof value !== "object" || value === null) {
      addValue(key, value);
      return;
    }

    if (Array.isArray(value)) {
      value.forEach((item, index) => {
        if (item && typeof item === "object") {
          Object.entries(item).forEach(([nestedKey, nestedValue]) => {
            addValue(`${key} ${index + 1} ${nestedKey}`, nestedValue);
          });
        } else {
          addValue(`${key} ${index + 1}`, item);
        }
      });
      return;
    }

    Object.entries(value).forEach(([nestedKey, nestedValue]) => {
      addValue(`${key} ${nestedKey}`, nestedValue);
    });
  });

  return flattened;
};

/* -----------------------------------------
   CSV export helper
----------------------------------------- */

const downloadCSV = (rows, columns, fileName) => {
  const escapeCSV = (value) => {
    const text = String(value ?? "");
    return `"${text.replace(/"/g, '""')}"`;
  };

  const header = columns.map((column) => escapeCSV(column.label));
  const body = rows.map((row) =>
    columns.map((column) => escapeCSV(row[column.key])).join(",")
  );

  const csv = [header.join(","), ...body].join("\r\n");
  const blob = new Blob(["\uFEFF", csv], {
    type: "text/csv;charset=utf-8;",
  });

  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
};

/* -----------------------------------------
   Main component
----------------------------------------- */

export default function CertificateDatabase({
  supabase,
  user,
  onBack,
  onNavigate,
}) {
  const [certificates, setCertificates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [activeCategory, setActiveCategory] = useState("All");
  const [activeType, setActiveType] = useState("All");
  const [searchText, setSearchText] = useState("");
  const [pageSize, setPageSize] = useState(10);
  const [page, setPage] = useState(1);
  const [selectedRecord, setSelectedRecord] = useState(null);

  /* Fetch the signed-in user's saved certificates */
  const loadCertificates = useCallback(async () => {
    if (!supabase || !user?.id) {
      setCertificates([]);
      setLoading(false);
      setLoadError("Please sign in to view your saved certificates.");
      return;
    }

    setLoading(true);
    setLoadError("");

    try {
      const { data, error } = await supabase
        .from("certificates")
        .select(
          "id, user_id, student_name, certificate_type, certificate_name, date_extracted, status, ocr_result"
        )
        .eq("user_id", user.id)
        .order("date_extracted", { ascending: false });

      if (error) throw error;

      setCertificates(data || []);
    } catch (error) {
      console.error("Unable to load certificate records:", error);
      setLoadError(
        error?.message || "Could not load certificate records."
      );
    } finally {
      setLoading(false);
    }
  }, [supabase, user?.id]);

  useEffect(() => {
    loadCertificates();
  }, [loadCertificates]);

  /* Category totals */
  const categoryCounts = useMemo(() => {
    const counts = {
      All: certificates.length,
      Education: 0,
      Government: 0,
      Identity: 0,
    };

    certificates.forEach((record) => {
      counts[getCategory(getCertificateType(record))] += 1;
    });

    return counts;
  }, [certificates]);

  /* Certificate types available for the selected category */
  const availableTypes = useMemo(() => {
    const filtered =
      activeCategory === "All"
        ? certificates
        : certificates.filter(
            (record) =>
              getCategory(getCertificateType(record)) === activeCategory
          );

    return [
      "All",
      ...new Set(filtered.map((record) => getCertificateType(record))),
    ];
  }, [certificates, activeCategory]);

  /* Search and filter records */
  const filteredRecords = useMemo(() => {
    const query = searchText.trim().toLowerCase();

    return certificates.filter((record) => {
      const type = getCertificateType(record);
      const category = getCategory(type);

      if (activeCategory !== "All" && category !== activeCategory) {
        return false;
      }

      if (activeType !== "All" && type !== activeType) {
        return false;
      }

      if (!query) return true;

      const searchableFields = flattenFields(record.ocr_result);

      const searchableText = [
        getStudentName(record),
        type,
        record.certificate_name,
        record.status,
        ...Object.values(searchableFields),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();

      return searchableText.includes(query);
    });
  }, [certificates, activeCategory, activeType, searchText]);

  /* Build a table with all fields present in the filtered records */
  const tableColumns = useMemo(() => {
    const columns = [
      { key: "row_number", label: "#", fixed: true },
      { key: "student_name", label: "Student Name" },
      { key: "certificate_type", label: "Certificate Type" },
    ];

    const fieldLabels = new Set();

    filteredRecords.forEach((record) => {
      Object.keys(flattenFields(record.ocr_result)).forEach((field) => {
        fieldLabels.add(field);
      });
    });

    [...fieldLabels]
      .sort((a, b) => a.localeCompare(b))
      .forEach((field) => {
        columns.push({
          key: `field:${field}`,
          label: field,
        });
      });

    columns.push(
      { key: "extracted_date", label: "Extracted Date" },
      { key: "status", label: "Status" },
      { key: "actions", label: "Actions" }
    );

    return columns;
  }, [filteredRecords]);

  /* Convert records to table-ready rows */
  const tableRows = useMemo(() => {
    return filteredRecords.map((record, index) => {
      const fields = flattenFields(record.ocr_result);
      const row = {
        row_number: index + 1,
        student_name: getStudentName(record),
        certificate_type: getCertificateType(record),
        extracted_date: formatDate(record.date_extracted),
        status: record.status || "Extracted",
        _record: record,
      };

      Object.entries(fields).forEach(([field, value]) => {
        row[`field:${field}`] = value;
      });

      return row;
    });
  }, [filteredRecords]);

  /* Pagination */
  const totalPages = Math.max(1, Math.ceil(tableRows.length / pageSize));
  const safePage = Math.min(page, totalPages);

  const visibleRows = tableRows.slice(
    (safePage - 1) * pageSize,
    safePage * pageSize
  );

  useEffect(() => {
    setPage(1);
  }, [activeCategory, activeType, searchText, pageSize]);

  /* Export all currently filtered structured details */
  const exportRows = filteredRecords.map((record, index) => {
    const fields = flattenFields(record.ocr_result);

    return {
      "#": index + 1,
      "Student Name": getStudentName(record),
      "Certificate Type": getCertificateType(record),
      ...fields,
      "Extracted Date": formatDate(record.date_extracted),
      Status: record.status || "Extracted",
    };
  });

  const exportColumns = exportRows.length
    ? Object.keys(exportRows[0]).map((key) => ({
        key,
        label: key,
      }))
    : [
        { key: "#", label: "#" },
        { key: "Student Name", label: "Student Name" },
        { key: "Certificate Type", label: "Certificate Type" },
        { key: "Extracted Date", label: "Extracted Date" },
        { key: "Status", label: "Status" },
      ];

  const handleExportCSV = () => {
    downloadCSV(
      exportRows,
      exportColumns,
      "SMART_ERP_Certificate_Details.csv"
    );
  };

  const handleExportExcel = () => {
    /* CSV can be opened directly in Excel */
    handleExportCSV();
  };

  const handleExportPDF = () => {
    window.print();
  };

  const handleDownloadRecord = (record) => {
    const fields = flattenFields(record.ocr_result);

    const content = [
      `Student Name: ${getStudentName(record)}`,
      `Certificate Type: ${getCertificateType(record)}`,
      `Extracted Date: ${formatDate(record.date_extracted)}`,
      `Status: ${record.status || "Extracted"}`,
      "",
      ...Object.entries(fields).map(([key, value]) => `${key}: ${value}`),
    ].join("\n");

    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "certificate-details.txt";
    link.click();
    URL.revokeObjectURL(url);
  };

  const titleForCategory =
    activeCategory === "All"
      ? "All Certificates"
      : `${activeCategory} Certificates`;

  return (
    <div className="certificate-db-page">
      {/* Top header: brand at the left, account controls at the right */}
      <header className="certificate-db-header">
        <div className="certificate-db-brand certificate-db-brand-header">
          <GraduationCap className="certificate-db-brand-icon" />
          <div className="certificate-db-brand-copy">
            <strong>SMART ERP</strong>
            <span>Certificate Data Extraction &amp; Management System</span>
          </div>
        </div>

        <div className="certificate-db-header-actions">
          <button
            className="header-icon-button"
            type="button"
            aria-label="Notifications"
            title="Notifications"
          >
            <Bell size={18} />
            <span className="notification-dot" />
          </button>

          <span className="header-separator" />

          <div className="account-label">
            <UserCircle size={21} />
            <span>Certificate Management</span>
            <ChevronDown size={14} />
          </div>
        </div>
      </header>

      <main className="certificate-db-main">
        {/* Page title */}
        <section className="database-title-row">
          <div className="database-title-icon">
            <Database size={27} />
          </div>
          <div>
            <h1>Certificate Database</h1>
            <p>Manage extracted certificate records</p>
          </div>
        </section>

        {/* Category tabs */}
        <section className="database-category-tabs" aria-label="Certificate categories">
          <button
            className={`category-tab ${activeCategory === "All" ? "active" : ""}`}
            onClick={() => {
              setActiveCategory("All");
              setActiveType("All");
            }}
            type="button"
          >
            <GraduationCap size={18} />
            All Certificates <span>{categoryCounts.All}</span>
          </button>

          <button
            className={`category-tab ${activeCategory === "Education" ? "active" : ""}`}
            onClick={() => {
              setActiveCategory("Education");
              setActiveType("All");
            }}
            type="button"
          >
            <BookOpen size={18} />
            Education <span>{categoryCounts.Education}</span>
          </button>

          <button
            className={`category-tab ${activeCategory === "Government" ? "active" : ""}`}
            onClick={() => {
              setActiveCategory("Government");
              setActiveType("All");
            }}
            type="button"
          >
            <Landmark size={18} />
            Government <span>{categoryCounts.Government}</span>
          </button>

          <button
            className={`category-tab ${activeCategory === "Identity" ? "active" : ""}`}
            onClick={() => {
              setActiveCategory("Identity");
              setActiveType("All");
            }}
            type="button"
          >
            <User size={18} />
            Identity <span>{categoryCounts.Identity}</span>
          </button>
        </section>

        {/* Type filters, search, export controls */}
        <section className="database-toolbar">
          <div className="certificate-type-filters">
            {availableTypes.map((type) => (
              <button
                key={type}
                type="button"
                className={`type-filter ${activeType === type ? "active" : ""}`}
                onClick={() => setActiveType(type)}
                title={type}
              >
                {type === "All" ? "All" : type}
              </button>
            ))}
          </div>

          <label className="database-search">
            <Search size={17} />
            <input
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              placeholder="Search student name or extracted certificate fields..."
            />
          </label>

          <div className="database-toolbar-actions">
            <button className="export-button export-pdf" onClick={handleExportPDF} type="button">
              <FileDown size={15} /> Export PDF
            </button>
            <button className="export-button export-excel" onClick={handleExportExcel} type="button">
              <FileSpreadsheet size={15} /> Export Excel
            </button>
            <button className="export-button export-csv" onClick={handleExportCSV} type="button">
              <FileText size={15} /> Export CSV
            </button>
            <button className="back-button" onClick={onBack} type="button">
              <ArrowLeft size={15} /> Back
            </button>
          </div>
        </section>

        {/* Data table */}
        <section className="database-table-card">
          <div className="database-table-heading">
            <GraduationCap size={18} />
            <strong>{titleForCategory}</strong>
            <span>({filteredRecords.length} records)</span>
          </div>

          {loading ? (
            <div className="database-message">Loading certificate records...</div>
          ) : loadError ? (
            <div className="database-message database-error">{loadError}</div>
          ) : filteredRecords.length === 0 ? (
            <div className="database-message">
              No certificate records match your search.
            </div>
          ) : (
            <>
              <div className="database-table-scroll">
                <table className="certificate-details-table">
                  <thead>
                    <tr>
                      {tableColumns.map((column) => (
                        <th key={column.key}>{column.label}</th>
                      ))}
                    </tr>
                  </thead>

                  <tbody>
                    {visibleRows.map((row) => (
                      <tr key={row._record.id}>
                        {tableColumns.map((column) => {
                          if (column.key === "actions") {
                            return (
                              <td key={column.key} className="table-actions-cell">
                                <button
                                  className="table-action-button"
                                  type="button"
                                  title="View full extracted details"
                                  aria-label="View full extracted details"
                                  onClick={() => setSelectedRecord(row._record)}
                                >
                                  <Eye size={16} />
                                </button>
                                <button
                                  className="table-action-button"
                                  type="button"
                                  title="Download certificate details"
                                  aria-label="Download certificate details"
                                  onClick={() => handleDownloadRecord(row._record)}
                                >
                                  <Download size={16} />
                                </button>
                              </td>
                            );
                          }

                          if (column.key === "status") {
                            return (
                              <td key={column.key}>
                                <span className="status-pill">
                                  <span className="status-dot" />
                                  {row.status}
                                </span>
                              </td>
                            );
                          }

                          return (
                            <td key={column.key}>
                              {row[column.key] ?? "—"}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="database-table-footer">
                <label className="entries-selector">
                  Show
                  <select
                    value={pageSize}
                    onChange={(event) => setPageSize(Number(event.target.value))}
                  >
                    <option value={5}>5</option>
                    <option value={10}>10</option>
                    <option value={25}>25</option>
                    <option value={50}>50</option>
                  </select>
                  entries
                </label>

                <div className="pagination-controls">
                  <span>
                    {tableRows.length === 0
                      ? "0 records"
                      : `${(safePage - 1) * pageSize + 1}–${Math.min(
                          safePage * pageSize,
                          tableRows.length
                        )} of ${tableRows.length}`}
                  </span>

                  <button
                    type="button"
                    aria-label="Previous page"
                    disabled={safePage <= 1}
                    onClick={() => setPage((current) => Math.max(1, current - 1))}
                  >
                    <ChevronLeft size={17} />
                  </button>

                  <span className="current-page">{safePage}</span>

                  <button
                    type="button"
                    aria-label="Next page"
                    disabled={safePage >= totalPages}
                    onClick={() =>
                      setPage((current) => Math.min(totalPages, current + 1))
                    }
                  >
                    <ChevronRight size={17} />
                  </button>
                </div>
              </div>
            </>
          )}
        </section>
      </main>

      {/* Footer: centered brand, navy text, blue border */}
      <footer className="certificate-db-footer">
        <div className="certificate-db-brand certificate-db-brand-footer">
          <GraduationCap className="certificate-db-brand-icon" />
          <div className="certificate-db-brand-copy">
            <strong>SMART ERP</strong>
            <span>Certificate Data Extraction &amp; Management System</span>
            <small>Scan&nbsp; • &nbsp;Extract&nbsp; • &nbsp;Manage&nbsp; • &nbsp;Secure</small>
          </div>
        </div>
      </footer>

      {/* Full details modal shows structured fields, not raw OCR text */}
      {selectedRecord && (
        <div
          className="certificate-detail-overlay"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setSelectedRecord(null);
            }
          }}
        >
          <section
            className="certificate-detail-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="certificate-detail-title"
          >
            <div className="certificate-detail-modal-header">
              <div>
                <h2 id="certificate-detail-title">Full Certificate Details</h2>
                <p>{getCertificateType(selectedRecord)}</p>
              </div>
              <button
                type="button"
                className="modal-close-button"
                aria-label="Close details"
                onClick={() => setSelectedRecord(null)}
              >
                <X size={20} />
              </button>
            </div>

            <div className="certificate-detail-modal-body">
              <div className="detail-field">
                <span>Student Name</span>
                <strong>{getStudentName(selectedRecord)}</strong>
              </div>
              <div className="detail-field">
                <span>Certificate Type</span>
                <strong>{getCertificateType(selectedRecord)}</strong>
              </div>
              <div className="detail-field">
                <span>Extracted Date</span>
                <strong>{formatDate(selectedRecord.date_extracted)}</strong>
              </div>
              <div className="detail-field">
                <span>Status</span>
                <strong>{selectedRecord.status || "Extracted"}</strong>
              </div>

              {Object.entries(flattenFields(selectedRecord.ocr_result)).map(
                ([field, value]) => (
                  <div className="detail-field" key={field}>
                    <span>{field}</span>
                    <strong>{value}</strong>
                  </div>
                )
              )}
            </div>

            <div className="certificate-detail-modal-footer">
              <button
                type="button"
                className="export-button export-pdf"
                onClick={() => handleDownloadRecord(selectedRecord)}
              >
                <Download size={15} /> Download Details
              </button>
              <button
                type="button"
                className="back-button"
                onClick={() => setSelectedRecord(null)}
              >
                Close
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
