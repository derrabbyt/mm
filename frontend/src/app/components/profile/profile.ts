import { Component, inject, OnInit } from '@angular/core';
import { AccountService } from '../../services/account/account.service';

@Component({
  selector: 'app-profile',
  imports: [],
  templateUrl: './profile.html',
  styleUrl: './profile.css',
})
export class Profile implements OnInit {
  private accounts = inject(AccountService);

  readonly account = this.accounts.account;

  ngOnInit() {
    this.accounts.load();
  }
}
