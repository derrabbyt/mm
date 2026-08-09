import { computed, inject, Injectable, signal } from '@angular/core';
import { Observable, switchMap } from 'rxjs';
import { MeetupsService as MeetupsApi } from '../../open-api/api/meetups.service';
import { CreateMeetupRequest } from '../../open-api/model/create-meetup-request';
import { MeetupRead } from '../../open-api/model/meetup-read';
import { MeetupParticipantRead } from '../../open-api/model/meetup-participant-read';
import { UpdateParticipantRequest } from '../../open-api/model/update-participant-request';
import { Position } from '../../open-api/model/position';
import { RendezvousRead } from '../../open-api/model/rendezvous-read';
import { TravelMode } from '../../open-api/model/travel-mode';
import { PhotonService } from '../photon/photon.service';
import { Router } from '@angular/router';

/** Below this the answer is either trivial or rejected, so we don't ask. */
const MIN_PLACED_FOR_RENDEZVOUS = 2;

/** Shown while the reverse lookup is in flight, and left in place if it fails. */
const UNNAMED_LOCATION = 'Dropped pin';

@Injectable({
  providedIn: 'root',
})
export class MeetupService {
  private api = inject(MeetupsApi);
  private photon = inject(PhotonService);
  private router = inject(Router);

  private readonly _meetups = signal<MeetupRead[]>([]);
  private readonly _meetupId = signal<string | null>(null);
  private readonly _meetup = signal<MeetupRead | null>(null);
  private readonly _participants = signal<MeetupParticipantRead[]>([]);
  private readonly _selectedId = signal<string | null>(null);
  private readonly _rendezvous = signal<RendezvousRead | null>(null);
  private readonly _rendezvousError = signal<string | null>(null);
  /** Participant id -> human-readable place. Display only, never sent to the
   *  API, and only filled for locations set during this session - reverse
   *  geocoding every participant on load would hammer Photon. */
  private readonly _addressLabels = signal<Record<string, string>>({});

  readonly meetups = this._meetups.asReadonly();
  readonly meetup = this._meetup.asReadonly();
  readonly participants = this._participants.asReadonly();
  readonly selectedId = this._selectedId.asReadonly();
  readonly rendezvous = this._rendezvous.asReadonly();
  readonly rendezvousError = this._rendezvousError.asReadonly();
  readonly addressLabels = this._addressLabels.asReadonly();

  readonly selected = computed(
    () => this._participants().find((p) => p.id === this._selectedId()) ?? null,
  );

  readonly placed = computed(() => this._participants().filter((p) => p.position));

  /** Travel times joined to participant names, in the order they are listed. */
  readonly travelTimes = computed(() => {
    const byId = new Map(this._participants().map((p) => [p.id, p]));
    return (this._rendezvous()?.per_participant ?? []).map((entry) => ({
      id: entry.participant_id,
      name: byId.get(entry.participant_id)?.name ?? 'Unknown',
      travelMode: byId.get(entry.participant_id)?.travel_mode ?? null,
      minutes: Math.round(entry.seconds / 60),
    }));
  });

  readonly excludedNames = computed(() => {
    const byId = new Map(this._participants().map((p) => [p.id, p]));
    return (this._rendezvous()?.excluded_participant_ids ?? []).map(
      (id) => byId.get(id)?.name ?? 'Unknown',
    );
  });

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
    this._rendezvous.set(null);
    this._rendezvousError.set(null);
    this._addressLabels.set({});

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
        next: (participants) => this.setParticipants(participants),
        error: (err) => console.error(`[meetup] adding participant "${name}" failed`, err),
      });
  }

  saveParticipant(id: string, name: string, travelMode: TravelMode) {
    this.update(id, { name, travel_mode: travelMode });
  }

  /** Place the selected participant, e.g. from a map click. */
  placeParticipant(position: Position) {
    const selected = this.selected();
    if (selected) {
      this.setParticipantPosition(selected.id, position);
    }
  }

  /**
   * Set one participant's position. Pass `label` when the caller already knows
   * what the place is called - picking it from the address search - otherwise
   * the name is looked up from the coordinates.
   */
  setParticipantPosition(id: string, position: Position, label?: string) {
    this.update(id, { position });

    if (label) {
      this.setAddressLabel(id, label);
      return;
    }

    this.setAddressLabel(id, UNNAMED_LOCATION);
    this.photon.reverse(position, { lang: 'de' }).subscribe({
      next: (place) => place && this.setAddressLabel(id, place.label),
      // The position is already saved, so a failed lookup costs only the name.
      error: (err) => console.error('[meetup] reverse geocoding failed', err),
    });
  }

  selectParticipant(id: string | null) {
    this._selectedId.set(id);
  }

  private setAddressLabel(id: string, label: string) {
    this._addressLabels.update((labels) => ({ ...labels, [id]: label }));
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
        next: (participants) => this.setParticipants(participants),
        error: (err) => console.error(`[meetup] updating participant ${id} failed`, err),
      });
  }

  private reloadParticipants(meetupId: string) {
    this.api.getParticipants({ meetupId }).subscribe({
      next: (participants) => this.setParticipants(participants),
      error: (err) => console.error('[meetup] loading participants failed', err),
    });
  }

  /** Every participant change moves the rendezvous, so the two travel together. */
  private setParticipants(participants: MeetupParticipantRead[]) {
    this._participants.set(participants);
    this.refreshRendezvous();
  }

  private refreshRendezvous() {
    const meetupId = this._meetupId();
    if (!meetupId || this.placed().length < MIN_PLACED_FOR_RENDEZVOUS) {
      this._rendezvous.set(null);
      this._rendezvousError.set(null);
      return;
    }

    this.api.getRendezvous({ meetupId }).subscribe({
      next: (rendezvous) => {
        this._rendezvous.set(rendezvous);
        this._rendezvousError.set(null);
      },
      error: (err) => {
        // 422s are expected here - someone off the grid, or nobody reachable
        // within the hour the matrices cover - and the envelope explains which.
        this._rendezvous.set(null);
        this._rendezvousError.set(err?.error?.message ?? 'No rendezvous spot could be found');
      },
    });
  }
}
