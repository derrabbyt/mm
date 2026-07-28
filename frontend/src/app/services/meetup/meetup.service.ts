import { computed, inject, Injectable, signal } from '@angular/core';
import { switchMap } from 'rxjs';
import { AddMemberRequestParams, MeetupService as MeetupApi } from '../../open-api/api/meetup.service';
import { MeetupMember } from '../../open-api/model/meetup-member';
import { Position } from '../../open-api/model/position';
import { AddMeetupMemberRequest } from '../../open-api/model/add-meetup-member-request';

@Injectable({
  providedIn: 'root',
})
export class MeetupService {

  private api = inject(MeetupApi);

  private readonly _members = signal<MeetupMember[]>([]);
  private readonly _selectedId = signal<string | null>(null);

  readonly members = this._members.asReadonly();
  readonly selectedId = this._selectedId.asReadonly();

  readonly selected = computed(() =>
    this._members().find((m) => m.id === this._selectedId()) ?? null,
  );

  /** Members that have been placed on the map. */
  readonly placed = computed(() => this._members().filter((m) => m.position));

  load() {
    this.api.getMembers().subscribe((members) => this._members.set(members));
  }

  /** Pull the current state from the backend. */
  refresh() {
    this.load();
  }

  addMember(member: AddMeetupMemberRequest) {
    const params: AddMemberRequestParams = { addMeetupMemberRequest: member };

    this.api
      .addMember(params)
      .pipe(
        switchMap((added) => {
          this._selectedId.set(added.id);
          return this.api.getMembers();
        }),
      )
      .subscribe((members) => this._members.set(members));
  }

  selectMember(id: string | null) {
    this._selectedId.set(id);
  }

  placeMember(position: Position) {
    const member = this.selected();
    if (!member) {
      return;
    }

    // updateMember is a full replace, so the current name has to be sent along
    // with the new position or it would be overwritten.
    this.api
      .updateMember({
        memberId: member.id,
        updateMeetupMemberRequest: { name: member.name, position },
      })
      .pipe(switchMap(() => this.api.getMembers()))
      .subscribe((members) => this._members.set(members));
  }
}
