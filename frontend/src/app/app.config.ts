import { ApplicationConfig, provideBrowserGlobalErrorListeners, inject } from '@angular/core';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { provideHttpClient } from '@angular/common/http';

import { routes } from './app.routes';
import { BASE_PATH, Configuration } from './open-api';
import { environment } from '../environments/environment';
import { SupabaseService } from './services/supabase/supabase.service';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(),
    {provide: BASE_PATH, useValue: environment.apiBaseUrl},
    {
      provide: Configuration,
      useFactory: () => {
        const supabase = inject(SupabaseService);
        return new Configuration({
          credentials: { HTTPBearer: () => supabase.accessToken },
        });
      },
    },
    ]
};

