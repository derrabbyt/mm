import { Routes } from '@angular/router';
import { Test } from './components/test/test';
import { Home } from './components/home/home';
import { Meetup } from './components/meetup/meetup';
import { Auth } from './components/auth/auth';
import { authGuard } from './guards/auth.guard';
import { ErrorFallback } from './components/error-fallback/error-fallback';
import { Profile } from './components/profile/profile';

export const routes: Routes = [
  { path: '', component: Home },
  { path: 'meetup/:id', component: Meetup, canActivate: [authGuard] },
  { path: 'profile', component: Profile, canActivate: [authGuard] },
  { path: 'test', component: Test, canActivate: [authGuard] },
  { path: 'auth', component: Auth },
  { path: 'error', component: ErrorFallback },
  { path: '**', redirectTo: '' },
];
