import { inject, Injectable, signal } from '@angular/core';
import { AccountsService as AccountsApi } from '../../open-api/api/accounts.service';
import { AccountRead } from '../../open-api/model/account-read';

@Injectable({
  providedIn: 'root',
})
export class AccountService {
  private api = inject(AccountsApi);

  private readonly _account = signal<AccountRead | null>(null);
  readonly account = this._account.asReadonly();

  load() {
    this.api.getMyAccount().subscribe((account) => this._account.set(account));
  }

  clear() {
    this._account.set(null);
  }
}
