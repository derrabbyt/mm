import { Component, inject, OnInit, signal } from '@angular/core';
import { OAuthProvider, SupabaseService } from '../../services/supabase/supabase.service';
import { User } from '../../types/user.type';

@Component({
  selector: 'app-auth',
  imports: [],
  templateUrl: './auth.html',
})
export class Auth implements OnInit {
  private supabase = inject(SupabaseService);

  readonly user = signal<User | null>(null);
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
  }

  private async loadUser() {
    const { data } = await this.supabase.getUser();
    this.user.set(data.user);
  }
}
