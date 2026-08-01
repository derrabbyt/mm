import { Component, inject, OnInit, signal } from '@angular/core';
import { OAuthProvider, SupabaseService } from '../../services/supabase/supabase.service';
import { AccountService } from '../../services/account/account.service';
import { User } from '../../types/user.type';

@Component({
  selector: 'app-auth',
  imports: [],
  templateUrl: './auth.html',
})
export class Auth implements OnInit {
  private supabase = inject(SupabaseService);
  private accounts = inject(AccountService);

  readonly user = signal<User | null>(null);
  readonly account = this.accounts.account;
  readonly error = signal('');
  readonly pending = signal<OAuthProvider | null>(null);

  ngOnInit() {
    void this.loadUser();
  }

  async signIn(provider: OAuthProvider) {
    this.error.set('');
    this.pending.set(provider);

    const { error } = await this.supabase.signInWith(provider);
    if (error) {
      this.error.set(error.message);
      this.pending.set(null);
    }
  }

  async signOut() {
    await this.supabase.signOut();
    this.user.set(null);
    this.accounts.clear();
  }

  private async loadUser() {
    const { data } = await this.supabase.getUser();
    this.user.set(data.user);

    // The backend account is the source of truth for display_name/avatar_url
    // once profile_customized is set - loading it here is what creates it on
    // first login, and refreshes it (without overwriting the profile) after.
    if (data.user) {
      this.accounts.load();
    }
  }
}
