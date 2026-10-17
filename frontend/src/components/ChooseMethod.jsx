
import React from "react";
import "./ChooseMethod.css";

export default function ChooseMethod({ onBack, onNext, onNavigate }) {
  const handleCamera = () => {
    if (onNavigate) {
      onNavigate("scanUpload");
    } else if (onNext) {
      onNext("camera");
    }
  };

  const handleUpload = () => {
    if (onNavigate) {
      onNavigate("upload");
    } else if (onNext) {
      onNext("upload");
    }
  };

  return (
    <div className="choose-method-page">

      {/* HEADER - SAME STYLE AS PROCESSING PAGE */}
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
            <p>Certificate Data Extraction and Management System</p>
          </div>
        </div>
      </header>

      {/* CENTERED MAIN CONTENT */}
      <main className="choose-method-main">
        <section className="choose-method-content">

          {/* PAGE HEADING */}
          <div className="choose-method-heading">
            <span className="heading-label">STEP 2 OF 3</span>
            <h1>Choose Method</h1>
            <p>Choose how you want to add your certificate.</p>
          </div>

          {/* METHOD CARDS */}
          <div className="method-grid">

            {/* CAMERA CARD */}
            <button
              type="button"
              className="method-card"
              onClick={handleCamera}
            >
              <div className="method-icon camera-icon">
                <svg
                  viewBox="0 0 24 24"
                  width="38"
                  height="38"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.7"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M4 7h3l1.5-2h7L17 7h3a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z" />
                  <circle cx="12" cy="13" r="4" />
                  <circle cx="12" cy="13" r="1.5" />
                </svg>
              </div>

              <h2>Scan with Camera</h2>

              <p>
                Use your camera to scan the certificate directly.
              </p>

              <span className="method-action">
                Open Camera <span aria-hidden="true">→</span>
              </span>
            </button>

            {/* UPLOAD CARD */}
            <button
              type="button"
              className="method-card"
              onClick={handleUpload}
            >
              <div className="method-icon upload-icon">
                <svg
                  viewBox="0 0 24 24"
                  width="38"
                  height="38"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.7"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6H10l2 2h7.5A1.5 1.5 0 0 1 21 9.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z" />
                  <path d="M3 10h18" />
                </svg>
              </div>

              <h2>Upload Document</h2>

              <p>
                Select an existing certificate image or document
                from your computer.
              </p>

              <span className="method-action">
                Choose File <span aria-hidden="true">→</span>
              </span>
            </button>

          </div>

          {/* BACK BUTTON */}
          <div className="choose-method-actions">
            <button
              type="button"
              className="method-back-button"
              onClick={onBack}
            >
              <span aria-hidden="true">←</span> Back
            </button>
          </div>

        </section>
      </main>

      {/* FOOTER */}
      <footer className="choose-method-footer">
        <div className="choose-method-footer-content">
          <span className="choose-method-footer-logo" aria-hidden="true">
            🎓
          </span>

          <span className="choose-method-footer-divider" />

          <p>
            SMART ERP CERTIFICATE DATA EXTRACTION AND MANAGEMENT SYSTEM
          </p>
        </div>
      </footer>

    </div>
  );
}
