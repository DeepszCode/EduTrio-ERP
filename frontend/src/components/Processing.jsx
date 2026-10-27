
import { useEffect, useRef, useState } from "react";
import "./Processing.css";

const API_BASE_URL = (
  import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"
).replace(/\/+$/, "");

async function getActiveSession(supabase, forceRefresh = false) {
  if (!supabase) {
    throw new Error("Supabase is not configured. Check the frontend environment settings.");
  }

  if (!forceRefresh) {
    const { data, error } = await supabase.auth.getSession();
    if (!error && data.session?.access_token) return data.session;
  }

  const { data, error } = await supabase.auth.refreshSession();
  if (error || !data.session?.access_token) {
    throw new Error("Your login session has expired. Please sign in again.");
  }
  return data.session;
}

function getDocType(certificate) {
  const name = (
    certificate?.certificateType ||
    certificate?.certificate_name ||
    certificate?.name ||
    certificate ||
    ""
  )
    .toString()
    .toLowerCase()
    .replace(/[-_/]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (name.includes("aadhaar") || name.includes("aadhar")) {
    return "aadhaar_card";
  }

  if (name.includes("community")) return "community_certificate";
  if (name.includes("income")) return "income_certificate";
  if (name.includes("nativity")) return "nativity_certificate";
  if (name.includes("birth")) return "birth_certificate";

  if (name.includes("diploma") && name.includes("semester")) {
    return "diploma_semester_marksheet";
  }

  if (name.includes("diploma") && name.includes("marksheet")) {
    return "diploma_semester_marksheet";
  }

  if (name.includes("diploma")) return "diploma_certificate";
  if (name.includes("degree")) return "degree_certificate";

  if (
    name.includes("transfer certificate") ||
    name === "tc" ||
    name.includes(" tc")
  ) {
    if (name.includes("cbse") && name.includes("10")) {
      return "cbse_10th_tc";
    }

    if (name.includes("cbse") && name.includes("12")) {
      return "cbse_12th_tc";
    }

    if (name.includes("10")) return "state_board_10th_tc";
    if (name.includes("12")) return "state_board_12th_tc";

    return "transfer_certificate";
  }

  if (name.includes("cbse") && name.includes("10")) {
    return "cbse_10th_marksheet";
  }

  if (name.includes("cbse") && name.includes("12")) {
    return "cbse_12th_marksheet";
  }

  if (name.includes("state board") && name.includes("10")) {
    return "state_board_10th";
  }

  if (name.includes("state board") && name.includes("12")) {
    return "state_board_12th";
  }

  return null;
}

export default function Processing({
  certificate,
  documentData,
  supabase,
  onBack,
  onNext,
}) {
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const [status, setStatus] = useState(
    "Connecting to the OCR backend..."
  );

  const onNextRef = useRef(onNext);

  useEffect(() => {
    onNextRef.current = onNext;
  }, [onNext]);

  const steps = [
    "Image Preprocessing",
    "OCR Processing",
    "Document Classification",
    "Data Extraction",
    "Validation",
  ];
  const activeStep = progress >= 100 ? 4 : progress < 20 ? 0 : progress < 40 ? 1 : progress < 60 ? 2 : progress < 80 ? 3 : 4;

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    let navigationTimer = null;

    const controller = new AbortController();

    async function extractCertificate() {
      try {
        setError("");
        setProgress(5);
        setStatus("Connecting to the OCR backend...");

        const file = documentData?.file;

        if (!file) {
          throw new Error(
            "Certificate file missing. Please go back and upload it again."
          );
        }

        const docType = getDocType(certificate);

        if (!docType) {
          throw new Error(
            "Unsupported certificate type. Please go back and select the certificate again."
          );
        }

        setStatus("Uploading document for OCR processing...");

        timer = setInterval(() => {
          setProgress((previous) => Math.min(previous + 2, 90));
        }, 300);

        const formData = new FormData();
        formData.append("file", file);

        const sendRequest = (accessToken) => fetch(
          `${API_BASE_URL}/extract/${encodeURIComponent(docType)}`,
          {
            method: "POST",
            body: formData,
            headers: { Authorization: `Bearer ${accessToken}` },
            signal: controller.signal,
          }
        );

        let session = await getActiveSession(supabase);
        let response = await sendRequest(session.access_token);
        if (response.status === 401) {
          session = await getActiveSession(supabase, true);
          response = await sendRequest(session.access_token);
        }
        if (response.status === 401) {
          await supabase.auth.signOut();
          throw new Error("Your login session is no longer valid. Please sign in again.");
        }

        const responseText = await response.text();
        let result;

        try {
          result = JSON.parse(responseText);
        } catch {
          throw new Error(
            `Backend returned an invalid response (HTTP ${response.status}). Check the backend terminal.`
          );
        }

        if (!response.ok) {
          const detail =
            typeof result.detail === "string"
              ? result.detail
              : JSON.stringify(result.detail || result);

          throw new Error(
            detail || `OCR request failed (${response.status}).`
          );
        }

        if (cancelled) return;

        clearInterval(timer);
        timer = null;

        setProgress(100);
        setStatus("Certificate data extracted successfully!");

        navigationTimer = setTimeout(() => {
          if (!cancelled) {
            onNextRef.current?.(result);
          }
        }, 400);
      } catch (err) {
        if (cancelled || err.name === "AbortError") return;

        if (timer) {
          clearInterval(timer);
          timer = null;
        }

        const message =
          err instanceof TypeError || /failed to fetch/i.test(err.message || "")
            ? `Cannot connect to the OCR backend at ${API_BASE_URL}. Check that FastAPI is running and VITE_API_URL points to this computer.`
            : err.message || "Unable to process the certificate. Please try again.";
        setError(message);

        setStatus("Processing failed");
      }
    }

    extractCertificate();

    return () => {
      cancelled = true;
      controller.abort();

      if (timer) clearInterval(timer);
      if (navigationTimer) clearTimeout(navigationTimer);
    };
  }, [certificate, documentData, supabase]);

  return (
    <div className="processing-page">

      {/* BLUE HEADER: LOGO AND TITLE ON THE LEFT */}
      <header className="processing-header">
        <div className="processing-brand">
          <div className="processing-brand-icon">
            <svg
              viewBox="0 0 24 24"
              width="30"
              height="30"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <path d="M14 2v6h6" />
              <path d="M8 13h8" />
              <path d="M8 17h8" />
              <path d="M8 9h2" />
            </svg>
          </div>

          <div className="processing-brand-text">
            <h2>SMART ERP</h2>
            <p>
              Certificate Data Extraction and Management System
            </p>
          </div>
        </div>
      </header>

      {/* CENTERED PROCESSING CONTENT */}
      <main className="processing-container">
        <div className="processing-step-label">
          STEP 3 OF 3
        </div>

        <h1>Processing Document</h1>

        <p className="processing-status">{status}</p>

        <div className="process-steps">
          {steps.map((step, index) => (
            <div className="process-step" key={step}>
              <div
                className={
                  index < activeStep
                    ? "step-icon completed"
                    : index === activeStep
                    ? "step-icon active"
                    : "step-icon"
                }
              >
                {index < activeStep ? "✓" : index + 1}
              </div>

              <span>{step}</span>
            </div>
          ))}
        </div>

        <div
          className={`loader-ring ${error ? "loader-error" : ""}`}
          aria-label={error ? "Processing failed" : "Processing"}
        />

        <h2>{error ? "Processing Error" : steps[activeStep]}</h2>

        <div
          className="progress-wrapper"
          role="progressbar"
          aria-valuenow={progress}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="progress-fill"
            style={{ width: `${progress}%` }}
          />
        </div>

        <div className="progress-value">{progress}%</div>

        {error && (
          <div className="processing-error" role="alert">
            <p>{error}</p>

            <button
              type="button"
              className="processing-back-btn"
              onClick={onBack}
            >
              ← Go Back
            </button>
          </div>
        )}
      </main>

      {/* BLUE FOOTER */}
      <footer className="processing-footer">
        <div className="processing-footer-content">
          <span
            className="processing-footer-logo"
            aria-label="Graduation cap"
          >
            🎓
          </span>

          <p className="processing-footer-title">
            SMART ERP CERTIFICATE DATA EXTRACTION AND MANAGEMENT SYSTEM
          </p>
        </div>
      </footer>
    </div>
  );
}
