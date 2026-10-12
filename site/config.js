/* The Supabase project the page reads (ADR 0004). The publishable key is public by design:
   it only maps a request to the anon or authenticated role, and RLS decides the rest
   (ADR 0005). `tools/devsite.py` serves its own copy of this file for local work. */
export const SUPABASE_URL = "https://uvczfqmaczgdbmvusxqt.supabase.co";
export const SUPABASE_KEY = "sb_publishable_chM0U_w3NKJB_Insa1ufOA_ayvXbOzc";
export const API_SCHEMA = "api";
