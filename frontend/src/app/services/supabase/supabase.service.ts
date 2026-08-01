import { Injectable } from '@angular/core';
import { createClient, SupabaseClient } from '@supabase/supabase-js';
import { environment } from '../../../environments/environment';

export type OAuthProvider = 'google' | 'github';

@Injectable({
  providedIn: 'root',
})
export class SupabaseService {
  private readonly supabase: SupabaseClient = createClient(
    environment.supabaseUrl,
    environment.supabaseKey,
  );

  // Kept in sync by onAuthStateChange so the OpenAPI client's Configuration
  // can read it synchronously per request, without awaiting getSession().
  private _accessToken: string | null = null;

  constructor() {
    this.supabase.auth.onAuthStateChange((_event, session) => {
      this._accessToken = session?.access_token ?? null;
    });
  }

  get accessToken(): string | undefined {
    return this._accessToken ?? undefined;
  }

  async signInWith(provider: OAuthProvider) {
    return await this.supabase.auth.signInWithOAuth({
      provider,
      options: { redirectTo: `${window.location.origin}/auth` },
    });
  }

  async getUser() {
    return await this.supabase.auth.getUser();
  }

  async signOut() {
    return await this.supabase.auth.signOut();
  }
}
