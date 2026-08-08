import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { SupabaseService } from '../../services/supabase/supabase.service';
import { AccountService } from '../../services/account/account.service';

@Component({
  selector: 'app-header',
  imports: [RouterLink],
  templateUrl: './header.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class Header {
  private supabase = inject(SupabaseService);
  private accounts = inject(AccountService);
  private router = inject(Router);

  readonly user = this.supabase.user;

  async logout() {
    await this.supabase.signOut();
    this.accounts.clear();
    await this.router.navigate(['/']);
  }
}
