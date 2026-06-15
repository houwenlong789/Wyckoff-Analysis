import { vi } from 'vitest'

vi.stubEnv('VITE_SUPABASE_URL', 'http://127.0.0.1:54321')
vi.stubEnv('VITE_SUPABASE_ANON_KEY', 'test-anon-key')
vi.stubGlobal('__APP_VERSION__', 'test-version')
