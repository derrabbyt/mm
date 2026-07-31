export const environment = {
  production: false,
  // Empty on purpose: ng serve proxies /api to localhost:8000 via
  // proxy.conf.json, which keeps requests same-origin and CORS out of the way.
  apiBaseUrl: '',
  supabaseUrl: 'https://umvhbbkfwhotsjgzcztb.supabase.co',
  supabaseKey: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVtdmhiYmtmd2hvdHNqZ3pjenRiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU0MjY5ODUsImV4cCI6MjEwMTAwMjk4NX0.fo16KeKhdS8oDALZFvwvp4hMGV3pnFNt_esW3o1YOVc',
};
