import { Routes } from '@angular/router';
import { Test } from './components/test/test';
import { Map } from './components/map/map';
import { Auth } from './components/auth/auth';

export const routes: Routes = [
    { path: 'test', component: Test },
    {path: 'map', component: Map},
    { path: 'auth', component: Auth },
];


