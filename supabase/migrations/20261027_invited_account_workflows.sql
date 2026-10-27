-- Secure Staff registration, student invitations, and the three existing
-- Admin accounts. This migration targets the live table shapes inspected on
-- 2026-10-26: user_roles(id,user_id,role,created_at) and
-- student_invitations(id,email,invited_by,status,created_at).

ALTER TABLE public.user_roles
  ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

CREATE SEQUENCE IF NOT EXISTS public.user_roles_id_seq;
SELECT setval('public.user_roles_id_seq', GREATEST(COALESCE((SELECT max(id) FROM public.user_roles), 1), (SELECT last_value FROM public.user_roles_id_seq)), true);
ALTER TABLE public.user_roles ALTER COLUMN id SET DEFAULT nextval('public.user_roles_id_seq');
ALTER SEQUENCE public.user_roles_id_seq OWNED BY public.user_roles.id;

CREATE UNIQUE INDEX IF NOT EXISTS user_roles_user_id_unique
  ON public.user_roles (user_id);

DO $$
DECLARE constraint_row record;
BEGIN
  FOR constraint_row IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'public.user_roles'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%role%'
  LOOP
    EXECUTE format('ALTER TABLE public.user_roles DROP CONSTRAINT %I', constraint_row.conname);
  END LOOP;
  ALTER TABLE public.user_roles
    ADD CONSTRAINT user_roles_role_allowed
    CHECK (role IN ('admin', 'staff', 'student'));
END;
$$;

CREATE OR REPLACE FUNCTION public.current_user_role()
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT role FROM public.user_roles
  WHERE user_id = (SELECT auth.uid())
  LIMIT 1;
$$;
REVOKE ALL ON FUNCTION public.current_user_role() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_user_role() TO authenticated;

ALTER TABLE public.user_roles ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.user_roles FROM anon, authenticated;
GRANT SELECT ON public.user_roles TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.user_roles TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.user_roles_id_seq TO service_role;
DROP POLICY IF EXISTS user_roles_select_own_or_admin ON public.user_roles;
CREATE POLICY user_roles_select_own_or_admin
  ON public.user_roles FOR SELECT TO authenticated
  USING (user_id = (SELECT auth.uid()) OR public.current_user_role() = 'admin');

CREATE TABLE IF NOT EXISTS public.approved_admin_accounts (
  user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.approved_admin_accounts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.approved_admin_accounts FROM anon, authenticated;

-- Preserve the three Admin accounts already explicitly assigned in user_roles.
INSERT INTO public.approved_admin_accounts (user_id)
SELECT DISTINCT user_id FROM public.user_roles WHERE role = 'admin'
ON CONFLICT (user_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS public.staff_registration_allowlist (
  email text PRIMARY KEY,
  enabled boolean NOT NULL DEFAULT true,
  added_by uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (email = lower(btrim(email)))
);
ALTER TABLE public.staff_registration_allowlist ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.staff_registration_allowlist FROM anon, authenticated;
GRANT SELECT ON public.staff_registration_allowlist TO service_role;
GRANT INSERT, UPDATE ON public.staff_registration_allowlist TO service_role;

ALTER TABLE public.student_invitations
  ADD COLUMN IF NOT EXISTS expires_at timestamptz NOT NULL DEFAULT (now() + interval '24 hours'),
  ADD COLUMN IF NOT EXISTS accepted_at timestamptz,
  ADD COLUMN IF NOT EXISTS accepted_by uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS revoked_at timestamptz,
  ADD COLUMN IF NOT EXISTS auth_invitation_sent_at timestamptz;

CREATE SEQUENCE IF NOT EXISTS public.student_invitations_id_seq;
SELECT setval('public.student_invitations_id_seq', GREATEST(COALESCE((SELECT max(id) FROM public.student_invitations), 1), (SELECT last_value FROM public.student_invitations_id_seq)), true);
ALTER TABLE public.student_invitations ALTER COLUMN id SET DEFAULT nextval('public.student_invitations_id_seq');
ALTER SEQUENCE public.student_invitations_id_seq OWNED BY public.student_invitations.id;

UPDATE public.student_invitations
SET email = lower(btrim(email))
WHERE email <> lower(btrim(email));

DO $$
DECLARE constraint_row record;
BEGIN
  FOR constraint_row IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'public.student_invitations'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%status%'
  LOOP
    EXECUTE format('ALTER TABLE public.student_invitations DROP CONSTRAINT %I', constraint_row.conname);
  END LOOP;
  ALTER TABLE public.student_invitations
    ADD CONSTRAINT student_invitations_status_allowed
    CHECK (status IN ('pending', 'accepted', 'revoked', 'expired', 'failed'));
END;
$$;

CREATE UNIQUE INDEX IF NOT EXISTS student_invitations_one_pending_email
  ON public.student_invitations (lower(email))
  WHERE status = 'pending' AND revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS student_invitations_email_status_expiry
  ON public.student_invitations (lower(email), status, expires_at);

ALTER TABLE public.student_invitations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.student_invitations FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.student_invitations TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.student_invitations_id_seq TO service_role;

CREATE OR REPLACE FUNCTION public.guard_smart_erp_user_role()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  IF NEW.role = 'admin' AND NOT EXISTS (
    SELECT 1 FROM public.approved_admin_accounts AS approved
    WHERE approved.user_id = NEW.user_id
  ) THEN
    RAISE EXCEPTION 'This account is not on the approved Admin list';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS guard_smart_erp_user_role ON public.user_roles;
CREATE TRIGGER guard_smart_erp_user_role
  BEFORE INSERT OR UPDATE OF user_id, role ON public.user_roles
  FOR EACH ROW EXECUTE FUNCTION public.guard_smart_erp_user_role();

-- The older open signup trigger assigned Student to every new Auth user. Remove
-- it; new Auth accounts must match an active invitation or Staff allowlist row.
DROP TRIGGER IF EXISTS assign_student_role_after_signup ON auth.users;
DROP FUNCTION IF EXISTS public.assign_student_role_on_signup();

CREATE OR REPLACE FUNCTION public.guard_smart_erp_auth_signup()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  normalized_email text := lower(btrim(coalesce(NEW.email, '')));
BEGIN
  IF normalized_email = '' THEN
    RAISE EXCEPTION 'A verified email address is required';
  END IF;

  IF EXISTS (
    SELECT 1 FROM public.student_invitations AS invitation
    WHERE lower(invitation.email) = normalized_email
      AND invitation.status = 'pending'
      AND invitation.revoked_at IS NULL
      AND invitation.expires_at > now()
  ) OR EXISTS (
    SELECT 1 FROM public.staff_registration_allowlist AS staff
    WHERE staff.email = normalized_email AND staff.enabled
  ) THEN
    RETURN NEW;
  END IF;

  RAISE EXCEPTION 'Registration requires a valid Student invitation or an approved Staff email';
END;
$$;

DROP TRIGGER IF EXISTS guard_smart_erp_auth_signup ON auth.users;
CREATE TRIGGER guard_smart_erp_auth_signup
  BEFORE INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.guard_smart_erp_auth_signup();

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
  IF new_role = 'admin' AND NOT EXISTS (
    SELECT 1 FROM public.approved_admin_accounts AS approved
    WHERE approved.user_id = target_user_id
  ) THEN
    RAISE EXCEPTION 'This account is not on the approved Admin list';
  END IF;
  INSERT INTO public.user_roles (user_id, role, updated_at)
  VALUES (target_user_id, new_role, now())
  ON CONFLICT (user_id) DO UPDATE
    SET role = EXCLUDED.role, updated_at = now();
END;
$$;
REVOKE ALL ON FUNCTION public.assign_user_role(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.assign_user_role(uuid, text) TO authenticated, service_role;

CREATE OR REPLACE FUNCTION public.accept_student_invitation(
  target_user_id uuid,
  verified_email text
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  actual_email text;
  invitation_id bigint;
  existing_role text;
BEGIN
  SELECT lower(btrim(auth_user.email)) INTO actual_email
  FROM auth.users AS auth_user
  WHERE auth_user.id = target_user_id
    AND auth_user.email_confirmed_at IS NOT NULL;

  IF actual_email IS NULL OR actual_email <> lower(btrim(verified_email)) THEN
    RAISE EXCEPTION 'The signed-in account email is not verified';
  END IF;

  SELECT invitation.id INTO invitation_id
  FROM public.student_invitations AS invitation
  WHERE lower(invitation.email) = actual_email
    AND invitation.status = 'pending'
    AND invitation.revoked_at IS NULL
    AND invitation.expires_at > now()
    AND invitation.accepted_at IS NULL
  ORDER BY invitation.created_at DESC
  LIMIT 1
  FOR UPDATE;

  IF invitation_id IS NULL THEN
    UPDATE public.student_invitations
    SET status = 'expired'
    WHERE lower(email) = actual_email
      AND status = 'pending'
      AND (expires_at <= now() OR revoked_at IS NOT NULL);
    RAISE EXCEPTION 'No active, unused invitation exists for this verified email';
  END IF;

  SELECT role INTO existing_role
  FROM public.user_roles
  WHERE user_id = target_user_id
  LIMIT 1;

  IF existing_role IS NOT NULL AND existing_role <> 'student' THEN
    RAISE EXCEPTION 'This account already has a different assigned role';
  END IF;

  INSERT INTO public.user_roles (user_id, role, updated_at)
  VALUES (target_user_id, 'student', now())
  ON CONFLICT (user_id) DO UPDATE SET role = 'student', updated_at = now();

  UPDATE public.student_invitations
  SET status = 'accepted', accepted_at = now(), accepted_by = target_user_id
  WHERE id = invitation_id;

  RETURN invitation_id;
END;
$$;
REVOKE ALL ON FUNCTION public.accept_student_invitation(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.accept_student_invitation(uuid, text) TO service_role;
