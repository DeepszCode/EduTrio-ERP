import { useState } from "react";
import {
  FileText,
  Home,
  LayoutDashboard,
  GraduationCap,
  Landmark,
  IdCard,
  ChevronRight,
  ArrowLeft,
  ArrowRight,
} from "lucide-react";

import "./CertificateCategory.css";

const categories = [
  {
    id: "education",
    title: "Education",
    description: "School, Diploma & Degree certificates",
    icon: GraduationCap,
    iconClass: "education-icon",
  },
  {
    id: "government",
    title: "Government",
    description: "Community, Income, Nativity, Birth certificates",
    icon: Landmark,
    iconClass: "government-icon",
  },
  {
    id: "identity",
    title: "Identity",
    description: "Aadhaar Card",
    icon: IdCard,
    iconClass: "identity-icon",
  },
];

export default function CertificateCategory({
  onBack,
  onNext,
  onNavigate,
  onHome,
}) {
  const [selectedCategory, setSelectedCategory] = useState("education");

  const handleBack = () => {
    if (typeof onBack === "function") {
      onBack();
    }
  };

  const handleHome = () => {
    if (typeof onHome === "function") {
      onHome();
    } else if (typeof onNavigate === "function") {
      onNavigate("welcome");
    }
  };

  const handleDashboard = () => {
    if (typeof onNavigate === "function") {
      onNavigate("dashboard");
    }
  };

  const handleNext = () => {
    if (!selectedCategory) {
      alert("Please select a certificate category.");
      return;
    }

    if (typeof onNext === "function") {
      onNext(selectedCategory);
      return;
    }

    if (typeof onNavigate === "function") {
      onNavigate("certificateType");
      return;
    }

    console.error("Certificate navigation handler is missing.");
  };

  return (
    <div className="cc-page">
      <header className="cc-header">
        <div className="cc-brand">
          <div className="cc-brand-icon">
            <FileText size={30} strokeWidth={1.8} />
          </div>

          <div className="cc-brand-text">
            <h2>SMART ERP</h2>
            <p>
              Certificate Data Extraction and Management System
            </p>
          </div>
        </div>

        <nav className="cc-navigation">
          <button
            type="button"
            className="cc-nav-link"
            onClick={handleHome}
          >
            <Home size={18} />
            <span>Home</span>
          </button>

          <button
            type="button"
            className="cc-nav-link"
            onClick={handleDashboard}
          >
            <LayoutDashboard size={18} />
            <span>Dashboard</span>
          </button>
        </nav>
      </header>

      <main className="cc-main">
        <section className="cc-content">
          <div className="cc-heading">
            <span className="cc-heading-line" />
            <h1>Select Certificate Category</h1>
            <p>
              Choose the category that matches your certificate type.
            </p>
          </div>

          <div className="cc-category-grid">
            {categories.map((category) => {
              const Icon = category.icon;
              const isSelected = selectedCategory === category.id;

              return (
                <button
                  type="button"
                  key={category.id}
                  className={`cc-category-card ${
                    isSelected ? "cc-selected" : ""
                  }`}
                  onClick={() => setSelectedCategory(category.id)}
                  aria-pressed={isSelected}
                >
                  <div
                    className={`cc-category-icon ${category.iconClass}`}
                  >
                    <Icon size={32} strokeWidth={1.8} />
                  </div>

                  <div className="cc-card-text">
                    <h2>{category.title}</h2>
                    <p>{category.description}</p>
                  </div>

                  <ChevronRight
                    className="cc-card-arrow"
                    size={25}
                    strokeWidth={2}
                  />

                  {isSelected && (
                    <span className="cc-selected-indicator">
                      Selected
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </section>

        <div className="cc-actions">
          <button
            type="button"
            className="cc-back-button"
            onClick={handleBack}
          >
            <ArrowLeft size={18} />
            <span>Back</span>
          </button>

          <button
            type="button"
            className="cc-next-button"
            onClick={handleNext}
          >
            <span>Next</span>
            <ArrowRight size={18} />
          </button>
        </div>
      </main>

      <footer className="cc-footer">
        <FileText size={22} />
        <span className="cc-footer-divider" />
        <p>
          SMART ERP CERTIFICATE DATA EXTRACTION AND MANAGEMENT SYSTEM
        </p>
      </footer>
    </div>
  );
}