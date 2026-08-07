import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { SupabaseService } from '../services/supabase/supabase.service';

export const authGuard: CanActivateFn = async (route, state) => {
  const supabaseService = inject(SupabaseService);
  const router = inject(Router);

  await supabaseService.ready;

  const { data } = await supabaseService.getUser();
  if (!data.user) {
    router.navigate(['/error'], { queryParams: { returnUrl: state.url } });
    //router.navigate(['/login'], { queryParams: { returnUrl: state.url } });

    return false;
  }
  return true;
};
