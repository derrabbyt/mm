import { computed, inject, Injectable, signal } from '@angular/core';
import { Observable, of, switchMap, tap } from 'rxjs';
import { MeetupsService as MeetupsApi } from '../../open-api/api/meetups.service';
import { MeetupRead } from '../../open-api/model/meetup-read';
import { MeetupParticipantRead } from '../../open-api/model/meetup-participant-read';
import { Position } from '../../open-api/model/position';

const DEFAULT_MEETUP_NAME = 'My meetup';
const DEFAULT_MEETUP_LOCATION = 'Vienna';

@Injectable({
  providedIn: 'root',
})
export class MeetupService {

  private api = inject(MeetupsApi);

  private readonly _meetups = signal<MeetupRead[]>([]);
  private readonly _activeMeetupId = signal<string | null>(null);
  private readonly _participants = signal<MeetupParticipantRead[]>([]);
  private readonly _selectedId = signal<string | null>(null);

  readonly meetups = this._meetups.asReadonly();
  readonly activeMeetupId = this._activeMeetupId.asReadonly();
  readonly participants = this._participants.asReadonly();
  readonly selectedId = this._selectedId.asReadonly();

  readonly activeMeetup = computed(() =>
    this._meetups().find((m) => m.id === this._activeMeetupId()) ?? null,
  );

  readonly selected = computed(() =>
    this._participants().find((p) => p.id === this._selectedId()) ?? null,
  );

  readonly placed = computed(() => this._participants().filter((p) => p.position));

  load() {
    this.api
      .getMeetups()
      .pipe(
        tap((meetups) => this._meetups.set(meetups)),
        switchMap((meetups) => (meetups.length ? of(meetups[0]) : this.createDefaultMeetup())),
        tap((meetup) => this._activeMeetupId.set(meetup.id)),
        switchMap((meetup) => this.api.getParticipants({ meetupId: meetup.id })),
      )
      .subscribe({
        next: (participants) => this._participants.set(participants),
        error: (err) =>
          console.error('[meetup] loading the meetup and its participants failed', err),
      });
  }

  private createDefaultMeetup(): Observable<MeetupRead> {
    return this.api
      .createMeetup({
        createMeetupRequest: {
          name: DEFAULT_MEETUP_NAME,
          location: DEFAULT_MEETUP_LOCATION,
          starts_at: new Date().toISOString(),
        },
      })
      .pipe(tap((created) => this._meetups.set([created])));
  }

  refresh() {
    const meetupId = this._activeMeetupId();
    if (!meetupId) {
      this.load();
      return;
    }

    this.api.getParticipants({ meetupId }).subscribe({
      next: (participants) => this._participants.set(participants),
      error: (err) => console.error('[meetup] refreshing participants failed', err),
    });
  }

  selectMeetup(meetupId: string) {
    this._activeMeetupId.set(meetupId);
    this._selectedId.set(null);
    this.api.getParticipants({ meetupId }).subscribe({
      next: (participants) => this._participants.set(participants),
      error: (err) =>
        console.error('[meetup] loading participants for the selected meetup failed', err),
    });
  }

  addParticipant(name: string) {
    const meetupId = this._activeMeetupId();
    if (!meetupId) {
      console.error('[meetup] cannot add a participant before a meetup is active');
      return;
    }

    this.api
      .addParticipant({ meetupId, addParticipantRequest: { name } })
      .pipe(
        switchMap((added) => {
          this._selectedId.set(added.id);
          return this.api.getParticipants({ meetupId });
        }),
      )
      .subscribe({
        next: (participants) => this._participants.set(participants),
        error: (err) => console.error(`[meetup] adding participant "${name}" failed`, err),
      });
  }

  selectParticipant(id: string | null) {
    this._selectedId.set(id);
  }

  placeParticipant(position: Position) {
    const meetupId = this._activeMeetupId();
    const participant = this.selected();
    if (!meetupId || !participant) {
      return;
    }

    this.api
      .updateParticipant({
        meetupId,
        participantId: participant.id,
        updateParticipantRequest: {
          name: participant.name,
          travel_mode: participant.travel_mode,
          account_id: participant.account_id,
          position,
        },
      })
      .pipe(switchMap(() => this.api.getParticipants({ meetupId })))
      .subscribe({
        next: (participants) => this._participants.set(participants),
        error: (err) =>
          console.error(`[meetup] placing participant "${participant.name}" failed`, err),
      });
  }
}
