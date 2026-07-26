import { Routes } from '@angular/router';
import { Test } from './components/test/test';
import { Map } from './components/map/map';

export const routes: Routes = [
    { path: 'test', component: Test },
    {path: 'map', component: Map},
];


