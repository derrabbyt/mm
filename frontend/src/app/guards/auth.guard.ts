import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { SupabaseService } from '../services/supabase.service';

export const authGuard: CanActivateFn = async (route, state) => {
  const supabaseService = inject(SupabaseService);
  const router = inject(Router);

  // getUser() resolves to { data, error }, which is truthy even when signed
  // out - the user itself is what has to be checked.
  const { data } = await supabaseService.getUser();
  if (!data.user) {
    router.navigate(['/auth'], { queryParams: { returnUrl: state.url } });
    return false;
  }
  return true;
};
