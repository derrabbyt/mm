import { computed, inject, Injectable, signal } from '@angular/core';
import { Observable, switchMap } from 'rxjs';
import { MeetupsService as MeetupsApi } from '../../open-api/api/meetups.service';
import { CreateMeetupRequest } from '../../open-api/model/create-meetup-request';
import { MeetupRead } from '../../open-api/model/meetup-read';
import { MeetupParticipantRead } from '../../open-api/model/meetup-participant-read';
import { UpdateParticipantRequest } from '../../open-api/model/update-participant-request';
import { Position } from '../../open-api/model/position';
import { TravelMode } from '../../open-api/model/travel-mode';
import { Router } from '@angular/router';

@Injectable({
  providedIn: 'root',
})
export class MeetupService {
  private api = inject(MeetupsApi);
  private router = inject(Router);
  

  private readonly _meetups = signal<MeetupRead[]>([]);
  private readonly _meetupId = signal<string | null>(null);
  private readonly _meetup = signal<MeetupRead | null>(null);
  private readonly _participants = signal<MeetupParticipantRead[]>([]);
  private readonly _selectedId = signal<string | null>(null);

  readonly meetups = this._meetups.asReadonly();
  readonly meetup = this._meetup.asReadonly();
  readonly participants = this._participants.asReadonly();
  readonly selectedId = this._selectedId.asReadonly();

  readonly selected = computed(
    () => this._participants().find((p) => p.id === this._selectedId()) ?? null,
  );

  readonly placed = computed(() => this._participants().filter((p) => p.position));

  loadMeetups() {
    this.api.getMeetups().subscribe({
      next: (meetups) => this._meetups.set(meetups),
      error: (err) => console.error('[meetup] loading meetups failed', err),
    });
  }

  create(request: CreateMeetupRequest): Observable<MeetupRead> {
    return this.api.createMeetup({ createMeetupRequest: request });
  }

  open(meetupId: string) {
    this._meetupId.set(meetupId);
    this._meetup.set(null);
    this._participants.set([]);
    this._selectedId.set(null);

    this.api.getMeetup({ meetupId }).subscribe({
      next: (meetup) => {
            this._meetup.set(meetup);
            this.reloadParticipants(meetupId);
      },
      error: (err) => { 
        console.error('[meetup] loading meetup failed', err); 
        this.router.navigate(['/error']);
      },
    });

  }

  addParticipant(name: string) {
    const meetupId = this._meetupId();
    if (!meetupId) {
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

  saveParticipant(id: string, name: string, travelMode: TravelMode) {
    this.update(id, { name, travel_mode: travelMode });
  }

  placeParticipant(position: Position) {
    const selected = this.selected();
    if (selected) {
      this.update(selected.id, { position });
    }
  }

  selectParticipant(id: string | null) {
    this._selectedId.set(id);
  }

  private update(id: string, changes: Partial<UpdateParticipantRequest>) {
    const meetupId = this._meetupId();
    const participant = this._participants().find((p) => p.id === id);
    if (!meetupId || !participant) {
      return;
    }

    this.api
      .updateParticipant({
        meetupId,
        participantId: id,
        updateParticipantRequest: {
          name: participant.name,
          travel_mode: participant.travel_mode,
          position: participant.position,
          account_id: participant.account_id,
          ...changes,
        },
      })
      .pipe(switchMap(() => this.api.getParticipants({ meetupId })))
      .subscribe({
        next: (participants) => this._participants.set(participants),
        error: (err) => console.error(`[meetup] updating participant ${id} failed`, err),
      });
  }

  private reloadParticipants(meetupId: string) {
    this.api.getParticipants({ meetupId }).subscribe({
      next: (participants) => this._participants.set(participants),
      error: (err) => console.error('[meetup] loading participants failed', err),
    });
  }
}
