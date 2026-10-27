
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  GraduationCap,
  Database,
  Search,
  Eye,
  Download,
  FileDown,
  FileSpreadsheet,
  FileText,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  X,
  BookOpen,
  School,
  FileBadge,
  Landmark,
  IdCard,
  HeartHandshake,
  Receipt,
  BriefcaseBusiness,
  Award,
  FileQuestion,
  Pencil,
  Trash2,
  Save,
  LoaderCircle,
} from "lucide-react";
import "./CertificateDatabase.css";

/* -----------------------------------------
   Certificate category helpers
----------------------------------------- */

const getCertificateType = (record) =>
  record?.certificate_type ||
  record?.certificateType ||
  record?.ocr_result?.doc_type ||
  record?.ocr_result?.document_type ||
  record?.certificate_name ||
  "Certificate";

const CERTIFICATE_CATEGORIES = [
  { id: "state10", title: "State Board 10th Marksheet", description: "Class 10 results and subject marks", icon: School },
  { id: "cbse10", title: "CBSE 10th Marksheet", description: "CBSE Class 10 marks and grades", icon: GraduationCap },
  { id: "state12", title: "State Board 12th Marksheet", description: "Higher secondary subject results", icon: BookOpen },
  { id: "cbse12", title: "CBSE 12th Marksheet", description: "CBSE Class 12 marks and grades", icon: BookOpen },
  { id: "transfer", title: "Transfer Certificate", description: "School leaving and transfer details", icon: FileBadge },
  { id: "birth", title: "Birth Certificate", description: "Birth and registration details", icon: Landmark },
  { id: "aadhaar", title: "Aadhaar Card", description: "Identity certificate records", icon: IdCard },
  { id: "income", title: "Income Certificate", description: "Income and issuing authority details", icon: Receipt },
  { id: "community", title: "Community Certificate", description: "Community and classification details", icon: HeartHandshake },
  { id: "diploma", title: "Diploma Certificate", description: "Diploma qualification records", icon: BriefcaseBusiness },
  { id: "degree", title: "Degree Certificate", description: "Degree and higher education records", icon: Award },
];

const getCategoryId = (record) => {
  const value = `${getCertificateType(record)} ${record?.certificate_name || ""}`.toLowerCase().replace(/[_-]+/g, " ");
  if (/transfer|\btc\b/.test(value)) return "transfer";
  if (/aadhaar|aadhar/.test(value)) return "aadhaar";
  if (/birth/.test(value)) return "birth";
  if (/income/.test(value)) return "income";
  if (/community/.test(value)) return "community";
  if (/diploma/.test(value)) return "diploma";
  if (/degree|bachelor|master/.test(value)) return "degree";
  const is10 = /10th|class\s*x\b|secondary|sslc/.test(value);
  const is12 = /12th|class\s*xii\b|higher\s*secondary|hsc/.test(value);
  if (/cbse|central board/.test(value) && is10) return "cbse10";
  if (/cbse|central board/.test(value) && is12) return "cbse12";
  if (/state board|state_board|tamil nadu|tn board/.test(value) && is10) return "state10";
  if (/state board|state_board|tamil nadu|tn board/.test(value) && is12) return "state12";
  if (/^10th marksheet$|^10th marks$|^class x marksheet$/.test(value.trim())) return "state10";
  if (/^12th marksheet$|^12th marks$|^class xii marksheet$/.test(value.trim())) return "state12";
  return `other:${getCertificateType(record)}`;
};

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

const safeFileSegment = (value) => String(value || "records")
  .trim()
  .replace(/[<>:"/\\|?*]+/g, "_")
  .replace(/\s+/g, "_");

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

const getEditableFieldEntries = (ocrResult) => {
  const sourceKey = ocrResult?.fields && typeof ocrResult.fields === "object"
    ? "fields"
    : ocrResult?.extracted_data && typeof ocrResult.extracted_data === "object"
      ? "extracted_data"
      : null;
  if (!sourceKey) return [];

  const entries = [];
  const visit = (value, path, label) => {
    if (value === null || value === undefined) return;
    if (Array.isArray(value)) {
      value.forEach((item, index) => {
        const descriptor = item && typeof item === "object"
          ? item.label || item.name || item.field || item.key
          : null;
        if (item && typeof item === "object" && Object.hasOwn(item, "value") && descriptor) {
          entries.push({
            path: [...path, index, "value"],
            label: String(descriptor).replace(/[_-]+/g, " ").trim(),
            value: String(item.value ?? ""),
          });
        } else {
          visit(item, [...path, index], `${label} ${index + 1}`.trim());
        }
      });
      return;
    }
    if (typeof value === "object") {
      const descriptor = value.label || value.name || value.field || value.key;
      if (Object.hasOwn(value, "value") && descriptor) {
        entries.push({
          path: [...path, "value"],
          label: String(descriptor).replace(/[_-]+/g, " ").trim(),
          value: String(value.value ?? ""),
        });
        return;
      }
      Object.entries(value).forEach(([key, nestedValue]) => {
        if (/raw.?text|full.?text/i.test(key)) return;
        const cleanKey = key.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
        visit(nestedValue, [...path, key], label ? `${label} · ${cleanKey}` : cleanKey);
      });
      return;
    }
    entries.push({ path, label, value: String(value) });
  };

  visit(ocrResult[sourceKey], [sourceKey], "");
  return entries;
};

const updateNestedValue = (source, path, value) => {
  const copy = JSON.parse(JSON.stringify(source || {}));
  let current = copy;
  path.slice(0, -1).forEach((part) => { current = current[part]; });
  current[path[path.length - 1]] = value;
  return copy;
};

const editSectionFor = (label) => {
  const value = String(label || "").toLowerCase().replace(/[_-]+/g, " ");
  if (/subject\s*\d+|subject.*\b(name|marks?|score)\b/.test(value)) return "subjects";
  if (/student|candidate|father|mother|parent|guardian|date of birth|emis|gender/.test(value)) return "student";
  if (/certificate|serial number|document type|extracted date|^status$/.test(value)) return "certificate";
  if (/exam|standard|session|result|marks?|score|percentage|roll|register|school|tmr|total/.test(value)) return "examination";
  return "additional";
};

const isNumericEditField = (label) => /\b(marks?|score|percentage|total|maximum|year|age|cgpa|gpa)\b/i.test(label);

const stableJson = (value) => {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
};

const editFailureMessage = (error) => {
  const code = String(error?.code || "");
  const message = String(error?.message || "");
  if (code === "42501" || /row.level security|permission denied/i.test(message)) {
    return "Supabase blocked this update. Check the signed-in role, record ownership, and certificate UPDATE/SELECT RLS policies.";
  }
  if (/constraint|invalid input syntax|out of range|date\/time/i.test(message)) {
    return `Some values are invalid for the certificate database: ${message}`;
  }
  if (error instanceof TypeError || /failed to fetch|network/i.test(message)) {
    return "Could not reach Supabase. Check your connection and try again; your edits are still in this form.";
  }
  return message || "Could not update this certificate record. Your edits are still in this form.";
};

const EDIT_SECTION_DEFS = [
  { key: "student", title: "Student Information" },
  { key: "certificate", title: "Certificate Information" },
  { key: "examination", title: "Examination Details" },
  { key: "subjects", title: "Subject Names and Marks" },
  { key: "additional", title: "Additional Certificate Details" },
];

const groupEditableFields = (fields) => {
  const groups = Object.fromEntries(EDIT_SECTION_DEFS.map(({ key }) => [key, []]));
  fields.forEach((field) => groups[editSectionFor(field.label)].push(field));
  groups.subjects.sort((left, right) => {
    const subjectDetails = (label) => {
      const match = String(label).match(/subject\s*(\d+)/i);
      return [Number(match?.[1] || 999), /name/i.test(label) ? 0 : /mark|score/i.test(label) ? 1 : 2];
    };
    const [leftNumber, leftKind] = subjectDetails(left.label);
    const [rightNumber, rightKind] = subjectDetails(right.label);
    return leftNumber - rightNumber || leftKind - rightKind;
  });
  return EDIT_SECTION_DEFS.map((section) => ({ ...section, fields: groups[section.key] }));
};

const updateOcrStudentName = (ocrResult, name) => {
  const copy = JSON.parse(JSON.stringify(ocrResult || {}));
  const nameFields = new Set(["student_name", "candidate_name", "studentname", "candidatename", "name"]);
  const syncName = (value) => {
    if (Array.isArray(value)) return value.map(syncName);
    if (!value || typeof value !== "object") return value;
    const descriptor = [value.field, value.key, value.name, value.label]
      .filter((item) => typeof item === "string")
      .map((item) => item.toLowerCase().replace(/[^a-z]/g, ""));
    if (Object.hasOwn(value, "value") && descriptor.some((item) => nameFields.has(item))) {
      return { ...value, value: name };
    }
    return Object.fromEntries(Object.entries(value).map(([key, child]) => [
      key,
      nameFields.has(key.toLowerCase().replace(/[^a-z]/g, "")) ? name : syncName(child),
    ]));
  };
  ["fields", "extracted_data"].forEach((sourceKey) => {
    if (copy[sourceKey] && typeof copy[sourceKey] === "object") copy[sourceKey] = syncName(copy[sourceKey]);
  });
  return copy;
};

const makeRecordDuplicateKey = (studentName, fields) => {
  const normalized = Object.entries(fields || {})
    .map(([key, value]) => [
      key.toLowerCase().replace(/[\s-]+/g, "_").trim(),
      String(value ?? "").trim().toLowerCase(),
    ])
    .sort(([left], [right]) => left.localeCompare(right));
  const byKey = Object.fromEntries(normalized);
  const identity = ["roll_number", "roll_no", "register_number", "register_no", "registration_number", "certificate_number", "certificate_serial_number", "tc_number", "serial_number", "permanent_register_number", "permanent_register_no", "admission_number", "id_number"]
    .map((key) => byKey[key])
    .find(Boolean);
  const fieldIdentity = identity || JSON.stringify(normalized);
  return `${String(studentName || "").trim().toLowerCase()}|${fieldIdentity}`;
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

const parseCSV = (text) => {
  const rows = [];
  let row = [];
  let value = "";
  let quoted = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted && char === '"' && text[index + 1] === '"') {
      value += '"';
      index += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === "," && !quoted) {
      row.push(value);
      value = "";
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && text[index + 1] === "\n") index += 1;
      row.push(value);
      if (row.some((cell) => cell.trim())) rows.push(row);
      row = [];
      value = "";
    } else {
      value += char;
    }
  }

  row.push(value);
  if (row.some((cell) => cell.trim())) rows.push(row);
  if (rows.length < 2) return { headers: [], rows: [], errors: ["CSV must include a header row and at least one data row."] };

  const headers = rows[0].map((header) => header.trim().replace(/^\uFEFF/, ""));
  const errors = [];
  if (quoted) errors.push("The CSV contains an unclosed quoted value.");
  if (headers.some((header) => !header)) errors.push("Every column must have a header.");
  const normalizedHeaders = headers.map((header) => header.toLowerCase().replace(/[\s-]+/g, "_"));
  if (new Set(normalizedHeaders).size !== normalizedHeaders.length) errors.push("Column headers must be unique.");
  if (headers.length < 2) errors.push("Include at least two columns, such as Student Name and Register Number.");
  const recognizedRecordColumns = new Set(["student_name", "candidate_name", "name", "roll_number", "roll_no", "register_number", "registration_number", "certificate_number", "certificate_serial_number", "tc_number", "permanent_register_number", "date_of_birth"]);
  if (!normalizedHeaders.some((header) => recognizedRecordColumns.has(header))) errors.push("Include a recognized student or certificate identifier column, such as Student Name or Register Number.");

  const dataRows = rows.slice(1).map((cells, rowIndex) => {
    if (cells.length !== headers.length) errors.push(`Row ${rowIndex + 2} has ${cells.length} values; expected ${headers.length}.`);
    return Object.fromEntries(headers.map((header, index) => [header, (cells[index] || "").trim()]));
  });
  const nonEmptyRows = dataRows.filter((row) => Object.values(row).some(Boolean));
  if (!nonEmptyRows.length) errors.push("The CSV contains no non-empty data rows.");
  return { headers, rows: nonEmptyRows, errors };
};

/* -----------------------------------------
   Main component
----------------------------------------- */

export default function CertificateDatabase({
  supabase,
  user,
  userRole = "student",
  onBack,
  selectedType = null,
  onSelectType,
}) {
  const [certificates, setCertificates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [searchText, setSearchText] = useState("");
  const [pageSize, setPageSize] = useState(10);
  const [page, setPage] = useState(1);
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [editingRecord, setEditingRecord] = useState(null);
  const [editDraft, setEditDraft] = useState(null);
  const [savingRecord, setSavingRecord] = useState(false);
  const [deletingRecordId, setDeletingRecordId] = useState(null);
  const [databaseActionMessage, setDatabaseActionMessage] = useState("");
  const [databaseActionError, setDatabaseActionError] = useState(false);
  const editModalRef = useRef(null);
  const editReturnFocusRef = useRef(null);
  const [importMessage, setImportMessage] = useState("");
  const [importing, setImporting] = useState(false);
  const [importPreview, setImportPreview] = useState(null);
  const importInputRef = useRef(null);
  const [internalSelectedType, setInternalSelectedType] = useState(null);
  const isExtractedDataPage = typeof onSelectType === "function";
  const activeType = isExtractedDataPage ? selectedType : internalSelectedType;
  const canManageRecord = (record) => Boolean(user?.id && (
    userRole === "admin" || (userRole === "staff" && record?.user_id === user.id)
  ));
  const userId = user?.id;

  /* Fetch records allowed by the signed-in user's database role. */
  const loadCertificates = useCallback(async () => {
    if (!supabase || !userId) {
      setCertificates([]);
      setLoading(false);
      setLoadError("Please sign in to view your saved certificates.");
      return;
    }

    setLoading(true);
    setLoadError("");

    try {
      let query = supabase
        .from("certificates")
        .select("id,user_id,student_name,certificate_type,certificate_name,date_extracted,status,ocr_result");
      if (userRole === "student") query = query.eq("student_user_id", userId);
      const { data, error } = await query.order("date_extracted", { ascending: false });

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
  }, [supabase, userId, userRole]);

  useEffect(() => {
    // Load persisted records and synchronize the loading state with Supabase.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadCertificates();
  }, [loadCertificates]);

  /* Search and filter records */
  const filteredRecords = useMemo(() => {
    const query = searchText.trim().toLowerCase();

    return certificates.filter((record) => {
      const type = getCertificateType(record);
      if (!activeType || getCategoryId(record) !== activeType) {
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
  }, [certificates, activeType, searchText]);

  const categoryCounts = useMemo(() => {
    const counts = new Map(CERTIFICATE_CATEGORIES.map((category) => [category.id, 0]));
    const otherTypes = new Set();
    certificates.forEach((record) => {
      const id = getCategoryId(record);
      if (counts.has(id)) counts.set(id, counts.get(id) + 1);
      else otherTypes.add(id);
    });
    const dynamicCategories = [...otherTypes].sort().map((id) => ({
      id,
      title: id.slice("other:".length),
      description: "Other supported certificate records",
      icon: FileQuestion,
    }));
    return [
      ...CERTIFICATE_CATEGORIES.map((category) => ({ ...category, count: counts.get(category.id) || 0 })),
      ...dynamicCategories.map((category) => ({
        ...category,
        count: certificates.filter((record) => getCategoryId(record) === category.id).length,
      })),
    ];
  }, [certificates]);

  const selectedCategory = categoryCounts.find((category) => category.id === activeType);
  const categoryTitle = selectedCategory?.title || activeType || "Certificate records";
  const categoryRecords = useMemo(
    () => certificates.filter((record) => getCategoryId(record) === activeType),
    [certificates, activeType]
  );

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
      `SMART_ERP_${safeFileSegment(categoryTitle)}_Records.csv`
    );
  };

  const handleExportExcel = () => {
    const escapeXML = (value) => String(value ?? "").replace(/[&<>"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char]));
    const rows = [exportColumns.map((column) => column.label), ...exportRows.map((row) => exportColumns.map((column) => row[column.key] ?? ""))];
    const xml = `<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet ss:Name="Records"><Table>${rows.map((row) => `<Row>${row.map((cell) => `<Cell><Data ss:Type="String">${escapeXML(cell)}</Data></Cell>`).join("")}</Row>`).join("")}</Table></Worksheet></Workbook>`;
    const blob = new Blob([xml], { type: "application/vnd.ms-excel;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `SMART_ERP_${safeFileSegment(categoryTitle)}_Records.xls`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const handleExportPDF = () => {
    window.print();
  };

  const handleImportCSV = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !activeType) return;
    if (!/\.csv$/i.test(file.name)) {
      setImportMessage("Choose a .csv file to import.");
      return;
    }
    if (!user?.id) {
      setImportMessage("Please sign in before importing records.");
      return;
    }

    setImportMessage("");
    try {
      const parsed = parseCSV(await file.text());
      const errors = [...parsed.errors];
      const imported = parsed.rows.map((row, index) => {
        const fields = {};
        let studentName = "";
        let rowType = "";
        for (const [header, value] of Object.entries(row)) {
          const key = header.trim().toLowerCase().replace(/[\s-]+/g, "_");
          if (["student_name", "candidate_name", "name"].includes(key)) {
            studentName = value;
          } else if (["certificate_type", "document_type"].includes(key)) {
            rowType = value;
          } else if (!["certificate_name", "date_extracted", "status", "user_id", "id", "row_number", "#", "extracted_date"].includes(key)) {
            fields[header] = value;
          }
        }
        if (rowType && getCategoryId({ certificate_type: rowType }) !== activeType) errors.push(`Row ${index + 2} belongs to ${rowType}, not ${selectedCategory?.title || activeType}.`);
        if (!Object.values(fields).some(Boolean) && !studentName) errors.push(`Row ${index + 2} has no usable certificate data.`);
        const duplicateKey = makeRecordDuplicateKey(studentName, fields);
        return {
          duplicateKey,
          record: {
          user_id: user.id,
          student_name: studentName,
          certificate_type: selectedCategory?.title || activeType,
          certificate_name: selectedCategory?.title || activeType,
          date_extracted: new Date().toISOString().slice(0, 10),
          status: "Imported",
          ocr_result: { doc_type: activeType, fields },
          },
        };
      });
      const seen = new Set();
      const existingKeys = new Set(categoryRecords.map((record) => makeRecordDuplicateKey(getStudentName(record), flattenFields(record.ocr_result))));
      const uniqueRows = imported.filter((entry, index) => {
        if (seen.has(entry.duplicateKey) || existingKeys.has(entry.duplicateKey)) {
          errors.push(`Row ${index + 2} duplicates a record already in this category or CSV.`);
          return false;
        }
        seen.add(entry.duplicateKey);
        return true;
      });
      setImportPreview({ fileName: file.name, rows: uniqueRows.map(({ record }) => record), errors });
    } catch (error) {
      setImportMessage(error?.message || "Could not import this CSV file.");
    }
  };

  const confirmImport = async () => {
    if (!importPreview?.rows?.length || importPreview.errors.length) return;
    setImporting(true);
    setImportMessage("");
    try {
      const { error } = await supabase.from("certificates").insert(importPreview.rows);
      if (error) throw error;
      setImportMessage(`Imported ${importPreview.rows.length} ${importPreview.rows.length === 1 ? "record" : "records"} into ${categoryTitle}.`);
      setImportPreview(null);
      await loadCertificates();
    } catch (error) {
      setImportMessage(error?.message || "Could not import this CSV file.");
    } finally {
      setImporting(false);
    }
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

  const startEditingRecord = (record) => {
    editReturnFocusRef.current = document.activeElement;
    setDatabaseActionMessage("");
    setDatabaseActionError(false);
    setEditingRecord(record);
    setEditDraft({
      student_name: record.student_name || (getStudentName(record) === "—" ? "" : getStudentName(record)),
      certificate_type: record.certificate_type || "",
      certificate_name: record.certificate_name || "",
      date_extracted: record.date_extracted || "",
      status: record.status || "Extracted",
      ocr_result: JSON.parse(JSON.stringify(record.ocr_result || {})),
    });
  };

  const closeEditModal = useCallback(() => {
    setEditingRecord(null);
    setEditDraft(null);
    window.requestAnimationFrame(() => editReturnFocusRef.current?.focus?.());
  }, []);

  useEffect(() => {
    if (!editingRecord) return undefined;
    const firstControl = editModalRef.current?.querySelector("input, button");
    firstControl?.focus();
    const handleDialogKeyDown = (event) => {
      if (event.key === "Escape" && !savingRecord) closeEditModal();
    };
    window.addEventListener("keydown", handleDialogKeyDown);
    return () => window.removeEventListener("keydown", handleDialogKeyDown);
  }, [editingRecord, savingRecord, closeEditModal]);

  const handleSaveRecord = async (event) => {
    event.preventDefault();
    if (!editingRecord || !editDraft || !canManageRecord(editingRecord)) return;
    if (!editDraft.certificate_type.trim()) {
      setDatabaseActionError(true);
      setDatabaseActionMessage("Certificate Type is required. Your edits are still in this form.");
      return;
    }
    setSavingRecord(true);
    setDatabaseActionMessage("");
    setDatabaseActionError(false);
    try {
      const { data: sessionData, error: sessionError } = await supabase.auth.getSession();
      if (sessionError || !sessionData?.session?.access_token) {
        throw new Error("Your session has expired. Sign in again, then reopen this certificate record.");
      }
      const ocrResult = updateOcrStudentName(editDraft.ocr_result, editDraft.student_name);
      const update = {
        student_name: editDraft.student_name.trim() || null,
        certificate_type: editDraft.certificate_type.trim(),
        certificate_name: editDraft.certificate_name.trim() || editDraft.certificate_type.trim(),
        date_extracted: editDraft.date_extracted.trim() || null,
        status: editDraft.status.trim() || "Extracted",
        ocr_result: ocrResult,
      };
      let updateQuery = supabase
        .from("certificates")
        .update(update)
        .eq("id", editingRecord.id);
      if (userRole === "staff") {
        updateQuery = updateQuery.eq("user_id", user.id);
      }
      const { data: savedRecord, error } = await updateQuery
        .select("id,user_id,student_name,certificate_type,certificate_name,date_extracted,status,ocr_result")
        .maybeSingle();
      if (error) throw error;
      let confirmedRecord = savedRecord;
      if (!confirmedRecord) {
        // Some PostgREST deployments can accept PATCH but omit its returned
        // representation. Read the row back through the same authenticated
        // client and confirm every edited value before showing success.
        let verifyQuery = supabase
          .from("certificates")
          .select("id,user_id,student_name,certificate_type,certificate_name,date_extracted,status,ocr_result")
          .eq("id", editingRecord.id);
        if (userRole === "staff") verifyQuery = verifyQuery.eq("user_id", user.id);
        const { data: persistedRecord, error: verifyError } = await verifyQuery.maybeSingle();
        if (verifyError) throw verifyError;
        const valuesMatch = persistedRecord
          && persistedRecord.student_name === update.student_name
          && persistedRecord.certificate_type === update.certificate_type
          && persistedRecord.certificate_name === update.certificate_name
          && persistedRecord.date_extracted === update.date_extracted
          && persistedRecord.status === update.status
          && stableJson(persistedRecord.ocr_result) === stableJson(update.ocr_result);
        if (!valuesMatch) {
          throw new Error("Supabase did not confirm this edit. The record may be unavailable to your role, or its UPDATE policy may be missing. Your changes are still in this form.");
        }
        confirmedRecord = persistedRecord;
      }

      setCertificates((previous) => previous.map((record) => record.id === confirmedRecord.id ? { ...record, ...confirmedRecord } : record));
      setDatabaseActionMessage("Certificate record updated.");
      closeEditModal();
    } catch (error) {
      setDatabaseActionError(true);
      setDatabaseActionMessage(editFailureMessage(error));
    } finally {
      setSavingRecord(false);
    }
  };

  const handleDeleteRecord = async (record) => {
    if (!canManageRecord(record)) return;
    const name = getStudentName(record);
    if (!window.confirm(`Delete the ${getCertificateType(record)} record for ${name}? This cannot be undone.`)) return;

    setDeletingRecordId(record.id);
    setDatabaseActionMessage("");
    setDatabaseActionError(false);
    try {
      const { data, error } = await supabase
        .from("certificates")
        .delete()
        .eq("id", record.id)
        .select("id")
        .maybeSingle();
      if (error) throw error;
      if (!data) throw new Error("The record was not deleted. Check your role and record permissions.");

      setCertificates((previous) => previous.filter((item) => item.id !== record.id));
      if (selectedRecord?.id === record.id) setSelectedRecord(null);
      setDatabaseActionMessage("Certificate record deleted.");
    } catch (error) {
      setDatabaseActionError(true);
      setDatabaseActionMessage(error?.message || "Could not delete this certificate record.");
    } finally {
      setDeletingRecordId(null);
    }
  };

  const titleForCategory = categoryTitle;
  const changeCategory = (categoryId) => {
    if (isExtractedDataPage) onSelectType(categoryId);
    else setInternalSelectedType(categoryId);
  };
  const selectCategory = (categoryId) => {
    setSearchText("");
    setPage(1);
    setImportMessage("");
    setImportPreview(null);
    changeCategory(categoryId);
  };
  const editableSections = editDraft
    ? groupEditableFields(getEditableFieldEntries(editDraft.ocr_result))
    : [];
  const renderOcrEditField = ({ path, label, value }) => {
    const numeric = isNumericEditField(label);
    const dateValue = String(value ?? "");
    const dateInput = /date/i.test(label) && /^\d{4}-\d{2}-\d{2}$/.test(dateValue);
    return (
      <label className="certificate-edit-field" key={JSON.stringify(path)}>
        <span>{label}</span>
        <input
          type={numeric ? "number" : dateInput ? "date" : "text"}
          min={numeric ? "0" : undefined}
          step={numeric ? "any" : undefined}
          value={value ?? ""}
          onChange={(event) => {
            const rawValue = event.target.value;
            const nextValue = numeric
              ? rawValue === "" ? null : Number(rawValue)
              : rawValue;
            setEditDraft((previous) => ({
              ...previous,
              ocr_result: updateNestedValue(previous.ocr_result, path, nextValue),
            }));
          }}
          aria-label={label}
        />
      </label>
    );
  };

  if (!activeType) {
    const types = categoryCounts.map((category) => category.title);
    return (
      <div className="certificate-db-page">
        <header className="certificate-db-header">
          <div className="certificate-db-brand certificate-db-brand-header">
            <GraduationCap className="certificate-db-brand-icon" />
            <div className="certificate-db-brand-copy"><strong>SMART ERP</strong><span>Certificate Data Extraction &amp; Management System</span></div>
          </div>
        </header>
        <main className="certificate-db-main">
          <nav className="extracted-breadcrumb" aria-label="Breadcrumb"><button type="button" onClick={onBack}>Dashboard</button><span>/</span><span>Extracted Data</span></nav>
          <section className="database-title-row extracted-page-title"><div className="database-title-icon"><Database size={27} /></div><div><h1>Extracted Data</h1><p>Manage, view, import and export your extracted certificate records.</p></div><button className="back-button extracted-dashboard-back" onClick={onBack} type="button"><ArrowLeft size={15} /> Back to Dashboard</button></section>
          {loading ? <div className="database-message">Loading saved certificate types...</div> : loadError ? <div className="database-message database-error">{loadError}</div> : types.length === 0 ? <div className="database-message">No saved certificate records yet. Extract and save a certificate to see it here.</div> : (
            <section className="extracted-category-grid" aria-label="Certificate types">
              {types.map((type) => {
                const category = categoryCounts.find((item) => item.title === type);
                const count = category?.count || 0;
                const CategoryIcon = category?.icon || FileQuestion;
                return <button key={category?.id || type} type="button" className="extracted-type-card" onClick={() => selectCategory(category?.id)}>
                  <span className={`extracted-type-icon ${category?.id?.split(":")[0]}`}><CategoryIcon size={22} /></span>
                  <span className="extracted-type-name">{type}</span>
                  <span className="extracted-type-count">{count} {count === 1 ? "record" : "records"}</span>
                  <span className="extracted-type-description">{category?.description}</span>
                  <span className="extracted-type-link">View records →</span>
                </button>;
              })}
            </section>
          )}
        </main>
        {isExtractedDataPage && <footer className="extracted-page-footer"><GraduationCap size={24} /><div><strong>SMART ERP</strong><span>Certificate Data Extraction &amp; Management System</span><small>Scan • Extract • Manage • Secure</small><small>© 2026 SMART ERP. All rights reserved.</small></div></footer>}
      </div>
    );
  }

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

      </header>

      <main className="certificate-db-main">
        <nav className="extracted-breadcrumb" aria-label="Breadcrumb"><button type="button" onClick={onBack}>Dashboard</button><span>/</span><button type="button" onClick={() => changeCategory(null)}>Extracted Data</button><span>/</span><span>{titleForCategory}</span></nav>
        {/* Page title */}
        <section className="database-title-row">
          <div className="database-title-icon">
            <Database size={27} />
          </div>
          <div>
            <h1>{titleForCategory}</h1>
            <p>Manage, view, import and export this certificate category.</p>
            <span className="extracted-selected-count">{loading ? "Loading records..." : `${filteredRecords.length} ${filteredRecords.length === 1 ? "record" : "records"}`}</span>
          </div>
          <button className="back-button extracted-dashboard-back" onClick={() => changeCategory(null)} type="button"><ArrowLeft size={15} /> Back to Categories</button>
        </section>

        {/* Type filters, search, export controls */}
        <section className="database-toolbar">
          <button className="back-button extracted-category-back" onClick={() => changeCategory(null)} type="button"><ArrowLeft size={15} /> Back to Categories</button>

          <label className="database-search">
            <Search size={17} />
            <input
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              placeholder="Search student name or extracted certificate fields..."
            />
          </label>

          <div className="database-toolbar-actions">
            <input ref={importInputRef} type="file" accept=".csv,text/csv" hidden onChange={handleImportCSV} />
            <button className="export-button export-csv" onClick={() => importInputRef.current?.click()} type="button" disabled={importing}>
              <FileText size={15} /> {importing ? "Importing..." : "Import CSV"}
            </button>
            <button className="export-button export-pdf" onClick={handleExportPDF} type="button">
              <FileDown size={15} /> Export PDF
            </button>
            <button className="export-button export-excel" onClick={handleExportExcel} type="button">
              <FileSpreadsheet size={15} /> Export Excel
            </button>
            <button className="export-button export-csv" onClick={handleExportCSV} type="button">
              <FileText size={15} /> Export CSV
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
                                  {canManageRecord(row._record) && <>
                                  <button
                                    className="table-action-button"
                                    type="button"
                                    title="Edit certificate record"
                                    aria-label={`Edit ${getStudentName(row._record)} certificate record`}
                                    onClick={() => startEditingRecord(row._record)}
                                  >
                                    <Pencil size={15} />
                                  </button>
                                  <button
                                    className="table-action-button table-action-delete"
                                    type="button"
                                    title="Delete certificate record"
                                    aria-label={`Delete ${getStudentName(row._record)} certificate record`}
                                    disabled={deletingRecordId === row._record.id}
                                    onClick={() => handleDeleteRecord(row._record)}
                                  >
                                    {deletingRecordId === row._record.id ? <LoaderCircle size={15} className="database-spin" /> : <Trash2 size={15} />}
                                  </button>
                                </>}
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
          {importMessage && <p className="database-message">{importMessage}</p>}
          {databaseActionMessage && <p className={`database-message ${databaseActionError ? "database-error" : "database-success"}`} role={databaseActionError ? "alert" : "status"}>{databaseActionMessage}</p>}
        </section>
      </main>

      {isExtractedDataPage && <footer className="extracted-page-footer"><GraduationCap size={24} /><div><strong>SMART ERP</strong><span>Certificate Data Extraction &amp; Management System</span><small>Scan • Extract • Manage • Secure</small><small>© 2026 SMART ERP. All rights reserved.</small></div></footer>}

      {/* Footer: previous branded markup retained for layout compatibility. */}
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
      {editingRecord && editDraft && (
        <div className="certificate-detail-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !savingRecord) closeEditModal(); }}>
          <section ref={editModalRef} className="certificate-detail-modal certificate-edit-modal" role="dialog" aria-modal="true" aria-labelledby="certificate-edit-title" aria-describedby="certificate-edit-subtitle">
            <div className="certificate-detail-modal-header">
              <div><h2 id="certificate-edit-title">Edit Certificate Record</h2><p id="certificate-edit-subtitle">{getCertificateType(editingRecord)}</p></div>
              <button type="button" className="modal-close-button" aria-label="Close editor" disabled={savingRecord} onClick={closeEditModal}><X size={20} /></button>
            </div>
            <form onSubmit={handleSaveRecord}>
              <div className="certificate-detail-modal-body certificate-edit-body">
                <section className="certificate-edit-section" aria-labelledby="edit-student-heading">
                  <h3 id="edit-student-heading">Student Information</h3>
                  <div className="certificate-edit-section-grid">
                    <label className="certificate-edit-field"><span>Student Name</span><input autoComplete="off" maxLength={200} value={editDraft.student_name} onChange={(event) => setEditDraft((previous) => ({ ...previous, student_name: event.target.value }))} /></label>
                    {editableSections.find((section) => section.key === "student")?.fields.map(renderOcrEditField)}
                  </div>
                </section>
                <section className="certificate-edit-section" aria-labelledby="edit-certificate-heading">
                  <h3 id="edit-certificate-heading">Certificate Information</h3>
                  <div className="certificate-edit-section-grid">
                    <label className="certificate-edit-field"><span>Certificate Type</span><input required maxLength={160} value={editDraft.certificate_type} onChange={(event) => setEditDraft((previous) => ({ ...previous, certificate_type: event.target.value }))} /></label>
                    <label className="certificate-edit-field"><span>Certificate Name</span><input maxLength={200} value={editDraft.certificate_name} onChange={(event) => setEditDraft((previous) => ({ ...previous, certificate_name: event.target.value }))} /></label>
                    <label className="certificate-edit-field"><span>Extracted Date</span><input type="date" value={String(editDraft.date_extracted || "").slice(0, 10)} onChange={(event) => setEditDraft((previous) => ({ ...previous, date_extracted: event.target.value }))} /></label>
                    <label className="certificate-edit-field"><span>Status</span><input maxLength={80} value={editDraft.status} onChange={(event) => setEditDraft((previous) => ({ ...previous, status: event.target.value }))} /></label>
                    {editableSections.find((section) => section.key === "certificate")?.fields.map(renderOcrEditField)}
                  </div>
                </section>
                {editableSections.filter((section) => ["examination", "subjects", "additional"].includes(section.key) && section.fields.length > 0).map((section) => (
                  <section className="certificate-edit-section" key={section.key} aria-labelledby={`edit-${section.key}-heading`}>
                    <h3 id={`edit-${section.key}-heading`}>{section.title}</h3>
                    <div className="certificate-edit-section-grid">{section.fields.map(renderOcrEditField)}</div>
                  </section>
                ))}
              </div>
              {databaseActionError && databaseActionMessage && <p className="database-action-error" role="alert">{databaseActionMessage}</p>}
              <div className="certificate-detail-modal-footer">
                <button type="button" className="back-button" disabled={savingRecord} onClick={closeEditModal}>Cancel</button>
                <button type="submit" className="export-button export-pdf" disabled={savingRecord}><Save size={15} /> {savingRecord ? "Saving..." : "Save Changes"}</button>
              </div>
            </form>
          </section>
        </div>
      )}
      {importPreview && (
        <div className="certificate-detail-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !importing) setImportPreview(null); }}>
          <section className="certificate-detail-modal import-preview-modal" role="dialog" aria-modal="true" aria-labelledby="import-preview-title">
            <div className="certificate-detail-modal-header"><div><h2 id="import-preview-title">Review CSV import</h2><p>{importPreview.fileName} · {importPreview.rows.length} ready to import · {categoryTitle}</p></div><button type="button" className="modal-close-button" aria-label="Close import preview" onClick={() => !importing && setImportPreview(null)}><X size={20} /></button></div>
            <div className="import-preview-body">
              {importPreview.errors.length > 0 && <div className="import-validation-errors"><strong>Fix these issues before importing:</strong><ul>{importPreview.errors.map((error, index) => <li key={`${index}-${error}`}>{error}</li>)}</ul></div>}
              {importPreview.rows.length > 0 ? <><strong>Preview (first 5 records)</strong><div className="import-preview-table-wrap"><table className="certificate-details-table"><thead><tr><th>Student Name</th><th>Certificate Type</th><th>Fields</th></tr></thead><tbody>{importPreview.rows.slice(0, 5).map((record, index) => <tr key={`${record.student_name}-${index}`}><td>{record.student_name || "—"}</td><td>{record.certificate_type}</td><td>{Object.entries(record.ocr_result.fields).map(([key, value]) => `${key}: ${value}`).join(" · ")}</td></tr>)}</tbody></table></div></> : <p className="database-message">No valid rows are ready to import.</p>}
            </div>
            <div className="certificate-detail-modal-footer"><button type="button" className="back-button" onClick={() => setImportPreview(null)} disabled={importing}>Cancel</button><button type="button" className="export-button export-excel" onClick={confirmImport} disabled={importing || !importPreview.rows.length || importPreview.errors.length > 0}>{importing ? "Importing..." : `Import ${importPreview.rows.length} records`}</button></div>
          </section>
        </div>
      )}
    </div>
  );
}
