// Production values. Swapped for environment.development.ts by the
// fileReplacements entry in angular.json's development configuration.
export const environment = {
  production: true,
  // Empty means same-origin: the deployed frontend calls /api on its own host.
  // Point it at an absolute URL when the backend lives somewhere else.
  apiBaseUrl: '',
  // Self-hosted Photon. Same-origin like apiBaseUrl, so whatever serves the
  // frontend has to route /photon to the container's port 2322.
  photonBaseUrl: '/photon',
  supabaseUrl: 'https://umvhbbkfwhotsjgzcztb.supabase.co',
  supabaseKey:
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVtdmhiYmtmd2hvdHNqZ3pjenRiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU0MjY5ODUsImV4cCI6MjEwMTAwMjk4NX0.fo16KeKhdS8oDALZFvwvp4hMGV3pnFNt_esW3o1YOVc',
};
