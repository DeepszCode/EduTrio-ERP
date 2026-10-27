-- Support the authenticated Admin user and Staff-approval management API.
-- Rows remain inaccessible to anon/authenticated client roles.
GRANT SELECT, INSERT, UPDATE ON public.staff_registration_allowlist TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.user_roles TO service_role;
