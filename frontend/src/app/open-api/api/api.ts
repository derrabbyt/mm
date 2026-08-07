export * from './accounts.service';
import { AccountsService } from './accounts.service';
export * from './main-test-api.service';
import { MainTestApiService } from './main-test-api.service';
export * from './meetups.service';
import { MeetupsService } from './meetups.service';
export const APIS = [AccountsService, MainTestApiService, MeetupsService];
