
import { useState, useEffect, useCallback, useRef } from "react";
import { supabase } from "./lib/supabase";
import { canAccessPage, normalizeRole } from "./auth/roles";

/* MAIN PAGES */
import WelcomePage from "./components/welcomePage";
import LoginPage, { PasswordResetPage } from "./components/LoginPage";
import Dashboard from "./components/Dashboard";

/* CERTIFICATE CATEGORY AND TYPE */
import CertificateCategory from "./components/CertificateCategory";
import CertificateType from "./components/CertificateType";

/* CERTIFICATE PROCESSING */
import ChooseMethod from "./components/ChooseMethod";
import ScanUpload from "./components/ScanUpload";
import UploadPage from "./components/UploadPage";
import Processing from "./components/Processing";
import ExtractedData from "./components/ExtractedData";
import CertificateDatabase from "./components/CertificateDatabase";

/* DASHBOARD MENU PAGES */
import MyCertificates from "./components/MyCertificates";
import Students from "./components/Students";
import Search from "./components/Search";
import Reports from "./components/Reports";
import AIAssistant from "./components/AIAssistant";
import { AIAssistantDock } from "./components/AIAssistant";
import { AIConversationProvider, useAIConversation } from "./components/AIConversationContext";
import Settings from "./components/Settings";
import AdminUsers from "./components/AdminUsers";

/* PAGE CONSTANTS */
const PAGES = {
  WELCOME: "welcome",
  LOGIN: "login",
  RESET_PASSWORD: "resetPassword",
  DASHBOARD: "dashboard",
  CERTIFICATE_CATEGORY: "certificateCategory",
  CERTIFICATE_TYPE: "certificateType",
  CHOOSE_METHOD: "chooseMethod",
  SCAN_UPLOAD: "scanUpload",
  UPLOAD: "upload",
  PROCESSING: "processing",
  EXTRACTED_DATA: "extractedData",
  EXTRACTED_DATA_MENU: "extractedDataMenu",
  CERTIFICATE_DATABASE: "certificateDatabase",
  MY_CERTIFICATES: "myCertificates",
  STUDENTS: "students",
  SEARCH: "search",
  REPORTS: "reports",
  AI_ASSISTANT: "aiAssistant",
  SETTINGS: "settings",
  USER_MANAGEMENT: "userManagement",
};

const VALID_PAGES = new Set(Object.values(PAGES));
const API_BASE_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, "");

function getRequestedPage() {
  const requested = new URLSearchParams(window.location.search).get("page");
  return requested && VALID_PAGES.has(requested) && ![PAGES.WELCOME, PAGES.LOGIN].includes(requested)
    ? requested
    : null;
}

function updatePageUrl(page, replace = false) {
  const url = new URL(window.location.href);
  if (page === PAGES.WELCOME || page === PAGES.LOGIN) url.searchParams.delete("page");
  else url.searchParams.set("page", page);
  if (replace) window.history.replaceState({ smartErpPage: page }, "", url.toString());
  else window.history.pushState({ smartErpPage: page }, "", url.toString());
}

/* CERTIFICATE TYPE HELPER */
const getCertificateType = (certificate, ocrResult) => {
  if (typeof certificate === "string") {
    return certificate;
  }

  return (
    certificate?.certificateType ||
    certificate?.name ||
    certificate?.type ||
    ocrResult?.doc_type ||
    "Unknown"
  );
};

/* STUDENT NAME HELPER */
const getStudentName = (ocrResult) => {
  const fields = Array.isArray(ocrResult?.fields)
    ? Object.fromEntries(
        ocrResult.fields
          .filter((field) => field && typeof field === "object")
          .map((field) => [
            field.field || field.key || field.name,
            field.value,
          ])
      )
    : ocrResult?.fields || ocrResult?.data || {};

  return (
    fields.student_name ||
    fields.candidate_name ||
    fields.studentName ||
    fields.candidateName ||
    fields.name ||
    ""
  );
};

/* FILE NAME HELPER */
const getCertificateName = (documentData) => {
  const fileName =
    documentData?.file?.name ||
    documentData?.fileName ||
    documentData?.filename ||
    documentData?.name;

  return typeof fileName === "string" && fileName.trim()
    ? fileName
    : "Certificate";
};

function AppContent() {
  const { setCompactOpen, resetConversation } = useAIConversation();
  /* APPLICATION STATE */
  const [currentPage, setCurrentPage] = useState(PAGES.WELCOME);
  const [user, setUser] = useState(null);
  const [assignedRole, setAssignedRole] = useState(null);
  const [roleError, setRoleError] = useState("");
  const passwordRecoveryPending = useRef(false);
  const [authLoading, setAuthLoading] = useState(true);
  const [savingCertificate, setSavingCertificate] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [globalSearchQuery, setGlobalSearchQuery] = useState("");
  const invitationAcceptancePending = useRef(
    new URLSearchParams(window.location.search).get("flow") === "student-invite"
  );
  const staffRegistrationPending = useRef(
    new URLSearchParams(window.location.search).get("flow") === "staff-register"
  );

  const [selectedCategory, setSelectedCategory] = useState(null);
  const [selectedCertificate, setSelectedCertificate] = useState(null);
  const [selectedExtractedType, setSelectedExtractedType] = useState(null);
  const [documentData, setDocumentData] = useState(null);

  const canEnterPage = useCallback((role, page) => {
    if (!canAccessPage(role, page)) return false;
    if (page === PAGES.CERTIFICATE_TYPE) return Boolean(selectedCategory);
    if ([PAGES.CHOOSE_METHOD, PAGES.SCAN_UPLOAD, PAGES.UPLOAD].includes(page)) {
      return Boolean(selectedCertificate);
    }
    if (page === PAGES.PROCESSING) return Boolean(selectedCertificate && documentData);
    if (page === PAGES.EXTRACTED_DATA) return Boolean(selectedCertificate && documentData?.ocrResult);
    if (page === PAGES.CERTIFICATE_DATABASE) return Boolean(selectedCertificate && documentData?.ocrResult);
    return true;
  }, [selectedCategory, selectedCertificate, documentData]);

  /* RESET CERTIFICATE WORKFLOW */
  const resetCertificateData = useCallback(() => {
    setSelectedCategory(null);
    setSelectedCertificate(null);
    setDocumentData(null);
  }, []);

  /* RESTORE AND MONITOR AUTH SESSION */
  useEffect(() => {
    if (!supabase) {
      setAuthLoading(false);
      return undefined;
    }

    let active = true;

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event, session) => {
      if (!active) return;

      if (event === "SIGNED_IN" || event === "SIGNED_OUT") {
        resetConversation();
        setCompactOpen(false);
      }

      if (event === "SIGNED_OUT") {
        setUser(null);
        setAssignedRole(null);
        resetCertificateData();
        setCurrentPage(PAGES.WELCOME);
        updatePageUrl(PAGES.WELCOME);
      }

      if (event === "PASSWORD_RECOVERY" && session?.user) {
        passwordRecoveryPending.current = true;
        setUser(session.user);
        setCurrentPage(PAGES.RESET_PASSWORD);
      }
      if (event === "SIGNED_IN" && invitationAcceptancePending.current && session?.user) {
        setUser(session.user);
        setCurrentPage(PAGES.RESET_PASSWORD);
      }
    });

    const restoreSession = async () => {
      try {
        const {
          data: { session },
          error,
        } = await supabase.auth.getSession();

        if (!active) return;

        if (error) {
          console.error("Session restore failed:", error.message);
          return;
        }

        if (session?.user) {
          if (invitationAcceptancePending.current) {
            setUser(session.user);
            setCurrentPage(PAGES.RESET_PASSWORD);
            return;
          }
          if (passwordRecoveryPending.current) {
            setUser(session.user);
            setCurrentPage(PAGES.RESET_PASSWORD);
            return;
          }
          const expectedRole = staffRegistrationPending.current
            ? "staff"
            : window.sessionStorage.getItem("smart-erp-login-role");
          try {
            await acceptAuthenticatedUser(session, expectedRole);
          } catch (roleCheckError) {
            console.error("Session role verification failed:", roleCheckError);
            setRoleError(roleCheckError?.message || "Unable to verify your account permissions.");
            setUser(null);
            setAssignedRole(null);
            setCurrentPage(PAGES.LOGIN);
          }
        } else {
          setUser(null);
          setAssignedRole(null);
          if (invitationAcceptancePending.current) {
            setRoleError("This Student invitation link is invalid or expired. Ask Staff to send a new invitation.");
            setCurrentPage(PAGES.LOGIN);
          } else if (staffRegistrationPending.current) {
            setRoleError("Confirm your Staff registration email, then sign in again as Staff.");
            setCurrentPage(PAGES.LOGIN);
          } else {
            setCurrentPage(PAGES.WELCOME);
          }
        }
      } catch (error) {
        console.error("Session error:", error);
      } finally {
        if (active) {
          setAuthLoading(false);
        }
      }
    };

    restoreSession();

    return () => {
      active = false;
      subscription.unsubscribe();
    };
  }, [resetCertificateData, resetConversation, setCompactOpen]);

  useEffect(() => {
    const handlePageHistory = () => {
      const requested = getRequestedPage();
      if (!requested) {
        setCurrentPage(user ? PAGES.DASHBOARD : PAGES.WELCOME);
        return;
      }
      if (!user) {
        setCurrentPage(PAGES.LOGIN);
        return;
      }
      const cameFromAppNavigation = window.history.state?.smartErpPage === requested;
      const page = cameFromAppNavigation && canEnterPage(assignedRole, requested)
        ? requested
        : PAGES.DASHBOARD;
      if (page !== requested) updatePageUrl(page, true);
      setCurrentPage(page);
    };
    window.addEventListener("popstate", handlePageHistory);
    return () => window.removeEventListener("popstate", handlePageHistory);
  }, [assignedRole, user, canEnterPage]);

  const acceptAuthenticatedUser = async (sessionOrUser, expectedRole = null) => {
    const authenticatedUser = sessionOrUser?.user || sessionOrUser;
    if (!authenticatedUser?.id) throw new Error("Unable to verify your login. Please sign in again.");
    let { data: roleRecord, error } = await supabase
      .from("user_roles")
      .select("role")
      .eq("user_id", authenticatedUser.id)
      .maybeSingle();
    if (error) throw new Error(`Could not verify your assigned role. Apply the account invitation migration and try again. (${error.message})`);

    if (!roleRecord?.role) {
      const endpoint = expectedRole === "staff"
        ? "/api/auth/activate-staff"
        : "/api/invitations/students/accept";
      const accessToken = sessionOrUser?.access_token || (await supabase.auth.getSession()).data?.session?.access_token;
      if (!accessToken) throw new Error("Your session is missing. Sign in again to complete account activation.");
      const activationResponse = await fetch(`${API_BASE_URL}${endpoint}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      const activationBody = await activationResponse.json().catch(() => ({}));
      if (!activationResponse.ok) {
        throw new Error(activationBody.detail || "Your account could not be activated. Check the invitation or Staff approval and try again.");
      }
      ({ data: roleRecord, error } = await supabase
        .from("user_roles")
        .select("role")
        .eq("user_id", authenticatedUser.id)
        .maybeSingle());
      if (error) throw new Error(`Could not reload the assigned role. (${error.message})`);
    }

    const trustedRole = normalizeRole(roleRecord?.role);
    if (!trustedRole) throw new Error("This account has no valid assigned role. Ask an administrator to configure it.");
    if (expectedRole && normalizeRole(expectedRole) !== trustedRole) {
      window.sessionStorage.removeItem("smart-erp-login-role");
      throw new Error(`This account is assigned to ${trustedRole[0].toUpperCase()}${trustedRole.slice(1)}, not ${expectedRole[0].toUpperCase()}${expectedRole.slice(1)}. Choose the assigned role and try again.`);
    }
    window.sessionStorage.removeItem("smart-erp-login-role");
    setRoleError("");
    setUser(authenticatedUser);
    setAssignedRole(trustedRole);
    resetCertificateData();
    const requestedPage = getRequestedPage();
    const cameFromAppNavigation = requestedPage
      && window.history.state?.smartErpPage === requestedPage;
    const landingPage = cameFromAppNavigation && canEnterPage(trustedRole, requestedPage)
      ? requestedPage
      : PAGES.DASHBOARD;
    if (requestedPage && landingPage !== requestedPage) updatePageUrl(landingPage, true);
    setCurrentPage(landingPage);
    passwordRecoveryPending.current = false;
    if (invitationAcceptancePending.current || staffRegistrationPending.current) {
      invitationAcceptancePending.current = false;
      staffRegistrationPending.current = false;
      const inviteUrl = new URL(window.location.href);
      inviteUrl.searchParams.delete("flow");
      window.history.replaceState({}, "", inviteUrl.toString());
    }
  };

  /* NAVIGATION */
  const navigate = useCallback((page) => {
    if (!VALID_PAGES.has(page)) {
      console.warn("Invalid page navigation:", page);
      return;
    }

    if (page !== PAGES.WELCOME && page !== PAGES.LOGIN && (!user || !canAccessPage(assignedRole, page))) {
      console.warn(`Role ${assignedRole} cannot access page ${page}`);
      return;
    }

    setCompactOpen(false);
    updatePageUrl(page);
    setCurrentPage(page);
  }, [assignedRole, setCompactOpen, user]);

  const withAssistantDock = (content) => (
    <>
      {content}
      <AIAssistantDock user={user} currentPage={currentPage} onExpand={() => navigate(PAGES.AI_ASSISTANT)} />
    </>
  );

  /* START NEW CERTIFICATE WORKFLOW */
  const startCertificateWorkflow = useCallback(() => {
    resetCertificateData();
    navigate(PAGES.CERTIFICATE_CATEGORY);
  }, [navigate, resetCertificateData]);

  /* HOME NAVIGATION */
  const goHome = useCallback(() => {
    navigate(PAGES.WELCOME);
  }, [navigate]);

  /* DASHBOARD NAVIGATION */
  const goDashboard = useCallback(() => {
    navigate(PAGES.DASHBOARD);
  }, [navigate]);

  /* LOGIN */
  const handleLogin = useCallback(
    async (sessionOrUser, expectedRole = null) => {
      if (!supabase) {
        alert("Supabase is not configured. Check your .env file.");
        return;
      }

      try {
        await acceptAuthenticatedUser(sessionOrUser, expectedRole);
        return true;
      } catch (error) {
        console.error("Login failed:", error);
        setRoleError(error?.message || "Unable to verify your login. Please try again.");
        setUser(null);
        setAssignedRole(null);
        setCurrentPage(PAGES.LOGIN);
        return false;
      }
    },
    [resetCertificateData, canEnterPage]
  );

  /* LOGOUT */
  const handleLogout = useCallback(async () => {
    if (!supabase) return;
    setSigningOut(true);

    try {
      const { error } = await supabase.auth.signOut();

      if (error) {
        console.error("Logout failed:", error.message);
        alert(`Logout failed: ${error.message}`);
        return;
      }

      setUser(null);
      setAssignedRole(null);
      resetCertificateData();
      navigate(PAGES.WELCOME);
    } catch (error) {
      console.error("Logout error:", error);
      alert("Unable to log out. Please try again.");
    } finally {
      setSigningOut(false);
    }
  }, [navigate, resetCertificateData]);

  /* SELECT CERTIFICATE CATEGORY */
  const handleCategoryNext = useCallback(
    (category) => {
      if (!category) {
        alert("Please select a certificate category.");
        return;
      }

      setSelectedCategory(category);
      setSelectedCertificate(null);
      setDocumentData(null);
      navigate(PAGES.CERTIFICATE_TYPE);
    },
    [navigate]
  );

  /* SELECT CERTIFICATE TYPE */
  const handleCertificateTypeNext = useCallback(
    (documentType) => {
      if (!documentType) {
        alert("Please select a certificate type.");
        return;
      }

      setSelectedCertificate(documentType);
      setDocumentData(null);
      navigate(PAGES.CHOOSE_METHOD);
    },
    [navigate]
  );

  /* SAVE EXTRACTED CERTIFICATE */
  const handleSaveCertificate = useCallback(
    async (correctedResult) => {
      if (savingCertificate) return;

      if (!supabase) {
        alert("Supabase is not configured. Check your .env file.");
        return;
      }

      if (!correctedResult) {
        alert("No extracted certificate data is available.");
        return;
      }

      setSavingCertificate(true);

      try {
        const {
          data: { user: authenticatedUser },
          error: authError,
        } = await supabase.auth.getUser();

        if (authError || !authenticatedUser?.id) {
          alert("Your session has expired. Please log in again.");
          return;
        }

        const certificateType = getCertificateType(
          selectedCertificate,
          correctedResult
        );

        const studentName = getStudentName(correctedResult);
        const certificateName = getCertificateName(documentData);

        const recordToSave = {
          user_id: authenticatedUser.id,
          student_name: studentName,
          certificate_type: certificateType,
          certificate_name: certificateName,
          date_extracted: new Date().toISOString().slice(0, 10),
          status: "Extracted",
          ocr_result: correctedResult,
        };

        const { error: insertError } = await supabase
          .from("certificates")
          .insert(recordToSave);

        if (insertError) {
          console.error("Certificate save failed:", insertError);
          alert(`Could not save certificate:\n${insertError.message}`);
          return;
        }

        setDocumentData((previous) => ({
          ...(previous || {}),
          ocrResult: correctedResult,
        }));

        navigate(PAGES.CERTIFICATE_DATABASE);
      } catch (error) {
        console.error("Unexpected save error:", error);
        alert(error?.message || "Unable to save certificate.");
      } finally {
        setSavingCertificate(false);
      }
    },
    [
      savingCertificate,
      selectedCertificate,
      documentData,
      navigate,
    ]
  );

  /* SUPABASE CONFIGURATION CHECK */
  if (!supabase) {
    return (
      <main
        style={{
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          padding: 24,
          fontFamily: "Arial, sans-serif",
          background: "#f3f7fd",
          color: "#153d6b",
        }}
      >
        <section>
          <h2>Supabase Configuration Missing</h2>
          <p>
            Add VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY
            to frontend/.env, then restart the development server.
          </p>
        </section>
      </main>
    );
  }

  /* SESSION LOADING */
  if (authLoading) {
    return (
      <main
        style={{
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          fontFamily: "Arial, sans-serif",
          color: "#153d6b",
          background: "#f3f7fd",
        }}
      >
        Checking your session...
      </main>
    );
  }

  /* WELCOME */
  if (currentPage === PAGES.WELCOME) {
    return <WelcomePage onStart={() => navigate(PAGES.LOGIN)} />;
  }

  /* LOGIN */
  if (currentPage === PAGES.LOGIN) {
    return (
      <LoginPage
        onLogin={handleLogin}
        onBack={goHome}
        authError={roleError}
      />
    );
  }

  if (currentPage === PAGES.RESET_PASSWORD) {
    return <PasswordResetPage invitation={invitationAcceptancePending.current} onComplete={handleLogin} onBack={() => navigate(PAGES.LOGIN)} />;
  }

  /* DASHBOARD */
  if (currentPage === PAGES.DASHBOARD) {
    return withAssistantDock(
      <Dashboard
        user={user}
        userRole={assignedRole}
        onNavigate={navigate}
        onUpload={startCertificateWorkflow}
        onLogout={handleLogout}
        logoutLoading={signingOut}
        onGlobalSearch={(query) => {
          setGlobalSearchQuery(query);
          navigate(PAGES.SEARCH);
        }}
      />
    );
  }

  /* CERTIFICATE CATEGORY */
  if (currentPage === PAGES.CERTIFICATE_CATEGORY) {
    return withAssistantDock(
      <CertificateCategory
        onBack={goDashboard}
        onHome={goHome}
        onNavigate={navigate}
        onNext={handleCategoryNext}
      />
    );
  }

  /* CERTIFICATE TYPE */
  if (currentPage === PAGES.CERTIFICATE_TYPE) {
    return withAssistantDock(
      <CertificateType
        certificate={selectedCategory}
        onBack={() => navigate(PAGES.CERTIFICATE_CATEGORY)}
        onNext={handleCertificateTypeNext}
        onNavigate={navigate}
      />
    );
  }

  /* CHOOSE SCAN OR UPLOAD */
  if (currentPage === PAGES.CHOOSE_METHOD) {
    return withAssistantDock(
      <ChooseMethod
        certificate={selectedCertificate}
        onBack={() => navigate(PAGES.CERTIFICATE_TYPE)}
        onNext={() => {
          setDocumentData(null);
          navigate(PAGES.SCAN_UPLOAD);
        }}
        onNavigate={(page) => {
          if (
            page === PAGES.UPLOAD ||
            page === PAGES.SCAN_UPLOAD
          ) {
            setDocumentData(null);
          }

          navigate(page);
        }}
      />
    );
  }

  /* CAMERA / SCAN */
  if (currentPage === PAGES.SCAN_UPLOAD) {
    return withAssistantDock(
      <ScanUpload
        certificate={selectedCertificate}
        onBack={() => navigate(PAGES.CHOOSE_METHOD)}
        onNext={(scanData) => {
          if (!scanData) {
            alert("No scanned document data was received.");
            return;
          }

          setDocumentData({
            ...scanData,
            source: "camera",
          });

          navigate(PAGES.PROCESSING);
        }}
        onNavigate={navigate}
      />
    );
  }

  /* FILE UPLOAD */
  if (currentPage === PAGES.UPLOAD) {
    return withAssistantDock(
      <UploadPage
        certificate={selectedCertificate}
        onBack={() => navigate(PAGES.CHOOSE_METHOD)}
        onNext={(uploadData) => {
          if (!uploadData) {
            alert("Please select a file first.");
            return;
          }

          setDocumentData({
            ...uploadData,
            source: "upload",
          });

          navigate(PAGES.PROCESSING);
        }}
        onNavigate={navigate}
      />
    );
  }

  /* OCR PROCESSING */
  if (currentPage === PAGES.PROCESSING) {
    return withAssistantDock(
      <Processing
        certificate={selectedCertificate}
        documentData={documentData}
        supabase={supabase}
        onBack={() =>
          navigate(
            documentData?.source === "upload"
              ? PAGES.UPLOAD
              : PAGES.SCAN_UPLOAD
          )
        }
        onNext={(ocrResult) => {
          if (!ocrResult) {
            alert("No extracted data was received.");
            return;
          }

          setDocumentData((previous) => ({
            ...(previous || {}),
            ocrResult,
          }));

          navigate(PAGES.EXTRACTED_DATA);
        }}
        onNavigate={navigate}
      />
    );
  }

  /* EXTRACTED CERTIFICATE REVIEW */
  if (currentPage === PAGES.EXTRACTED_DATA) {
    return withAssistantDock(
      <ExtractedData
        certificate={selectedCertificate}
        documentData={documentData}
        onBack={() =>
          navigate(
            documentData?.source === "upload"
              ? PAGES.UPLOAD
              : PAGES.SCAN_UPLOAD
          )
        }
        onNext={handleSaveCertificate}
        onNavigate={navigate}
        saving={savingCertificate}
      />
    );
  }

  /* CERTIFICATE DATABASE */
  if (currentPage === PAGES.CERTIFICATE_DATABASE) {
    return withAssistantDock(
      <CertificateDatabase
        supabase={supabase}
        user={user}
        userRole={assignedRole}
        documentData={documentData}
        certificate={selectedCertificate}
        onBack={() => navigate(PAGES.EXTRACTED_DATA)}
        onNavigate={navigate}
      />
    );
  }

  /* DASHBOARD MENU PAGES */
  if (currentPage === PAGES.AI_ASSISTANT) {
    return (
      <AIAssistant
        onBack={goDashboard}
        onReturnToSmall={() => {
          goDashboard();
          setCompactOpen(true);
        }}
      />
    );
  }

  const menuPages = {
    [PAGES.EXTRACTED_DATA_MENU]: CertificateDatabase,
    [PAGES.MY_CERTIFICATES]: MyCertificates,
    [PAGES.STUDENTS]: Students,
    [PAGES.SEARCH]: Search,
    [PAGES.REPORTS]: Reports,
    [PAGES.SETTINGS]: Settings,
    [PAGES.USER_MANAGEMENT]: AdminUsers,
  };

  const PageComponent = menuPages[currentPage];

  if (PageComponent) {
    return withAssistantDock(
      <PageComponent
        supabase={supabase}
        user={user}
        userRole={assignedRole}
        documentData={documentData}
        certificate={selectedCertificate}
        selectedType={currentPage === PAGES.EXTRACTED_DATA_MENU ? selectedExtractedType : undefined}
        onSelectType={currentPage === PAGES.EXTRACTED_DATA_MENU ? setSelectedExtractedType : undefined}
        initialQuery={currentPage === PAGES.SEARCH ? globalSearchQuery : ""}
        onGlobalSearch={(query) => {
          setGlobalSearchQuery(query);
          navigate(PAGES.SEARCH);
        }}
        onLogout={handleLogout}
        logoutLoading={signingOut}
        onBack={() => {
          if (currentPage === PAGES.EXTRACTED_DATA_MENU) setSelectedExtractedType(null);
          goDashboard();
        }}
        onNavigate={navigate}
      />
    );
  }

  /* FALLBACK */
  return <WelcomePage onStart={() => navigate(PAGES.LOGIN)} />;
}

export default function App() {
  return (
    <AIConversationProvider>
      <AppContent />
    </AIConversationProvider>
  );
}
