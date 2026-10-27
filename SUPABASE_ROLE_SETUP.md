# SMART ERP role and invitation setup

## Live schema inspected

The Supabase REST schema reports `public.user_roles(id integer, user_id uuid, role text, created_at timestamptz)` and `public.student_invitations(id integer, email text, invited_by uuid, status text, created_at timestamptz)`. The inspected data had three Admin rows, six Student rows, no duplicate `user_id` values, and no invitation rows. The invitation table had no expiry or acceptance fields, so the second migration adds them. The migration retains the existing three Admin accounts in a database allowlist and prevents other accounts from being assigned Admin.

## Apply migrations

Apply [`20261026_role_access_control.sql`](supabase/migrations/20261026_role_access_control.sql), [`20261027_invited_account_workflows.sql`](supabase/migrations/20261027_invited_account_workflows.sql), [`20261028_admin_user_management.sql`](supabase/migrations/20261028_admin_user_management.sql), [`20261029_certificate_student_scope.sql`](supabase/migrations/20261029_certificate_student_scope.sql), [`20261030_state_board_hse_groups.sql`](supabase/migrations/20261030_state_board_hse_groups.sql), and [`20261031_ai_conversation_history.sql`](supabase/migrations/20261031_ai_conversation_history.sql), in order. The certificate-scope migration repairs installations where `certificates.student_user_id` is missing and restores role-scoped policies. The HSE groups migration only adds State Board 12th fields, and the chat-history migration adds a private, user-owned conversation table; existing tables and rows remain intact.

The Auth signup trigger now allows new Auth accounts only when the email is on the Staff allowlist or has a valid pending Student invitation. Role assignment occurs only after verified Staff activation or atomic Student invitation acceptance.

## Approve Staff registration

An Admin/project owner must add each Staff email before registration. Use lowercase email addresses:

```sql
insert into public.staff_registration_allowlist (email)
values (lower('staff@example.com'))
on conflict (email) do update set enabled = true;
```

The Staff member then chooses **Create Account → Staff**, registers, confirms the email, and signs in as Staff. The backend assigns `staff` only after checking the verified Supabase identity against this server-side database allowlist. Staff access is never granted from browser role data.

## Student invitations

Staff can invite a Student from the **Students** page. The backend stores the normalized email and verified inviter UUID, sets a 24-hour database expiry, then sends a Supabase Auth invitation. The link must redirect to the frontend with `?flow=student-invite`; the Student sets a password, and the backend atomically marks the invitation accepted and assigns `student`. Expired, revoked, accepted, and failed invitations cannot be accepted. Supabase Auth invitation links also have their own expiry configured under Auth email OTP settings.

Admins can open **User Management** from the sidebar to change assigned roles and approve or disable Staff registration emails. Only the three UUIDs seeded into `approved_admin_accounts` can receive or retain the Admin role. The Students page lists all invitations to Admins and only each Staff member's own invitations; pending invitations can be revoked.

Protected pages also support direct query routes such as `/?page=reports` and `/?page=userManagement`. A logged-in role that is not permitted for the requested page is sent to Dashboard.

In Supabase Auth URL Configuration, allow the local/deployed frontend redirect URL, including `?flow=student-invite` and `?flow=staff-register`. Existing users with role rows continue to log in normally. Admin registration is disabled, and database triggers enforce that only the three existing allowlisted Admin UUIDs can keep or receive the Admin role.

## Backend environment

Set these values in `backend/.env` or the backend deployment environment:

```text
SUPABASE_SERVICE_ROLE_KEY=<server-only Supabase service role/secret key>
SMART_ERP_FRONTEND_URL=http://localhost:5173
```

Use the deployed frontend origin for `SMART_ERP_FRONTEND_URL` in production. The service key stays in the backend and must never be copied into `frontend/.env`. `VITE_API_URL` should point the frontend to the FastAPI service. Supabase Auth must have email invitations enabled and the redirect URL allowlisted.

The student identity feature continues to use `certificates.student_user_id`; certificate rows need a verified student link before appearing in that Student's views.
