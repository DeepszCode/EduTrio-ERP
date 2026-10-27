-- Add the certificate-to-auth-user link used for Student ownership and restore
-- role-scoped access on installations where earlier policy migrations were not
-- applied. Existing certificate values are preserved; unlinked rows stay NULL.

ALTER TABLE public.certificates
  ADD COLUMN IF NOT EXISTS student_user_id uuid
  REFERENCES auth.users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS certificates_student_user_id_idx
  ON public.certificates (student_user_id);

ALTER TABLE public.certificates ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.certificates TO authenticated;
REVOKE ALL ON public.certificates FROM anon;

CREATE OR REPLACE FUNCTION public.current_user_role()
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT role
  FROM public.user_roles
  WHERE user_id = (SELECT auth.uid())
  LIMIT 1;
$$;
REVOKE ALL ON FUNCTION public.current_user_role() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_user_role() TO authenticated;

DROP POLICY IF EXISTS certificates_role_scope_select ON public.certificates;
CREATE POLICY certificates_role_scope_select
  ON public.certificates AS RESTRICTIVE FOR SELECT TO authenticated
  USING (public.current_user_role() IN ('admin', 'staff') OR student_user_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS certificates_role_access_select ON public.certificates;
CREATE POLICY certificates_role_access_select
  ON public.certificates FOR SELECT TO authenticated
  USING (
    public.current_user_role() IN ('admin', 'staff')
    OR (public.current_user_role() = 'student' AND student_user_id = (SELECT auth.uid()))
  );

DROP POLICY IF EXISTS certificates_role_scope_insert ON public.certificates;
CREATE POLICY certificates_role_scope_insert
  ON public.certificates AS RESTRICTIVE FOR INSERT TO authenticated
  WITH CHECK (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())));
DROP POLICY IF EXISTS certificates_role_access_insert ON public.certificates;
CREATE POLICY certificates_role_access_insert
  ON public.certificates FOR INSERT TO authenticated
  WITH CHECK (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())));

DROP POLICY IF EXISTS certificates_role_scope_update ON public.certificates;
CREATE POLICY certificates_role_scope_update
  ON public.certificates AS RESTRICTIVE FOR UPDATE TO authenticated
  USING (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())))
  WITH CHECK (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())));
DROP POLICY IF EXISTS certificates_role_access_update ON public.certificates;
CREATE POLICY certificates_role_access_update
  ON public.certificates FOR UPDATE TO authenticated
  USING (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())))
  WITH CHECK (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())));

DROP POLICY IF EXISTS certificates_role_scope_delete ON public.certificates;
CREATE POLICY certificates_role_scope_delete
  ON public.certificates AS RESTRICTIVE FOR DELETE TO authenticated
  USING (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())));
DROP POLICY IF EXISTS certificates_role_access_delete ON public.certificates;
CREATE POLICY certificates_role_access_delete
  ON public.certificates FOR DELETE TO authenticated
  USING (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())));

NOTIFY pgrst, 'reload schema';
