import { useState, useEffect } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Search,
  X,
  GraduationCap,
  Landmark,
  Contact,
  FileText,
  ShieldCheck,
  CheckCircle2,
  FolderOpen,
  LockKeyhole,
} from "lucide-react";

import "./CertificateType.css";

/* CATEGORY INFORMATION */
const categoryInfo = {
  education: {
    title: "Education Certificates",
    subtitle: "Select the education document you want to upload.",
    eyebrow: "ACADEMIC DOCUMENTS",
    color: "blue",
    icon: GraduationCap,
  },

  government: {
    title: "Government Certificates",
    subtitle: "Select the government certificate you want to upload.",
    eyebrow: "GOVERNMENT DOCUMENTS",
    color: "orange",
    icon: Landmark,
  },

  identity: {
    title: "Identity Documents",
    subtitle: "Select the identity document you want to upload.",
    eyebrow: "IDENTITY VERIFICATION",
    color: "teal",
    icon: Contact,
  },
};

/* CERTIFICATE OPTIONS */
const certificateGroups = {
  education: [
    {
      title: "School Certificates",
      color: "blue",
      options: [
        "State Board 10th Marksheet",
        "State Board 12th Marksheet",
        "State Board 10th Transfer Certificate",
        "State Board 12th Transfer Certificate",
        "CBSE 10th Marksheet",
        "CBSE 12th Marksheet",
        "CBSE 10th Transfer Certificate",
        "CBSE 12th Transfer Certificate",
      ],
    },
    {
      title: "Diploma Certificates",
      color: "purple",
      options: [
        "Diploma Certificate",
        "Diploma Marksheet",
      ],
    },
    {
      title: "Degree Certificates",
      color: "green",
      options: [
        "Degree Certificate",
        "Degree Marksheet",
      ],
    },
  ],

  government: [
    {
      title: "Government Certificates",
      color: "orange",
      options: [
        "Community Certificate",
        "Income Certificate",
        "Nativity Certificate",
        "Birth Certificate",
      ],
    },
  ],

  identity: [
    {
      title: "Identity Documents",
      color: "teal",
      options: [
        "Aadhaar Card",
      ],
    },
  ],
};

/* NORMALIZE CATEGORY VALUES */
function normalizeCategory(value) {
  const normalized = String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ");

  const aliases = {
    education: "education",
    "education certificate": "education",
    "education certificates": "education",
    academic: "education",
    academics: "education",

    government: "government",
    "government certificate": "government",
    "government certificates": "government",

    identity: "identity",
    "identity document": "identity",
    "identity documents": "identity",
    "identity certificate": "identity",
  };

  return aliases[normalized] || null;
}

export default function CertificateType({
  certificate = "education",
  onBack,
  onNext,
}) {
  const category = normalizeCategory(certificate);
  const info = category ? categoryInfo[category] : null;
  const groups = category ? certificateGroups[category] : [];

  const [selected, setSelected] = useState("");
  const [search, setSearch] = useState("");

  /* Clear selection when category changes */
  useEffect(() => {
    setSelected("");
    setSearch("");
  }, [category]);

  const filteredGroups = groups
    .map((group) => ({
      ...group,
      options: group.options.filter((option) =>
        option.toLowerCase().includes(search.trim().toLowerCase())
      ),
    }))
    .filter((group) => group.options.length > 0);

  const totalOptions = groups.reduce(
    (total, group) => total + group.options.length,
    0
  );

  const filteredCount = filteredGroups.reduce(
    (total, group) => total + group.options.length,
    0
  );

  function handleContinue() {
    if (!selected || typeof onNext !== "function") return;
    onNext(selected);
  }

  /* Invalid or missing category */
  if (!info) {
    return (
      <div className="ct-page">
        <div className="ct-container">
          <button className="ct-back" onClick={onBack} type="button">
            <ArrowLeft size={18} />
            Back to categories
          </button>

          <div className="ct-empty-category">
            <FolderOpen size={42} />
            <h2>Category not recognized</h2>
            <p>
              Please go back and select Education, Government, or Identity.
            </p>
            <button
              className="ct-continue"
              type="button"
              onClick={onBack}
            >
              Select Category <ArrowRight size={18} />
            </button>
          </div>
        </div>
      </div>
    );
  }

  const HeroIcon = info.icon;

  return (
    <div className="ct-page">
      <div className="ct-container">
        {/* TOP NAVIGATION */}
        <header className="ct-topbar">
          <button
            className="ct-back"
            type="button"
            onClick={onBack}
          >
            <ArrowLeft size={18} />
            <span>Back</span>
          </button>

          <div className="ct-breadcrumb">
            <span>Certificates</span>
            <span className="ct-breadcrumb-separator">/</span>
            <span>{info.title}</span>
          </div>

          <div className="ct-secure-label">
            <LockKeyhole size={15} />
            <span>Secure Upload</span>
          </div>
        </header>

        {/* HERO SECTION */}
        <section className={`ct-hero ct-hero-${info.color}`}>
          <div className="ct-hero-content">
            <div className="ct-eyebrow">
              <HeroIcon size={16} />
              {info.eyebrow}
            </div>

            <h1>Choose Certificate Type</h1>

            <p>{info.subtitle}</p>

            <div className="ct-trust">
              <ShieldCheck size={17} />
              <span>Your documents stay protected</span>
            </div>
          </div>

          <div className={`ct-hero-art ct-art-${info.color}`}>
            <div className="ct-art-orbit">
              <div className="ct-art-document">
                <FileText
                  className="ct-art-main-icon"
                  size={54}
                  strokeWidth={1.5}
                />
                <span className="ct-art-line" />
                <span className="ct-art-line" />
                <span className="ct-art-line short" />
                <CheckCircle2 className="ct-art-check" size={25} />
              </div>

              <div className="ct-art-floating">
                <HeroIcon size={23} />
              </div>
            </div>

            <span className="ct-art-caption">
              Smart ERP Document Center
            </span>
          </div>
        </section>

        {/* CERTIFICATE SELECTION */}
        <section className="ct-selection">
          <div className="ct-section-heading">
            <div>
              <span className="ct-section-label">DOCUMENT LIBRARY</span>
              <h2>Select your document</h2>
              <p>Choose one certificate to continue.</p>
            </div>

            <span className="ct-count">
              {totalOptions} documents
            </span>
          </div>

          {/* SEARCH */}
          <div className="ct-search">
            <Search size={19} />

            <input
              type="text"
              placeholder="Search certificate type..."
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              aria-label="Search certificate types"
            />

            {search && (
              <button
                type="button"
                className="ct-clear-search"
                onClick={() => setSearch("")}
                aria-label="Clear search"
              >
                <X size={17} />
              </button>
            )}
          </div>

          {/* GROUPS */}
          <div className="ct-groups">
            {filteredGroups.map((group) => (
              <section className="ct-group" key={group.title}>
                <div className="ct-group-heading">
                  <div className={`ct-group-icon ct-icon-${group.color}`}>
                    <FolderOpen size={19} />
                  </div>

                  <h3 className="ct-group-title">
                    {group.title}
                  </h3>

                  <span className="ct-group-count">
                    {group.options.length}
                  </span>
                </div>

                <div className="ct-options">
                  {group.options.map((option) => {
                    const isSelected = selected === option;

                    return (
                      <button
                        key={option}
                        type="button"
                        className={`ct-option ${
                          isSelected ? "selected" : ""
                        }`}
                        onClick={() => setSelected(option)}
                        aria-pressed={isSelected}
                      >
                        <span
                          className={`ct-option-icon ct-option-${group.color}`}
                        >
                          <FileText size={20} />
                        </span>

                        <span className="ct-option-name">
                          {option}
                        </span>

                        <span className="ct-option-check">
                          {isSelected ? (
                            <CheckCircle2 size={21} />
                          ) : (
                            <span className="ct-radio" />
                          )}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </section>
            ))}

            {filteredGroups.length === 0 && (
              <div className="ct-empty">
                <Search size={35} />
                <h3>No certificates found</h3>
                <p>
                  Try another search term or clear your search.
                </p>

                <button
                  type="button"
                  onClick={() => setSearch("")}
                >
                  Clear Search
                </button>
              </div>
            )}
          </div>
        </section>

        {/* BOTTOM ACTION BAR */}
        <footer className="ct-footer">
          <div className="ct-footer-inner">
            <div className="ct-footer-summary">
              <div className="ct-footer-icon">
                <FileText size={20} />
              </div>

              <div>
                <span className="ct-footer-label">
                  SELECTED DOCUMENT
                </span>

                <strong>
                  {selected || "No document selected"}
                </strong>
              </div>
            </div>

            <button
              className="ct-continue"
              type="button"
              onClick={handleContinue}
              disabled={!selected}
            >
              Continue
              <ArrowRight size={18} />
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
