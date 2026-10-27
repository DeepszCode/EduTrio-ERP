export const ROLES = Object.freeze({
  ADMIN: "admin",
  STAFF: "staff",
  STUDENT: "student",
});

export const ROLE_LABELS = Object.freeze({
  [ROLES.ADMIN]: "Admin",
  [ROLES.STAFF]: "Staff",
  [ROLES.STUDENT]: "Student",
});

export const ROLE_PAGES = Object.freeze({
  [ROLES.ADMIN]: new Set([
    "dashboard", "resetPassword", "certificateCategory", "certificateType", "chooseMethod",
    "scanUpload", "upload", "processing", "extractedData", "extractedDataMenu",
    "certificateDatabase", "myCertificates", "students", "search", "reports",
    "aiAssistant", "settings", "userManagement",
  ]),
  [ROLES.STAFF]: new Set([
    "dashboard", "resetPassword", "certificateCategory", "certificateType", "chooseMethod",
    "scanUpload", "upload", "processing", "extractedData", "extractedDataMenu",
    "certificateDatabase", "myCertificates", "students", "search", "aiAssistant",
  ]),
  [ROLES.STUDENT]: new Set([
    "dashboard", "resetPassword", "extractedDataMenu", "certificateDatabase", "myCertificates",
    "students", "search", "aiAssistant",
  ]),
});

export function normalizeRole(value) {
  const role = String(value || "").trim().toLowerCase();
  return Object.hasOwn(ROLE_LABELS, role) ? role : null;
}

export function canAccessPage(role, page) {
  return Boolean(ROLE_PAGES[normalizeRole(role)]?.has(page));
}
