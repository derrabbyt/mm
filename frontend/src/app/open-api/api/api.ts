export * from './accounts.service';
import { AccountsService } from './accounts.service';
export * from './main-test-api.service';
import { MainTestApiService } from './main-test-api.service';
export * from './meetup.service';
import { MeetupService } from './meetup.service';
export const APIS = [AccountsService, MainTestApiService, MeetupService];
