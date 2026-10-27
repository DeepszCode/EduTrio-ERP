-- Apply in Supabase SQL Editor or with the Supabase CLI.
-- Existing accounts are assigned Student by default. Promote trusted users
-- to Admin/Staff explicitly after reviewing them; browser metadata is ignored.

CREATE TABLE IF NOT EXISTS public.user_roles (
  user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  role text NOT NULL DEFAULT 'student'
    CHECK (role IN ('admin', 'staff', 'student')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Existing projects may already have user_roles(id,user_id,role,created_at).
-- Keep that shape and make user_id suitable for role lookups/upserts.
ALTER TABLE public.user_roles
  ADD COLUMN IF NOT EXISTS id bigint,
  ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
CREATE SEQUENCE IF NOT EXISTS public.user_roles_id_seq;
SELECT setval('public.user_roles_id_seq', GREATEST(COALESCE((SELECT max(id) FROM public.user_roles), 1), (SELECT last_value FROM public.user_roles_id_seq)), true);
UPDATE public.user_roles SET id = nextval('public.user_roles_id_seq') WHERE id IS NULL;
ALTER TABLE public.user_roles ALTER COLUMN id SET DEFAULT nextval('public.user_roles_id_seq');
ALTER TABLE public.user_roles ALTER COLUMN id SET NOT NULL;
ALTER SEQUENCE public.user_roles_id_seq OWNED BY public.user_roles.id;
CREATE UNIQUE INDEX IF NOT EXISTS user_roles_id_unique ON public.user_roles(id);
CREATE UNIQUE INDEX IF NOT EXISTS user_roles_user_id_unique ON public.user_roles(user_id);

ALTER TABLE public.user_roles ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.current_user_role()
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT ur.role
  FROM public.user_roles AS ur
  WHERE ur.user_id = (SELECT auth.uid())
  LIMIT 1;
$$;

REVOKE ALL ON FUNCTION public.current_user_role() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_user_role() TO authenticated;

DROP POLICY IF EXISTS user_roles_select_own_or_admin ON public.user_roles;
CREATE POLICY user_roles_select_own_or_admin
  ON public.user_roles FOR SELECT TO authenticated
  USING (user_id = (SELECT auth.uid()) OR public.current_user_role() = 'admin');

-- Clients can never insert, change, or delete role assignments directly.
REVOKE ALL ON public.user_roles FROM anon, authenticated;
GRANT SELECT ON public.user_roles TO authenticated;

CREATE OR REPLACE FUNCTION public.assign_user_role(target_user_id uuid, new_role text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  IF new_role NOT IN ('admin', 'staff', 'student') THEN
    RAISE EXCEPTION 'Unsupported role';
  END IF;

  IF coalesce((SELECT auth.role()), '') <> 'service_role'
     AND coalesce(public.current_user_role(), '') <> 'admin' THEN
    RAISE EXCEPTION 'Only an administrator may assign roles';
  END IF;

  INSERT INTO public.user_roles (user_id, role, updated_at)
  VALUES (target_user_id, new_role, now())
  ON CONFLICT (user_id) DO UPDATE
    SET role = EXCLUDED.role, updated_at = now();
END;
$$;

REVOKE ALL ON FUNCTION public.assign_user_role(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.assign_user_role(uuid, text) TO authenticated, service_role;

CREATE OR REPLACE FUNCTION public.assign_student_role_on_signup()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  INSERT INTO public.user_roles (user_id, role)
  VALUES (NEW.id, 'student')
  ON CONFLICT (user_id) DO NOTHING;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS assign_student_role_after_signup ON auth.users;
CREATE TRIGGER assign_student_role_after_signup
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.assign_student_role_on_signup();

INSERT INTO public.user_roles (user_id, role)
SELECT id, 'student' FROM auth.users
ON CONFLICT (user_id) DO NOTHING;

-- Students can read only rows linked to their authenticated UUID; staff/admin
-- can manage the shared certificate database. The restrictive policies also
-- constrain any broader permissive policies that may already exist.
ALTER TABLE public.certificates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.certificates
  ADD COLUMN IF NOT EXISTS student_user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL;
REVOKE ALL ON public.certificates FROM anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.certificates TO authenticated;

DROP POLICY IF EXISTS certificates_role_scope_select ON public.certificates;
CREATE POLICY certificates_role_scope_select
  ON public.certificates AS RESTRICTIVE FOR SELECT TO authenticated
  USING (public.current_user_role() IN ('admin', 'staff') OR student_user_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS certificates_role_scope_insert ON public.certificates;
CREATE POLICY certificates_role_scope_insert
  ON public.certificates AS RESTRICTIVE FOR INSERT TO authenticated
  WITH CHECK (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())));
DROP POLICY IF EXISTS certificates_role_scope_update ON public.certificates;
CREATE POLICY certificates_role_scope_update
  ON public.certificates AS RESTRICTIVE FOR UPDATE TO authenticated
  USING (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())))
  WITH CHECK (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())));
DROP POLICY IF EXISTS certificates_role_scope_delete ON public.certificates;
CREATE POLICY certificates_role_scope_delete
  ON public.certificates AS RESTRICTIVE FOR DELETE TO authenticated
  USING (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())));

DROP POLICY IF EXISTS certificates_role_access_select ON public.certificates;
CREATE POLICY certificates_role_access_select
  ON public.certificates FOR SELECT TO authenticated
  USING (public.current_user_role() IN ('admin', 'staff', 'student') AND (public.current_user_role() IN ('admin', 'staff') OR student_user_id = (SELECT auth.uid())));
DROP POLICY IF EXISTS certificates_role_access_insert ON public.certificates;
CREATE POLICY certificates_role_access_insert
  ON public.certificates FOR INSERT TO authenticated
  WITH CHECK (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())));
DROP POLICY IF EXISTS certificates_role_access_update ON public.certificates;
CREATE POLICY certificates_role_access_update
  ON public.certificates FOR UPDATE TO authenticated
  USING (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())))
  WITH CHECK (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())));
DROP POLICY IF EXISTS certificates_role_access_delete ON public.certificates;
CREATE POLICY certificates_role_access_delete
  ON public.certificates FOR DELETE TO authenticated
  USING (public.current_user_role() IN ('admin', 'staff') AND (public.current_user_role() = 'admin' OR user_id = (SELECT auth.uid())));

-- Legacy student/certificate tables are accessed by the FastAPI service-role
-- client. Require a trusted Staff/Admin role for authenticated direct access;
-- the API independently checks the same role before using its service key.
DO $$
DECLARE
  table_name text;
  legacy_tables text[] := ARRAY[
    'student_identification', 'state_board_10th', 'state_board_12th',
    'cbse_10th_marksheet', 'cbse_12th_marksheet', 'state_board_10th_tc',
    'state_board_12th_tc', 'cbse_10th_tc', 'cbse_12th_tc',
    'birth_certificate', 'aadhaar_card', 'community_certificate',
    'income_certificate', 'nativity_certificate', 'degree_certificate',
    'diploma_certificate', 'diploma_semester_marksheet'
  ];
BEGIN
  FOREACH table_name IN ARRAY legacy_tables LOOP
    IF to_regclass(format('public.%I', table_name)) IS NOT NULL THEN
      EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);
      EXECUTE format('REVOKE ALL ON public.%I FROM anon', table_name);
      EXECUTE format('DROP POLICY IF EXISTS erp_role_scope ON public.%I', table_name);
      EXECUTE format(
        'CREATE POLICY erp_role_scope ON public.%I AS RESTRICTIVE FOR ALL TO authenticated USING (public.current_user_role() IN (''admin'', ''staff'')) WITH CHECK (public.current_user_role() IN (''admin'', ''staff''))',
        table_name
      );
      EXECUTE format('DROP POLICY IF EXISTS erp_role_access ON public.%I', table_name);
      EXECUTE format(
        'CREATE POLICY erp_role_access ON public.%I FOR ALL TO authenticated USING (public.current_user_role() IN (''admin'', ''staff'')) WITH CHECK (public.current_user_role() IN (''admin'', ''staff''))',
        table_name
      );
    END IF;
  END LOOP;
END;
$$;
