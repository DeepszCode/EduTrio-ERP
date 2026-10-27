-- Persist AI Assistant conversations per signed-in user. Row ownership is
-- enforced by RLS; chat transcripts are never readable across accounts.

CREATE TABLE IF NOT EXISTS public.ai_conversations (
  id text NOT NULL,
  user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  title text NOT NULL DEFAULT 'New chat',
  messages jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(messages) = 'array'),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, id)
);

ALTER TABLE public.ai_conversations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ai_conversations FROM anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.ai_conversations TO authenticated;

DROP POLICY IF EXISTS ai_conversations_owner_access ON public.ai_conversations;
CREATE POLICY ai_conversations_owner_access
  ON public.ai_conversations FOR ALL TO authenticated
  USING (user_id = (SELECT auth.uid()))
  WITH CHECK (user_id = (SELECT auth.uid()));

CREATE INDEX IF NOT EXISTS ai_conversations_user_updated_idx
  ON public.ai_conversations (user_id, updated_at DESC);

NOTIFY pgrst, 'reload schema';
