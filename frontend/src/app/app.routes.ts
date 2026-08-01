import { Routes } from '@angular/router';
import { Test } from './components/test/test';
import { Map } from './components/map/map';
import { Auth } from './components/auth/auth';
import { authGuard } from './guards/auth.guard';
import { ErrorFallback } from './components/error-fallback/error-fallback';

export const routes: Routes = [
    { path: 'test', component: Test, canActivate: [authGuard] },
    {path: 'map', component: Map, canActivate: [authGuard] },
    { path: 'auth', component: Auth },
    {path: 'error', component: ErrorFallback},
];


