
import { useRef, useState } from "react";
import Webcam from "react-webcam";
import "./ScanUpload.css";

export default function ScanUpload({ onBack, onNext }) {
  const webcamRef = useRef(null);

  const [capturedImage, setCapturedImage] = useState(null);
  const [autoCrop, setAutoCrop] = useState(true);
  const [cameraError, setCameraError] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [rotation, setRotation] = useState(0);
  const [enhanced, setEnhanced] = useState(false);
  const [flashEnabled, setFlashEnabled] = useState(false);

  const videoConstraints = {
    facingMode: { ideal: "environment" },
    width: { ideal: 1280 },
    height: { ideal: 720 },
  };

  const handleCameraReady = () => {
    setCameraReady(true);
    setCameraError(false);
  };

  const handleCameraError = () => {
    setCameraReady(false);
    setCameraError(true);
  };

  const captureImage = () => {
    if (!webcamRef.current || !cameraReady) {
      alert("Camera is not ready. Please wait or retry.");
      return;
    }

    const imageSrc = webcamRef.current.getScreenshot();

    if (!imageSrc) {
      alert("Unable to scan certificate. Please try again.");
      return;
    }

    setCapturedImage(imageSrc);
  };

  const retakeImage = () => {
    setCapturedImage(null);
    setRotation(0);
    setEnhanced(false);
  };

  const handleContinue = () => {
    if (!capturedImage) {
      alert("Please scan the certificate first.");
      return;
    }

    onNext?.({
      image: capturedImage,
      autoCrop,
      source: "camera",
      rotation,
      enhanced,
    });
  };

  const retryCamera = () => {
    setCameraError(false);
    setCameraReady(false);
  };

  const rotateImage = () => {
    setRotation((previous) => (previous + 90) % 360);
  };

  return (
    <div className="scan-page">
      {/* FULL-WIDTH HEADER */}
      <header className="scan-header">
        <button
          type="button"
          className="scan-header-back"
          onClick={onBack}
          aria-label="Go back"
        >
          <svg viewBox="0 0 24 24" width="21" height="21" fill="none">
            <path
              d="M19 12H5M12 19l-7-7 7-7"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>

        <div className="scan-header-logo" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="28" height="28" fill="none">
            <path
              d="M7 3.5h7l5 5V21H7a2 2 0 0 1-2-2V5.5a2 2 0 0 1 2-2Z"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinejoin="round"
            />
            <path
              d="M14 3.5v5h5M8.5 13h7M8.5 16.5h7"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>

        <div className="scan-header-brand">SMART ERP</div>
      </header>

      {/* MAIN CONTENT */}
      <main className="scan-content">
        <section className="scan-page-heading">
          <div className="scan-step">STEP 3 OF 3</div>
          <h1>Scan Certificate</h1>
          <p>
            Position the certificate inside the frame and scan it using your
            camera.
          </p>
        </section>

        {/* CAMERA AND TOOLS */}
        <div className="capture-layout">
          <section className="camera-box">
            {!capturedImage ? (
              <div className="camera-wrapper">
                {!cameraError ? (
                  <>
                    <Webcam
                      key={cameraError ? "camera-retry" : "camera"}
                      ref={webcamRef}
                      audio={false}
                      screenshotFormat="image/jpeg"
                      screenshotQuality={0.95}
                      className="camera-feed"
                      videoConstraints={videoConstraints}
                      onUserMedia={handleCameraReady}
                      onUserMediaError={handleCameraError}
                      mirrored={false}
                    />

                    <div className="scanner-frame" aria-hidden="true">
                      <span className="corner tl" />
                      <span className="corner tr" />
                      <span className="corner bl" />
                      <span className="corner br" />
                    </div>

                    <div
                      className={`camera-status ${
                        cameraReady ? "ready" : ""
                      }`}
                    >
                      {cameraReady ? "● Camera Ready" : "Starting camera..."}
                    </div>
                  </>
                ) : (
                  <div className="camera-error">
                    <div className="camera-error-icon">📷</div>
                    <h3>Camera Access Required</h3>
                    <p>
                      Allow camera permission in your browser to scan the
                      certificate.
                    </p>
                    <button
                      type="button"
                      className="primary-btn"
                      onClick={retryCamera}
                    >
                      Retry Camera
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <div className="captured-wrapper">
                <img
                  src={capturedImage}
                  alt="Scanned certificate preview"
                  className={`captured-preview ${
                    enhanced ? "image-enhanced" : ""
                  }`}
                  style={{ transform: `rotate(${rotation}deg)` }}
                />
                <div className="scanned-label">✓ Certificate Scanned</div>
              </div>
            )}
          </section>

          {/* CAMERA TOOLS */}
          <aside className="camera-tools">
            <div className="tool-card">
              <span>Auto Crop</span>
              <label className="switch">
                <input
                  type="checkbox"
                  checked={autoCrop}
                  onChange={() => setAutoCrop((previous) => !previous)}
                />
                <span className="slider" />
              </label>
            </div>

            <button
              type="button"
              className="tool-card tool-button"
              onClick={rotateImage}
              disabled={!capturedImage}
            >
              <span className="tool-icon">↻</span>
              <span>Rotate</span>
            </button>

            <button
              type="button"
              className={`tool-card tool-button ${
                enhanced ? "tool-active" : ""
              }`}
              onClick={() => setEnhanced((previous) => !previous)}
              disabled={!capturedImage}
            >
              <span className="tool-icon">✨</span>
              <span>{enhanced ? "Enhanced" : "Enhance"}</span>
            </button>

            <button
              type="button"
              className={`tool-card tool-button ${
                flashEnabled ? "tool-active" : ""
              }`}
              onClick={() => setFlashEnabled((previous) => !previous)}
              title="Flash support depends on the camera and browser"
            >
              <span className="tool-icon">⚡</span>
              <span>{flashEnabled ? "Flash On" : "Flash"}</span>
            </button>
          </aside>
        </div>

        {/* ACTION BUTTONS */}
        <div className="capture-actions">
          <button
            type="button"
            className="outline-btn"
            onClick={onBack}
          >
            ← Back
          </button>

          {!capturedImage ? (
            <button
              type="button"
              className="primary-btn"
              onClick={captureImage}
              disabled={!cameraReady || cameraError}
            >
              📷 Scan Certificate
            </button>
          ) : (
            <>
              <button
                type="button"
                className="outline-btn"
                onClick={retakeImage}
              >
                ↻ Retake
              </button>

              <button
                type="button"
                className="primary-btn"
                onClick={handleContinue}
              >
                Continue →
              </button>
            </>
          )}
        </div>
      </main>

      {/* FOOTER */}
      <footer className="scan-footer">
        <div className="scan-footer-content">
          <span className="scan-footer-logo" aria-hidden="true">
            🎓
          </span>
          <span className="scan-footer-title">
            SMART ERP CERTIFICATE DATA EXTRACTION AND MANAGEMENT SYSTEM
          </span>
        </div>
      </footer>
    </div>
  );
}
