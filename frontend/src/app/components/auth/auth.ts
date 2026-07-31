import { Component, inject, OnInit, signal } from '@angular/core';
import { SupabaseService } from '../../services/supabase.service';
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
  readonly loading = signal(false);

  ngOnInit() {
    void this.loadUser();
  }

  async signInWithGoogle() {
    this.error.set('');
    this.loading.set(true);

    const { error } = await this.supabase.signInWithGoogle();
    // On success the browser is already navigating to Google, so only the
    // failure path has anything left to do here.
    if (error) {
      this.error.set(error.message);
      this.loading.set(false);
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
